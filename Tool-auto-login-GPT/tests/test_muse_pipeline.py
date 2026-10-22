import asyncio
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from auto_yt import main
from auto_yt.services import (
    chatgpt_worker,
    chatgpt_projects,
    muse_pipeline,
    muse_provider,
    muse_semantic_planner,
    muse_settings,
    muse_studio_service,
    video_production,
)


def _captions(duration: int) -> list[dict]:
    return [
        {"start": float(start), "end": float(min(duration, start + 5)), "text": f"line {start}"}
        for start in range(0, duration, 5)
    ]


def _semantic_payload(windows: list[dict]) -> dict:
    return {
        "story_context": "A coherent documentary story with concrete visual continuity.",
        "scenes": [
            {
                "index": int(window["index"]),
                "subject": f"documentary subject {window['index']}",
                "action": f"performs a distinct visible action for scene {window['index']}",
                "setting": f"specific location {window['index']}",
                "era": f"period {window['index']}",
                "visual_anchor": f"unique concrete visual anchor for scene {window['index']}",
                "safe_visual_anchor": (
                    f"quiet non-violent documentary moment for scene {window['index']}"
                ),
                "must_show": [
                    f"visible object {window['index']}",
                    f"location marker {window['index']}",
                ],
                "composition": "wide layered documentary framing",
                "lighting": "soft directional daylight",
                "color": "muted earth tones",
                "narrative_role": "setup",
            }
            for window in windows
        ],
    }


class TestMuseSettings(unittest.TestCase):
    def test_global_setting_uses_studio_path_and_drops_legacy_gpm_keys(self):
        self.assertEqual(
            muse_settings.normalize_muse_settings({"gpm_profile_id": " legacy "}),
            {"muse_studio_path": muse_settings.DEFAULT_MUSE_STUDIO_PATH},
        )
        self.assertEqual(
            muse_settings.normalize_muse_settings({"muse_studio_path": " C:\\Muse_AI_Studio "}),
            {"muse_studio_path": "C:\\Muse_AI_Studio"},
        )
        with patch.object(muse_settings, "write_private_text") as write_private:
            saved = muse_settings.save_muse_settings(
                {"muse_studio_path": "C:\\Muse", "gpm_profile_id": "profile-1"}
            )
        self.assertEqual(saved, {"muse_studio_path": "C:\\Muse"})
        serialized = write_private.call_args.args[1]
        self.assertIn('"muse_studio_path": "C:\\\\Muse"', serialized)
        self.assertNotIn('"gpm_profile_id":', serialized)

    def test_normalize_muse_mode_defaults_and_reference_limit(self):
        normalized = chatgpt_projects.normalize_image_generation_settings(
            {
                "render_mode": "muse_ai",
                "muse_settings": {
                    "video_coverage_mode": "invalid",
                    "video_coverage_minutes": 500,
                    "reference_sets": [
                        {
                            "id": "hero",
                            "name": "Hero",
                            "aliases": ["An", "an", "Anh An"],
                            "images": [f"folder/image-{index}.png" for index in range(8)],
                        }
                    ],
                },
            }
        )
        self.assertEqual(normalized["render_mode"], "muse_ai")
        self.assertEqual(normalized["provider"], "muse_ai")
        self.assertEqual(normalized["muse_settings"]["video_coverage_mode"], "intro_only")
        self.assertEqual(normalized["muse_settings"]["video_coverage_minutes"], 500)
        reference_set = normalized["muse_settings"]["reference_sets"][0]
        self.assertEqual(reference_set["aliases"], ["An", "Anh An"])
        self.assertEqual(len(reference_set["images"]), 6)
        self.assertTrue(all("/" not in value for value in reference_set["images"]))

    def test_legacy_modes_are_unchanged(self):
        self.assertEqual(
            chatgpt_projects.normalize_image_generation_settings({"render_mode": "stock_video"})[
                "render_mode"
            ],
            "stock_video",
        )
        self.assertEqual(
            chatgpt_projects.normalize_image_generation_settings({})["render_mode"],
            "google_flow",
        )

    def test_muse_reference_set_requires_one_to_six_images(self):
        with self.assertRaisesRegex(ValueError, "1 đến 6 ảnh"):
            chatgpt_projects.validate_image_generation_settings(
                {
                    "render_mode": "muse_ai",
                    "muse_settings": {
                        "reference_sets": [
                            {"id": "hero", "name": "Hero", "aliases": [], "images": []}
                        ]
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "1 đến 6 ảnh"):
            chatgpt_projects.validate_image_generation_settings(
                {
                    "render_mode": "muse_ai",
                    "muse_settings": {
                        "reference_sets": [
                            {
                                "id": "hero",
                                "name": "Hero",
                                "aliases": [],
                                "images": [f"hero-{index}.png" for index in range(7)],
                            }
                        ]
                    },
                }
            )


class TestMuseScenePlan(unittest.TestCase):
    def test_caption_aligned_windows_and_short_final_scene(self):
        windows = muse_pipeline.build_fixed_scene_windows(
            _captions(25),
            25,
            intro_end_seconds=12,
            coverage_mode="intro_only",
            coverage_minutes=5,
        )
        self.assertEqual([item["duration"] for item in windows], [10.0, 10.0, 5.0])
        self.assertEqual([item["start"] for item in windows], [0.0, 10.0, 20.0])
        self.assertEqual([item["is_video"] for item in windows], [True, True, False])

    def test_all_coverage_modes(self):
        intro = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="intro_only", coverage_minutes=5
        )
        first_minute = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="first_n_minutes", coverage_minutes=1
        )
        full = muse_pipeline.build_fixed_scene_windows(
            _captions(70), 70, intro_end_seconds=0, coverage_mode="full", coverage_minutes=5
        )
        self.assertEqual(sum(item["is_video"] for item in intro), 1)
        self.assertEqual(sum(item["is_video"] for item in first_minute), 6)
        self.assertTrue(all(item["is_video"] for item in full))

    def test_accepts_video_over_ninety_minutes(self):
        duration = 91 * 60 + 5
        windows = muse_pipeline.build_fixed_scene_windows(
            _captions(duration),
            duration,
            intro_end_seconds=0,
            coverage_mode="full",
            coverage_minutes=5,
        )
        self.assertEqual(len(windows), 547)
        self.assertEqual(windows[-1]["start"], 5460.0)
        self.assertEqual(windows[-1]["end"], float(duration))
        self.assertEqual(windows[-1]["duration"], 5.0)
        self.assertTrue(all(item["is_video"] for item in windows))

    def test_first_n_minutes_coverage_can_extend_beyond_ninety_minutes(self):
        duration = 121 * 60
        windows = muse_pipeline.build_fixed_scene_windows(
            _captions(duration),
            duration,
            intro_end_seconds=0,
            coverage_mode="first_n_minutes",
            coverage_minutes=120,
        )
        self.assertTrue(windows[719]["is_video"])
        self.assertFalse(windows[720]["is_video"])

    def test_plan_hash_changes_when_reference_content_changes(self):
        first = muse_pipeline._bind_reference_hashes(
            {"plan_hash": "base-plan"}, [{"sha256": "reference-a"}]
        )["plan_hash"]
        second = muse_pipeline._bind_reference_hashes(
            {"plan_hash": "base-plan"}, [{"sha256": "reference-b"}]
        )["plan_hash"]
        self.assertNotEqual(first, second)

    def test_caption_windows_match_sentence_boundaries_without_duplication(self):
        captions = [
            {"start": 0.08, "end": 5.4, "text": "Đèo Mang Yang mở đầu."},
            {"start": 5.4, "end": 10.66, "text": "Quốc lộ 19 năm 1954."},
            {"start": 10.66, "end": 19.3, "text": "Đoàn xe GM100 rời An Khê về Pleiku."},
            {"start": 19.3, "end": 26.1, "text": "Trung đoàn 96 chuẩn bị phục kích."},
            {"start": 26.56, "end": 30.22, "text": "Lực lượng giữ vị trí chờ đợi."},
            {"start": 30.22, "end": 35.22, "text": "Đoàn xe GM100 bị chặn."},
            {"start": 35.22, "end": 41.96, "text": "Xe thông tin mất liên lạc."},
            {"start": 41.96, "end": 50.56, "text": "Binh đoàn Pháp bị chia cắt và rút lui."},
            {"start": 51.02, "end": 56.32, "text": "Mang Yang trở thành nơi tưởng niệm."},
            {"start": 56.32, "end": 58.82, "text": "Hãy LIKE và đăng ký kênh."},
        ]
        windows = muse_pipeline.build_fixed_scene_windows(
            captions,
            58.823,
            intro_end_seconds=0,
            coverage_mode="full",
            coverage_minutes=5,
        )
        self.assertEqual(
            [(item["start"], item["end"]) for item in windows],
            [
                (0.0, 10.66),
                (10.66, 19.3),
                (19.3, 30.22),
                (30.22, 41.96),
                (41.96, 50.56),
                (50.56, 58.823),
            ],
        )
        joined = " || ".join(item["transcript"] for item in windows)
        for caption in captions:
            self.assertEqual(joined.count(caption["text"]), 1)

    def test_long_caption_is_split_into_bounded_non_overlapping_windows(self):
        text = " ".join(f"word{index}" for index in range(36))
        windows = muse_pipeline.build_fixed_scene_windows(
            [{"start": 0.0, "end": 25.0, "text": text}],
            25.0,
            intro_end_seconds=0,
            coverage_mode="full",
            coverage_minutes=5,
        )
        self.assertEqual(windows[0]["start"], 0.0)
        self.assertEqual(windows[-1]["end"], 25.0)
        self.assertTrue(all(item["duration"] <= 12.0 for item in windows))
        self.assertEqual(
            [item["start"] for item in windows[1:]],
            [item["end"] for item in windows[:-1]],
        )
        words = " ".join(item["transcript"] for item in windows).split()
        self.assertEqual(words, text.split())

    def test_long_leading_and_trailing_silence_stays_bounded(self):
        windows = muse_pipeline.build_fixed_scene_windows(
            [{"start": 20.0, "end": 25.0, "text": "one spoken caption"}],
            50.0,
            intro_end_seconds=0,
            coverage_mode="full",
            coverage_minutes=5,
        )
        self.assertEqual(windows[0]["start"], 0.0)
        self.assertEqual(windows[-1]["end"], 50.0)
        self.assertTrue(all(item["duration"] <= 12.0 for item in windows))
        self.assertEqual(
            [item["start"] for item in windows[1:]],
            [item["end"] for item in windows[:-1]],
        )
        self.assertEqual(
            sum("one spoken caption" in item["transcript"] for item in windows),
            1,
        )


