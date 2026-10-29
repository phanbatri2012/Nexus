import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from nexus_engine.services import database as db
from nexus_engine.services import muse_provider
from nexus_engine.services import short_video_api
from nexus_engine.services import short_video_pipeline
from nexus_engine.services import short_video_renderer
from nexus_engine.services.google_flow_worker import FlowVideoPending, GoogleFlowWorker
from nexus_engine.services.short_video_api import CharacterEraRequest, CreateShortVideoRequest
from nexus_engine.services.short_video_character_profiles import (
    ShortVideoCharacterProfileRegistry,
)
from nexus_engine.services.short_video_registry import ShortVideoProjectRegistry


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "short_video_projects" / "bac-ho-ngam-tho.v1.json"
CHARACTER_MANIFEST_PATH = (
    ROOT / "config" / "short_video_character_profiles" / "bac-ho.v1.json"
)


def manifest_data():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def voice_record():
    return {
        "id": "66cb0b63-125b-4615-8600-e918c56a9c7d",
        "name": "bac_ho_doc_tho_1",
        "provider_id": "omnivoice",
        "provider_voice_id": "voice-profile",
        "revision": 1,
        "status": "active",
        "config": {},
    }


def profile_snapshot():
    data = manifest_data()
    data["resolved_voice_snapshot"] = {
        "voice_id": data["voice_profile"]["voice_id"],
        "voice_name": data["voice_profile"]["voice_name"],
        "provider_id": "omnivoice",
        "provider_voice_id": "voice-profile",
        "voice_revision": 1,
        "config": {"target_words_per_minute": 145},
    }
    return data


