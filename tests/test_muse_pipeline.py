import asyncio
import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from nexus_engine import main
from nexus_engine.services import (
    chatgpt_worker,
    chatgpt_projects,
    database,
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
    payload = {
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
    if windows and windows[-1].get("is_video"):
        index = len(windows)
        payload["terminal_frame"] = {
            "index": index,
            "subject": f"documentary subject {windows[-1]['index']}",
            "action": "settles into a visibly completed final state after the transition",
            "setting": f"specific location {windows[-1]['index']}",
            "era": f"period {windows[-1]['index']}",
            "visual_anchor": f"distinct terminal visual anchor after scene {windows[-1]['index']}",
            "safe_visual_anchor": "quiet non-violent completed documentary moment",
            "must_show": [
                f"visible object {windows[-1]['index']}",
                f"location marker {windows[-1]['index']}",
            ],
            "composition": "wide layered documentary framing after the movement completes",
            "lighting": "soft directional daylight",
            "color": "muted earth tones",
            "narrative_role": "conclusion",
        }
    return payload


def _planner_windows(count: int, *, final_is_video: bool = False) -> list[dict]:
    return [
        {
            "index": index,
            "start": float(index * 10),
            "end": float((index + 1) * 10),
            "duration": 10.0,
            "transcript": f"quiet narration for scene {index}",
            "is_video": bool(final_is_video and index == count - 1),
            "media_type": "video" if final_is_video and index == count - 1 else "image",
        }
        for index in range(count)
    ]


def _batch_payload(windows: list[dict], *, needs_terminal_frame: bool = False) -> dict:
    payload = _semantic_payload(windows)
    payload.pop("story_context", None)
    if needs_terminal_frame:
        payload["terminal_frame"]["index"] = int(windows[-1]["index"]) + 1
    else:
        payload.pop("terminal_frame", None)
    return payload


def _overview_payload() -> dict:
    return {
        "story_context": "A coherent documentary story with stable visual continuity.",
        "continuity_rules": [
            "Keep the same documentary visual language.",
            "Preserve source-grounded places and time periods.",
            "Carry recurring subjects forward without inventing events.",
        ],
        "recurring_anchors": ["documentary subject", "source location"],
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
        payload = {
            "story_context": "The 1954 GM100 retreat through Mang Yang.",
            "scenes": scenes,
            "terminal_frame": {
                "index": len(windows),
                "subject": "những người được tưởng niệm",
                "action": "stand in a completed quiet remembrance after the final camera move",
                "setting": "đèo Mang Yang, Việt Nam",
                "era": "năm 1954 remembered from the present day",
                "visual_anchor": "A distinct final wide view of Mang Yang after the memorial gathering settles.",
                "safe_visual_anchor": "A quiet completed memorial moment at Mang Yang.",
                "must_show": ["đèo Mang Yang", "những người được tưởng niệm"],
                "composition": "wide final landscape framing with still foreground figures",
                "lighting": "humid overcast daylight",
                "color": "muted tropical earth tones",
                "narrative_role": "conclusion",
            },
        }
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
            "terminal_frame": {
                "index": 1,
                "subject": "Apple product designers and iPhone 17",
                "action": "The designers finish the demonstration and place the phone on its stand.",
                "setting": "a product design studio inside Apple Park, California",
                "era": "September 2026 after the product demonstration",
                "visual_anchor": "The iPhone 17 rests on its stand with Apple Park visible beyond the studio.",
                "safe_visual_anchor": "Designers calmly observe the finished phone display at Apple Park.",
                "must_show": ["iPhone 17 prototype", "Apple Park studio"],
                "composition": "clean final wide product framing",
                "lighting": "soft daylight through glass walls",
                "color": "silver, white, and natural green",
                "narrative_role": "conclusion",
            },
        }
        plan = muse_semantic_planner.compile_muse_scene_plan(
            payload,
            windows,
            title="Inside the iPhone 17 design process",
            style="clean contemporary technology product photography",
        )
        scene = plan["scenes"][0]
        self.assertEqual(plan["planner_version"], "semantic_v3")
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
        self.assertNotIn("terminal_frame", plan)

    def test_final_video_requires_terminal_frame(self):
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
        payload.pop("terminal_frame")

        with self.assertRaises(muse_semantic_planner.SemanticPlanValidationError):
            muse_semantic_planner.validate_semantic_plan(payload, windows)

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
                    "frame_contract_version": "start_end_v1",
                    "start_frame_hash": "start-hash",
                    "end_frame_hash": "end-hash",
                    "raw_prompt": "must not leak",
                    "api_key": "must not leak",
                    "selected_references": [{"path": "secret-path"}],
                },
            }
        )
        self.assertEqual(filtered["prompt_policy_version"], "balanced_v2")
        self.assertEqual(filtered["refusal_count"], 1)
        self.assertEqual(filtered["frame_contract_version"], "start_end_v1")
        self.assertEqual(filtered["start_frame_hash"], "start-hash")
        self.assertEqual(filtered["end_frame_hash"], "end-hash")
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

    def test_planner_checkpoint_hash_mismatch_is_ignored_and_broken_pending_pauses(self):
        prepared = {"plan_hash": "current-hash", "windows": _planner_windows(31)}
        with (
            patch.object(
                muse_pipeline.db,
                "get_latest_video_artifact",
                return_value={"content_hash": "old-hash"},
            ),
            patch.object(muse_pipeline, "_load_json_artifact") as load_json,
        ):
            self.assertEqual(muse_pipeline._load_planner_checkpoint(42, prepared), {})
        load_json.assert_not_called()

        broken_pending = {
            "plan_hash": "current-hash",
            "execution_version": "batched_v1",
            "batch_size": 30,
            "batches_per_chat": 4,
            "total_scenes": 31,
            "total_batches": 2,
            "chat_groups": {
                "0": {
                    "pending_prompt": {
                        "step": "batch_0",
                        "fingerprint": "fingerprint",
                    }
                }
            },
            "completed_batches": {},
            "invalid_steps": {},
            "failed_steps": {},
        }
        with (
            patch.object(
                muse_pipeline.db,
                "get_latest_video_artifact",
                return_value={"content_hash": "current-hash"},
            ),
            patch.object(
                muse_pipeline,
                "_load_json_artifact",
                return_value=({}, broken_pending),
            ),
        ):
            with self.assertRaisesRegex(
                muse_pipeline.MuseSemanticPlanNeedsAttention,
                "không thể recover",
            ):
                muse_pipeline._load_planner_checkpoint(42, prepared)

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

    def test_global_overview_is_bounded_and_preserves_first_and_last_batch(self):
        windows = _planner_windows(1500)
        summaries = muse_semantic_planner.build_overview_batch_summaries(windows)
        self.assertEqual(len(summaries), 48)
        self.assertEqual(summaries[0]["batch_index"], 0)
        self.assertEqual(summaries[-1]["batch_index"], 49)
        self.assertEqual(summaries[0]["scene_start_index"], 0)
        self.assertEqual(summaries[-1]["scene_end_index"], 1499)

    def test_global_overview_rejects_prose_placeholder_and_invalid_limits(self):
        with self.assertRaises(muse_semantic_planner.SemanticPlanValidationError):
            muse_semantic_planner.parse_strict_planner_response(
                "Here is the result: " + json.dumps(_overview_payload())
            )
        invalid = _overview_payload()
        invalid["story_context"] = "unknown"
        invalid["continuity_rules"] = ["only one rule"]
        with self.assertRaises(muse_semantic_planner.SemanticPlanValidationError) as raised:
            muse_semantic_planner.validate_global_overview(invalid)
        self.assertIn("story_context", str(raised.exception))
        self.assertIn("continuity_rules", str(raised.exception))

    def test_batch_validation_uses_global_indices_terminal_policy_and_prior_anchors(self):
        windows = _planner_windows(31, final_is_video=True)
        first_payload = _batch_payload(windows[:30])
        first = muse_semantic_planner.validate_semantic_batch(
            first_payload,
            windows[:30],
            previous_visual_anchors=[],
            needs_terminal_frame=False,
            terminal_index=31,
        )
        final_payload = _batch_payload(windows[30:], needs_terminal_frame=True)
        final = muse_semantic_planner.validate_semantic_batch(
            final_payload,
            windows[30:],
            previous_visual_anchors=[scene["visual_anchor"] for scene in first["scenes"]],
            needs_terminal_frame=True,
            terminal_index=31,
        )
        self.assertEqual(final["scenes"][0]["index"], 30)
        self.assertEqual(final["terminal_frame"]["index"], 31)

        duplicate = _batch_payload(windows[30:])
        duplicate["scenes"][0]["visual_anchor"] = first["scenes"][0]["visual_anchor"]
        with self.assertRaisesRegex(
            muse_semantic_planner.SemanticPlanValidationError,
            "trùng visual_anchor",
        ):
            muse_semantic_planner.validate_semantic_batch(
                duplicate,
                windows[30:],
                previous_visual_anchors=[first["scenes"][0]["visual_anchor"]],
                needs_terminal_frame=False,
                terminal_index=31,
            )

    def test_batched_worker_uses_expected_batch_and_chat_group_counts(self):
        for scene_count, expected_batches, expected_groups in ((31, 2, 1), (360, 12, 3)):
            with self.subTest(scene_count=scene_count):
                windows = _planner_windows(scene_count)
                responses = [json.dumps(_overview_payload())]
                responses.extend(
                    json.dumps(_batch_payload(windows[start : start + 30]))
                    for start in range(0, scene_count, 30)
                )
                page = MagicMock()
                page.url = "https://chatgpt.com/c/planner-batched"
                context = MagicMock()
                context.pages = [page]
                playwright = MagicMock()
                with (
                    tempfile.TemporaryDirectory() as temp_dir,
                    patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path(temp_dir)),
                    patch.object(chatgpt_worker, "sync_playwright") as sync_playwright,
                    patch.object(chatgpt_worker, "launch_chatgpt_context", return_value=context),
                    patch.object(
                        chatgpt_worker,
                        "get_chatgpt_project_url",
                        return_value="https://chatgpt.com/g/project",
                    ),
                    patch.object(chatgpt_worker, "navigate_to_chatgpt_project") as navigate,
                    patch.object(
                        chatgpt_worker,
                        "is_chatgpt_conversation_url",
                        side_effect=lambda value: "/c/" in str(value),
                    ),
                    patch.object(
                        chatgpt_worker,
                        "send_or_recover_generation_prompt",
                        side_effect=responses,
                    ) as send_or_recover,
                ):
                    sync_playwright.return_value.__enter__.return_value = playwright
                    result = chatgpt_worker.generate_muse_scene_plan_only(
                        title="Title",
                        style="documentary",
                        windows=windows,
                        prompt_version="default",
                        planner_state={},
                    )
                self.assertEqual(send_or_recover.call_count, expected_batches + 1)
                self.assertEqual(navigate.call_count, expected_groups)
                self.assertEqual(result["planner_batch_count"], expected_batches)
                self.assertEqual(result["planner_chat_group_count"], expected_groups)
                self.assertEqual(len(result["semantic_plan"]["scenes"]), scene_count)
                self.assertEqual(result["planner_source"], "chatgpt_playwright_batched_v1")

    def test_exactly_thirty_scenes_keeps_single_prompt_behavior(self):
        windows = _planner_windows(30)
        page = MagicMock()
        page.url = "https://chatgpt.com/c/planner-single"
        context = MagicMock()
        context.pages = [page]
        playwright = MagicMock()
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path(temp_dir)),
            patch.object(chatgpt_worker, "sync_playwright") as sync_playwright,
            patch.object(chatgpt_worker, "launch_chatgpt_context", return_value=context),
            patch.object(
                chatgpt_worker,
                "get_chatgpt_project_url",
                return_value="https://chatgpt.com/g/project",
            ),
            patch.object(chatgpt_worker, "navigate_to_chatgpt_project"),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_conversation_url",
                side_effect=lambda value: "/c/" in str(value),
            ),
            patch.object(
                chatgpt_worker,
                "send_prompt",
                return_value=json.dumps(_semantic_payload(windows)),
            ) as send,
            patch.object(chatgpt_worker, "send_or_recover_generation_prompt") as batched_send,
        ):
            sync_playwright.return_value.__enter__.return_value = playwright
            result = chatgpt_worker.generate_muse_scene_plan_only(
                title="Title",
                style="documentary",
                windows=windows,
                prompt_version="default",
            )
        send.assert_called_once()
        batched_send.assert_not_called()
        self.assertEqual(result["planner_source"], "chatgpt_playwright_batch")

    def test_invalid_long_batch_repairs_only_that_batch(self):
        windows = _planner_windows(31)
        responses = [
            json.dumps(_overview_payload()),
            "{}",
            json.dumps(_batch_payload(windows[:30])),
            json.dumps(_batch_payload(windows[30:])),
        ]
        page = MagicMock()
        page.url = "https://chatgpt.com/c/planner-repair"
        context = MagicMock()
        context.pages = [page]
        playwright = MagicMock()
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path(temp_dir)),
            patch.object(chatgpt_worker, "sync_playwright") as sync_playwright,
            patch.object(chatgpt_worker, "launch_chatgpt_context", return_value=context),
            patch.object(
                chatgpt_worker,
                "get_chatgpt_project_url",
                return_value="https://chatgpt.com/g/project",
            ),
            patch.object(chatgpt_worker, "navigate_to_chatgpt_project"),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_conversation_url",
                side_effect=lambda value: "/c/" in str(value),
            ),
            patch.object(
                chatgpt_worker,
                "send_or_recover_generation_prompt",
                side_effect=responses,
            ) as send_or_recover,
        ):
            sync_playwright.return_value.__enter__.return_value = playwright
            result = chatgpt_worker.generate_muse_scene_plan_only(
                title="Title",
                style="documentary",
                windows=windows,
                prompt_version="default",
                planner_state={},
            )
        steps = [call.args[2] for call in send_or_recover.call_args_list]
        self.assertEqual(steps, ["overview", "batch_0", "batch_0_repair", "batch_1"])
        self.assertEqual(len(result["semantic_plan"]["scenes"]), 31)

    def test_batched_worker_resume_after_five_batches_starts_at_batch_six(self):
        windows = _planner_windows(180)
        completed = {
            str(batch_index): muse_semantic_planner.validate_semantic_batch(
                _batch_payload(windows[batch_index * 30 : (batch_index + 1) * 30]),
                windows[batch_index * 30 : (batch_index + 1) * 30],
                previous_visual_anchors=[
                    f"unique concrete visual anchor for scene {index}"
                    for index in range(batch_index * 30)
                ],
                needs_terminal_frame=False,
                terminal_index=180,
            )
            for batch_index in range(5)
        }
        state = {
            "overview": _overview_payload(),
            "completed_batches": completed,
            "chat_groups": {"1": {"chat_url": "https://chatgpt.com/c/group-1"}},
            "invalid_steps": {},
            "failed_steps": {},
        }
        page = MagicMock()
        page.url = "https://chatgpt.com/c/group-1"
        context = MagicMock()
        context.pages = [page]
        playwright = MagicMock()
        final_response = json.dumps(_batch_payload(windows[150:180]))
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path(temp_dir)),
            patch.object(chatgpt_worker, "sync_playwright") as sync_playwright,
            patch.object(chatgpt_worker, "launch_chatgpt_context", return_value=context),
            patch.object(chatgpt_worker, "check_chatgpt_page_attention"),
            patch.object(chatgpt_worker, "ensure_expected_conversation_page"),
            patch.object(chatgpt_worker, "wait_for_conversation_history"),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_conversation_url",
                side_effect=lambda value: "/c/" in str(value),
            ),
            patch.object(
                chatgpt_worker,
                "send_or_recover_generation_prompt",
                return_value=final_response,
            ) as send_or_recover,
        ):
            sync_playwright.return_value.__enter__.return_value = playwright
            result = chatgpt_worker.generate_muse_scene_plan_only(
                title="Title",
                style="documentary",
                windows=windows,
                prompt_version="default",
                planner_state=state,
            )
        self.assertEqual(send_or_recover.call_count, 1)
        self.assertEqual(send_or_recover.call_args.args[2], "batch_5")
        self.assertEqual(result["planner_resumed_batch_count"], 5)
        self.assertEqual(len(result["semantic_plan"]["scenes"]), 180)

    def test_pending_prompt_recovery_never_calls_send_prompt_twice(self):
        prompt = "stable planner prompt"
        state = {
            "pending_prompt": {
                "step": "batch_5",
                "fingerprint": chatgpt_worker.get_prompt_fingerprint(prompt),
                "status": "submitted",
            }
        }
        page = MagicMock()
        with (
            patch.object(
                chatgpt_worker,
                "recover_pending_prompt_response",
                return_value="recovered response",
            ) as recover,
            patch.object(chatgpt_worker, "send_prompt") as send,
        ):
            response = chatgpt_worker.send_or_recover_generation_prompt(
                page,
                state,
                "batch_5",
                prompt,
                persistence_callback=MagicMock(),
            )
        self.assertEqual(response, "recovered response")
        recover.assert_called_once()
        send.assert_not_called()

    def test_strict_pending_recovery_pauses_when_prompt_presence_is_unknown(self):
        page = MagicMock()
        with (
            patch.object(chatgpt_worker, "wait_for_existing_assistant_response", return_value=""),
            patch.object(chatgpt_worker, "recover_assistant_response_from_backend", return_value=""),
            patch.object(chatgpt_worker, "recover_assistant_response_after_reload", return_value=""),
            patch.object(chatgpt_worker, "_prompt_presence_in_conversation", return_value=None),
            patch.object(chatgpt_worker, "send_prompt") as send,
        ):
            with self.assertRaises(chatgpt_worker.ChatGPTPendingPromptRecoveryError):
                chatgpt_worker.recover_pending_prompt_response(
                    page,
                    "pending prompt",
                    fail_closed_if_prompt_presence_unknown=True,
                )
        send.assert_not_called()

    def test_three_hour_scale_has_36_batches_and_nine_chat_groups(self):
        windows = _planner_windows(1080)
        batches = [windows[index : index + 30] for index in range(0, len(windows), 30)]
        self.assertEqual(len(batches), 36)
        self.assertTrue(all(len(batch) <= 30 for batch in batches))
        self.assertEqual((len(batches) + 3) // 4, 9)
        self.assertEqual(
            len(muse_semantic_planner.build_overview_batch_summaries(windows)),
            36,
        )


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

    def test_video_prompt_maps_start_and_end_frames(self):
        prompt = muse_provider.MusePromptBuilder.video(
            "A character walks forward.", duration_seconds=10
        )
        self.assertIn("@Image1 is the required first frame", prompt)
        self.assertIn("@Image2 is the required final frame", prompt)
        self.assertNotIn("character references", prompt)
        self.assertIn("16:9", prompt)


class TestMuseStartEndPipeline(unittest.TestCase):
    @staticmethod
    def _plan() -> dict:
        scenes = []
        for index in range(3):
            scenes.append(
                {
                    "index": index,
                    "duration": 10.0,
                    "is_video": True,
                    "media_type": "video",
                    "prompt": f"scene image prompt {index}",
                    "action": f"camera moves through scene {index}",
                    "transcript": f"scene narration {index}",
                    "style": "documentary realism",
                }
            )
        return {
            "planner_version": "semantic_v3",
            "reference_snapshot": [
                {
                    "set_id": "character",
                    "set_name": "Character",
                    "set_index": 0,
                    "image_index": 0,
                    "aliases": ["character"],
                    "path": "character.png",
                    "sha256": "character-hash",
                    "primary": True,
                }
            ],
            "scenes": scenes,
            "terminal_frame": {
                "index": 3,
                "is_video": False,
                "media_type": "image",
                "prompt": "distinct terminal image prompt",
                "action": "the final state settles visibly",
                "transcript": "scene narration 2",
                "style": "documentary realism",
            },
        }

    def _run_prepare(self, root: Path, checksum_overrides: dict[str, str] | None = None):
        checksum_overrides = checksum_overrides or {}
        root.mkdir(parents=True, exist_ok=True)
        video_calls = []

        class Session:
            async def __aenter__(self):
                return self.provider

            async def __aexit__(self, *_args):
                return False

            def __init__(self, provider):
                self.provider = provider

        class FakeProvider:
            def __init__(self, **_kwargs):
                self.capacity_preflight = {"status": "ready"}

            def open_session(self):
                return Session(self)

        async def fake_generate(**kwargs):
            artifact_type = kwargs["artifact_type"]
            if kwargs["expected"] == "image":
                path = root / f"{artifact_type.replace(':', '_')}.png"
                path.write_bytes(artifact_type.encode("utf-8"))
                return {
                    "status": "ready",
                    "path": str(path),
                    "metadata": {
                        "checksum": checksum_overrides.get(
                            artifact_type,
                            f"{artifact_type}-hash",
                        )
                    },
                }
            video_calls.append(
                {
                    "scene_index": int(kwargs["scene"]["index"]),
                    "request_hash": kwargs["request_hash"],
                    "references": list(kwargs["references"]),
                }
            )
            return {
                "status": "ready",
                "path": str(root / f"video_{kwargs['scene']['index']}.mp4"),
                "metadata": {
                    "frame_contract_version": muse_provider.MUSE_FRAME_CONTRACT_VERSION
                },
            }

        with (
            patch.object(muse_pipeline, "MuseStudioApiProvider", FakeProvider),
            patch.object(muse_pipeline, "_generate_request", new=fake_generate),
        ):
            result = asyncio.run(
                muse_pipeline._prepare_media_async(
                    video_id=7,
                    snapshot={"image_generation_settings": {}},
                    prepared={},
                    plan=self._plan(),
                    progress=lambda *_args, **_kwargs: None,
                    cancel_check=lambda: None,
                    job_id="job-7",
                )
            )
        return result, video_calls

    def test_pairs_consecutive_scenes_and_last_scene_to_terminal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result, calls = self._run_prepare(Path(temp_dir))

        self.assertEqual(len(result["scene_results"]), 3)
        self.assertIsNotNone(result["terminal_frame_artifact"])
        self.assertEqual(
            [
                [reference["sha256"] for reference in call["references"]]
                for call in calls
            ],
            [
                ["muse_scene_image:0-hash", "muse_scene_image:1-hash"],
                ["muse_scene_image:1-hash", "muse_scene_image:2-hash"],
                ["muse_scene_image:2-hash", "muse_terminal_frame-hash"],
            ],
        )
        self.assertTrue(
            all(
                reference["sha256"] != "character-hash"
                for call in calls
                for reference in call["references"]
            )
        )

    def test_changing_endpoint_checksum_changes_related_request_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, baseline = self._run_prepare(root / "baseline")
            _, changed_scene = self._run_prepare(
                root / "changed-scene",
                {"muse_scene_image:1": "changed-scene-1-hash"},
            )
            _, changed_terminal = self._run_prepare(
                root / "changed-terminal",
                {"muse_terminal_frame": "changed-terminal-hash"},
            )

        baseline_hashes = [call["request_hash"] for call in baseline]
        changed_scene_hashes = [call["request_hash"] for call in changed_scene]
        changed_terminal_hashes = [call["request_hash"] for call in changed_terminal]
        self.assertNotEqual(baseline_hashes[0], changed_scene_hashes[0])
        self.assertNotEqual(baseline_hashes[1], changed_scene_hashes[1])
        self.assertEqual(baseline_hashes[2], changed_scene_hashes[2])
        self.assertEqual(baseline_hashes[:2], changed_terminal_hashes[:2])
        self.assertNotEqual(baseline_hashes[2], changed_terminal_hashes[2])


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

    def test_high_risk_video_keeps_both_endpoint_frames(self):
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
        self.assertEqual(selected, references)
        self.assertEqual(metadata["prompt_strategy"], "risk_rewritten")
        self.assertIn("@Image1 to @Image2", prompt)
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
    def test_open_session_does_not_run_capacity_preflight(self):
        provider = muse_provider.MuseStudioApiProvider()

        async def run():
            async with provider.open_session():
                return dict(provider.capacity_preflight)

        with (
            patch.object(muse_provider.muse_studio_service, "acquire"),
            patch.object(muse_provider.muse_studio_service, "release") as release,
            patch.object(provider, "_post_json") as post,
        ):
            capacity = asyncio.run(run())

        post.assert_not_called()
        release.assert_called_once()
        self.assertEqual(capacity, {})

    def test_open_session_preserves_capability_disabled_error(self):
        provider = muse_provider.MuseStudioApiProvider()
        capability_error = muse_studio_service.MuseStudioCapabilityDisabled(
            "task_recovery",
            "release gate disabled",
        )

        async def run():
            async with provider.open_session():
                self.fail("session must not open while capability is disabled")

        with (
            patch.object(
                muse_provider.muse_studio_service,
                "acquire",
                side_effect=capability_error,
            ),
            patch.object(muse_provider.muse_studio_service, "release") as release,
        ):
            with self.assertRaises(muse_studio_service.MuseStudioCapabilityDisabled):
                asyncio.run(run())

        release.assert_not_called()

    def test_submit_response_captures_only_sanitized_capacity_summary(self):
        provider = muse_provider.MuseStudioApiProvider()
        provider._capture_media_capacity(
            {
                "media_capacity": {
                    "status": "degraded",
                    "eligible": 0,
                    "unknown": 1,
                    "exhausted": 2,
                    "skipped": 0,
                    "cache_hit": True,
                    "checked_at": 1,
                    "elapsed_seconds": 0.1,
                    "api_key": "must-not-leak",
                    "path": "must-not-leak",
                }
            }
        )

        self.assertEqual(provider.capacity_preflight["status"], "degraded")
        serialized = json.dumps(provider.capacity_preflight)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("path", serialized)

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
            endpoint, payload, idempotency_key, timeout_seconds = post.call_args.args
            self.assertEqual(endpoint, "/v1/images/tasks")
            self.assertEqual(payload["reference_image"], [str(path.resolve()) for path in references])
            self.assertEqual(idempotency_key, "a" * 64)
            self.assertEqual(timeout_seconds, muse_provider.MUSE_ADMISSION_TIMEOUT_SECONDS)

    def test_video_payload_sends_explicit_start_end_and_has_idempotency_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            references = []
            for name in ("start.png", "end.png"):
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
                        references[0],
                        references[1],
                        Path(temp_dir) / "clip",
                        request_hash="b" * 64,
                    )
                )
            endpoint, payload, idempotency_key, timeout_seconds = post.call_args.args
            self.assertEqual(endpoint, "/v1/videos")
            self.assertEqual(payload["start_image"], str(references[0].resolve()))
            self.assertEqual(payload["end_image"], str(references[1].resolve()))
            self.assertNotIn("reference_image", payload)
            self.assertEqual(idempotency_key, "b" * 64)
            self.assertEqual(timeout_seconds, muse_provider.MUSE_ADMISSION_TIMEOUT_SECONDS)

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
                    asyncio.run(
                        provider.create_video("prompt", scene, scene, Path(temp_dir) / "video")
                    )

    def test_admission_429_is_retryable_before_video_submission(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scene = Path(temp_dir) / "scene.png"
            scene.write_bytes(b"scene")
            provider = muse_provider.MuseStudioApiProvider()
            with patch.object(
                provider, "_post_json", side_effect=muse_provider.MuseAdmissionRetry("queue full")
            ):
                with self.assertRaises(muse_provider.MuseAdmissionRetry):
                    asyncio.run(
                        provider.create_video("prompt", scene, scene, Path(temp_dir) / "video")
                    )

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
                            scene,
                            scene,
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

    def test_terminal_provider_timeouts_are_classified_as_retryable(self):
        cases = (
            (
                "Waiting for generation timed out; no new result appeared",
                muse_provider.MuseTaskTransientFailure,
            ),
            ("Hết thời gian chờ tạo ảnh", muse_provider.MuseTaskTransientFailure),
            ("等待生成超时，未出现新的生成结果", muse_provider.MuseTaskTransientFailure),
            (
                "任务总等待时限已到，停止重试",
                muse_provider.MuseTaskWaitLimitExceeded,
            ),
        )
        for message, expected_error in cases:
            with self.subTest(message=message):
                self.assertIs(
                    muse_provider.classify_muse_failure(message),
                    expected_error,
                )
                with self.assertRaises(expected_error):
                    muse_provider.MuseStudioApiProvider._raise_task_failure(
                        {"error": message, "elapsed": 360.9}
                    )

    def test_quota_failures_have_a_distinct_non_transient_classification(self):
        for message in (
            "账号额度不足",
            "quota exceeded",
            "media_quota_exhausted",
            "所有 Muse 媒体账号额度均已耗尽",
        ):
            with self.subTest(message=message):
                self.assertIs(
                    muse_provider.classify_muse_failure(message),
                    muse_provider.MuseQuotaUnavailable,
                )
        with self.assertRaises(muse_provider.MuseQuotaUnavailable) as raised:
            muse_provider.MuseStudioApiProvider._raise_task_failure(
                {
                    "error": "账号额度不足",
                    "updated_at": 10,
                    "media_capacity": {
                        "status": "exhausted",
                        "eligible": 0,
                        "unknown": 0,
                        "exhausted": 3,
                        "skipped": 1,
                    },
                }
            )
        self.assertEqual(raised.exception.capacity_preflight["exhausted"], 3)
        self.assertNotIn("account_id", raised.exception.capacity_preflight)

    def test_service_restart_failure_is_retryable_for_structured_and_legacy_tasks(self):
        tasks = (
            {
                "error": "service restarted",
                "error_code": "service_restart_interrupted",
                "retryable": True,
                "runtime_id": "runtime-2",
            },
            {"error": "服务重启中断了任务，请重新提交"},
        )
        for task in tasks:
            with self.subTest(task=task):
                with self.assertRaises(muse_provider.MuseServiceInterrupted):
                    muse_provider.MuseStudioApiProvider._raise_task_failure(task)

    def test_heartbeat_is_throttled_to_ten_seconds(self):
        heartbeats = []
        provider = muse_provider.MuseStudioApiProvider(heartbeat=heartbeats.append)
        provider.set_heartbeat_context(phase="videos", scene_index=3)
        with patch.object(
            muse_provider.time,
            "monotonic",
            side_effect=[5.0, 10.0, 10.1],
        ):
            provider._last_heartbeat_at = 0.0
            provider._emit_heartbeat({"status": "processing", "progress": 20})
            provider._emit_heartbeat({"status": "processing", "progress": 40})
            provider._emit_heartbeat({"status": "processing", "progress": 50})

        self.assertEqual(len(heartbeats), 1)
        self.assertEqual(heartbeats[0]["phase"], "videos")
        self.assertEqual(heartbeats[0]["scene_index"], 3)
        self.assertEqual(heartbeats[0]["progress"], 40)

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
    def test_recovery_epoch_rotates_idempotency_key_and_tracks_submission_attempt(self):
        base_hash = "request-hash"
        initial = muse_pipeline._submission_hash_for_attempt(
            base_hash, 1, "original", recovery_epoch=0
        )
        resumed = muse_pipeline._submission_hash_for_attempt(
            base_hash, 1, "original", recovery_epoch=1
        )
        resumed_second = muse_pipeline._submission_hash_for_attempt(
            base_hash, 2, "original", recovery_epoch=1
        )

        self.assertEqual(initial, base_hash)
        self.assertNotEqual(resumed, initial)
        self.assertNotEqual(resumed_second, resumed)

    def test_recreate_purge_includes_terminal_frame(self):
        artifacts = [
            {"id": 1, "artifact_type": "muse_scene_plan", "path": ""},
            {"id": 2, "artifact_type": "muse_scene_image:0", "path": ""},
            {"id": 3, "artifact_type": "muse_terminal_frame", "path": ""},
            {"id": 4, "artifact_type": "unrelated", "path": ""},
            {"id": 5, "artifact_type": "muse_scene_plan_checkpoint", "path": ""},
        ]
        with (
            patch.object(muse_pipeline.db, "list_video_artifacts", return_value=artifacts),
            patch.object(muse_pipeline.db, "delete_video_artifact") as delete,
        ):
            removed = muse_pipeline.purge_muse_artifacts(42)

        self.assertEqual(removed, 4)
        self.assertEqual([call.args[0] for call in delete.call_args_list], [1, 2, 3, 5])

    def test_partial_reset_purges_endpoint_dependencies_and_preserves_prefix(self):
        artifacts = [
            {"id": 1, "artifact_type": "muse_scene_plan", "path": ""},
            {"id": 2, "artifact_type": "muse_scene_image:1", "path": ""},
            {"id": 3, "artifact_type": "muse_scene_image:2", "path": ""},
            {"id": 4, "artifact_type": "muse_scene_video:0", "path": ""},
            {"id": 5, "artifact_type": "muse_scene_video:1", "path": ""},
            {"id": 6, "artifact_type": "muse_scene_video:2", "path": ""},
            {"id": 7, "artifact_type": "muse_terminal_frame", "path": ""},
            {"id": 8, "artifact_type": "muse_media_manifest", "path": ""},
            {"id": 9, "artifact_type": "final_mp4", "path": ""},
            {"id": 10, "artifact_type": "muse_scene_plan_checkpoint", "path": ""},
        ]
        with (
            patch.object(muse_pipeline.db, "list_video_artifacts", return_value=artifacts),
            patch.object(muse_pipeline.db, "delete_video_artifact") as delete,
        ):
            removed = muse_pipeline.purge_muse_artifacts_from_index(42, 2)

        self.assertEqual(removed, 6)
        self.assertEqual(
            [call.args[0] for call in delete.call_args_list],
            [3, 5, 6, 7, 8, 9],
        )
        self.assertNotIn(1, [call.args[0] for call in delete.call_args_list])
        self.assertNotIn(2, [call.args[0] for call in delete.call_args_list])
        self.assertNotIn(4, [call.args[0] for call in delete.call_args_list])
        self.assertNotIn(10, [call.args[0] for call in delete.call_args_list])

    def test_quota_checkpoint_preserves_attempt_and_rotates_submission_hash(self):
        artifact = None

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "metadata": kwargs["metadata"],
            }
            return artifact

        base_hash = "request-hash"
        initial_hash = muse_pipeline._submission_hash_for_attempt(
            base_hash, 1, "original"
        )
        with patch.object(
            muse_pipeline.db, "upsert_video_artifact", side_effect=upsert
        ):
            muse_pipeline._checkpoint_quota_failure(
                video_id=1,
                artifact_type="muse_scene_image:0",
                request_hash=base_hash,
                metadata={
                    "attempt": 1,
                    "submission_hash": initial_hash,
                    "provider_task_id": "quota-task",
                    "endpoint": "/v1/images/tasks/quota-task",
                    "submitted_at": "2026-01-01T00:00:00Z",
                },
                error=muse_provider.MuseQuotaUnavailable("quota exceeded"),
                generation_started=time.monotonic(),
                capacity_preflight={"status": "ready", "eligible": 1},
            )

        metadata = artifact["metadata"]
        recovery_hash = muse_pipeline._submission_hash_for_attempt(
            base_hash,
            metadata["attempt"],
            "original",
            metadata["quota_recovery_sequence"],
        )
        self.assertEqual(artifact["status"], "error")
        self.assertEqual(metadata["attempt"], 1)
        self.assertTrue(metadata["quota_retryable"])
        self.assertEqual(metadata["quota_recovery_sequence"], 1)
        self.assertEqual(metadata["last_provider_task_id"], "quota-task")
        self.assertNotIn("provider_task_id", metadata)
        self.assertNotEqual(recovery_hash, initial_hash)

    def test_quota_resume_resubmits_with_new_key_without_consuming_attempt(self):
        artifact = None
        submission_hashes = []

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

            class QuotaAdapter:
                capacity_preflight = {
                    "status": "exhausted",
                    "eligible": 0,
                    "unknown": 0,
                    "exhausted": 3,
                }

                async def create_image(self, _prompt, _paths, _destination, **kwargs):
                    submission_hashes.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="quota-task",
                            endpoint="/v1/images/tasks/quota-task",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    error = muse_provider.MuseQuotaUnavailable("quota exceeded")
                    error.capacity_preflight = dict(self.capacity_preflight)
                    raise error

            class SuccessAdapter:
                capacity_preflight = {
                    "status": "ready",
                    "eligible": 1,
                    "unknown": 0,
                    "exhausted": 2,
                }

                async def create_image(self, _prompt, _paths, _destination, **kwargs):
                    submission_hashes.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="recovery-task",
                            endpoint="/v1/images/tasks/recovery-task",
                            submitted_at="2026-01-01T00:01:00Z",
                        )
                    )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "recovery-task",
                        "endpoint": "/v1/images/tasks/recovery-task",
                        "submitted_at": "2026-01-01T00:01:00Z",
                    }

            async def generate(adapter):
                return await muse_pipeline._generate_request(
                    adapter=adapter,
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

            with (
                patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                patch.object(
                    muse_pipeline,
                    "_validate_image",
                    return_value={"width": 1920, "height": 1080},
                ),
            ):
                with self.assertRaises(muse_provider.MuseQuotaUnavailable):
                    asyncio.run(generate(QuotaAdapter()))
                paused_metadata = dict(artifact["metadata"])
                result = asyncio.run(generate(SuccessAdapter()))

        self.assertEqual(paused_metadata["attempt"], 1)
        self.assertTrue(paused_metadata["quota_retryable"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["metadata"]["attempt"], 1)
        self.assertEqual(result["metadata"]["quota_recovery_sequence"], 1)
        self.assertNotIn("quota_retryable", result["metadata"])
        self.assertNotIn("terminal_provider_task", result["metadata"])
        self.assertEqual(len(submission_hashes), 2)
        self.assertNotEqual(submission_hashes[0], submission_hashes[1])

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
            end_frame = root / "end.png"
            first_frame.write_bytes(b"scene")
            end_frame.write_bytes(b"end")
            calls = []

            class AlwaysRefuseAdapter:
                async def create_video(
                    self, prompt, start_frame, final_frame, _destination, **kwargs
                ):
                    calls.append((prompt, [start_frame, final_frame], kwargs["request_hash"]))
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
                        references=[
                            {"path": str(first_frame), "sha256": "scene-hash"},
                            {"path": str(end_frame), "sha256": "end-hash"},
                        ],
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

    def test_provider_wait_limit_schedules_successor_without_degrading_video(self):
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
            end_frame = root / "end.png"
            first_frame.write_bytes(b"scene")
            end_frame.write_bytes(b"end")
            calls = []

            class WaitLimitAdapter:
                async def create_video(
                    self, _prompt, _start_frame, _end_frame, _destination, **kwargs
                ):
                    calls.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="video-task-wait-limit",
                            endpoint="/v1/videos/video-task-wait-limit",
                            submitted_at="2026-01-01T00:00:00Z",
                        )
                    )
                    raise muse_provider.MuseTaskWaitLimitExceeded(
                        "任务总等待时限已到，停止重试"
                    )

            async def run():
                with (
                    patch.object(muse_pipeline.db, "get_latest_video_artifact", side_effect=get_latest),
                    patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=WaitLimitAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_video:0",
                        request_hash="request",
                        prompt="subtle camera motion",
                        references=[
                            {"path": str(first_frame), "sha256": "scene-hash"},
                            {"path": str(end_frame), "sha256": "end-hash"},
                        ],
                        omitted=[],
                        destination=root / "clip",
                        expected="video",
                        scene={"index": 0, "duration": 10},
                        cancel_check=lambda: None,
                        policy_metadata={"prompt_strategy": "original"},
                    )

            with self.assertRaises(muse_pipeline.MuseRetryScheduled) as raised:
                asyncio.run(run())

        self.assertEqual(len(calls), 1)
        self.assertEqual(raised.exception.delay_seconds, 60.0)
        self.assertEqual(artifact["status"], "error")
        self.assertFalse(artifact["metadata"].get("degraded_to_image", False))
        self.assertTrue(artifact["metadata"]["provider_retry_exhausted"])
        self.assertTrue(artifact["metadata"]["retryable_generation"])
        self.assertEqual(artifact["metadata"]["last_provider_task_id"], "video-task-wait-limit")
        self.assertNotIn("provider_task_id", artifact["metadata"])

    def test_provider_wait_limit_retries_image_within_submission_budget(self):
        artifact = None

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        error = muse_provider.MuseTaskWaitLimitExceeded(
            "任务总等待时限已到，停止重试"
        )
        with (
            patch.object(muse_pipeline.db, "upsert_video_artifact", side_effect=upsert),
            self.assertRaises(muse_pipeline.MuseRetryScheduled),
        ):
            muse_pipeline._checkpoint_terminal_transient_failure(
                video_id=1,
                artifact_type="muse_scene_image:0",
                request_hash="request",
                scene={"index": 0},
                metadata={"attempt": 1},
                error=error,
                expected="image",
                generation_started=time.monotonic(),
            )

        self.assertEqual(artifact["status"], "error")
        self.assertNotIn("terminal_generation", artifact["metadata"])
        self.assertTrue(artifact["metadata"]["retryable_generation"])
        self.assertTrue(artifact["metadata"]["provider_retry_exhausted"])

    def test_video_transient_failure_at_attempt_limit_requires_resume(self):
        artifact = None

        def upsert(**kwargs):
            nonlocal artifact
            artifact = {
                "status": kwargs["status"],
                "content_hash": kwargs["content_hash"],
                "path": kwargs.get("path", ""),
                "metadata": kwargs["metadata"],
            }
            return artifact

        with (
            patch.object(
                muse_pipeline.db,
                "upsert_video_artifact",
                side_effect=upsert,
            ),
            self.assertRaises(muse_pipeline.MuseRecoveryExhausted),
        ):
            muse_pipeline._checkpoint_terminal_transient_failure(
                video_id=1,
                artifact_type="muse_scene_video:0",
                request_hash="request",
                scene={"index": 0},
                metadata={
                    "attempt": muse_pipeline.MAX_REQUEST_ATTEMPTS,
                    "provider_task_id": "video-task-third-timeout",
                    "endpoint": "/v1/videos/video-task-third-timeout",
                    "submitted_at": "2026-01-01T00:00:00Z",
                },
                error=muse_provider.MuseTaskTransientFailure("generation timeout"),
                expected="video",
                generation_started=time.monotonic(),
            )

        self.assertEqual(artifact["status"], "error")
        self.assertFalse(artifact["metadata"].get("degraded_to_image", False))
        self.assertTrue(artifact["metadata"]["infrastructure_failure"])
        self.assertNotIn("terminal_generation", artifact["metadata"])
        self.assertEqual(artifact["metadata"]["recovery_state"], "retry_wait")
        self.assertTrue(artifact["metadata"]["recovery_exhausted"])
        self.assertFalse(artifact["metadata"]["provider_retry_exhausted"])
        self.assertTrue(artifact["metadata"]["retryable_generation"])
        self.assertEqual(
            artifact["metadata"]["last_provider_task_id"],
            "video-task-third-timeout",
        )

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
            end_frame = root / "end.png"
            first_frame.write_bytes(b"scene")
            end_frame.write_bytes(b"end")
            calls = []

            class AlwaysRefuseAdapter:
                async def create_video(
                    self, prompt, start_frame, final_frame, _destination, **kwargs
                ):
                    calls.append((prompt, [start_frame, final_frame], kwargs["request_hash"]))
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
                            {"path": str(end_frame), "sha256": "end-hash"},
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
        self.assertEqual(calls[0][1], [first_frame, end_frame])
        self.assertEqual(calls[1][1], [first_frame, end_frame])
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

    def test_connection_loss_before_receipt_keeps_submitting_and_reuses_same_key(self):
        artifact = None
        submitted_hashes = []

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

            class ConnectionLossAdapter:
                async def create_image(self, _prompt, _references, _destination, **kwargs):
                    submitted_hashes.append(kwargs["request_hash"])
                    raise muse_provider.MuseTransientError("connection lost before receipt")

            class SuccessAdapter:
                async def create_image(self, _prompt, _references, _destination, **kwargs):
                    submitted_hashes.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="image-task-recovered",
                            endpoint="/v1/images/tasks/image-task-recovered",
                            submitted_at="2026-01-01T00:01:00Z",
                        )
                    )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "image-task-recovered",
                        "endpoint": "/v1/images/tasks/image-task-recovered",
                        "submitted_at": "2026-01-01T00:01:00Z",
                    }

            async def generate(adapter):
                return await muse_pipeline._generate_request(
                    adapter=adapter,
                    video_id=1,
                    artifact_type="muse_scene_image:0",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=root / "scene",
                    expected="image",
                    scene={"index": 0},
                    cancel_check=lambda: None,
                    policy_metadata={"prompt_strategy": "original"},
                )

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
                patch.object(
                    muse_pipeline,
                    "_validate_image",
                    return_value={"width": 1920, "height": 1080},
                ),
            ):
                with self.assertRaises(muse_pipeline.MuseRetryScheduled) as raised:
                    asyncio.run(generate(ConnectionLossAdapter()))
                submitting = dict(artifact)
                result = asyncio.run(generate(SuccessAdapter()))

        self.assertFalse(raised.exception.consume_attempt)
        self.assertEqual(submitting["status"], "submitting")
        self.assertEqual(submitting["metadata"]["submission_transport_retry_count"], 1)
        self.assertEqual(submitted_hashes, ["request", "request"])
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
        self.assertEqual(render_video.call_args.kwargs["fallback_image_paths"], [None])

    def test_renderer_passes_matching_fallback_image_and_hashes_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "audio.wav"
            captions = root / "captions.srt"
            video = root / "scene.mp4"
            image = root / "scene.png"
            audio.write_bytes(b"audio")
            captions.write_text("captions", encoding="utf-8")
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            scene = {"index": 0, "is_video": True, "duration": 10}
            plan_artifact = {
                "content_hash": "bound-plan",
                "metadata": {"base_plan_hash": "base-plan"},
            }
            plan = {"scenes": [scene], "visual_bible": {}}
            manifest = {
                "version": 3,
                "plan_hash": "bound-plan",
                "caption_hash": "caption-hash",
                "media": [
                    {
                        "scene_index": 0,
                        "path": str(video),
                        "sha256": muse_pipeline._sha256_file(video),
                        "requested_media_type": "video",
                        "resolved_media_type": "video",
                        "fallback_image": {
                            "path": str(image),
                            "sha256": muse_pipeline._sha256_file(image),
                        },
                    }
                ],
            }
            hashes = []
            for policy in ("smart_extend_v1", "smart_extend_v2"):
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
                        "VIDEO_EXTENSION_POLICY_VERSION",
                        policy,
                    ),
                    patch.object(
                        muse_pipeline.video_production,
                        "render_video",
                        return_value=render_result,
                    ) as render_video,
                ):
                    muse_pipeline.render_prepared_muse_video(
                        1,
                        {},
                        lambda *_args, **_kwargs: None,
                        lambda: None,
                    )
                self.assertEqual(
                    render_video.call_args.kwargs["fallback_image_paths"],
                    [image],
                )
                hashes.append(render_video.call_args.kwargs["input_hash"])

            self.assertNotEqual(hashes[0], hashes[1])

    def test_prepare_media_writes_v3_manifest_with_video_fallback_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "scene.png"
            video = root / "scene.mp4"
            image.write_bytes(b"image")
            video.write_bytes(b"video")
            scene = {"index": 0, "is_video": True, "duration": 10}
            plan = {
                "visual_bible": {},
                "scenes": [scene],
                "planner_version": "semantic_v2",
                "planner_source": "chatgpt_playwright_batch",
                "planner_cache_hit": False,
                "planner_elapsed_seconds": 1.0,
                "anchor_coverage": {"ratio": 1.0},
            }
            prepared_media = {
                "scene_results": [
                    {
                        "scene": scene,
                        "image_artifact": {
                            "path": str(image),
                            "metadata": {"checksum": muse_pipeline._sha256_file(image)},
                        },
                        "video_artifact": {
                            "path": str(video),
                            "metadata": {"checksum": muse_pipeline._sha256_file(video)},
                        },
                    }
                ],
                "generation_summary": {"degraded_to_image_count": 0},
            }
            with (
                patch.object(
                    muse_pipeline,
                    "_prepare_inputs",
                    return_value={"plan_hash": "bound-plan", "caption_hash": "captions"},
                ),
                patch.object(muse_pipeline, "_bind_reference_hashes"),
                patch.object(muse_pipeline, "_load_or_build_plan", return_value=plan),
                patch.object(
                    muse_pipeline,
                    "_prepare_media_async",
                    new=AsyncMock(return_value=prepared_media),
                ),
                patch.object(
                    muse_pipeline,
                    "_save_json_artifact",
                    return_value={"id": 1},
                ) as save_artifact,
            ):
                muse_pipeline.prepare_muse_media(
                    1,
                    {},
                    lambda *_args, **_kwargs: None,
                    lambda: None,
                    job_id="job",
                    reference_snapshot=[],
                )

            manifest = save_artifact.call_args.args[3]
            self.assertEqual(manifest["version"], 3)
            self.assertEqual(manifest["media"][0]["path"], str(video))
            self.assertEqual(
                manifest["media"][0]["fallback_image"],
                {
                    "path": str(image),
                    "sha256": muse_pipeline._sha256_file(image),
                },
            )

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

    def test_terminal_timeout_on_reconcile_starts_new_idempotent_attempt(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "attempt": 1,
                "provider": muse_provider.MUSE_PROVIDER_NAME,
                "provider_task_id": "image-task-timeout",
                "endpoint": "/v1/images/tasks/image-task-timeout",
                "submitted_at": "2026-01-01T00:00:00Z",
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

        class TimedOutReceiptAdapter:
            async def reconcile(self, **_kwargs):
                raise muse_provider.MuseTaskTransientFailure(
                    "等待生成超时，未出现新的生成结果"
                )

            async def create_image(self, *_args, **_kwargs):
                raise AssertionError("A terminal receipt must be checkpointed before resubmission")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            async def checkpoint_timeout():
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
                        adapter=TimedOutReceiptAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=root / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                        policy_metadata={"prompt_strategy": "original"},
                    )

            with self.assertRaises(muse_pipeline.MuseRetryScheduled):
                asyncio.run(checkpoint_timeout())

            self.assertEqual(artifact["status"], "error")
            self.assertTrue(artifact["metadata"]["retryable_generation"])
            self.assertEqual(
                artifact["metadata"]["last_provider_task_id"],
                "image-task-timeout",
            )
            self.assertNotIn("provider_task_id", artifact["metadata"])

            generated = root / "generated.png"
            generated.write_bytes(b"generated")
            submitted_hashes = []

            class SuccessfulRetryAdapter:
                async def create_image(self, _prompt, _references, _destination, **kwargs):
                    submitted_hashes.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="image-task-retry",
                            endpoint="/v1/images/tasks/image-task-retry",
                            submitted_at="2026-01-01T00:01:00Z",
                        )
                    )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "image-task-retry",
                        "endpoint": "/v1/images/tasks/image-task-retry",
                        "submitted_at": "2026-01-01T00:01:00Z",
                    }

            async def retry_generation():
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
                    patch.object(
                        muse_pipeline,
                        "_validate_image",
                        return_value={"width": 1920, "height": 1080},
                    ),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=SuccessfulRetryAdapter(),
                        video_id=1,
                        artifact_type="muse_scene_image:0",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=root / "scene",
                        expected="image",
                        scene={"index": 0},
                        cancel_check=lambda: None,
                        policy_metadata={"prompt_strategy": "original"},
                    )

            result = asyncio.run(retry_generation())

        self.assertEqual(result["status"], "ready")
        self.assertEqual(artifact["metadata"]["attempt"], 2)
        self.assertEqual(len(submitted_hashes), 1)
        self.assertNotEqual(submitted_hashes[0], "request")
        self.assertEqual(submitted_hashes[0], artifact["metadata"]["submission_hash"])

    def test_legacy_restart_error_with_receipt_is_reconciled_before_resubmit(self):
        artifact = {
            "status": "error",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "provider": muse_provider.MUSE_PROVIDER_NAME,
                "provider_task_id": "legacy-task",
                "endpoint": "/v1/images/tasks/legacy-task",
                "submitted_at": "2026-01-01T00:00:00Z",
                "submission_hash": "request",
                "submission_variant": "original",
                "attempt": 1,
                "terminal_generation": True,
                "error_class": "MuseError",
            },
        }

        def get_latest(*_args, **_kwargs):
            return artifact

        def upsert(**kwargs):
            artifact.update(
                {
                    "status": kwargs["status"],
                    "content_hash": kwargs["content_hash"],
                    "metadata": kwargs["metadata"],
                }
            )
            return artifact

        class LegacyRestartAdapter:
            async def reconcile(self, **_kwargs):
                raise muse_provider.MuseServiceInterrupted(
                    "服务重启中断了任务，请重新提交"
                )

            async def create_image(self, *_args, **_kwargs):
                raise AssertionError("must checkpoint the old receipt first")

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
                    adapter=LegacyRestartAdapter(),
                    video_id=348,
                    artifact_type="muse_terminal_frame",
                    request_hash="request",
                    prompt="prompt",
                    references=[],
                    omitted=[],
                    destination=Path("unused"),
                    expected="image",
                    scene={"index": 7},
                    cancel_check=lambda: None,
                    policy_metadata={"prompt_strategy": "original"},
                )

        with self.assertRaises(muse_pipeline.MuseRetryScheduled):
            asyncio.run(run())

        self.assertEqual(artifact["status"], "error")
        self.assertTrue(artifact["metadata"]["retryable_generation"])
        self.assertEqual(artifact["metadata"]["last_provider_task_id"], "legacy-task")
        self.assertNotIn("provider_task_id", artifact["metadata"])
        self.assertEqual(artifact["metadata"]["recovery_state"], "retry_wait")
        self.assertNotIn("terminal_generation", artifact["metadata"])

        submitted = []
        with tempfile.TemporaryDirectory() as temp_dir:
            generated = Path(temp_dir) / "terminal.png"
            generated.write_bytes(b"valid-image")

            class SuccessorAdapter:
                async def create_image(self, *_args, **kwargs):
                    submitted.append(kwargs["request_hash"])
                    kwargs["on_submitted"](
                        muse_provider.MuseReceipt(
                            provider_task_id="successor-task",
                            endpoint="/v1/images/tasks/successor-task",
                            submitted_at="2026-01-01T00:01:00Z",
                        )
                    )
                    return {
                        "path": str(generated),
                        "mime_type": "image/png",
                        "provider": muse_provider.MUSE_PROVIDER_NAME,
                        "provider_task_id": "successor-task",
                        "endpoint": "/v1/images/tasks/successor-task",
                        "submitted_at": "2026-01-01T00:01:00Z",
                    }

            async def run_successor():
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
                    patch.object(
                        muse_pipeline,
                        "_validate_image",
                        return_value={"width": 1920, "height": 1080},
                    ),
                ):
                    return await muse_pipeline._generate_request(
                        adapter=SuccessorAdapter(),
                        video_id=348,
                        artifact_type="muse_terminal_frame",
                        request_hash="request",
                        prompt="prompt",
                        references=[],
                        omitted=[],
                        destination=Path(temp_dir) / "terminal",
                        expected="image",
                        scene={"index": 7},
                        cancel_check=lambda: None,
                        policy_metadata={"prompt_strategy": "original"},
                    )

            successor_result = asyncio.run(run_successor())

        self.assertEqual(successor_result["status"], "ready")
        self.assertEqual(len(submitted), 1)
        self.assertNotEqual(submitted[0], "request")

    def test_legacy_contradictory_flags_normalize_to_retry_wait(self):
        metadata = muse_pipeline._normalize_recovery_metadata(
            {
                "attempt": 2,
                "terminal_generation": True,
                "retryable_generation": True,
                "last_error_class": "MuseServiceInterrupted",
            }
        )

        self.assertEqual(metadata["recovery_state_version"], 2)
        self.assertEqual(metadata["recovery_state"], "retry_wait")
        self.assertTrue(metadata["retryable_generation"])
        self.assertNotIn("terminal_generation", metadata)

    def test_reconcile_budget_exhaustion_preserves_receipt_and_pauses(self):
        artifact = {
            "status": "submitting",
            "content_hash": "request",
            "path": "",
            "metadata": {
                "provider": muse_provider.MUSE_PROVIDER_NAME,
                "provider_task_id": "running-task",
                "endpoint": "/v1/images/tasks/running-task",
                "submitted_at": "2026-01-01T00:00:00Z",
                "submission_hash": "request",
                "submission_variant": "original",
                "attempt": 1,
                "reconcile_count": muse_pipeline.MAX_RECONCILE_ATTEMPTS - 1,
            },
        }

        def upsert(**kwargs):
            artifact.update(
                {
                    "status": kwargs["status"],
                    "content_hash": kwargs["content_hash"],
                    "metadata": kwargs["metadata"],
                }
            )
            return artifact

        adapter = AsyncMock()
        adapter.reconcile.return_value = None
        with (
            patch.object(
                muse_pipeline.db,
                "get_latest_video_artifact",
                return_value=artifact,
            ),
            patch.object(
                muse_pipeline.db,
                "upsert_video_artifact",
                side_effect=upsert,
            ),
            self.assertRaises(muse_pipeline.MuseReconcileExhausted),
        ):
            asyncio.run(
                muse_pipeline._generate_request(
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
                    policy_metadata={"prompt_strategy": "original"},
                )
            )

        self.assertEqual(
            artifact["metadata"]["reconcile_count"],
            muse_pipeline.MAX_RECONCILE_ATTEMPTS,
        )
        self.assertEqual(artifact["metadata"]["provider_task_id"], "running-task")
        self.assertEqual(artifact["metadata"]["recovery_state"], "reconciling")

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
    def test_smart_extension_policy_boundaries(self):
        cases = [
            ((10.0, 10.0, False), "trim"),
            ((9.7, 10.0, False), "hold_last_frame"),
            ((9.2, 10.0, False), "slowdown"),
            ((8.5, 10.0, False), "slowdown_then_hold"),
            ((7.0, 10.0, True), "degraded_to_image"),
        ]
        for (source, target, has_fallback), expected in cases:
            with self.subTest(expected=expected):
                result = video_production._plan_video_extension(
                    source_duration=source,
                    target_duration=target,
                    has_fallback_image=has_fallback,
                )
                self.assertEqual(result["strategy"], expected)

        with self.assertRaisesRegex(video_production.VideoProductionError, "ảnh fallback"):
            video_production._plan_video_extension(
                source_duration=7.0,
                target_duration=10.0,
                has_fallback_image=False,
            )

    def test_video_segments_never_stream_loop_and_keep_exact_target(self):
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
                patch.object(
                    video_production,
                    "probe_scene_video",
                    side_effect=[
                        {"duration_seconds": 9.7},
                        {"duration_seconds": 10.0},
                    ],
                ),
                patch.object(video_production.subprocess, "Popen", side_effect=fake_popen),
                patch.object(video_production.process_registry, "register_process"),
                patch.object(video_production.process_registry, "unregister_process"),
            ):
                results, records = video_production._render_segments(
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
                self.assertNotIn("-stream_loop", command)
            self.assertEqual(commands[0][commands[0].index("-t") + 1], "10.000")
            self.assertEqual(commands[1][commands[1].index("-t") + 1], "3.500")
            self.assertEqual(
                [record["strategy"] for record in records],
                ["hold_last_frame", "trim"],
            )
            self.assertIn(
                "tpad=stop_mode=clone",
                commands[0][commands[0].index("-filter_complex") + 1],
            )

    def test_corrupt_video_uses_matching_scene_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video = root / "scene.mp4"
            image = root / "scene.png"
            captions = root / "captions.srt"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            captions.write_text(
                "1\n00:00:00,000 --> 00:00:10,000\nTest caption\n",
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
                patch.object(
                    video_production,
                    "probe_scene_video",
                    side_effect=video_production.VideoProductionError("corrupt clip"),
                ),
                patch.object(video_production.subprocess, "Popen", side_effect=fake_popen),
                patch.object(video_production.process_registry, "register_process"),
                patch.object(video_production.process_registry, "unregister_process"),
            ):
                _, records = video_production._render_segments(
                    ffmpeg_exe="ffmpeg",
                    encoder="libx264",
                    video_id=1,
                    scenes=[{"index": 0, "start": 0.0, "end": 10.0, "duration": 10.0}],
                    media_paths=[video],
                    fallback_image_paths=[image],
                    srt_path=captions,
                    input_hash="b" * 64,
                    progress=lambda *_args, **_kwargs: None,
                    cancel_check=lambda: None,
                    crop_watermark=False,
                )

            self.assertNotIn("-stream_loop", commands[0])
            self.assertEqual(commands[0][commands[0].index("-i") + 1], str(image))
            self.assertEqual(commands[0][commands[0].index("-loop") + 1], "1")
            self.assertEqual(records[0]["strategy"], "degraded_to_image")
            self.assertTrue(records[0]["render_degraded_to_image"])
            self.assertEqual(records[0]["reason_code"], "video_duration_invalid")

    def test_real_ffmpeg_extension_keeps_duration_without_rewinding(self):
        from PIL import Image, ImageStat

        ffmpeg = video_production.imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            captions = root / "captions.srt"
            captions.write_text(
                "1\n00:00:00,000 --> 00:00:04,800\nTest caption\n",
                encoding="utf-8",
            )
            generated = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x90:r=10:d=4",
                    "-vf",
                    "geq=lum='40+40*T':cb=128:cr=128",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)

            with (
                patch.object(video_production, "SEGMENTS_DIR", root / "segments"),
                patch.object(video_production, "TARGET_WIDTH", 320),
                patch.object(video_production, "TARGET_HEIGHT", 180),
                patch.object(video_production, "TARGET_FPS", 10),
                patch.object(video_production.process_registry, "register_process"),
                patch.object(video_production.process_registry, "unregister_process"),
            ):
                segments, records = video_production._render_segments(
                    ffmpeg_exe=ffmpeg,
                    encoder="libx264",
                    video_id=1,
                    scenes=[{"index": 0, "start": 0.0, "end": 4.8, "duration": 4.8}],
                    media_paths=[source],
                    srt_path=captions,
                    input_hash="c" * 64,
                    progress=lambda *_args, **_kwargs: None,
                    cancel_check=lambda: None,
                    crop_watermark=False,
                )

            self.assertEqual(records[0]["strategy"], "slowdown_then_hold")
            details = video_production.probe_scene_video(segments[0], ffmpeg)
            self.assertLessEqual(abs(details["duration_seconds"] - 4.8), 0.1)

            samples = []
            for label, timestamp in (("early", "0.5"), ("late", "4.0")):
                frame = root / f"{label}.png"
                extracted = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-y",
                        "-ss",
                        timestamp,
                        "-i",
                        str(segments[0]),
                        "-frames:v",
                        "1",
                        str(frame),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(extracted.returncode, 0, extracted.stderr)
                with Image.open(frame) as image:
                    samples.append(ImageStat.Stat(image.convert("L")).mean[0])
            self.assertGreater(samples[1], samples[0] + 30)


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

    @unittest.skipUnless(muse_studio_service.os.name == "nt", "Windows-only recovery")
    def test_recover_verified_idle_orphaned_muse_runtime_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_dir = root / "chrome_user_data" / "muse_profiles" / "generate"
            nexus_python = str(Path(muse_studio_service.sys.executable).resolve())
            root_process = {
                "process_id": 4100,
                "parent_process_id": 1,
                "parent_running": True,
                "name": "python.exe",
                "executable_path": nexus_python,
                "command_line": (
                    f'"{nexus_python}" -m uvicorn app:app --host 127.0.0.1 --port 18610'
                ),
            }
            api_process = {
                "process_id": 4200,
                "parent_process_id": 4100,
                "parent_running": True,
                "name": "python.exe",
                "executable_path": nexus_python,
                "command_line": (
                    f'"{nexus_python}" -m uvicorn app:app --host 127.0.0.1 --port 18610'
                ),
            }
            browser_process = {
                "process_id": 4300,
                "parent_process_id": 4200,
                "parent_running": True,
                "name": "chrome.exe",
                "executable_path": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "command_line": (
                    "chrome.exe --headless=new --remote-debugging-port=19210 "
                    f'--user-data-dir="{profile_dir}" about:blank'
                ),
            }
            runtime = {"runtime_id": "runtime-safe", "active_media_tasks": 0}
            completed = MagicMock(returncode=0)
            with (
                patch.object(
                    muse_studio_service,
                    "MUSE_SERVICE_STATE_PATH",
                    root / "missing-service-state.json",
                ),
                patch.object(muse_studio_service, "_process", None),
                patch.object(muse_studio_service, "_api_key", ""),
                patch.object(muse_studio_service, "_has_active_muse_jobs", return_value=True),
                patch.object(
                    muse_studio_service,
                    "_port_in_use",
                    side_effect=[True, True, False, False],
                ),
                patch.object(
                    muse_studio_service,
                    "_windows_port_process",
                    side_effect=[api_process, browser_process, api_process, browser_process],
                ),
                patch.object(muse_studio_service, "_windows_process", return_value=root_process),
                patch.object(muse_studio_service, "_cdp_is_muse_browser", return_value=True),
                patch.object(
                    muse_studio_service,
                    "_probe_idle_unowned_muse_runtime",
                    side_effect=[runtime, runtime],
                ),
                patch.object(
                    muse_studio_service.subprocess,
                    "run",
                    return_value=completed,
                ) as run,
            ):
                recovered = muse_studio_service._recover_orphaned_muse_runtime(root)

        self.assertTrue(recovered)
        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/F", "/T", "/PID", "4100"],
        )

    def test_idle_orphan_probe_refuses_active_provider_task(self):
        with patch.object(
            muse_studio_service,
            "_request_json",
            side_effect=[
                {"status": "ok"},
                {
                    "status": "ready",
                    "runtime_id": "runtime-busy",
                    "active_media_tasks": 1,
                    "accounts": 3,
                },
            ],
        ):
            runtime = muse_studio_service._probe_idle_unowned_muse_runtime()

        self.assertIsNone(runtime)

    def test_processing_muse_job_blocks_idle_cleanup(self):
        with patch.object(
            muse_studio_service.db,
            "list_system_jobs",
            return_value=[{"status": "processing"}],
        ) as list_jobs:
            self.assertTrue(muse_studio_service._has_active_muse_jobs())
        list_jobs.assert_called_once_with(limit=None, job_type="muse_media_generation")

    def test_future_retry_wait_muse_job_blocks_idle_cleanup(self):
        with patch.object(
            muse_studio_service.db,
            "list_system_jobs",
            return_value=[
                {
                    "status": "retry_wait",
                    "next_retry_at": "2999-01-01T00:00:00Z",
                }
            ],
        ):
            self.assertTrue(muse_studio_service._has_active_muse_jobs())

    def test_muse_job_database_failure_blocks_idle_cleanup(self):
        with patch.object(
            muse_studio_service.db,
            "list_system_jobs",
            side_effect=RuntimeError("database unavailable"),
        ):
            self.assertTrue(muse_studio_service._has_active_muse_jobs())

    def test_active_provider_task_blocks_idle_shutdown(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        with (
            patch.object(muse_studio_service, "_has_active_muse_jobs", return_value=False),
            patch.object(
                muse_studio_service,
                "_request_json",
                return_value={"runtime_id": "runtime-1", "active_media_tasks": 1},
            ),
            patch.object(muse_studio_service, "schedule_idle_close") as schedule,
            patch.object(muse_studio_service, "shutdown_owned_service") as shutdown,
        ):
            muse_studio_service._idle_close_callback()

        schedule.assert_called_once_with()
        shutdown.assert_not_called()

    def test_ready_probe_failure_blocks_idle_shutdown(self):
        process = MagicMock()
        process.poll.return_value = None
        muse_studio_service._process = process
        with patch.object(
            muse_studio_service,
            "_request_json",
            side_effect=TimeoutError("readyz timeout"),
        ):
            self.assertTrue(muse_studio_service._has_active_provider_tasks())

    def test_foreign_port_is_never_reused_or_killed(self):
        with (
            patch.object(muse_studio_service, "_validate_studio_path", return_value=Path("C:/Muse")),
            patch.object(muse_studio_service, "_port_in_use", return_value=True),
            patch.object(
                muse_studio_service,
                "_recover_orphaned_muse_runtime",
                return_value=False,
            ),
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
            patch.object(
                muse_studio_service,
                "task_recovery_release_status",
                return_value={"enabled": True, "successful_runs": 3},
            ),
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
        self.assertEqual(environment["MUSE2API_TASK_RECOVERY_ENABLED"], "1")
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

    def test_task_recovery_release_requires_three_e2e_runs_and_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            release_path = Path(temp_dir) / "muse-task-recovery-release.json"
            with patch.object(
                muse_studio_service,
                "MUSE_TASK_RECOVERY_RELEASE_PATH",
                release_path,
            ):
                self.assertFalse(
                    muse_studio_service.task_recovery_release_status()["enabled"]
                )
                with self.assertRaisesRegex(ValueError, "3 lượt"):
                    muse_studio_service.record_task_recovery_e2e_verification(
                        successful_runs=2,
                        evidence_sha256="a" * 64,
                    )
                with self.assertRaisesRegex(ValueError, "digest"):
                    muse_studio_service.record_task_recovery_e2e_verification(
                        successful_runs=3,
                        evidence_sha256="invalid",
                    )
                status = muse_studio_service.record_task_recovery_e2e_verification(
                    successful_runs=3,
                    evidence_sha256="b" * 64,
                )

            self.assertTrue(status["enabled"])
            self.assertEqual(status["contract_version"], 1)
            self.assertEqual(status["successful_runs"], 3)
            serialized = release_path.read_text(encoding="utf-8").casefold()
            for forbidden in ("api_key", "cookie", "token", "password", "prompt", "path"):
                self.assertNotIn(forbidden, serialized)

    def test_start_refuses_unreleased_task_recovery_without_spawning(self):
        with (
            patch.object(
                muse_studio_service,
                "_validate_studio_path",
                return_value=Path("C:/Muse"),
            ),
            patch.object(muse_studio_service, "_muse_ports_available", return_value=True),
            patch.object(
                muse_studio_service,
                "task_recovery_release_status",
                return_value={"enabled": False, "successful_runs": 0},
            ),
            patch.object(muse_studio_service.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioCapabilityDisabled,
                "E2E start/end",
            ):
                muse_studio_service.ensure_started()

        popen.assert_not_called()

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

    def test_probe_rejects_muse_without_start_end_capability(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {"status": "ready", "accounts": 1, "browser_running": True},
            {
                "object": "list",
                "data": [{"id": "muse-image"}, {"id": "muse-video"}],
            },
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioConfigurationError,
                "start/end",
            ):
                muse_studio_service._probe_owned_service()

    def test_probe_rejects_muse_without_task_recovery_capability(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {
                "status": "ready",
                "accounts": 1,
                "browser_running": True,
                "active_media_tasks": 0,
                "runtime_id": "runtime-1",
            },
            {
                "object": "list",
                "data": [{"id": "muse-image"}, {"id": "muse-video"}],
                "capabilities": {"video_start_end_frames": 1},
            },
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            with self.assertRaisesRegex(
                muse_studio_service.MuseStudioCapabilityDisabled,
                "task recovery",
            ):
                muse_studio_service._probe_owned_service()

    def test_probe_accepts_and_sanitizes_recovery_capabilities(self):
        muse_studio_service._api_key = "in-memory-only"
        responses = [
            {"status": "ok"},
            {
                "status": "ready",
                "accounts": 1,
                "browser_running": True,
                "active_media_tasks": 2,
                "runtime_id": "runtime-1",
            },
            {
                "object": "list",
                "data": [{"id": "muse-image"}, {"id": "muse-video"}],
                "capabilities": {
                    "video_start_end_frames": 1,
                    "task_recovery": 1,
                },
            },
        ]
        with patch.object(muse_studio_service, "_request_json", side_effect=responses):
            status = muse_studio_service._probe_owned_service()

        self.assertEqual(status["video_start_end_frames"], 1)
        self.assertEqual(status["task_recovery"], 1)
        self.assertEqual(status["active_media_tasks"], 2)
        self.assertEqual(status["runtime_id"], "runtime-1")
        serialized = json.dumps(status).casefold()
        for secret_field in ("api_key", "cookie", "token", "password"):
            self.assertNotIn(secret_field, serialized)

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


class TestMuseScheduledQueue(unittest.TestCase):
    def test_future_retry_wait_does_not_block_queued_muse_job(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "nexus-test.db"
            with patch.object(database, "DB_PATH", db_path):
                with closing(sqlite3.connect(db_path)) as conn:
                    conn.execute(
                        "CREATE TABLE videos (id INTEGER PRIMARY KEY, video_status TEXT)"
                    )
                    conn.execute(
                        """
                        CREATE TABLE system_jobs (
                            id TEXT PRIMARY KEY, job_type TEXT, status TEXT,
                            title TEXT, progress TEXT, payload_json TEXT,
                            result_json TEXT, error TEXT, video_id INTEGER,
                            prompt_version TEXT, voice_id TEXT, voice_name TEXT,
                            tts_provider_id TEXT, voice_revision INTEGER,
                            voice_snapshot_json TEXT, attempt INTEGER DEFAULT 0,
                            recovery_count INTEGER DEFAULT 0,
                            resume_from_step TEXT DEFAULT '', next_retry_at TEXT DEFAULT '',
                            cancel_requested INTEGER DEFAULT 0, created_at TEXT,
                            updated_at TEXT, started_at TEXT DEFAULT '', finished_at TEXT DEFAULT ''
                        )
                        """
                    )
                database.create_system_job(
                    "muse-future",
                    "muse_media_generation",
                    "future",
                    {},
                )
                database.schedule_system_job_recovery(
                    "muse-future",
                    "muse_media_generation",
                    3600,
                    "",
                )
                database.create_system_job(
                    "muse-queued",
                    "muse_media_generation",
                    "queued",
                    {},
                )

                self.assertTrue(
                    database.has_claimable_system_jobs("muse_media_generation")
                )
                self.assertEqual(
                    database.get_next_system_job_retry_delay("muse_media_generation"),
                    0.0,
                )
                claimed = database.claim_next_system_job("muse_media_generation")

                self.assertEqual(claimed["id"], "muse-queued")
                remaining_delay = database.get_next_system_job_retry_delay(
                    "muse_media_generation"
                )
                self.assertGreater(remaining_delay, 3500)


class TestMuseRouting(unittest.TestCase):
    def test_startup_pauses_muse_jobs_without_launching_provider(self):
        legacy_history = [
            {
                "at": f"2026-10-24T00:00:{index:02d}Z",
                "error_class": "MuseServiceBusy",
                "message": "busy",
            }
            for index in range(20)
        ]
        job = {
            "id": "muse-restart",
            "job_type": "muse_media_generation",
            "status": "retry_wait",
            "video_id": 348,
            "result": {
                "muse_error_history": legacy_history,
                "muse_recovery": {"recovery_epoch": 1, "wait_reason": "manual_resume"},
            },
        }
        updated = {**job, "status": "paused"}
        with (
            patch.object(main.db, "list_system_jobs", return_value=[job]) as list_jobs,
            patch.object(main.db, "update_system_job", return_value=updated) as update_job,
            patch.object(main.db, "update_video_production_state") as update_video,
            patch.object(main.muse_studio_service, "ensure_started") as ensure_started,
        ):
            paused = main._pause_muse_jobs_for_manual_resume_after_restart()

        self.assertEqual(paused, 1)
        list_jobs.assert_called_once_with(
            limit=None,
            job_type="muse_media_generation",
            statuses={"queued", "retry_wait"},
        )
        updates = update_job.call_args.kwargs
        self.assertEqual(updates["status"], "paused")
        self.assertEqual(updates["next_retry_at"], "")
        self.assertEqual(updates["error"], "")
        result = updates["result_json"]
        self.assertEqual(result["attention_required"], "muse_resume_after_restart")
        self.assertEqual(result["muse_recovery"]["wait_reason"], "restart_resume_required")
        self.assertEqual(len(result["muse_error_history"]), 1)
        self.assertEqual(result["muse_error_history"][0]["repeat_count"], 20)
        update_video.assert_called_once()
        ensure_started.assert_not_called()

    def test_legacy_muse_retry_notification_requires_manual_resume(self):
        job = {
            "id": "muse-stale",
            "job_type": "muse_media_generation",
            "status": "retry_wait",
            "video_id": 348,
            "progress": "Chờ tự phục hồi từ bước muse_media_generation",
            "resume_from_step": "muse_media_generation",
            "next_retry_at": "2026-10-24T16:35:27+00:00",
            "result": {"muse_wait_reason": "service_busy"},
        }
        with patch.object(
            main._muse_media_coordinator,
            "status",
            return_value={"running": False},
        ):
            item = main._system_job_center_item(job, None)

        self.assertEqual(item["status"], "retry_wait")
        self.assertEqual(item["display_status"], "paused")
        self.assertEqual(item["notification"]["state"], "resume_required")
        self.assertFalse(item["notification"]["retry_active"])
        self.assertEqual(item["notification"]["retry_at"], "")
        self.assertTrue(item["can_resume"])
        self.assertTrue(item["muse_can_resume"])
        self.assertNotIn("muse_media_generation", item["progress"])

    def test_flow_retry_notification_stays_automatic(self):
        job = {
            "id": "flow-wait",
            "job_type": "flow_media_generation",
            "status": "retry_wait",
            "video_id": 349,
            "progress": "Chờ tự phục hồi từ bước flow_image_reconcile",
            "resume_from_step": "flow_image_reconcile",
            "next_retry_at": "2026-10-25T00:00:00+00:00",
            "result": {},
        }
        with patch.object(
            main._flow_media_coordinator,
            "status",
            return_value={"running": True},
        ):
            item = main._system_job_center_item(job, None)

        self.assertEqual(item["display_status"], "retry_wait")
        self.assertEqual(item["notification"]["state"], "auto_recovery")
        self.assertTrue(item["notification"]["retry_active"])
        self.assertIn("Google Flow tạo ảnh", item["progress"])
        self.assertNotIn("flow_image_reconcile", item["progress"])

    def test_render_status_treats_stale_muse_retry_as_paused(self):
        job = {
            "id": "muse-stale",
            "job_type": "muse_media_generation",
            "status": "retry_wait",
            "video_id": 348,
            "progress": "Chờ tự phục hồi từ bước muse_media_generation",
            "resume_from_step": "muse_media_generation",
            "next_retry_at": "2026-10-24T16:35:27+00:00",
            "payload": {"snapshot": {"image_generation_settings": {"render_mode": "muse_ai"}}},
            "result": {"muse_wait_reason": "service_busy"},
        }
        with (
            patch.object(main.db, "get_video", return_value={"id": 348, "prompt_version": "v1"}),
            patch.object(main.db, "get_latest_video_artifact", return_value=None),
            patch.object(main.db, "list_system_jobs", return_value=[job]),
            patch.object(main.video_production, "get_render_mode", return_value="muse_ai"),
            patch.object(main._muse_media_coordinator, "status", return_value={"running": False}),
        ):
            response = main.get_render_status(348)

        self.assertFalse(response["is_active"])
        self.assertTrue(response["is_paused"])
        self.assertEqual(response["state"], "paused")
        self.assertEqual(response["job"]["display_status"], "paused")
        self.assertEqual(response["job"]["notification"]["state"], "resume_required")

    def test_recreate_is_consumed_once_before_muse_pipeline_runs(self):
        job = {
            "id": "muse-recreate",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {
                "video_id": 42,
                "snapshot": {"image_generation_settings": {}},
                "force_new_project": True,
                "mode": "recreate",
            },
            "result": {},
        }
        with (
            patch.object(main.db, "get_latest_video_artifact", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
            patch.object(main.muse_pipeline, "purge_muse_artifacts") as purge,
            patch.object(
                main.muse_pipeline,
                "prepare_muse_media",
                return_value={"manifest": "ready"},
            ) as prepare,
            patch.object(main, "_raise_if_video_job_canceled"),
            patch.object(
                main,
                "_enqueue_video_render_job",
                return_value=({"id": "render-job"}, True),
            ),
        ):
            main._execute_muse_media_generation_job(job)

        purge.assert_called_once_with(42)
        payload_updates = [
            call.kwargs["payload_json"]
            for call in update_job.call_args_list
            if "payload_json" in call.kwargs
        ]
        self.assertEqual(len(payload_updates), 1)
        self.assertFalse(payload_updates[0]["force_new_project"])
        self.assertFalse(prepare.call_args.kwargs["force_new_project"])

    def test_partial_reset_is_consumed_before_final_artifact_shortcut(self):
        job = {
            "id": "muse-partial-reset",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {
                "video_id": 42,
                "snapshot": {"image_generation_settings": {}},
                "partial_reset_from": 3,
            },
            "result": {"recovery_epoch": 1},
        }
        with (
            patch.object(main.muse_pipeline, "purge_muse_artifacts_from_index", return_value=5) as purge,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "get_latest_video_artifact", return_value=None),
            patch.object(main.muse_pipeline, "prepare_muse_media", return_value={}) as prepare,
            patch.object(main, "_raise_if_video_job_canceled"),
            patch.object(
                main,
                "_enqueue_video_render_job",
                return_value=({"id": "render-job"}, True),
            ),
            patch.object(main.db, "update_video_production_state"),
        ):
            main._execute_muse_media_generation_job(job)

        purge.assert_called_once_with(42, 3)
        payload_updates = [
            call.kwargs["payload_json"]
            for call in update_job.call_args_list
            if "payload_json" in call.kwargs
        ]
        self.assertNotIn("partial_reset_from", payload_updates[0])
        prepare.assert_called_once()

    def test_job_center_retry_and_resume_share_muse_recovery_helper(self):
        job = {
            "id": "muse-resume",
            "job_type": "muse_media_generation",
            "status": "paused",
            "video_id": 42,
            "payload": {},
            "result": {
                "recovery_epoch": 2,
                "muse_session_attempt": 3,
                "muse_service_busy_attempt": 3,
                "attention_required": "muse_service_unstable",
                "muse_error_history": [{"message": "redacted"}],
            },
        }
        resumed = {**job, "status": "queued"}
        for action in ("retry", "resume"):
            with self.subTest(action=action):
                with (
                    patch.object(main.db, "get_system_job", return_value=job),
                    patch.object(main, "_system_job_center_item", return_value={"can_resume": True}),
                    patch.object(main, "_job_supports_action", return_value=True),
                    patch.object(main.db, "list_active_system_jobs", return_value=[]),
                    patch.object(main.db, "update_system_job", return_value=resumed) as update_job,
                    patch.object(main.db, "update_video_production_state"),
                    patch.object(main, "_kick_muse_media_queue"),
                ):
                    main._run_system_job_center_action(
                        job["id"],
                        action,
                        kick_queues=False,
                    )

                result = update_job.call_args.kwargs["result_json"]
                self.assertEqual(result["recovery_epoch"], 3)
                self.assertEqual(result["muse_error_history"], [{"message": "redacted"}])
                self.assertNotIn("muse_session_attempt", result)
                self.assertNotIn("muse_service_busy_attempt", result)
                self.assertNotIn("attention_required", result)

    def test_muse_routes_to_independent_media_job(self):
        expected = ({"id": "muse-job", "job_type": "muse_media_generation"}, True)
        with (
            patch.object(main.db, "list_system_jobs", return_value=[]),
            patch.object(main, "_enqueue_muse_media_job", return_value=expected) as enqueue,
        ):
            job, created, phase = main._route_video_render_job(
                video_id=42,
                snapshot={"image_generation_settings": {"render_mode": "muse_ai"}},
            )
        enqueue.assert_called_once()
        self.assertEqual(job["job_type"], "muse_media_generation")
        self.assertTrue(created)
        self.assertEqual(phase, "media_generation")

    def test_muse_resume_routes_to_existing_recoverable_job(self):
        existing = {
            "id": "muse-existing",
            "job_type": "muse_media_generation",
            "status": "error",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        resumed = {**existing, "status": "queued"}
        with (
            patch.object(main.db, "list_system_jobs", return_value=[existing]),
            patch.object(main, "_resume_muse_media_job", return_value=resumed) as resume,
            patch.object(main, "_enqueue_muse_media_job") as enqueue,
        ):
            job, created, phase = main._route_video_render_job(
                video_id=42,
                snapshot={"image_generation_settings": {"render_mode": "muse_ai"}},
                mode="resume",
            )

        self.assertEqual(job["id"], "muse-existing")
        self.assertFalse(created)
        self.assertEqual(phase, "media_generation")
        resume.assert_called_once()
        enqueue.assert_not_called()

    def test_running_muse_partial_reset_waits_for_safe_stop_without_purge(self):
        running = {
            "id": "muse-running",
            "job_type": "muse_media_generation",
            "status": "running",
            "video_id": 42,
            "payload": {
                "snapshot": {
                    "image_generation_settings": {"render_mode": "muse_ai"}
                }
            },
        }
        with (
            patch.object(main.db, "get_video", return_value={"id": 42, "prompt_version": "v1"}),
            patch.object(main.db, "list_system_jobs", side_effect=[[running], [running]]),
            patch.object(main, "cancel_render_video") as cancel,
            patch.object(main.muse_pipeline, "purge_muse_artifacts_from_index") as purge,
        ):
            response = main.reset_scenes_from(42, from_index=2)

        self.assertEqual(response.status_code, 202)
        self.assertIn(b"waiting_for_safe_stop", response.body)
        cancel.assert_called_once_with(42)
        purge.assert_not_called()

    def test_render_status_returns_only_safe_muse_job_fields(self):
        job = {
            "id": "muse-job",
            "job_type": "muse_media_generation",
            "status": "paused",
            "title": "Muse job",
            "progress": "checkpoint",
            "error": "",
            "payload": {
                "snapshot": {
                    "image_generation_settings": {"render_mode": "muse_ai"}
                },
                "secret_prompt": "must-not-leak",
            },
            "result": {
                "attention_required": "muse_service_unstable",
                "muse_recovery": {
                    "recovery_epoch": 2,
                    "submission_attempt": 1,
                    "reconcile_count": 3,
                    "service_busy_attempt": 2,
                    "wait_reason": "reconcile_retry_exhausted",
                    "provider_payload": "must-not-leak",
                },
            },
        }
        with (
            patch.object(main.db, "get_video", return_value={"id": 42, "prompt_version": "v1"}),
            patch.object(main.db, "get_latest_video_artifact", return_value=None),
            patch.object(main.db, "list_system_jobs", return_value=[job]),
        ):
            response = main.get_render_status(42)

        serialized = json.dumps(response)
        self.assertNotIn("secret_prompt", serialized)
        self.assertNotIn("must-not-leak", serialized)
        self.assertNotIn("payload", response["job"])
        self.assertEqual(response["job"]["muse_recovery"]["recovery_epoch"], 2)
        self.assertEqual(response["job"]["muse_recovery"]["service_busy_attempt"], 2)

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

    def test_third_session_transient_failure_pauses_for_resume(self):
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
            any(call_item.kwargs.get("status") == "paused" for call_item in update_job.call_args_list)
        )
        self.assertTrue(
            any(
                call_item.kwargs.get("result_json", {}).get("attention_required")
                == "muse_service_unstable"
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
        self.assertEqual(
            schedule.call_args.kwargs["result_json"]["muse_recovery"]["service_busy_attempt"],
            1,
        )

    def test_second_service_busy_wait_uses_bounded_backoff(self):
        job = {
            "id": "muse-profile-busy-second",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {"muse_service_busy_attempt": 1},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job"),
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(job, muse_provider.MuseServiceBusy("busy"))

        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 60.0)
        result = schedule.call_args.kwargs["result_json"]
        self.assertEqual(result["muse_service_busy_attempt"], 2)
        self.assertEqual(result["muse_recovery"]["service_busy_attempt"], 2)
        self.assertEqual(schedule.call_args.kwargs["error"], "")

    def test_third_service_busy_failure_pauses_with_port_conflict(self):
        job = {
            "id": "muse-profile-busy-final",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {"muse_service_busy_attempt": 2},
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state"),
        ):
            main._handle_production_job_error(job, muse_provider.MuseServiceBusy("busy"))

        schedule.assert_not_called()
        pause_call = next(
            call_item
            for call_item in update_job.call_args_list
            if call_item.kwargs.get("status") == "paused"
        )
        result = pause_call.kwargs["result_json"]
        self.assertEqual(result["attention_required"], "muse_service_port_conflict")
        self.assertEqual(result["muse_recovery"]["wait_reason"], "service_port_conflict")
        self.assertEqual(result["muse_recovery"]["service_busy_attempt"], 3)
        self.assertEqual(pause_call.kwargs["error"], "")

    def test_muse_error_history_is_deduplicated_and_capped(self):
        result = {}
        for _ in range(15):
            result["muse_error_history"] = main._append_muse_error_history(
                result,
                muse_provider.MuseServiceBusy("busy"),
                "busy",
            )
        self.assertEqual(len(result["muse_error_history"]), 1)
        self.assertEqual(result["muse_error_history"][0]["repeat_count"], 15)

        result = {
            "muse_error_history": [
                {
                    "at": f"2026-10-24T00:00:{index:02d}Z",
                    "error_class": f"Error{index}",
                    "message": f"failure-{index}",
                }
                for index in range(12)
            ]
        }
        history = main._append_muse_error_history(
            result,
            muse_provider.MuseServiceBusy("latest"),
            "latest",
        )
        self.assertEqual(len(history), main.MUSE_ERROR_HISTORY_LIMIT)
        self.assertEqual(history[-1]["message"], "latest")

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

    def test_chatgpt_planner_transient_wait_does_not_consume_muse_attempt(self):
        job = {
            "id": "muse-planner-transient",
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
                muse_pipeline.MusePlannerTransient(
                    "temporary", delay_seconds=120.0, consume_attempt=False
                ),
            )
        result = schedule.call_args.kwargs["result_json"]
        self.assertEqual(schedule.call_args.kwargs["delay_seconds"], 120.0)
        self.assertEqual(result["muse_wait_reason"], "chatgpt_planner_transient")
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

    def test_unreleased_task_recovery_pauses_without_consuming_submission(self):
        job = {
            "id": "muse-capability-disabled",
            "job_type": "muse_media_generation",
            "video_id": 348,
            "payload": {"force_new_project": False},
            "result": {
                "muse_recovery": {
                    "recovery_epoch": 2,
                    "submission_attempt": 0,
                    "reconcile_count": 0,
                }
            },
        }
        error = muse_studio_service.MuseStudioCapabilityDisabled(
            "task_recovery",
            "release gate disabled",
        )
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state") as update_video,
        ):
            main._handle_production_job_error(job, error)

        schedule.assert_not_called()
        update = update_job.call_args.kwargs
        self.assertEqual(update["status"], "paused")
        self.assertEqual(update["error"], "")
        self.assertEqual(update["finished_at"], "")
        result = update["result_json"]
        self.assertEqual(result["attention_required"], "muse_capability_disabled")
        self.assertEqual(result["muse_wait_reason"], "capability_disabled")
        self.assertEqual(result["muse_recovery"]["submission_attempt"], 0)
        self.assertEqual(result["muse_recovery"]["wait_reason"], "capability_disabled")
        self.assertIn("checkpoint", update["progress"])
        self.assertEqual(
            update_video.call_args.kwargs["current_stage"],
            "muse_capability_disabled",
        )

    def test_confirmed_muse_quota_exhaustion_pauses_for_resume(self):
        job = {
            "id": "muse-quota-exhausted",
            "job_type": "muse_media_generation",
            "video_id": 42,
            "payload": {},
            "result": {},
        }
        error = muse_provider.MuseQuotaUnavailable("quota exceeded")
        error.capacity_preflight = {
            "status": "exhausted",
            "eligible": 0,
            "unknown": 0,
            "exhausted": 3,
            "cache_hit": True,
            "checked_at": 1,
            "elapsed_seconds": 0.2,
        }
        with (
            patch.object(main.db, "get_youtube_publish_workflow_by_job", return_value=None),
            patch.object(main.db, "get_system_job", return_value=job),
            patch.object(main.db, "schedule_system_job_recovery") as schedule,
            patch.object(main.db, "update_system_job") as update_job,
            patch.object(main.db, "update_video_production_state") as update_video,
        ):
            main._handle_production_job_error(job, error)

        schedule.assert_not_called()
        self.assertEqual(update_job.call_args.kwargs["status"], "paused")
        self.assertEqual(
            update_job.call_args.kwargs["result_json"]["attention_required"],
            "muse_quota_exhausted",
        )
        self.assertEqual(
            update_job.call_args.kwargs["result_json"]["muse_quota_preflight"]["exhausted"],
            3,
        )
        self.assertIn("Resume", update_job.call_args.kwargs["progress"])
        self.assertEqual(
            update_video.call_args.kwargs["current_stage"],
            "muse_quota_exhausted",
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
            "video_start_end_frames": 1,
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

    def test_muse_accounts_response_exposes_sanitized_quota_states(self):
        now = int(time.time())
        accounts = [
            {
                "id": "weekly-exhausted-extra-available",
                "label": "Account 1",
                "cookies": {"secret_cookie": "do-not-return"},
                "cookies_exp": {"hatch_vml": now + 3600},
                "quota": {
                    "found": True,
                    "plan": "Free plan",
                    "weekly_used_pct": 100,
                    "weekly_reset": "Nov 2",
                    "extra_left": "2.7B tokens left",
                    "extra_left_value": 2_700_000_000,
                    "checked_at": now,
                    "raw": "authorization=secret",
                },
            },
            {
                "id": "fully-exhausted",
                "label": "Account 2",
                "cookies": {"secret_cookie": "do-not-return"},
                "cookies_exp": {"hatch_vml": now + 3600},
                "quota": {
                    "found": True,
                    "weekly_used_pct": 100,
                    "extra_pool_present": True,
                    "extra_left_value": 0,
                    "checked_at": now,
                },
            },
            {
                "id": "stale",
                "label": "Account 3",
                "cookies": {"secret_cookie": "do-not-return"},
                "quota": {
                    "found": True,
                    "weekly_used_pct": 10,
                    "checked_at": now - main.MUSE_QUOTA_CACHE_TTL_SECONDS - 1,
                },
            },
        ]
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                main.muse_settings,
                "load_muse_settings",
                return_value={"muse_studio_path": temp_dir},
            ),
            patch.object(
                main.muse_studio_service,
                "_studio_data_dir",
                return_value=Path(temp_dir),
            ),
        ):
            (Path(temp_dir) / "accounts.json").write_text(
                json.dumps(accounts),
                encoding="utf-8",
            )
            response = main.get_muse_accounts_endpoint()

        payload = json.loads(response.body)
        self.assertEqual(
            [item["media_quota_status"] for item in payload["items"]],
            ["eligible", "exhausted", "unknown"],
        )
        self.assertEqual(
            payload["items"][0]["quota"]["weekly_remaining_pct"],
            0,
        )
        self.assertEqual(
            payload["items"][0]["quota"]["extra_left_value"],
            2_700_000_000,
        )
        serialized = response.body.decode("utf-8").lower()
        self.assertNotIn("do-not-return", serialized)
        self.assertNotIn("authorization=secret", serialized)
        self.assertNotIn('"raw"', serialized)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_refresh_muse_account_quota_uses_lease_and_sanitizes_response(self):
        now = int(time.time())
        with (
            patch.object(
                main.muse_studio_service,
                "status",
                return_value={"active_media_tasks": 0},
            ),
            patch.object(main.muse_studio_service, "acquire") as acquire,
            patch.object(main.muse_studio_service, "release") as release,
            patch.object(
                main.muse_provider,
                "refresh_account_quota",
                return_value={
                    "found": True,
                    "weekly_used_pct": 100,
                    "weekly_reset": "Nov 2",
                    "extra_pool_present": True,
                    "extra_left_value": 0,
                    "checked_at": now,
                    "raw": "cookie=secret",
                },
            ) as refresh,
        ):
            response = main.refresh_muse_account_quota_endpoint("account-1")

        payload = json.loads(response.body)
        acquire.assert_called_once_with()
        refresh.assert_called_once_with("account-1")
        release.assert_called_once_with()
        self.assertEqual(payload["quota"]["status"], "exhausted")
        self.assertNotIn("raw", payload["quota"])

    def test_refresh_muse_account_quota_rejects_active_media_task(self):
        with (
            patch.object(
                main.muse_studio_service,
                "status",
                return_value={"active_media_tasks": 1},
            ),
            patch.object(main.muse_studio_service, "acquire") as acquire,
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.refresh_muse_account_quota_endpoint("account-1")

        self.assertEqual(raised.exception.status_code, 409)
        acquire.assert_not_called()

    def test_refresh_muse_account_quota_rejects_invalid_id_before_starting_service(self):
        with (
            patch.object(main.muse_studio_service, "status") as status,
            patch.object(main.muse_studio_service, "acquire") as acquire,
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.refresh_muse_account_quota_endpoint("../invalid")

        self.assertEqual(raised.exception.status_code, 400)
        status.assert_not_called()
        acquire.assert_not_called()

    def test_refresh_muse_account_quota_rejects_claimable_muse_job(self):
        with (
            patch.object(
                main._muse_media_coordinator,
                "status",
                return_value={"current_job_id": ""},
            ),
            patch.object(main.db, "has_claimable_system_jobs", return_value=True),
            patch.object(main.muse_studio_service, "status") as status,
            patch.object(main.muse_studio_service, "acquire") as acquire,
        ):
            with self.assertRaises(main.HTTPException) as raised:
                main.refresh_muse_account_quota_endpoint("account-1")

        self.assertEqual(raised.exception.status_code, 409)
        status.assert_not_called()
        acquire.assert_not_called()

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