class TestMuseSemanticPlanner(unittest.TestCase):
    def test_historical_regression_fixture_keeps_source_anchors_and_removes_cta(self):
        windows = muse_pipeline.build_fixed_scene_windows(
            [
                {"start": 0.0, "end": 10.66, "text": "Đèo Mang Yang, quốc lộ 19 năm 1954."},
                {"start": 10.66, "end": 19.3, "text": "Đoàn xe GM100 rời An Khê về Pleiku."},
                {"start": 19.3, "end": 30.22, "text": "Trung đoàn 96 chuẩn bị phục kích."},
                {"start": 30.22, "end": 41.96, "text": "Đoàn xe GM100 bị chặn, xe thông tin mất liên lạc."},
                {"start": 41.96, "end": 50.56, "text": "Binh đoàn Pháp bị chia cắt và rút lui."},
                {"start": 50.56, "end": 58.82, "text": "Tưởng niệm tại Mang Yang. Hãy LIKE và đăng ký."},
            ],
            58.82,
            intro_end_seconds=0,
            coverage_mode="full",
            coverage_minutes=5,
        )
        anchors = [
            ("đèo Mang Yang", "quốc lộ 19", "năm 1954"),
            ("đoàn xe GM100", "tuyến An Khê đến Pleiku", "năm 1954"),
            ("Trung đoàn 96", "khu vực Mang Yang", "năm 1954"),
            ("đoàn xe GM100", "quốc lộ 19 tại Mang Yang", "năm 1954"),
            ("Binh đoàn Pháp", "đường rút lui gần Mang Yang", "năm 1954"),
            ("những người được tưởng niệm", "đèo Mang Yang, Việt Nam", "năm 1954"),
        ]
        roles = ["opening", "setup", "rising_tension", "turning_point", "aftermath", "memorial"]
        scenes = []
        for index, (subject, setting, era) in enumerate(anchors):
            visual_anchor = (
                f"{subject} in {setting} with a distinct period vehicle formation for scene {index}"
            )
            safe_anchor = (
                f"{subject} waiting quietly in {setting} beside stationary period vehicles"
            )
            scenes.append(
                {
                    "index": index,
                    "subject": subject,
                    "action": f"moves through a distinct documentary beat for scene {index}",
                    "setting": setting,
                    "era": era,
                    "visual_anchor": visual_anchor,
                    "safe_visual_anchor": safe_anchor,
                    "must_show": [subject, setting],
                    "composition": "wide shot with layered road depth",
                    "lighting": "humid overcast daylight",
                    "color": "muted tropical earth tones",
                    "narrative_role": roles[index],
                }
            )
        payload = {"story_context": "The 1954 GM100 retreat through Mang Yang.", "scenes": scenes}
        plan = muse_semantic_planner.compile_muse_scene_plan(
            payload,
            windows,
            title="Trận Mang Yang năm 1954",
            style="historical documentary realism",
        )
        serialized = json.dumps(plan, ensure_ascii=False).casefold()
        for anchor in ("mang yang", "1954", "quốc lộ 19", "gm100", "trung đoàn 96", "việt nam"):
            self.assertIn(anchor, serialized)
        self.assertNotIn("contemporary", serialized)
        final_visual = " ".join(
            str(plan["scenes"][-1].get(key) or "")
            for key in ("subject", "action", "visual_anchor", "safe_visual_anchor", "prompt")
        ).casefold()
        self.assertNotIn("like", final_visual)
        self.assertNotIn("đăng ký", final_visual)
        self.assertEqual(plan["scenes"][-1]["narrative_role"], "memorial")

    def test_modern_technology_plan_preserves_genre_without_historical_bias(self):
        windows = [
            {
                "index": 0,
                "start": 0.0,
                "end": 9.0,
                "duration": 9.0,
                "transcript": (
                    "Tại Apple Park tháng 9 năm 2026, nhóm thiết kế giới thiệu iPhone 17."
                ),
                "is_video": True,
                "media_type": "video",
            }
        ]
        payload = {
            "story_context": "A present-day technology product launch focused on industrial design.",
            "scenes": [
                {
                    "index": 0,
                    "subject": "Apple product designers and iPhone 17",
                    "action": "The team demonstrates the phone's camera controls at a worktable.",
                    "setting": "a product design studio inside Apple Park, California",
                    "era": "September 2026 during the product launch",
                    "visual_anchor": "A close product demonstration with the campus visible beyond glass walls.",
                    "safe_visual_anchor": "Designers calmly inspect the phone at a clean studio worktable.",
                    "must_show": ["iPhone 17 prototype", "Apple Park studio"],
                    "composition": "clean medium-wide product framing",
                    "lighting": "soft daylight through glass walls",
                    "color": "silver, white, and natural green",
                    "narrative_role": "demonstration",
                }
            ],
        }
        plan = muse_semantic_planner.compile_muse_scene_plan(
            payload,
            windows,
            title="Inside the iPhone 17 design process",
            style="clean contemporary technology product photography",
        )
        scene = plan["scenes"][0]
        self.assertEqual(plan["planner_version"], "semantic_v2")
        self.assertEqual(scene["narrative_role"], "demonstration")
        self.assertEqual(scene["style"], "clean contemporary technology product photography")
        self.assertIn("Apple Park", scene["prompt"])
        self.assertIn("iPhone 17", scene["prompt"])
        self.assertNotIn("historical", scene["prompt"].casefold())
        self.assertNotIn("documentary", scene["prompt"].casefold())

    def test_lifestyle_plan_without_named_entities_is_valid(self):
        windows = [
            {
                "index": 0,
                "start": 0.0,
                "end": 8.0,
                "duration": 8.0,
                "transcript": "trộn bột với nước rồi để hỗn hợp nghỉ trong mười phút",
                "is_video": False,
                "media_type": "image",
            }
        ]
        payload = {
            "story_context": "A calm step-by-step home cooking demonstration.",
            "scenes": [
                {
                    "index": 0,
                    "subject": "a home cook and a ceramic mixing bowl",
                    "action": "The cook folds water into flour with a wooden spoon.",
                    "setting": "a bright compact home kitchen beside a window",
                    "era": "present-day morning during the recipe preparation",
                    "visual_anchor": "Hands mixing a smooth dough in a ceramic bowl on a wooden counter.",
                    "safe_visual_anchor": "Hands calmly mixing dough in a ceramic bowl on a wooden counter.",
                    "must_show": ["ceramic mixing bowl", "wooden spoon"],
                    "composition": "overhead close-up with ingredients at the edge",
                    "lighting": "soft morning window light",
                    "color": "warm cream and natural wood",
                    "narrative_role": "demonstration",
                }
            ],
        }
        plan = muse_semantic_planner.compile_muse_scene_plan(
            payload, windows, title="Baking at home", style="warm lifestyle photography"
        )
        self.assertEqual(plan["anchor_coverage"]["ratio"], 1.0)
        self.assertIn("warm lifestyle photography", plan["scenes"][0]["prompt"])
        self.assertNotIn("historical", plan["scenes"][0]["prompt"].casefold())

    def test_parser_accepts_fenced_json_and_validator_rejects_generic_plan(self):
        windows = [
            {
                "index": 0,
                "start": 0.0,
                "end": 8.0,
                "duration": 8.0,
                "transcript": "a quiet lower-case transcript",
                "is_video": True,
                "media_type": "video",
            }
        ]
        payload = _semantic_payload(windows)
        parsed = muse_semantic_planner.parse_planner_response(
            "```json\n" + json.dumps(payload) + "\n```"
        )
        self.assertEqual(
            muse_semantic_planner.validate_semantic_plan(parsed, windows)["scenes"][0]["index"],
            0,
        )
        payload["scenes"][0]["setting"] = "cinematic scene"
        with self.assertRaises(muse_semantic_planner.SemanticPlanValidationError):
            muse_semantic_planner.validate_semantic_plan(payload, windows)

    def test_image_refusal_fallback_keeps_semantic_anchors(self):
        prompt, references = muse_pipeline._build_refusal_fallback(
            {
                "index": 0,
                "setting": "Mang Yang pass on Route 19, Vietnam",
                "era": "1954",
                "safe_visual_anchor": "A halted GM100 convoy waits beside the mountain road.",
                "must_show": ["Mang Yang pass", "Route 19 marker", "GM100 vehicles"],
                "composition": "wide road perspective",
                "lighting": "overcast daylight",
                "color": "muted tropical colors",
            },
            "image",
            [{"path": "reference.png", "sha256": "reference"}],
        )
        self.assertEqual(references, [])
        self.assertIn("Mang Yang pass", prompt)
        self.assertIn("1954", prompt)
        self.assertIn("GM100", prompt)
        self.assertNotIn("empty landscape or interior", prompt)

    def test_manifest_generation_metadata_uses_allowlist(self):
        filtered = muse_pipeline._manifest_generation_metadata(
            {
                "_muse_cache_hit": True,
                "metadata": {
                    "prompt_policy_version": "balanced_v2",
                    "prompt_strategy": "risk_rewritten",
                    "refusal_count": 1,
                    "fallback_count": 1,
                    "generation_elapsed_seconds": 12.5,
                    "raw_prompt": "must not leak",
                    "api_key": "must not leak",
                    "selected_references": [{"path": "secret-path"}],
                },
            }
        )
        self.assertEqual(filtered["prompt_policy_version"], "balanced_v2")
        self.assertEqual(filtered["refusal_count"], 1)
        self.assertTrue(filtered["cache_hit"])
        serialized = json.dumps(filtered)
        for forbidden in ("raw_prompt", "api_key", "secret-path", "must not leak"):
            self.assertNotIn(forbidden, serialized)

    def test_plan_cache_skips_builder_and_missing_builder_fails_closed(self):
        windows = [
            {
                "index": 0,
                "start": 0.0,
                "end": 8.0,
                "duration": 8.0,
                "transcript": "a quiet lower-case transcript",
                "is_video": True,
                "media_type": "video",
            }
        ]
        payload = _semantic_payload(windows)
        compiled = muse_semantic_planner.compile_muse_scene_plan(
            payload, windows, title="Title", style="documentary"
        )
        compiled.update(
            {
                "planner_source": "chatgpt_playwright_batch",
                "planner_cache_hit": False,
                "planner_elapsed_seconds": 12.0,
            }
        )
        prepared = {
            "plan_hash": "plan-hash",
            "base_plan_hash": "base-hash",
            "windows": windows,
            "video": {"generated_title": "Title"},
        }
        snapshot = {"image_generation_settings": {"style_prompt": "documentary"}}
        builder = MagicMock()
        with patch.object(
            muse_pipeline,
            "_load_json_artifact",
            return_value=({"content_hash": "plan-hash"}, compiled),
        ):
            cached = muse_pipeline._load_or_build_plan(
                42,
                snapshot,
                prepared,
                [],
                force_new_project=False,
                semantic_plan_builder=builder,
            )
        builder.assert_not_called()
        self.assertTrue(cached["planner_cache_hit"])

        with patch.object(
            muse_pipeline,
            "_load_json_artifact",
            side_effect=video_production.VideoProductionError("missing"),
        ):
            with self.assertRaises(muse_pipeline.MuseSemanticPlanNeedsAttention):
                muse_pipeline._load_or_build_plan(
                    42,
                    snapshot,
                    prepared,
                    [],
                    force_new_project=False,
                    semantic_plan_builder=None,
                )

    def test_chatgpt_planner_repairs_invalid_json_once(self):
        windows = [
            {
                "index": 0,
                "start": 0.0,
                "end": 8.0,
                "duration": 8.0,
                "transcript": "a quiet lower-case transcript",
                "is_video": True,
                "media_type": "video",
            }
        ]
        valid_response = json.dumps(_semantic_payload(windows))
        page = MagicMock()
        page.url = "https://chatgpt.com/c/planner-test"
        context = MagicMock()
        context.pages = [page]
        playwright = MagicMock()
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path(temp_dir)),
            patch.object(chatgpt_worker, "sync_playwright") as sync_playwright,
            patch.object(chatgpt_worker, "launch_chatgpt_context", return_value=context),
            patch.object(chatgpt_worker, "get_chatgpt_project_url", return_value="https://chatgpt.com/g/project"),
            patch.object(chatgpt_worker, "navigate_to_chatgpt_project"),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_conversation_url",
                side_effect=lambda value: "/c/" in str(value),
            ),
            patch.object(chatgpt_worker, "send_prompt", side_effect=["{}", valid_response]) as send,
        ):
            sync_playwright.return_value.__enter__.return_value = playwright
            result = chatgpt_worker.generate_muse_scene_plan_only(
                title="Title",
                style="documentary",
                windows=windows,
                prompt_version="default",
            )
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result["semantic_plan"]["scenes"][0]["index"], 0)
        context.close.assert_called_once()