def active_character_profile(config_dir: Path, assets_dir: Path) -> dict:
    data = json.loads(CHARACTER_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["status"] = "active"
    root = assets_dir / data["key"] / f"v{data['profile_version']}"
    for era_index, era in enumerate(data["eras"]):
        references = []
        for ordinal, role in enumerate(("primary_portrait", "half_body")):
            asset_id = f"{era['key']}_{role}"
            relative = f"{era['key']}/{role}.png"
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.new(
                "RGB",
                (512, 768),
                (40 + era_index * 20, 40 + ordinal * 30, 40),
            ).save(destination)
            references.append(
                {
                    "id": asset_id,
                    "file": relative,
                    "role": role,
                    "pose": "front three-quarter" if ordinal == 0 else "standing half body",
                    "primary": ordinal == 0,
                    "source_label": "Test fixture",
                    "source_year": str(era["year_from"]),
                    "rights_note": "Generated fixture for automated tests only",
                }
            )
        era["references"] = references
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "bac-ho.v1.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return data


def empty_draft_character_profile_data() -> dict:
    data = json.loads(CHARACTER_MANIFEST_PATH.read_text(encoding="utf-8"))
    data["status"] = "draft"
    for era in data["eras"]:
        era["references"] = []
    return data


def snapshot_character_profile(root: Path) -> dict:
    config_dir = root / "character-config"
    assets_dir = root / "character-assets"
    active_character_profile(config_dir, assets_dir)
    registry = ShortVideoCharacterProfileRegistry(config_dir, assets_dir)
    return registry.snapshot_for_item("bac-ho", 1, root / "item")


class ShortVideoRegistryTests(unittest.TestCase):
    def _registry(self, *documents):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        directory = Path(temp.name)
        for index, document in enumerate(documents):
            (directory / f"project-{index}.json").write_text(
                json.dumps(document, ensure_ascii=False), encoding="utf-8"
            )
        return temp, ShortVideoProjectRegistry(directory)

    def test_valid_manifest_resolves_voice_dependency(self):
        temp, registry = self._registry(manifest_data())
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                project = registry.get("bac-ho-ngam-tho")
            self.assertTrue(project["available"])
            self.assertEqual(project["pipeline_profile"]["id"], "poem_to_short_v1")
            self.assertEqual(project["visual_profile"]["aspect_ratio"], "9:16")
        finally:
            temp.cleanup()

    def test_create_snapshot_can_select_google_flow(self):
        temp, registry = self._registry(manifest_data())
        try:
            with (
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.get_voice",
                    return_value=voice_record(),
                ),
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.build_voice_snapshot",
                    return_value={
                        "voice_id": voice_record()["id"],
                        "voice_name": voice_record()["name"],
                        "provider_id": "omnivoice",
                        "config": {},
                    },
                ),
            ):
                snapshot = registry.snapshot_for_create(
                    "bac-ho-ngam-tho",
                    visual_provider="google_flow",
                )
            self.assertEqual(snapshot["visual_profile"]["provider"], "google_flow")
            self.assertIn("muse", snapshot["visual_profile"]["available_providers"])
        finally:
            temp.cleanup()

    def test_create_snapshot_can_select_another_active_omnivoice_voice(self):
        temp, registry = self._registry(manifest_data())
        selected = {
            **voice_record(),
            "id": "11111111-2222-4333-8444-555555555555",
            "name": "bac_ho_doc_tho_2",
            "provider_voice_id": "voice-profile-2",
        }
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                side_effect=lambda voice_id, **_kwargs: (
                    selected if voice_id == selected["id"] else voice_record()
                ),
            ):
                snapshot = registry.snapshot_for_create(
                    "bac-ho-ngam-tho",
                    voice_id=selected["id"],
                )
            self.assertEqual(snapshot["voice_profile"]["voice_id"], selected["id"])
            self.assertEqual(snapshot["voice_profile"]["voice_name"], selected["name"])
            self.assertEqual(snapshot["resolved_voice_snapshot"]["voice_id"], selected["id"])
            self.assertEqual(snapshot["resolved_voice_snapshot"]["provider_id"], "omnivoice")
        finally:
            temp.cleanup()

    def test_create_snapshot_rejects_selectable_voice_from_another_provider(self):
        temp, registry = self._registry(manifest_data())
        selected = {
            **voice_record(),
            "id": "11111111-2222-4333-8444-555555555555",
            "name": "not-omnivoice",
            "provider_id": "genmax",
        }
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                side_effect=lambda voice_id, **_kwargs: (
                    selected if voice_id == selected["id"] else voice_record()
                ),
            ):
                with self.assertRaisesRegex(ValueError, "OmniVoice"):
                    registry.snapshot_for_create(
                        "bac-ho-ngam-tho",
                        voice_id=selected["id"],
                    )
        finally:
            temp.cleanup()

    def test_create_snapshot_rejects_voice_without_single_narrator_permission(self):
        temp, registry = self._registry(manifest_data())
        selected = {
            **voice_record(),
            "id": "11111111-2222-4333-8444-555555555555",
            "name": "guest-only-voice",
            "casting_profile": {"usage_modes": ["multi_guest"]},
        }
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                side_effect=lambda voice_id, **_kwargs: (
                    selected if voice_id == selected["id"] else voice_record()
                ),
            ):
                with self.assertRaisesRegex(ValueError, "một người đọc"):
                    registry.snapshot_for_create(
                        "bac-ho-ngam-tho",
                        voice_id=selected["id"],
                    )
        finally:
            temp.cleanup()

    def test_default_visual_provider_must_be_available(self):
        document = manifest_data()
        document["visual_profile"]["available_providers"] = ["google_flow"]
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("available_providers", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_duplicate_key_marks_every_copy_unavailable(self):
        first = manifest_data()
        second = deepcopy(first)
        second["profile_version"] = "2.0.0"
        temp, registry = self._registry(first, second)
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                projects = registry.list_projects()
            self.assertEqual(len(projects), 2)
            self.assertTrue(all(not project["available"] for project in projects))
            self.assertTrue(all("Trùng project key" in " ".join(project["errors"]) for project in projects))
        finally:
            temp.cleanup()

    def test_secret_field_is_rejected_without_crashing_registry(self):
        document = manifest_data()
        document["visual_profile"]["api_key"] = "must-not-be-stored"
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("không được chứa trường bí mật", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_secret_suffix_is_rejected(self):
        document = manifest_data()
        document["visual_profile"]["provider_access_token"] = "must-not-be-stored"
        temp, registry = self._registry(document)
        try:
            project = registry.list_projects()[0]
            self.assertFalse(project["available"])
            self.assertIn("provider_access_token", " ".join(project["errors"]))
        finally:
            temp.cleanup()

    def test_dependency_exception_marks_project_unavailable(self):
        temp, registry = self._registry(manifest_data())
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                side_effect=RuntimeError("dependency offline"),
            ):
                project = registry.get("bac-ho-ngam-tho")
            self.assertIsNotNone(project)
            self.assertFalse(project["available"])
            self.assertIn("dependency", project["dependencies"][1]["detail"].casefold())
        finally:
            temp.cleanup()

    def test_archived_projects_are_hidden_from_menu_query(self):
        document = manifest_data()
        document["status"] = "archived"
        temp, registry = self._registry(document)
        try:
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                self.assertEqual(registry.list_projects(include_archived=False), [])
                self.assertEqual(len(registry.list_projects(include_archived=True)), 1)
        finally:
            temp.cleanup()


class ShortVideoCharacterProfileTests(unittest.TestCase):
    def test_draft_manifest_is_visible_but_unavailable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "bac-ho.v1.json").write_text(
                json.dumps(empty_draft_character_profile_data(), ensure_ascii=False),
                encoding="utf-8",
            )
            registry = ShortVideoCharacterProfileRegistry(config_dir, root / "assets")
            profile = registry.get("bac-ho", 1)
            self.assertIsNotNone(profile)
            self.assertFalse(profile["available"])
            self.assertEqual(profile["status"], "draft")
            self.assertEqual(
                [era["key"] for era in profile["eras"]],
                [
                    "nguyen_ai_quoc_1919_1923",
                    "international_1924_1940",
                    "pac_bo_1941_1942",
                    "prison_1942_1943",
                    "viet_bac_1944_1954",
                    "president_1954_1969",
                ],
            )
            self.assertIn("12 đến 18 ảnh", " ".join(profile["errors"]))

    def test_active_profile_snapshots_all_assets_by_hash(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            assets_dir = root / "assets"
            data = active_character_profile(config_dir, assets_dir)
            registry = ShortVideoCharacterProfileRegistry(config_dir, assets_dir)
            profile = registry.get("bac-ho", 1)
            self.assertTrue(profile["available"])
            self.assertTrue(all(era["available"] for era in profile["eras"]))

            item_dir = root / "item"
            snapshot = registry.snapshot_for_item("bac-ho", 1, item_dir)
            references = [
                reference
                for era in snapshot["eras"]
                for reference in era["references"]
            ]
            self.assertEqual(len(references), 12)
            self.assertTrue(all((item_dir / ref["snapshot_path"]).is_file() for ref in references))
            self.assertNotIn(str(assets_dir), json.dumps(snapshot, ensure_ascii=False))

            first_source = (
                assets_dir
                / data["key"]
                / f"v{data['profile_version']}"
                / data["eras"][0]["references"][0]["file"]
            )
            first_snapshot = item_dir / references[0]["snapshot_path"]
            original_bytes = first_snapshot.read_bytes()
            Image.new("RGB", (512, 768), (240, 240, 240)).save(first_source)
            self.assertEqual(first_snapshot.read_bytes(), original_bytes)

    def test_draft_profile_assets_can_be_uploaded_previewed_and_moved_to_trash(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            assets_dir = root / "assets"
            config_dir.mkdir()
            (config_dir / "bac-ho.v1.json").write_text(
                json.dumps(empty_draft_character_profile_data(), ensure_ascii=False),
                encoding="utf-8",
            )
            image_bytes = io.BytesIO()
            Image.new("RGB", (512, 768), (80, 80, 80)).save(image_bytes, format="PNG")
            registry = ShortVideoCharacterProfileRegistry(config_dir, assets_dir)
            era_key = "nguyen_ai_quoc_1919_1923"
            profile = registry.add_reference_asset(
                "bac-ho",
                1,
                era_key=era_key,
                original_filename="portrait.png",
                content=image_bytes.getvalue(),
                role="primary_portrait",
                pose="front three-quarter",
                source_label="Test archive",
                source_year="1920",
                rights_note="Automated test fixture",
            )
            era = next(item for item in profile["eras"] if item["key"] == era_key)
            self.assertEqual(era["reference_count"], 1)
            self.assertTrue(era["assets"][0]["available"])
            asset_id = era["assets"][0]["id"]
            source = registry.asset_path("bac-ho", 1, asset_id)
            self.assertIsNotNone(source)
            self.assertTrue(source.is_file())

            profile = registry.add_reference_asset(
                "bac-ho",
                1,
                era_key=era_key,
                original_filename="half-body.png",
                content=image_bytes.getvalue(),
                role="half_body",
                pose="standing half body",
                source_label="Test archive",
                source_year="1920",
                rights_note="Automated test fixture",
            )
            era = next(item for item in profile["eras"] if item["key"] == era_key)
            self.assertTrue(era["available"])
            self.assertFalse(profile["available"])

            profile = registry.remove_reference_asset("bac-ho", 1, asset_id)
            era = next(item for item in profile["eras"] if item["key"] == era_key)
            self.assertEqual(era["reference_count"], 1)
            self.assertFalse(source.exists())
            trash = assets_dir / "bac-ho" / "v1" / ".trash"
            self.assertEqual(len(list(trash.glob(f"{asset_id}-*.png"))), 1)

    def test_secret_and_traversal_are_rejected_without_crashing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            assets_dir = root / "assets"
            data = active_character_profile(config_dir, assets_dir)
            data["provider_token"] = "do-not-store"
            (config_dir / "bac-ho.v1.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            registry = ShortVideoCharacterProfileRegistry(config_dir, assets_dir)
            profile = registry.list_profiles()[0]
            self.assertFalse(profile["available"])
            self.assertIn("provider_token", " ".join(profile["errors"]))

            data.pop("provider_token")
            data["eras"][0]["references"][0]["file"] = "../escape.png"
            (config_dir / "bac-ho.v1.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            profile = registry.list_profiles()[0]
            self.assertFalse(profile["available"])
            self.assertIn("đường dẫn tương đối", " ".join(profile["errors"]))

    def test_project_dependency_uses_active_character_profile(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            character_config = root / "character-config"
            character_assets = root / "character-assets"
            active_character_profile(character_config, character_assets)
            character_registry = ShortVideoCharacterProfileRegistry(
                character_config, character_assets
            )
            project_config = root / "project-config"
            project_config.mkdir()
            project = manifest_data()
            project["character_profile"] = {
                "key": "bac-ho",
                "profile_version": 1,
                "required": True,
            }
            (project_config / "project.json").write_text(
                json.dumps(project, ensure_ascii=False), encoding="utf-8"
            )
            registry = ShortVideoProjectRegistry(project_config, character_registry)
            with patch(
                "nexus_engine.services.short_video_registry.voice_config.get_voice",
                return_value=voice_record(),
            ):
                entry = registry.get("bac-ho-ngam-tho")
            self.assertTrue(entry["available"])
            self.assertTrue(entry["character_profile_summary"]["available"])

    def test_optional_draft_character_profile_does_not_block_existing_pipeline(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            character_config = root / "character-config"
            character_assets = root / "character-assets"
            character_config.mkdir()
            (character_config / "bac-ho.v1.json").write_text(
                CHARACTER_MANIFEST_PATH.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            character_registry = ShortVideoCharacterProfileRegistry(
                character_config, character_assets
            )
            project_config = root / "project-config"
            project_config.mkdir()
            (project_config / "project.json").write_text(
                json.dumps(manifest_data(), ensure_ascii=False), encoding="utf-8"
            )
            registry = ShortVideoProjectRegistry(project_config, character_registry)
            with (
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.get_voice",
                    return_value=voice_record(),
                ),
                patch(
                    "nexus_engine.services.short_video_registry.voice_config.build_voice_snapshot",
                    return_value={
                        "voice_id": voice_record()["id"],
                        "voice_name": voice_record()["name"],
                        "provider_id": "omnivoice",
                        "config": {},
                    },
                ),
            ):
                snapshot = registry.snapshot_for_create("bac-ho-ngam-tho")
                snapshot = registry.snapshot_character_profile(snapshot, root / "item")
            self.assertNotIn("character_profile", snapshot)
            self.assertNotIn("resolved_character_profile", snapshot)


class ShortVideoStoreAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.db_patch = patch.object(db, "DB_PATH", self.root / "short-video.db")
        self.artifact_patch = patch.object(short_video_pipeline, "SHORT_VIDEO_DIR", self.root / "artifacts")
        self.db_patch.start()
        self.artifact_patch.start()
        db.init_db()

    def tearDown(self):
        self.artifact_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def test_character_era_schema_accepts_manifest_keys_and_rejects_unsafe_values(self):
        self.assertEqual(
            CharacterEraRequest(era_key="nguyen_ai_quoc_1919_1923").era_key,
            "nguyen_ai_quoc_1919_1923",
        )
        self.assertEqual(
            CharacterEraRequest(era_key="international_1924_1940").era_key,
            "international_1924_1940",
        )
        with self.assertRaises(ValueError):
            CharacterEraRequest(era_key="../president_1954_1969")
        with self.assertRaises(ValueError):
            CharacterEraRequest(era_key="president-1954-1969")

    def test_double_click_is_idempotent_and_kicks_once(self):
        snapshot = profile_snapshot()
        request = CreateShortVideoRequest(
            title="Nguyên tiêu",
            poem="Rằm xuân lồng lộng trăng soi",
            client_request_id="request-123456",
            voice_id=snapshot["resolved_voice_snapshot"]["voice_id"],
        )
        with (
            patch.object(
                short_video_api.registry,
                "snapshot_for_create",
                return_value=snapshot,
            ) as snapshot_for_create,
            patch.object(short_video_api.short_video_worker, "kick") as kick,
        ):
            first = short_video_api.create_short_video_item("bac-ho-ngam-tho", request)
            second = short_video_api.create_short_video_item("bac-ho-ngam-tho", request)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["item"]["id"], second["item"]["id"])
        self.assertEqual(kick.call_count, 1)
        snapshot_for_create.assert_called_once_with(
            "bac-ho-ngam-tho",
            visual_provider="",
            voice_id=snapshot["resolved_voice_snapshot"]["voice_id"],
        )
        jobs = db.list_system_jobs(limit=None, job_type="short_video_generation")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["payload"]["action"], "generate")
        stored = db.get_short_video_item(first["item"]["id"])
        self.assertEqual(stored["options"]["visual_provider"], "muse")
        self.assertEqual(
            stored["options"]["voice_id"],
            snapshot["resolved_voice_snapshot"]["voice_id"],
        )
        self.assertEqual(stored["options"]["character_era"], "auto")

    def test_public_item_does_not_expose_filesystem_path_or_receipts(self):
        snapshot = profile_snapshot()
        snapshot["resolved_character_profile"] = {
            "key": "bac-ho",
            "profile_version": 1,
            "eras": [
                {
                    "key": "viet_bac_1944_1954",
                    "references": [
                        {
                            "id": "private-reference",
                            "snapshot_path": "references/private.png",
                            "sha256": "a" * 64,
                        }
                    ],
                }
            ],
        }
        item, _job, _created = db.create_short_video_item_with_job(
            item_id="11111111-1111-4111-8111-111111111111",
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="Tiếng suối trong như tiếng hát xa",
            manifest=snapshot,
            job_id="short-video-public",
            payload={"short_video_id": "11111111-1111-4111-8111-111111111111"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )
        public = short_video_api._public_item(item, detail=True)
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("provider_task_id", serialized)
        self.assertNotIn("snapshot_path", serialized)
        self.assertNotIn("a" * 64, serialized)
        self.assertIn("/api/short-video/items/", public["artifact_urls"]["draft"])

    def test_character_era_override_updates_options_and_queues_visuals_only(self):
        snapshot = profile_snapshot()
        snapshot["resolved_character_profile"] = {
            "key": "bac-ho",
            "profile_version": 1,
            "eras": [
                {"key": "viet_bac_1944_1954", "label": "Việt Bắc", "references": []},
                {"key": "president_1954_1969", "label": "Chủ tịch nước", "references": []},
            ],
        }
        item_id = "44444444-4444-4444-8444-444444444444"
        item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="Tiếng suối trong như tiếng hát xa",
            manifest=snapshot,
            job_id="short-video-era-original",
            payload={"short_video_id": item_id, "action": "generate"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
            options={"visual_provider": "muse", "character_era": "auto"},
        )
        db.update_system_job(job["id"], status="completed", finished_at=db.utc_now())
        db.update_short_video_item(item["id"], status="awaiting_review")
        with patch.object(short_video_api.short_video_worker, "kick"):
            response = short_video_api.update_short_video_character_era(
                item_id,
                CharacterEraRequest(era_key="president_1954_1969"),
            )
        stored = db.get_short_video_item(item_id)
        queued = db.get_system_job(response["job"]["id"])
        self.assertEqual(stored["options"]["character_era"], "president_1954_1969")
        self.assertNotIn("resolved_character_era", stored["options"])
        self.assertEqual(queued["payload"]["action"], "regenerate_visuals")

    def test_restart_pauses_short_job_without_starting_worker(self):
        snapshot = profile_snapshot()
        item_id = "33333333-3333-4333-8333-333333333333"
        _item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Rằm tháng giêng",
            source_text="Rằm xuân lồng lộng trăng soi",
            manifest=snapshot,
            job_id="short-video-restart-paused",
            payload={"short_video_id": item_id, "action": "generate"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )
        self.assertEqual(job["status"], "queued")
        worker = short_video_api.short_video_worker
        with patch.object(worker, "_COORDINATOR", None):
            self.assertEqual(worker.pause_after_restart(), 1)
            self.assertFalse(worker.status()["running"])
        paused = db.get_system_job("short-video-restart-paused")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            db.get_short_video_item(item_id)["current_stage"],
            "short_video_resume_after_restart",
        )


class ShortVideoPipelineUnitTests(unittest.TestCase):
    def test_character_scene_quota_stays_between_sixty_and_eighty_percent(self):
        self.assertEqual(
            [short_video_pipeline.character_scene_count(count) for count in range(1, 5)],
            [1, 1, 2, 3],
        )
        for count in range(5, 25):
            visible = short_video_pipeline.character_scene_count(count)
            self.assertGreaterEqual(visible, int((count * 0.6) + 0.999999))
            self.assertLessEqual(visible, int(count * 0.8))

    def test_storyboard_character_policy_marks_identity_and_environment_scenes(self):
        scenes = [
            {
                "index": index,
                "prompt": f"base prompt {index}",
                "subject": "Bác Hồ làm việc" if index == 2 else "rừng Việt Bắc",
                "action": "quiet scene",
                "setting": "Việt Bắc",
                "transcript": "cảnh khuya",
            }
            for index in range(10)
        ]
        profile = {
            "resolved_character_profile": {
                "key": "bac-ho",
                "profile_version": 1,
                "identity_prompt": "stable historical identity",
                "style_prompt": "neutral archival black and white documentary photography",
                "appearance_policy": {
                    "min_scene_ratio": 0.6,
                    "target_scene_ratio": 0.7,
                    "max_scene_ratio": 0.8,
                    "allowed_shots": ["medium", "half_body", "full_body"],
                },
                "eras": [
                    {
                        "key": "viet_bac_1944_1954",
                        "label": "Việt Bắc",
                        "keywords": ["việt bắc", "cảnh khuya"],
                        "appearance_prompt": "historically accurate resistance-era appearance",
                        "context_prompt": "northern resistance forest base",
                    }
                ],
            }
        }
        plan = short_video_pipeline._apply_character_profile_to_plan(
            {"scenes": scenes},
            profile,
            {"character_resolution": {"era_key": "viet_bac_1944_1954", "source": "auto", "confidence": 0.9}},
        )
        visible = [scene for scene in plan["scenes"] if scene["character_presence"]]
        environment = [scene for scene in plan["scenes"] if not scene["character_presence"]]
        self.assertEqual(len(visible), 7)
        self.assertTrue(all(scene["reference_role"] for scene in visible))
        self.assertTrue(all("preserve the identity" in scene["prompt"] for scene in visible))
        self.assertTrue(all("Do not show Ho Chi Minh" in scene["prompt"] for scene in environment))
        self.assertTrue(all("black-and-white" in scene["prompt"] for scene in plan["scenes"]))

    def test_reference_selection_is_provider_specific_and_era_locked(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            item_dir = Path(temp_dir)
            era_dir = item_dir / "references" / "viet_bac_1944_1954"
            era_dir.mkdir(parents=True)
            primary = era_dir / "primary.png"
            half_body = era_dir / "half.png"
            primary.write_bytes(b"primary")
            half_body.write_bytes(b"half")
            profile = {
                "resolved_character_profile": {
                    "eras": [
                        {
                            "key": "viet_bac_1944_1954",
                            "references": [
                                {
                                    "id": "primary",
                                    "role": "primary_portrait",
                                    "primary": True,
                                    "sha256": short_video_pipeline._file_sha256(primary),
                                    "snapshot_path": primary.relative_to(item_dir).as_posix(),
                                },
                                {
                                    "id": "half",
                                    "role": "half_body",
                                    "primary": False,
                                    "sha256": short_video_pipeline._file_sha256(half_body),
                                    "snapshot_path": half_body.relative_to(item_dir).as_posix(),
                                },
                            ],
                        }
                    ]
                }
            }
            checkpoint = {"character_resolution": {"era_key": "viet_bac_1944_1954"}}
            scene = {"character_presence": True, "reference_role": "half_body"}
            muse = short_video_pipeline._scene_character_references(
                scene=scene,
                checkpoint=checkpoint,
                profile=profile,
                item_dir=item_dir,
                provider="muse",
            )
            flow = short_video_pipeline._scene_character_references(
                scene=scene,
                checkpoint=checkpoint,
                profile=profile,
                item_dir=item_dir,
                provider="google_flow",
            )
            self.assertEqual([entry["id"] for entry in muse], ["primary", "half"])
            self.assertEqual([entry["id"] for entry in flow], ["half"])
            self.assertEqual(
                short_video_pipeline._scene_character_references(
                    scene={"character_presence": False},
                    checkpoint=checkpoint,
                    profile=profile,
                    item_dir=item_dir,
                    provider="muse",
                ),
                [],
            )

    def test_visual_regeneration_preserves_audio_and_timing_only(self):
        checkpoint = {
            "audio_revision": 1,
            "audio": {"normalized_path": "audio.wav"},
            "timing": {"path": "timing.json"},
            "character_resolution": {"era_key": "viet_bac_1944_1954"},
            "storyboard": {"path": "storyboard.json"},
            "scenes": [{"index": 0, "image": {"path": "0.png"}}],
            "artifacts": {"draft": {"path": "draft.mp4"}, "final": {"path": "final.mp4"}},
        }
        short_video_pipeline._invalidate_for_visual_regeneration(checkpoint)
        self.assertEqual(checkpoint["audio"]["normalized_path"], "audio.wav")
        self.assertEqual(checkpoint["timing"]["path"], "timing.json")
        self.assertEqual(checkpoint["storyboard"], {})
        self.assertEqual(checkpoint["scenes"], [])
        self.assertEqual(checkpoint["artifacts"], {})
        self.assertNotIn("character_resolution", checkpoint)
        self.assertEqual(checkpoint["scene_revisions"]["0"], 1)

    def test_low_confidence_era_stops_before_media_pipeline(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            item_dir = Path(temp_dir)
            ref_dir = item_dir / "references" / "viet_bac_1944_1954"
            ref_dir.mkdir(parents=True)
            references = []
            for asset_id, primary in (("primary", True), ("half", False)):
                path = ref_dir / f"{asset_id}.png"
                path.write_bytes(asset_id.encode("ascii"))
                references.append(
                    {
                        "id": asset_id,
                        "primary": primary,
                        "sha256": short_video_pipeline._file_sha256(path),
                        "snapshot_path": path.relative_to(item_dir).as_posix(),
                    }
                )
            profile = {
                "resolved_character_profile": {
                    "key": "bac-ho",
                    "profile_version": 1,
                    "snapshot_hash": "snapshot",
                    "eras": [
                        {
                            "key": "viet_bac_1944_1954",
                            "label": "Việt Bắc",
                            "references": references,
                        }
                    ],
                }
            }
            item = {
                "id": "11111111-1111-4111-8111-111111111111",
                "title": "Bài thơ chưa rõ",
                "source_text": "Nội dung không chỉ rõ thời kỳ",
                "options": {"character_era": "auto"},
            }
            with (
                patch.object(short_video_pipeline.chatgpt_runtime, "try_start_operation", return_value=True),
                patch.object(short_video_pipeline.chatgpt_runtime, "finish_operation"),
                patch.object(
                    short_video_pipeline,
                    "resolve_short_video_character_era",
                    return_value={
                        "era_key": "viet_bac_1944_1954",
                        "confidence": 0.55,
                        "reason": "Văn bản không có dấu hiệu thời kỳ đủ rõ ràng.",
                        "planner_source": "mock",
                    },
                ),
                patch.object(short_video_pipeline.db, "update_short_video_item"),
                patch.object(short_video_pipeline, "_save_checkpoint"),
            ):
                with self.assertRaises(short_video_pipeline.ShortVideoAttentionRequired) as raised:
                    short_video_pipeline._ensure_character_era(
                        checkpoint={},
                        item_dir=item_dir,
                        job={"id": "job-era"},
                        item=item,
                        profile=profile,
                    )
            self.assertEqual(raised.exception.reason, "character_era_low_confidence")

    def test_monochrome_scene_filter_is_explicit(self):
        color_filter = short_video_renderer._scene_filter(540, 960, 30, 5, 0)
        monochrome_filter = short_video_renderer._scene_filter(
            540, 960, 30, 5, 0, monochrome=True
        )
        self.assertNotIn("hue=s=0", color_filter)
        self.assertIn("hue=s=0", monochrome_filter)

    def test_scene_windows_are_vertical_pipeline_sized_and_bounded(self):
        windows = short_video_pipeline.build_scene_windows(
            poem="\n".join(f"Dòng thơ {index}" for index in range(60)),
            timings=[],
            duration=179.0,
            pipeline={"scene_max_seconds": 8, "max_scenes": 24},
        )
        self.assertLessEqual(len(windows), 24)
        self.assertTrue(all(float(scene["duration"]) <= 8.01 for scene in windows))
        self.assertLessEqual(sum(bool(scene["is_video"]) for scene in windows), 3)
        self.assertFalse(windows[-1]["is_video"])

    def test_scene_windows_honor_manifest_video_scene_limit(self):
        windows = short_video_pipeline.build_scene_windows(
            poem="Một\nHai\nBa\nBốn",
            timings=[],
            duration=28.0,
            pipeline={"scene_max_seconds": 8, "max_scenes": 24},
            max_video_scenes=0,
        )
        self.assertFalse(any(scene["is_video"] for scene in windows))

    def test_audio_regeneration_invalidates_all_dependent_stages(self):
        checkpoint = {
            "audio_revision": 2,
            "audio": {"normalized_path": "audio.wav"},
            "timing": {"path": "timing.json"},
            "storyboard": {"path": "storyboard.json"},
            "scenes": [{"index": 0}],
            "artifacts": {"draft": {"path": "draft.mp4"}, "final": {"path": "final.mp4"}},
        }
        short_video_pipeline._invalidate_for_audio_regeneration(checkpoint)
        self.assertEqual(checkpoint["audio_revision"], 3)
        self.assertEqual(checkpoint["audio"], {})
        self.assertEqual(checkpoint["timing"], {})
        self.assertEqual(checkpoint["storyboard"], {})
        self.assertEqual(checkpoint["scenes"], [])
        self.assertEqual(checkpoint["artifacts"], {})

    def test_scene_regeneration_preserves_audio_and_other_scenes(self):
        checkpoint = {
            "audio": {"normalized_path": "audio.wav"},
            "scene_revisions": {},
            "scenes": [
                {"index": 0, "image": {"path": "0.png"}, "video": {"path": "0.mp4"}},
                {"index": 1, "image": {"path": "1.png"}, "video": {}},
                {"index": 2, "image": {"path": "2.png"}, "video": {}},
            ],
            "artifacts": {"draft": {"path": "draft.mp4"}},
        }
        short_video_pipeline._invalidate_scene(checkpoint, 1)
        self.assertEqual(checkpoint["audio"]["normalized_path"], "audio.wav")
        self.assertEqual(checkpoint["scenes"][0]["image"]["path"], "0.png")
        self.assertEqual(checkpoint["scenes"][0]["video"], {})
        self.assertEqual(checkpoint["scenes"][1]["image"], {})
        self.assertEqual(checkpoint["scenes"][2]["image"]["path"], "2.png")

    def test_media_dispatches_to_google_flow_from_profile_snapshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            item_dir = Path(temp_dir)
            checkpoint = {"scenes": []}
            with (
                patch.object(
                    short_video_pipeline,
                    "_generate_flow_media_async",
                    new=AsyncMock(),
                ) as flow_generator,
                patch.object(
                    short_video_pipeline,
                    "_generate_muse_media_async",
                    new=AsyncMock(),
                ) as muse_generator,
            ):
                short_video_pipeline._ensure_media(
                    checkpoint=checkpoint,
                    item_dir=item_dir,
                    job={"id": "job-flow"},
                    item={"id": "11111111-1111-4111-8111-111111111111"},
                    profile={"visual_profile": {"provider": "google_flow"}},
                    plan={"scenes": []},
                )
            flow_generator.assert_awaited_once()
            muse_generator.assert_not_awaited()

    def test_flow_vertical_image_validation_accepts_portrait_output(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (900, 1600), (20, 30, 40)).save(image_buffer, format="PNG")
        response = SimpleNamespace(
            status=200,
            body=AsyncMock(return_value=image_buffer.getvalue()),
        )
        page = SimpleNamespace(
            url="https://flow.google.com/project/test",
            request=SimpleNamespace(get=AsyncMock(return_value=response)),
        )
        worker = GoogleFlowWorker(page)
        worker._expected_image_aspect_ratio = "9:16"
        self.assertTrue(
            asyncio.run(worker._validate_image_url("https://flow.google.com/asb/test"))
        )

    def test_flow_video_intent_submits_only_after_confirmed_missing(self):
        async def exercise(submit_if_missing):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                start = Path(temp_dir) / "start.png"
                end = Path(temp_dir) / "end.png"
                start.write_bytes(b"start")
                end.write_bytes(b"end")
                page = SimpleNamespace(url="https://flow.google.com/project/test")
                worker = GoogleFlowWorker(page)
                worker._ensure_project_canvas = AsyncMock()
                worker._find_video_for_prompt = AsyncMock(
                    return_value={"state": "missing", "source": ""}
                )
                worker._ensure_agent_session_ready = AsyncMock(
                    side_effect=RuntimeError("submission path reached")
                )
                with self.assertRaises((RuntimeError, FlowVideoPending)) as raised:
                    await worker.generate_scene_video(
                        prompt="cinematic motion",
                        avoid_prompt="text",
                        start_frame_path=start,
                        end_frame_path=end,
                        request_key="short-video-request",
                        reconcile_only=True,
                        submit_if_prompt_missing=submit_if_missing,
                    )
                return worker, raised.exception

        blocked_worker, blocked_error = asyncio.run(exercise(False))
        self.assertIsInstance(blocked_error, FlowVideoPending)
        blocked_worker._ensure_agent_session_ready.assert_not_awaited()

        resumed_worker, resumed_error = asyncio.run(exercise(True))
        self.assertIsInstance(resumed_error, RuntimeError)
        resumed_worker._ensure_agent_session_ready.assert_awaited_once()


class MuseVerticalAdapterTests(unittest.TestCase):
    def test_short_requests_send_real_vertical_parameters_and_defaults_stay_wide(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            start = root / "start.png"
            end = root / "end.png"
            start.write_bytes(b"image")
            end.write_bytes(b"image")
            provider = muse_provider.MuseStudioApiProvider()
            provider._submit_and_wait = AsyncMock(return_value={"path": str(root / "out.mp4")})
            asyncio.run(
                provider.create_video(
                    "vertical prompt",
                    start,
                    end,
                    root / "out.mp4",
                    aspect_ratio="9:16",
                    resolution="1080x1920",
                    duration_seconds=7,
                )
            )
            payload = provider._submit_and_wait.call_args.kwargs["payload"]
            self.assertEqual(payload["aspect_ratio"], "9:16")
            self.assertEqual(payload["resolution"], "1080x1920")
            self.assertEqual(payload["duration"], 7)

            provider._submit_and_wait.reset_mock()
            asyncio.run(provider.create_video("wide prompt", start, end, root / "wide.mp4"))
            default_payload = provider._submit_and_wait.call_args.kwargs["payload"]
            self.assertEqual(default_payload["aspect_ratio"], "16:9")
            self.assertEqual(default_payload["resolution"], "1920x1080")


class ShortVideoMockE2ETests(ShortVideoStoreAndApiTests):
    def test_mock_google_flow_character_pipeline_runs_to_review(self):
        snapshot = profile_snapshot()
        snapshot["visual_profile"]["provider"] = "google_flow"
        snapshot["visual_profile"]["max_video_scenes"] = 0
        item_id = "55555555-5555-4555-8555-555555555555"
        item_dir = self.root / "artifacts" / "bac-ho-ngam-tho" / item_id
        character_config = self.root / "flow-character-config"
        character_assets = self.root / "flow-character-assets"
        active_character_profile(character_config, character_assets)
        character_registry = ShortVideoCharacterProfileRegistry(
            character_config, character_assets
        )
        snapshot["resolved_character_profile"] = character_registry.snapshot_for_item(
            "bac-ho", 1, item_dir
        )
        _item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="tiếng suối trong như tiếng hát xa\ntrăng lồng cổ thụ bóng lồng hoa",
            manifest=snapshot,
            job_id="short-video-e2e-flow-character",
            payload={"short_video_id": item_id, "action": "generate"},
            voice_snapshot=snapshot["resolved_voice_snapshot"],
            options={
                "visual_provider": "google_flow",
                "character_era": "viet_bac_1944_1954",
            },
        )

        class FakeOmniVoice:
            def health(self, *, test_connection=False):
                return {"ok": True}

            def download_audio(self, _task_id, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"mock-wav")
                return destination

        class FakeFlow:
            def __init__(self):
                self.calls = []

            async def generate_scene(self, **kwargs):
                self.calls.append(
                    {
                        "reference_ids": list(kwargs.get("reference_ids") or []),
                        "reference_paths": dict(kwargs.get("reference_paths") or {}),
                        "aspect_ratio": (kwargs.get("image_settings") or {}).get("aspect_ratio"),
                    }
                )
                return f"https://flow.google.com/asb/{len(self.calls)}"

            async def download_image(self, _asset_url, destination):
                path = Path(destination)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (108, 192), (90, 90, 90)).save(path)

        fake_flow = FakeFlow()

        @contextlib.asynccontextmanager
        async def fake_flow_session(**_kwargs):
            yield fake_flow

        def fake_planner(**kwargs):
            return {
                "semantic_plan": {
                    "story_context": "Một đêm trăng tại chiến khu Việt Bắc.",
                    "scenes": [
                        {
                            "index": int(window["index"]),
                            "subject": "Bác Hồ và núi rừng Việt Bắc",
                            "action": "quiet contemplation under moonlight",
                            "setting": "Việt Bắc resistance base",
                            "era": "1944-1954",
                            "visual_anchor": f"moonlit stream {window['index']}",
                            "safe_visual_anchor": f"forest stream {window['index']}",
                            "must_show": ["forest", "moonlight"],
                            "composition": "vertical documentary composition",
                            "lighting": "natural moonlight",
                            "color": "neutral black and white",
                            "narrative_role": "continuation",
                        }
                        for window in kwargs["windows"]
                    ],
                },
                "planner_source": "chatgpt_playwright_mock",
                "chat_url": "",
            }

        def fake_normalize(_source, destination, *, job_id):
            del job_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"normalized-wav")
            return destination

        def fake_render(*, destination, width, height, fps, monochrome, **_kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"mock-mp4")
            return {
                "path": str(destination),
                "qc": {
                    "duration_seconds": 12,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "has_audio": True,
                    "has_subtitle_stream": False,
                    "text_overlay_enabled": False,
                    "monochrome_verified": monochrome,
                },
            }

        with (
            patch.object(short_video_pipeline.tts, "get_provider", return_value=FakeOmniVoice()),
            patch.object(
                short_video_pipeline.tts,
                "submit_tts_task",
                return_value={"id": "omnivoice:flow-task"},
            ),
            patch.object(
                short_video_pipeline.tts,
                "get_tts_task",
                return_value={"id": "omnivoice:flow-task", "status": "completed"},
            ),
            patch.object(short_video_pipeline.short_video_renderer, "normalize_audio", side_effect=fake_normalize),
            patch.object(short_video_pipeline.short_video_renderer, "probe_duration", return_value=12.0),
            patch.object(
                short_video_pipeline,
                "_transcribe_audio",
                return_value=[
                    {"start": 0.0, "end": 6.0, "text": "tiếng suối trong như tiếng hát xa"},
                    {"start": 6.0, "end": 12.0, "text": "trăng lồng cổ thụ bóng lồng hoa"},
                ],
            ),
            patch.object(short_video_pipeline.chatgpt_runtime, "try_start_operation", return_value=True),
            patch.object(short_video_pipeline.chatgpt_runtime, "finish_operation"),
            patch.object(short_video_pipeline, "generate_muse_scene_plan_only", side_effect=fake_planner),
            patch.object(short_video_pipeline, "_flow_worker_session", fake_flow_session),
            patch.object(short_video_pipeline.short_video_renderer, "render", side_effect=fake_render),
        ):
            short_video_pipeline.execute(job)

        stored = db.get_short_video_item(item_id)
        self.assertEqual(stored["status"], "awaiting_review")
        self.assertTrue(fake_flow.calls)
        self.assertTrue(all(call["aspect_ratio"] == "9:16" for call in fake_flow.calls))
        self.assertTrue(any(len(call["reference_ids"]) == 1 for call in fake_flow.calls))
        self.assertTrue(any(not call["reference_ids"] for call in fake_flow.calls))
        self.assertTrue(
            all(len(call["reference_ids"]) <= 1 for call in fake_flow.calls)
        )

    def test_mock_pipeline_runs_from_poem_to_review_and_final(self):
        snapshot = profile_snapshot()
        item_id = "22222222-2222-4222-8222-222222222222"
        item, job, _created = db.create_short_video_item_with_job(
            item_id=item_id,
            project_key="bac-ho-ngam-tho",
            profile_version="1.0.0",
            title="Cảnh khuya",
            source_text="tiếng suối trong như tiếng hát xa\ntrăng lồng cổ thụ bóng lồng hoa",
            manifest=snapshot,
            job_id="short-video-e2e-generate",
            payload={
                "short_video_id": item_id,
                "project_key": "bac-ho-ngam-tho",
                "action": "generate",
                "checkpoint": {"version": 1, "stage": "queued"},
            },
            voice_snapshot=snapshot["resolved_voice_snapshot"],
        )

        class FakeOmniVoice:
            def health(self, *, test_connection=False):
                return {"ok": True}

            def download_audio(self, _task_id, destination):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"mock-wav")
                return destination

        class FakeMuse:
            def __init__(self, **_kwargs):
                pass

            @contextlib.asynccontextmanager
            async def open_session(self):
                yield self

            async def reconcile(self, **_kwargs):
                return None

            async def create_image(self, _prompt, _references, destination, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id=f"image-{destination.stem}",
                        endpoint=f"/v1/images/tasks/{destination.stem}",
                        submitted_at="2026-10-29T00:00:00+00:00",
                    )
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (108, 192), (70, 90, 110)).save(destination)
                return {"path": str(destination)}

            async def create_video(self, _prompt, _start, _end, destination, **kwargs):
                kwargs["on_submitted"](
                    muse_provider.MuseReceipt(
                        provider_task_id=f"video-{destination.stem}",
                        endpoint=f"/v1/videos/{destination.stem}",
                        submitted_at="2026-10-29T00:00:00+00:00",
                    )
                )
                destination.write_bytes(b"mock-video")
                return {"path": str(destination)}

        def fake_planner(**kwargs):
            scenes = []
            for window in kwargs["windows"]:
                index = int(window["index"])
                scenes.append(
                    {
                        "index": index,
                        "subject": "khung cảnh thiên nhiên tĩnh lặng",
                        "action": f"ánh trăng lan nhẹ trên cảnh vật số {index}",
                        "setting": "rừng núi Việt Nam về đêm",
                        "era": "thời kháng chiến",
                        "visual_anchor": f"vầng trăng và dòng suối cảnh {index}",
                        "safe_visual_anchor": f"mặt nước phản chiếu ánh trăng cảnh {index}",
                        "must_show": ["vầng trăng", "dòng suối"],
                        "composition": "vertical layered landscape composition",
                        "lighting": "soft moonlight through old trees",
                        "color": "indigo ink and warm paper tones",
                        "narrative_role": "continuation",
                    }
                )
            return {
                "semantic_plan": {
                    "story_context": "Một đêm trăng yên tĩnh giữa núi rừng Việt Nam.",
                    "scenes": scenes,
                },
                "planner_source": "chatgpt_playwright_mock",
                "chat_url": "",
            }

        def fake_normalize(_source, destination, *, job_id):
            del job_id
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"normalized-wav")
            return destination

        def fake_render(*, destination, width, height, fps, **_kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"mock-mp4")
            return {
                "path": str(destination),
                "qc": {
                    "duration_seconds": 12,
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "has_audio": True,
                    "has_subtitle_stream": False,
                    "text_overlay_enabled": False,
                },
            }

        with (
            patch.object(short_video_pipeline.tts, "get_provider", return_value=FakeOmniVoice()),
            patch.object(
                short_video_pipeline.tts,
                "submit_tts_task",
                return_value={"id": "omnivoice:one-task"},
            ) as submit_tts,
            patch.object(
                short_video_pipeline.tts,
                "get_tts_task",
                return_value={"id": "omnivoice:one-task", "status": "completed"},
            ),
            patch.object(short_video_pipeline.tts, "retry_tts_task"),
            patch.object(short_video_pipeline.short_video_renderer, "normalize_audio", side_effect=fake_normalize),
            patch.object(short_video_pipeline.short_video_renderer, "probe_duration", return_value=12.0),
            patch.object(
                short_video_pipeline,
                "_transcribe_audio",
                return_value=[
                    {"start": 0.0, "end": 6.0, "text": "tiếng suối trong như tiếng hát xa"},
                    {"start": 6.0, "end": 12.0, "text": "trăng lồng cổ thụ bóng lồng hoa"},
                ],
            ),
            patch.object(short_video_pipeline.chatgpt_runtime, "try_start_operation", return_value=True),
            patch.object(short_video_pipeline.chatgpt_runtime, "finish_operation"),
            patch.object(short_video_pipeline, "generate_muse_scene_plan_only", side_effect=fake_planner),
            patch.object(short_video_pipeline, "MuseStudioApiProvider", FakeMuse),
            patch.object(
                short_video_pipeline.short_video_renderer,
                "probe",
                return_value={
                    "duration_seconds": 7,
                    "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
                },
            ),
            patch.object(short_video_pipeline.short_video_renderer, "render", side_effect=fake_render),
        ):
            short_video_pipeline.execute(job)
            submit_tts.assert_called_once()
            submitted_text = submit_tts.call_args.args[0]
            self.assertIn("Cảnh khuya", submitted_text)
            self.assertIn("tiếng suối", submitted_text)

            draft_item = db.get_short_video_item(item_id)
            self.assertEqual(draft_item["status"], "awaiting_review")
            self.assertEqual(db.get_system_job(job["id"])["status"], "completed")

            final_item, final_job = db.create_short_video_job(
                item_id=item_id,
                job_id="short-video-e2e-final",
                title="final: Cảnh khuya",
                payload={
                    "short_video_id": item_id,
                    "project_key": "bac-ho-ngam-tho",
                    "action": "final_render",
                    "checkpoint": {"version": 1, "stage": "final_render"},
                },
                voice_snapshot=snapshot["resolved_voice_snapshot"],
                stage="final_render",
            )
            self.assertEqual(final_item["status"], "queued")
            short_video_pipeline.execute(final_job)

        completed = db.get_short_video_item(item_id)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["completed_at"])
        final_state = db.get_system_job("short-video-e2e-final")
        self.assertEqual(final_state["status"], "completed")


if __name__ == "__main__":
    unittest.main()