class TestMuseReferences(unittest.TestCase):
    def test_alias_primary_and_configuration_order_are_deterministic(self):
        snapshot = []
        for set_index in range(2):
            for image_index in range(4):
                snapshot.append(
                    {
                        "set_id": f"set-{set_index}",
                        "set_name": "Anh An" if set_index == 0 else "Chi Binh",
                        "set_index": set_index,
                        "image_index": image_index,
                        "aliases": ["An"] if set_index == 0 else ["Binh"],
                        "path": f"image-{set_index}-{image_index}.png",
                        "sha256": f"{set_index}-{image_index}",
                        "primary": image_index == 0,
                    }
                )
        selected, omitted = muse_pipeline.select_scene_references(
            {"transcript": "Anh An bước vào phòng cùng Binh", "action": "An greets Binh"},
            snapshot,
            limit=6,
        )
        self.assertEqual(len(selected), 6)
        self.assertTrue(selected[0]["primary"])
        self.assertEqual(selected[0]["set_id"], "set-0")
        self.assertEqual(len(omitted), 2)
        self.assertTrue(all(item["omission_reason"] == "reference_limit" for item in omitted))

    def test_video_prompt_maps_scene_frame_to_image_one(self):
        prompt = muse_provider.MusePromptBuilder.video(
            "A character walks forward.", reference_count=6, duration_seconds=10
        )
        self.assertIn("@Image1 is the required first frame", prompt)
        self.assertIn("@Image2 through @Image6", prompt)
        self.assertIn("16:9", prompt)


class TestMuseBalancedPromptPolicy(unittest.TestCase):
    def test_risk_classifier_is_bilingual_and_avoids_broad_terms(self):
        for action in (
            "Súng máy nổ vang và xe thông tin bị phá hủy.",
            "The machine gun attack caused an explosion.",
            "Những mất mát và hy sinh được tưởng niệm.",
        ):
            with self.subTest(action=action):
                result = muse_pipeline.classify_scene_risk({"action": action})
                self.assertEqual(result["risk_level"], "high")

        result = muse_pipeline.classify_scene_risk(
            {"action": "Đội ngũ đánh giá ảnh tư liệu trong một căn phòng yên tĩnh."}
        )
        self.assertEqual(result["risk_level"], "low")

    def test_high_risk_image_is_rewritten_and_keeps_references(self):
        references = [{"path": "hero.png", "sha256": "hero"}]
        prompt, selected, metadata = muse_pipeline._prepare_balanced_submission(
            {
                "action": "Súng máy nổ vang và đoàn xe bị phá hủy.",
                "composition": "wide shot",
                "lighting": "natural light",
                "color": "muted colors",
                "setting": "Mang Yang pass",
                "era": "1954",
                "safe_visual_anchor": (
                    "The GM100 convoy halted on Route 19 at Mang Yang pass while radio crews "
                    "checked period equipment."
                ),
                "must_show": ["Mang Yang pass", "GM100 convoy", "Route 19 marker"],
            },
            "image",
            "original prompt",
            references,
        )
        self.assertEqual(selected, references)
        self.assertEqual(metadata["prompt_strategy"], "risk_rewritten")
        self.assertEqual(metadata["prompt_policy_version"], "balanced_v2")
        self.assertIn("Mang Yang pass", prompt)
        self.assertNotIn("Súng máy", prompt)
        self.assertNotIn("phá hủy", prompt)

    def test_high_risk_video_uses_only_first_frame(self):
        references = [
            {"path": "scene.png", "sha256": "scene"},
            {"path": "hero.png", "sha256": "hero"},
        ]
        prompt, selected, metadata = muse_pipeline._prepare_balanced_submission(
            {"action": "Súng máy nổ vang và đoàn xe bị phá hủy.", "duration": 10},
            "video",
            "original prompt",
            references,
        )
        self.assertEqual(selected, references[:1])
        self.assertEqual(metadata["prompt_strategy"], "risk_rewritten")
        self.assertIn("subtle cinematic motion", prompt)
        self.assertNotIn("đoàn xe bị phá hủy", prompt)

    def test_modern_high_risk_rewrite_preserves_requested_genre(self):
        prompt, selected, metadata = muse_pipeline._prepare_balanced_submission(
            {
                "action": "A laboratory explosion caused injuries near the robot.",
                "setting": "a robotics laboratory in Tokyo",
                "era": "March 2026 during a daytime safety review",
                "safe_visual_anchor": (
                    "Engineers inspect a powered-down robot from behind a transparent safety barrier."
                ),
                "must_show": ["powered-down robot", "transparent safety barrier"],
                "composition": "orderly medium-wide laboratory view",
                "lighting": "clean overhead laboratory light",
                "color": "white, graphite, and safety yellow",
                "style": "clean contemporary technology news photography",
            },
            "image",
            "original prompt",
            [{"path": "robot.png", "sha256": "robot"}],
        )
        self.assertEqual(metadata["prompt_strategy"], "risk_rewritten")
        self.assertEqual(len(selected), 1)
        self.assertIn("clean contemporary technology news photography", prompt)
        self.assertIn("robotics laboratory in Tokyo", prompt)
        self.assertNotIn("historical", prompt.casefold())
        self.assertNotIn("documentary", prompt.casefold())


class TestMuseStudioApiProvider(unittest.TestCase):
    def test_image_payload_has_all_references_and_idempotency_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            references = []
            for index in range(3):
                path = Path(temp_dir) / f"ref-{index}.png"
                path.write_bytes(f"reference-{index}".encode())
                references.append(path)
            provider = muse_provider.MuseStudioApiProvider()
            with (
                patch.object(provider, "_post_json", return_value={"task_id": "image-1"}) as post,
                patch.object(provider, "_wait_for_task", new=AsyncMock(return_value={"path": "done"})),
            ):
                asyncio.run(
                    provider.create_image(
                        "image prompt",
                        references,
                        Path(temp_dir) / "scene",
                        request_hash="a" * 64,
                    )
                )
            endpoint, payload, idempotency_key = post.call_args.args
            self.assertEqual(endpoint, "/v1/images/tasks")
            self.assertEqual(payload["reference_image"], [str(path.resolve()) for path in references])
            self.assertEqual(idempotency_key, "a" * 64)

    def test_video_payload_keeps_scene_frame_first_and_has_idempotency_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            references = []
            for name in ("scene.png", "hero.png", "villain.png"):
                path = Path(temp_dir) / name
                path.write_bytes(name.encode())
                references.append(path)
            provider = muse_provider.MuseStudioApiProvider()
            with (
                patch.object(provider, "_post_json", return_value={"task_id": "video-1"}) as post,
                patch.object(provider, "_wait_for_task", new=AsyncMock(return_value={"path": "done"})),
            ):
                asyncio.run(
                    provider.create_video(
                        "video prompt",
                        references,
                        Path(temp_dir) / "clip",
                        request_hash="b" * 64,
                    )
                )
            endpoint, payload, idempotency_key = post.call_args.args
            self.assertEqual(endpoint, "/v1/videos")
            self.assertEqual(payload["reference_image"][0], str(references[0].resolve()))
            self.assertEqual(payload["reference_image"], [str(path.resolve()) for path in references])
            self.assertEqual(idempotency_key, "b" * 64)

    def test_image_and_idempotent_video_connection_loss_are_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            failure = urllib.error.URLError(ConnectionResetError("lost"))
            with patch.object(provider, "_post_json", side_effect=failure):
                with self.assertRaises(muse_provider.MuseTransientError):
                    asyncio.run(provider.create_image("prompt", [], Path(temp_dir) / "image"))
            with patch.object(provider, "_post_json", side_effect=failure):
                with self.assertRaises(muse_provider.MuseTransientError):
                    asyncio.run(provider.create_video("prompt", [scene], Path(temp_dir) / "video"))

    def test_admission_429_is_retryable_before_video_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            with patch.object(
                provider, "_post_json", side_effect=muse_provider.MuseAdmissionRetry("queue full")
            ):
                with self.assertRaises(muse_provider.MuseAdmissionRetry):
                    asyncio.run(provider.create_video("prompt", [scene], Path(temp_dir) / "video"))

    def test_idempotent_video_missing_task_id_is_retryable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            with patch.object(provider, "_post_json", return_value={}):
                with self.assertRaises(muse_provider.MuseTransientError):
                    asyncio.run(
                        provider.create_video(
                            "prompt",
                            [scene],
                            Path(temp_dir) / "video",
                            request_hash="c" * 64,
                        )
                    )

    def test_reconcile_polls_existing_task_without_submitting(self):
        provider = muse_provider.MuseStudioApiProvider()
        receipt = muse_provider.MuseReceipt(
            provider_task_id="task-1",
            endpoint="/v1/videos/task-1",
            submitted_at="2026-01-01T00:00:00Z",
        )
        downloaded = {"path": "clip.mp4", "mime_type": "video/mp4"}
        with (
            patch.object(provider, "_get_json", return_value={"status": "completed", "url": "/v1/media/clip.mp4"}) as get,
            patch.object(provider, "_download_completed_task", return_value=downloaded),
            patch.object(provider, "_post_json") as post,
        ):
            result = asyncio.run(
                provider.reconcile(receipt=receipt, destination=Path("clip"), expected="video")
            )
        get.assert_called_once_with("/v1/videos/task-1")
        post.assert_not_called()
        self.assertEqual(result["provider_task_id"], "task-1")

    def test_refusal_and_external_media_url_fail_closed(self):
        for message in (
            "Generation refused by safety policy",
            "模型未生成媒体，仅返回文本: Ảnh theo brief này mình chưa tạo được.",
        ):
            with self.subTest(message=message):
                with self.assertRaises(muse_provider.MuseRefusalError):
                    muse_provider.MuseStudioApiProvider._raise_task_failure(
                        {"error": message}
                    )
        self.assertIs(
            muse_provider.classify_muse_failure("Ảnh được tạo thành công."),
            muse_provider.MuseError,
        )
        self.assertIs(
            muse_provider.classify_muse_failure("Không đọc được ảnh tham chiếu."),
            muse_provider.MuseReferenceError,
        )
        provider = muse_provider.MuseStudioApiProvider()
        with patch.object(
            muse_provider.muse_studio_service,
            "api_credentials",
            return_value=("http://127.0.0.1:18610", "secret"),
        ):
            with self.assertRaisesRegex(muse_provider.MuseError, "ngoài localhost"):
                provider._download_completed_task(
                    {"url": "https://example.com/video.mp4"}, Path("unused"), "video"
                )


class TestMuseRecovery(unittest.TestCase):
    def test_refused_image_retries_once_with_non_graphic_fallback(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reference = root / "reference.png"
            reference.write_bytes(b"reference")
            generated = root / "generated.png"
            generated.write_bytes(b"generated")
            calls = []

            class RefusalThenSuccessAdapter:
                async def create_image(self, prompt, reference_paths, _destination, **kwargs):
                    calls.append((prompt, list(reference_paths), kwargs["request_hash"]))
                    receipt_number = len(calls)
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id=f"image-task-{receipt_number}",
                            endpoint=f"/v1/images/tasks/image-task-{receipt_number}",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    if receipt_number == 1:
                        raise muse_provider.MuseRefusalError(
                            "模型未生成媒体，仅返回文本: Ảnh theo brief này mình chưa tạo được."
                        )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "image-task-2",
                        "endpoint": "/v1/images/tasks/image-task-2",
                        "submitted_at": "2026-01-01T00:00:00Z",
                    }

            async def run():
                with (
                    patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                    patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                    patch.object(
                        muse_pipeline,
                        "_validate_image",
                        return_value={"width": 1920, "height": 1080},
                    ),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=RefusalThenSuccessAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="original sensitive prompt",
                        references=[
                            {
                                "path": str(reference),
                                "sha256": "reference-hash",
                            }
                        ],
                        omitted=[],
                        destination=root / "scene",
                        expected="image",
                        scene={
                            "index": 0,
                            "setting": "Mang Yang pass on Route 19, Vietnam",
                            "era": "1954",
                            "safe_visual_anchor": (
                                "A halted GM100 convoy waits beside the mountain road."
                            ),
                            "must_show": [
                                "Mang Yang pass",
                                "Route 19 marker",
                                "GM100 vehicles",
                            ],
                            "composition": "wide shot",
                            "lighting": "natural light",
                            "color": "natural tones",
                        },
                        cancel_check=lambda: None,
                    )

            result = asyncio.run(run())

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], [reference])
        self.assertEqual(calls[1][1], [])
        self.assertNotEqual(calls[0][2], calls[1][2])
        self.assertIn("non-graphic", calls[1][0])
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["metadata"]["refusal_fallback"])
        self.assertEqual(result["metadata"]["provider_task_id"], "image-task-2")

    def test_safe_video_refusal_degrades_to_existing_scene_image(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_frame = root / "scene.png"
            first_frame.write_bytes(b"scene")
            calls = []

            class AlwaysRefuseAdapter:
                async def create_video(self, prompt, reference_paths, _destination, **kwargs):
                    calls.append((prompt, list(reference_paths), kwargs["request_hash"]))
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="video-task-1",
                            endpoint="/v1/videos/video-task-1",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    error = muse_provider.MuseRefusalError("Generation refused by safety policy")
                    error.provider_elapsed_seconds = 12.5
                    raise error

            async def run():
                with (
                    patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                    patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=AlwaysRefuseAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_video:0",
                        request_hash="request",
                        prompt="safe motion prompt",
                        references=[{"path": str(first_frame), "sha256": "scene-hash"}],
                        omitted=[],
                        destination=root / "clip",
                        expected="video",
                        scene={"index": 0, "duration": 10},
                        cancel_check=lambda: None,
                        policy_metadata={
                            "prompt_policy_version": "balanced_v2",
                            "prompt_strategy": "risk_rewritten",
                            "risk_level": "high",
                            "risk_categories": ["weapons", "attack"],
                            "risk_match_count": 2,
                        },
                    )

            result = asyncio.run(run())

        self.assertEqual(len(calls), 1)
        self.assertTrue(result["degraded_to_image"])
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["metadata"]["refusal_count"], 1)
        self.assertEqual(result["metadata"]["fallback_count"], 0)
        self.assertEqual(result["metadata"]["refusal_elapsed_seconds"], 12.5)

    def test_original_video_refusal_uses_one_fallback_then_degrades(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_frame = root / "scene.png"
            extra_reference = root / "hero.png"
            first_frame.write_bytes(b"scene")
            extra_reference.write_bytes(b"hero")
            calls = []

            class AlwaysRefuseAdapter:
                async def create_video(self, prompt, reference_paths, _destination, **kwargs):
                    calls.append((prompt, list(reference_paths), kwargs["request_hash"]))
                    number = len(calls)
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id=f"video-task-{number}",
                            endpoint=f"/v1/videos/video-task-{number}",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    error = muse_provider.MuseRefusalError("Generation refused by safety policy")
                    error.provider_elapsed_seconds = 5
                    raise error

            async def run():
                with (
                    patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                    patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=AlwaysRefuseAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_video:0",
                        request_hash="request",
                        prompt="ordinary camera motion",
                        references=[
                            {"path": str(first_frame), "sha256": "scene-hash"},
                            {"path": str(extra_reference), "sha256": "hero-hash"},
                        ],
                        omitted=[],
                        destination=root / "clip",
                        expected="video",
                        scene={"index": 0, "duration": 10},
                        cancel_check=lambda: None,
                        policy_metadata={
                            "prompt_policy_version": "balanced_v2",
                            "prompt_strategy": "original",
                            "risk_level": "low",
                            "risk_categories": [],
                            "risk_match_count": 0,
                        },
                    )

            result = asyncio.run(run())

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], [first_frame, extra_reference])
        self.assertEqual(calls[1][1], [first_frame])
        self.assertTrue(result["degraded_to_image"])
        self.assertEqual(result["metadata"]["refusal_count"], 2)
        self.assertEqual(result["metadata"]["fallback_count"], 1)
        self.assertEqual(result["metadata"]["refusal_elapsed_seconds"], 10)

    def test_submission_without_receipt_resumes_with_same_idempotency_key(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "attempt": 1,
                "provider": muse_provider.MUSE_PROVIDER_NAME,
                "submission_hash": "request",
                "submission_variant": "original",
                "idempotent_submission": True,
                "prompt_strategy": "original",
            },
        }

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generated = root / "generated.png"
            generated.write_bytes(b"generated")
            submitted_hashes = []

            class SuccessAdapter:
                async def create_image(self, _prompt, _references, _destination, **kwargs):
                    submitted_hashes.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="image-task-1",
                            endpoint="/v1/images/tasks/image-task-1",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "image-task-1",
                        "endpoint": "/v1/images/tasks/image-task-1",
                        "submitted_at": "2026-01-01T00:00:00Z",
                    }

            async def run():
                with (
                    patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                    patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                    patch.object(
                        muse_pipeline,
                        "_validate_image",
                        return_value={"width": 1920, "height": 1080},
                    ),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=SuccessAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="safe prompt",
                        references=[],
                        omitted=[],
                        destination=root / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                        policy_metadata={"prompt_strategy": "original"},
                    )

            result = asyncio.run(run())

        self.assertEqual(submitted_hashes, ["request"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metadata"]["attempt"], 1)

    def test_renderer_accepts_video_scene_resolved_as_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "audio.wav"
            captions = root / "captions.srt"
            image = root / "scene.png"
            audio.write_bytes(b"audio")
            captions.write_text("captions", encoding="utf-8")
            image.write_bytes(b"image")
            scene = {"index": 0, "is_video": True, "duration": 10}
            plan_artifact = {
                "content_hash": "bound-plan",
                "metadata": {"base_plan_hash": "base-plan"},
            }
            plan = {"scenes": [scene], "visual_bible": {}}
            manifest = {
                "plan_hash": "bound-plan",
                "caption_hash": "caption-hash",
                "media": [
                    {
                        "scene_index": 0,
                        "path": str(image),
                        "sha256": muse_pipeline._sha256_file(image),
                        "requested_media_type": "video",
                        "resolved_media_type": "image",
                        "degraded_to_image": True,
                    }
                ],
            }
            render_result = {"id": 1, "path": str(root / "final.mp4")}
            with (
                patch.object(
                    muse_pipeline,
                    "_prepare_inputs",
                    return_value={
                        "plan_hash": "base-plan",
                        "caption_hash": "caption-hash",
                        "audio_path": audio,
                        "srt_path": captions,
                    },
                ),
                patch.object(
                    muse_pipeline,
                    "_load_json_artifact",
                    side_effect=[(plan_artifact, plan), ({}, manifest)],
                ),
                patch.object(
                    muse_pipeline.video_production,
                    "render_video",
                    return_value=render_result,
                ) as render_video,
            ):
                result = muse_pipeline.render_prepared_muse_video(
                    1,
                    {},
                    lambda *_args, **_kwargs: None,
                    lambda: None,
                )

        self.assertEqual(result["artifact"], render_result)
        self.assertEqual(render_video.call_args.kwargs["media_paths"], [image])

    def test_corrupt_cached_artifact_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scene.png"
            path.write_bytes(b"changed")
            artifact = {
                "status": "ready",
                "content_hash": "request",
                "path": str(path),
                "metadata": {"checksum": "not-the-real-checksum"},
            }
            self.assertFalse(muse_pipeline._valid_cached_artifact(artifact, "request"))

            adapter = AsyncMock()

            async def run():
                with patch.object(
                    muse_pipeline.db, "get_latest_video_artifact", return_value=artifact
                ):
                    await muse_pipeline._generate_request(
                        adapter=adapter,
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=Path(temp_dir) / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                    )

            with self.assertRaisesRegex(muse_provider.MuseError, "Recreate"):
                asyncio.run(run())
            adapter.create_image.assert_not_awaited()

    def test_invalid_downloaded_media_is_terminal(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with tempfile.TemporaryDirectory() as temp_dir:
            corrupt = Path(temp_dir) / "result.png"
            corrupt.write_bytes(b"not-an-image")
            adapter = AsyncMock()
            adapter.create_image.return_value = {
                "path": str(corrupt),
                "mime_type": "image/png",
            }

            async def run():
                with (
                    patch.object(
                        muse_pipeline.db,
                        "get_latest_video_artifact",
                        side_effect=get_latest,
                    ),
                    patch.object(
                        muse_pipeline.db,
                        "upsert_video_artifact",
                        side_effect=upsert,
                    ),
                ):
                    await muse_pipeline._generate_request(
                        adapter=adapter,
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=Path(temp_dir) / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                    )

            with self.assertRaises(muse_provider.MuseError):
                asyncio.run(run())

        self.assertEqual(artifact["status"], "error")
        self.assertTrue(artifact["metadata"]["invalid_media"])

    def test_submission_without_receipt_stops_before_resend(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {"attempt": 1},
        }
        adapter = AsyncMock()

        async def run():
            with patch.object(muse_pipeline.db, "get_latest_video_artifact", return_value=artifact):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_provider.MuseAmbiguousSubmissionError):
            asyncio.run(run())
        adapter.create_image.assert_not_awaited()

    def test_transient_error_after_receipt_is_reconciled_without_resubmit(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        class ReceiptThenTimeoutAdapter:
            async def create_image(self, *_args, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id="image-task-1",
                        endpoint="/v1/images/tasks/image-task-1",
                        submitted_at="2026-01-01T00:00:00Z",
                    )
                )
                raise muse_provider.MuseTransientError("timeout")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=ReceiptThenTimeoutAdapter(),
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_pipeline.MuseRetryScheduled) as caught:
            asyncio.run(run())
        self.assertFalse(caught.exception.consume_attempt)
        self.assertEqual(artifact["status"], "submitting")
        self.assertEqual(artifact["metadata"]["provider"], "muse_studio_api")
        self.assertEqual(artifact["metadata"]["provider_task_id"], "image-task-1")

    def test_ambiguous_submission_remains_fail_closed(self):
        artifact = None

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        adapter = AsyncMock()
        adapter.create_image.side_effect = muse_provider.MuseAmbiguousSubmissionError("ambiguous")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(muse_provider.MuseAmbiguousSubmissionError):
            asyncio.run(run())
        self.assertEqual(artifact["status"], "submitting")
        self.assertTrue(artifact["metadata"]["ambiguous_submission"])

    def test_cancel_after_receipt_preserves_reconciliation_state(self):
        artifact = None

        class VideoJobCanceled(RuntimeError):
            pass

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        class ReceiptThenCanceledAdapter:
            async def create_image(self, *_args, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id="image-task-cancel",
                        endpoint="/v1/images/tasks/image-task-cancel",
                        submitted_at="2026-01-01T00:00:00Z",
                    )
                )
                raise VideoJobCanceled("canceled")

        async def run():
            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            ):
                await muse_pipeline._generate_request(
                    adapter=ReceiptThenCanceledAdapter(),
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaises(VideoJobCanceled):
            asyncio.run(run())
        self.assertEqual(artifact["status"], "submitting")
        self.assertEqual(artifact["metadata"]["provider_task_id"], "image-task-cancel")

    def test_legacy_browser_receipt_requires_recreate(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "thread_url": "https://muse.ai/thread/legacy",
                "turn_id": "legacy-turn",
                "submitted_at": "2026-01-01T00:00:00Z",
            },
        }
        adapter = AsyncMock()

        async def run():
            with patch.object(muse_pipeline.db, "get_latest_video_artifact", return_value=artifact):
                await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                )

        with self.assertRaisesRegex(muse_provider.MuseAmbiguousSubmissionError, "Recreate"):
            asyncio.run(run())
        adapter.reconcile.assert_not_awaited()


class TestMuseRenderContract(unittest.TestCase):
    def test_video_segments_loop_and_trim_to_each_scene_duration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_paths = []
            for index in range(2):
                path = root / f"scene-{index}.mp4"
                path.write_bytes(b"video")
                media_paths.append(path)
            srt_path = root / "captions.srt"
            srt_path.write_text(
                "1\n00:00:00,000 --> 00:00:13,500\nTest caption\n",
                encoding="utf-8",
            )
            commands = []

            def fake_popen(command, **_kwargs):
                commands.append(command)
                Path(command[-1]).write_bytes(b"segment")
                process = MagicMock()
                process.returncode = 0
                process.communicate.return_value = ("", "")
                return process

            with (
                patch.object(video_production, "SEGMENTS_DIR", root / "segments"),
                patch.object(video_production, "_write_segment_srt", return_value=False),
                patch.object(video_production.subprocess, "Popen", side_effect=fake_popen),
                patch.object(video_production.process_registry, "register_process"),
                patch.object(video_production.process_registry, "unregister_process"),
            ):
                results = video_production._render_segments(
                    ffmpeg_exe="ffmpeg",
                    encoder="libx264",
                    video_id=1,
                    scenes=[
                        {"index": 0, "start": 0.0, "end": 10.0, "duration": 10.0},
                        {"index": 1, "start": 10.0, "end": 13.5, "duration": 3.5},
                    ],
                    media_paths=media_paths,
                    srt_path=srt_path,
                    input_hash="a" * 64,
                    progress=lambda *_args, **_kwargs: None,
                    cancel_check=lambda: None,
                    crop_watermark=False,
                )

            self.assertEqual(len(results), 2)
            self.assertEqual(len(commands), 2)
            for command in commands:
                self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "10.000")
            self.assertEqual(commands[1][commands[1].index("-t") + 1], "3.500")


class TestMuseStudioServiceLifecycle(unittest.TestCase):
    def tearDown(self):
        timer = muse_studio_service._idle_timer
        if timer is not None:
            timer.cancel()
        muse_studio_service._idle_timer = None
        muse_studio_service._process = None
        muse_studio_service._api_key = ""
        muse_studio_service._instance_id = ""
        muse_studio_service._studio_path = None
        muse_studio_service._stdout_handle = None
        muse_studio_service._stderr_handle = None

    def test_validate_path_requires_studio_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaises(muse_studio_service.MuseStudioConfigurationError):
                muse_studio_service.validate_studio_path(root)
            (root / "muse2api_backend").mkdir()
            (root / "main.py").write_text("", encoding="utf-8")
            (root / "muse2api_backend" / "app.py").write_text("", encoding="utf-8")
            self.assertEqual(muse_studio_service.validate_studio_path(root), str(root.resolve()))

    def test_source_mode_prefers_legacy_backend_data_like_muse_studio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_data = root / "muse2api_data"
            runtime_data.mkdir()
            self.assertEqual(muse_studio_service._studio_data_dir(root), runtime_data.resolve())
            legacy_data = root / "muse2api_backend" / "data"
            legacy_data.mkdir(parents=True)
            self.assertEqual(muse_studio_service._studio_data_dir(root), legacy_data.resolve())

    @unittest.skipUnless(muse_studio_service.sys.platform == "win32", "Windows-only launcher")
    def test_open_studio_uses_nexus_python_and_confirms_process_stays_alive(self):
        process = MagicMock(pid=4321)
        process.wait.side_effect = muse_studio_service.subprocess.TimeoutExpired("Muse", 1.5)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.py").write_text("", encoding="utf-8")
            with (
                patch.object(muse_studio_service, "_validate_studio_path", return_value=root),
                patch.object(muse_studio_service, "_port_in_use", return_value=False),
                patch.object(muse_studio_service, "LOGS_DIR", root / "logs"),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDOUT_PATH",
                    root / "logs" / "muse-ui.stdout.log",
                ),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDERR_PATH",
                    root / "logs" / "muse-ui.stderr.log",
                ),
                patch.object(muse_studio_service.subprocess, "Popen", return_value=process) as popen,
            ):
                result = muse_studio_service.open_studio()

        self.assertTrue(result["success"])
        self.assertEqual(result["pid"], 4321)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(Path(muse_studio_service.sys.executable).resolve()))
        self.assertEqual(Path(command[1]).name, "main.py")
        self.assertNotIn("PYTHONPATH", popen.call_args.kwargs["env"])

    @unittest.skipUnless(muse_studio_service.sys.platform == "win32", "Windows-only launcher")
    def test_open_studio_reports_early_process_exit(self):
        process = MagicMock(pid=4321)
        process.wait.return_value = 1
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "main.py").write_text("", encoding="utf-8")
            with (
                patch.object(muse_studio_service, "_validate_studio_path", return_value=root),
                patch.object(muse_studio_service, "_port_in_use", return_value=False),
                patch.object(muse_studio_service, "LOGS_DIR", root / "logs"),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDOUT_PATH",
                    root / "logs" / "muse-ui.stdout.log",
                ),
                patch.object(
                    muse_studio_service,
                    "MUSE_STUDIO_STDERR_PATH",
                    root / "logs" / "muse-ui.stderr.log",
                ),
                patch.object(muse_studio_service.subprocess, "Popen", return_value=process),
            ):
                with self.assertRaisesRegex(
                    muse_studio_service.MuseStudioServiceUnavailable,
                    "dừng ngay khi khởi động",
                ):
                    muse_studio_service.open_studio()

    @unittest.skipUnless(muse_studio_service.os.name == "nt", "Windows-only recovery")
    def test_recover_verified_orphaned_muse_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_dir = root / "chrome_user_data" / "muse_profiles" / "generate"
            process = {
                "process_id": 4321,
                "parent_process_id": 9876,
                "parent_running": False,
                "name": "chrome.exe",
                "command_line": (
                    'chrome.exe --headless=new --remote-debugging-port=19210 '
                    f'--user-data-dir="{profile_dir}" about:blank'
                ),
            }
            completed = MagicMock(returncode=0)
            with (
                patch.object(
                    muse_studio_service,
                    "_port_in_use",
                    side_effect=[False, True, False],
                ),
                patch.object(
                    muse_studio_service,
                    "_windows_port_process",
                    return_value=process,
                ),
                patch.object(muse_studio_service, "_cdp_is_muse_browser", return_value=True),
                patch.object(
                    muse_studio_service.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                recovered = muse_studio_service._recover_orphaned_muse_browser(root)

        self.assertTrue(recovered)
        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/F", "/T", "/PID", "4321"],
        )

    @unittest.skipUnless(muse_studio_service.os.name == "nt", "Windows-only recovery")
    def test_recovery_refuses_browser_with_live_parent(self):
        process = {
            "process_id": 4321,
            "parent_process_id": 9876,
            "parent_running": True,
            "name": "chrome.exe",
            "command_line": "",
        }
        with (
            patch.object(muse_studio_service, "_port_in_use", side_effect=[False, True]),
            patch.object(muse_studio_service, "_windows_port_process", return_value=process),
            patch.object(muse_studio_service.subprocess, "run") as run,
        ):
            recovered = muse_studio_service._recover_orphaned_muse_browser(Path("C:/Muse"))

        self.assertFalse(recovered)
        run.assert_not_called()

    @unittest.skipUnless(muse_studio_service.os.name == "nt", "Windows-only recovery")
    def test_recovery_refuses_foreign_headless_browser_profile(self):
        process = {
            "process_id": 4321,
            "parent_process_id": 9876,
            "parent_running": False,
            "name": "chrome.exe",
            "command_line": (
                'chrome.exe --headless=new --remote-debugging-port=19210 '
                '--user-data-dir="C:\\Users\\Public\\unrelated-profile" about:blank'
            ),
        }
        with (
            patch.object(muse_studio_service, "_port_in_use", side_effect=[False, True]),
            patch.object(muse_studio_service, "_windows_port_process", return_value=process),
            patch.object(muse_studio_service, "_cdp_is_muse_browser") as cdp_probe,
            patch.object(muse_studio_service.subprocess, "run") as run,
        ):
            recovered = muse_studio_service._recover_orphaned_muse_browser(Path("C:/Muse"))

        self.assertFalse(recovered)
        cdp_probe.assert_not_called()
        run.assert_not_called()

    @unittest.skipUnless(muse_studio_service.os.name == "nt", "Windows-only recovery")
    def test_recovery_removes_verified_orphan_while_muse_job_is_active(self):
        profile_dir = Path("C:/Muse/chrome_user_data/muse_profiles/generate")
        process = {
            "process_id": 4321,
            "parent_process_id": 9876,
            "parent_running": False,
            "name": "chrome.exe",
            "command_line": (
                "chrome.exe --headless=new --remote-debugging-port=19210 "
                f'--user-data-dir="{profile_dir}" about:blank'
            ),
        }
        completed = MagicMock(returncode=0)
        with (
            patch.object(
                muse_studio_service,
                "_port_in_use",
                side_effect=[False, True, False],
            ),
            patch.object(muse_studio_service, "_has_active_muse_jobs", return_value=True),
            patch.object(muse_studio_service, "_windows_port_process", return_value=process),
            patch.object(muse_studio_service, "_cdp_is_muse_browser", return_value=True),
            patch.object(muse_studio_service.subprocess, "run", return_value=completed) as run,
        ):
            recovered = muse_studio_service._recover_orphaned_muse_browser(Path("C:/Muse"))

        self.assertTrue(recovered)
        run.assert_called_once()

    def test_processing_muse_job_blocks_idle_cleanup(self):
        with patch.object(
            muse_studio_service.db,
            "list_system_jobs",
            return_value=[{"status": "processing"}],
        ) as list_jobs:
            self.assertTrue(muse_studio_service._has_active_muse_jobs())
        list_jobs.assert_called_once_with(limit=None, job_type="muse_media_generation")

    def test_foreign_port_is_never_reused_or_killed(self):
        with (
            patch.object(muse_studio_service, "_validate_studio_path", return_value=Path("C:/Muse")),
            patch.object(muse_studio_service, "_port_in_use", return_value=True),
            patch.object(muse_studio_service.subprocess, "Popen") as popen,
        ):
            with self.assertRaises(muse_studio_service.MuseStudioServiceBusy):
                muse_studio_service.ensure_started()
        popen.assert_not_called()

    def test_start_keeps_api_key_out_of_command_line_and_state(self):
        process = MagicMock()
        process.pid = 4321
        process.poll.return_value = None
        status = {
            "running": True,
            "owned": True,
            "api_ready": True,
            "accounts": 1,
            "browser_running": True,
            "api_url": muse_studio_service.MUSE_API_BASE_URL,
        }
        with (
            patch.object(muse_studio_service, "_validate_studio_path", return_value=Path("C:/Muse")),
            patch.object(muse_studio_service, "_port_in_use", return_value=False),
            patch.object(muse_studio_service.Path, "is_file", return_value=True),
            patch.object(muse_studio_service, "open", mock_open()),
            patch.object(muse_studio_service.subprocess, "Popen", return_value=process) as popen,
            patch.object(muse_studio_service.process_registry, "register_process"),
            patch.object(muse_studio_service, "_write_state") as write_state,
            patch.object(muse_studio_service, "_probe_owned_service", return_value=status),
        ):
            self.assertEqual(muse_studio_service.ensure_started(), status)
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command[-1], "--muse-service")
        self.assertNotIn(environment["MUSE2API_KEY"], " ".join(command))
        self.assertEqual(environment["MUSE2API_HOST"], "127.0.0.1")
        self.assertEqual(environment["MUSE2API_PORT"], "18610")
        self.assertEqual(environment["MUSE2API_CDP_PORT"], "19210")
        self.assertEqual(environment["MUSE2API_CONCURRENCY"], "1")
        write_state.assert_called_once_with(process, Path("C:/Muse"))

    def test_state_marker_contains_no_credentials(self):
        process = MagicMock()
        process.pid = 4321
        muse_studio_service._instance_id = "instance-1"
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "muse-service.json"
            with patch.object(muse_studio_service, "MUSE_SERVICE_STATE_PATH", state_path):
                muse_studio_service._write_state(process, Path("C:/Muse"))
            serialized = state_path.read_text(encoding="utf-8").lower()
        for forbidden in ("api_key", "cookie", "token", "proxy", "secret"):
            self.assertNotIn(forbidden, serialized)

    def test_no_account_fails_readiness(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {"status": "no_account", "accounts": 0, "browser_running": False},
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioConfigurationError, "account"
            ):
                muse_studio_service._probe_owned_service()

    def test_probe_rejects_incompatible_model_contract(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {"status": "ready", "accounts": 1, "browser_running": True},
            {"object": "list", "data": [{"id": "muse-image"}]},
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioConfigurationError, "không tương thích"
            ):
                muse_studio_service._probe_owned_service()

    def test_release_schedules_sixty_second_idle_close(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        timer = MagicMock()
        with patch.object(muse_studio_service.threading, "Timer", return_value=timer) as timer_cls:
            muse_studio_service.release()
        timer_cls.assert_called_once_with(
            muse_studio_service.MUSE_IDLE_CLOSE_SECONDS,
            muse_studio_service._idle_close_callback,
        )
        timer.start.assert_called_once()

    def test_shutdown_uses_the_owned_popen_handle_without_process_lookup(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        muse_studio_service._api_key = "in-memory-only"
        muse_studio_service._studio_path = Path("C:/Muse")
        with (
            patch.object(muse_studio_service.process_registry, "unregister_process") as unregister,
            patch.object(muse_studio_service, "_remove_state"),
            patch.object(muse_studio_service, "_close_log_handles"),
        ):
            muse_studio_service.shutdown_owned_service()

        if muse_studio_service.os.name == "nt":
            process.send_signal.assert_called_once_with(muse_studio_service.signal.CTRL_BREAK_EVENT)
        else:
            process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        unregister.assert_called_once_with(muse_studio_service.MUSE_SERVICE_PROCESS_KEY, process)
        self.assertIsNone(muse_studio_service._process)
        self.assertEqual(muse_studio_service._api_key, "")


class TestMuseRouting(unittest.TestCase):
    def test_muse_routes_to_independent_media_job(self):
        expected = ({"id": "muse-job", "job_type": "muse_media_generation"}, True)
        with patch.object(main, "_enqueue_muse_media_job", return_value=expected) as enqueue:
            job, created, phase = main._route_video_render_job(
                video_id=42,
                snapshot={"image_generation_settings": {"render_mode": "muse_ai"}},
            )
        enqueue.assert_called_once()
        self.assertEqual(job["job_type"], "muse_media_generation")
        self.assertTrue(created)
        self.assertEqual(phase, "media_generation")

    def test_explicit_enqueue_wakes_an_existing_recovered_muse_job(self):
        existing = {"id": "muse-recovered", "job_type": "muse_media_generation"}
        snapshot = {
            "prompt_version": "documentary",
            "image_generation_settings": {"render_mode": "muse_ai"},
        }
        with (
            patch.object(muse_pipeline, "snapshot_reference_assets", return_value=[]),
            patch.object(main.db, "create_system_job_if_absent", return_value=(existing, False)),
            patch.object(main, "_kick_muse_media_queue") as kick,
        ):
            job, created = main._enqueue_muse_media_job(
                video_id=42,
                snapshot=snapshot,
            )

        self.assertEqual(job, existing)
        self.assertFalse(created)
        kick.assert_called_once_with()

    def test_session_transient_retries_after_sixty_then_three_hundred_seconds(self):
        for previous_attempt, expected_delay in ((0, 60.0), (1, 300.0)):
            job = {
                "id": f"muse-session-{previous_attempt}",
                "job_type": "muse_media_generation",
                "video_id": 42,
                "payload": {},
                "result": {"muse_session_attempt": previous_attempt},
            }
            with (
                patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
                patch.object(main.db, "get_system_job", return_value=job),
                patch.object(main.db, "schedule_system_job_recovery") as schedule,
                patch.object(main.db, "update_system_job"),
                patch.object(main.db, "update_video_production_state"),
            ):
                main._handle_production_job_error(
                    job, muse_provider.MuseTransientError("temporary session failure")
                )

            self.assertEqual(schedule.call_args.kwargs["delay_seconds"], expected_delay)
            self.assertEqual(
                schedule.call_args.kwargs["result_json"]["muse_session_attempt"],
                previous_attempt + 1,
            )

    def test_third_session_transient_failure_is_terminal(self):
        job = {
            "id": "muse-session-final",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {"muse_session_attempt": 2},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseTransientError("temporary session failure")
            )

        schedule.assert_not_called()
        self.assertTrue(
            any(call_item.kwargs.get("status") == "error" for call_item in update_job.call_args_list)
        )
        self.assertTrue(
            any(
                call_item.kwargs.get("result_json", {}).get("muse_session_attempt") == 3
                for call_item in update_job.call_args_list
            )
        )

    def test_service_busy_wait_does_not_consume_session_attempt(self):
        job = {
            "id": "muse-profile-busy",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job"),
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(job, muse_provider.MuseServiceBusy("busy"))

        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 15.0)
        self.assertNotIn("muse_session_attempt", schedule.call_args.kwargs["result_json"])
        self.assertEqual(schedule.call_args.kwargs["result_json"]["muse_wait_reason"], "service_busy")

    def test_chatgpt_planner_busy_wait_does_not_consume_muse_attempt(self):
        job = {
            "id": "muse-planner-busy",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job"),
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job,
                muse_pipeline.MusePlannerBusy(
                    "busy", delay_seconds=30.0, consume_attempt=False
                ),
            )
        result = schedule.call_args.kwargs["result_json"]
        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 30.0)
        self.assertEqual(result["muse_wait_reason"], "chatgpt_busy")
        self.assertNotIn("muse_session_attempt", result)

    def test_invalid_semantic_plan_pauses_before_muse(self):
        job = {
            "id": "muse-plan-invalid",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state") as update_video,
        ):
            main._handle_production_job_error(
                job,
                muse_pipeline.MuseSemanticPlanNeedsAttention("invalid anchors"),
            )
        self.assertEqual(update_job.call_args.kwargs["status"], "paused")
        self.assertEqual(
            update_job.call_args.kwargs["result_json"]["attention_required"],
            "muse_semantic_plan",
        )
        self.assertEqual(
            update_video.call_args.kwargs["current_stage"],
            "muse_semantic_plan_needs_attention",
        )

    def test_chatgpt_attention_pauses_muse_planner(self):
        job = {
            "id": "muse-plan-login",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main, "_schedule_automatic_chatgpt_login", return_value=True),
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job,
                main.ChatGPTAttentionRequiredError("verify login"),
            )
        self.assertEqual(update_job.call_args.kwargs["status"], "paused")
        self.assertEqual(
            update_job.call_args.kwargs["result_json"]["attention_required"],
            "chatgpt_verification",
        )
        self.assertEqual(
            update_job.call_args.kwargs["result_json"]["automatic_login"],
            "pending",
        )

    def test_muse_auth_failure_does_not_enter_youtube_pause_flow(self):
        job = {
            "id": "muse-no-proxy",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main, "_pause_youtube_publish_job") as pause_youtube,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(
                job, muse_provider.MuseAuthenticationError("no account")
            )

        pause_youtube.assert_not_called()
        self.assertEqual(update_job.call_args.kwargs["status"], "error")

    def test_provider_source_has_no_playwright_or_gpm_fallback(self):
        source = Path(muse_provider.__file__).read_text(encoding="utf-8")
        self.assertNotIn("playwright", source.lower())
        self.assertNotIn("gpm_service", source)
        self.assertIn("/v1/media/", source)
        self.assertIn("parsed.hostname != muse_studio_service.MUSE_API_HOST", source)

    def test_muse_settings_response_exposes_only_sanitized_service_status(self):
        safe_status = {
            "running": True,
            "owned": True,
            "api_ready": True,
            "accounts": 2,
            "browser_running": True,
            "api_url": "http://127.0.0.1:18610",
        }
        with (
            patch.object(main.muse_settings, "load_muse_settings", return_value={"muse_studio_path": "C:\\Muse"}),
            patch.object(main.muse_studio_service, "status", return_value=safe_status),
        ):
            response = main.get_muse_settings_endpoint()
        serialized = response.body.decode("utf-8")
        self.assertIn('"accounts":2', serialized)
        for secret_field in ("api_key", "cookie", "token", "proxy"):
            self.assertNotIn(secret_field, serialized.lower())

    def test_legacy_muse_profile_routes_are_removed(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        self.assertNotIn('/api/muse/profile/open', source)
        self.assertNotIn('/api/muse/profile/verify', source)
        self.assertIn('/api/muse/studio/open', source)
        self.assertIn('/api/muse/service/verify', source)

    def test_muse_api_responses_disable_caching(self):
        response = main._muse_json_response({"success": True})
        self.assertEqual(response.headers["cache-control"], "no-store")
        error = main._muse_http_exception(400, ValueError("bad request"))
        self.assertEqual(error.headers["Cache-Control"], "no-store")

    def test_startup_recovers_but_does_not_kick_muse_queue(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        startup_start = source.index('@app.on_event("startup")')
        startup_end = source.index('@app.on_event("shutdown")', startup_start)
        startup = source[startup_start:startup_end]
        self.assertIn('"muse_media_generation"', startup)
        self.assertNotIn("_kick_muse_media_queue()", startup)


if __name__ == "__main__":
    unittest.main()
