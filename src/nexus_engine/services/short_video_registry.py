"""Versioned configuration registry for temporary Short Video projects."""

from __future__ import annotations

import json
import logging
import re
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from nexus_engine.paths import SHORT_VIDEO_PROJECTS_CONFIG_DIR
from nexus_engine.services import vieneu_tts, voice_config
from nexus_engine.services.short_video_character_profiles import (
    ShortVideoCharacterProfileRegistry,
    character_profile_registry,
)


logger = logging.getLogger(__name__)

PROJECT_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_PIPELINES = frozenset({"poem_to_short_v1"})
SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "refresh_token",
        "secret",
        "token",
        "access_token",
    }
)


class PipelineProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    max_duration_seconds: int = Field(ge=1, le=180)
    scene_target_seconds: float = Field(ge=1, le=30)
    scene_min_seconds: float = Field(ge=1, le=30)
    scene_max_seconds: float = Field(ge=1, le=30)
    max_scenes: int = Field(ge=1, le=24)


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: list[Literal["title", "poem"]]
    title_max_length: int = Field(ge=1, le=1000)
    poem_max_length: int = Field(ge=1, le=50000)

    @field_validator("required")
    @classmethod
    def validate_required_fields(cls, value: list[str]) -> list[str]:
        if set(value) != {"title", "poem"} or len(value) != 2:
            raise ValueError("input_schema.required phải gồm đúng title và poem")
        return value


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["omnivoice"]
    voice_id: str = Field(min_length=1, max_length=200)
    voice_name: str = Field(min_length=1, max_length=200)
    selectable: bool = False
    title_pause_ms: int = Field(ge=0, le=5000)
    single_task: Literal[True]


class AudioProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["vieneu_v3_turbo"]
    profile_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    profile_version: int = Field(default=1, ge=1, le=10000)
    status: Literal["draft", "active"] = "draft"
    backend: Literal["onnx"]
    precision: Literal["fp32"]
    sample_rate: Literal[48000]
    use_ref_codes: Literal[True]
    denoise_reference: Literal[False]
    apply_watermark: Literal[True]


class VisualProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["muse", "google_flow"]
    available_providers: list[Literal["muse", "google_flow"]]
    mode: Literal["hybrid", "images"]
    aspect_ratio: Literal["9:16"]
    image_resolution: Literal["1080x1920"]
    max_video_scenes: int = Field(ge=0, le=3)
    google_flow_image_model: str = Field(default="nano_banana_2_1", min_length=1, max_length=100)
    google_flow_video_model: str = Field(default="omni_1_1_flash", min_length=1, max_length=100)
    style: str = Field(min_length=20, max_length=2000)
    forbid: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("available_providers")
    @classmethod
    def validate_available_providers(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("visual_profile.available_providers không được rỗng")
        return unique

    @model_validator(mode="after")
    def validate_default_provider(self) -> "VisualProfile":
        if self.provider not in self.available_providers:
            raise ValueError("visual_profile.provider phải thuộc available_providers")
        return self


class RenderTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width: int = Field(ge=320, le=2160)
    height: int = Field(ge=568, le=3840)
    fps: Literal[30]
    video_codec: str | None = None
    audio_codec: str | None = None


class RenderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: RenderTarget
    final: RenderTarget
    subtitles: Literal[False]
    title_card: Literal[False]
    credits: Literal[False]
    music: Literal[False]
    ambience: Literal[False]


class ReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_approval_required: Literal[True]
    review_points: Literal[1]


class CharacterProfileDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    profile_version: int = Field(ge=1, le=10000)
    required: bool = True


class ShortVideoProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    status: Literal["active", "paused", "archived"]
    order: int = Field(default=100, ge=-10000, le=10000)
    profile_version: str = Field(min_length=1, max_length=80)
    pipeline_profile: PipelineProfile
    input_schema: InputSchema
    voice_profile: VoiceProfile | None = None
    audio_profile: AudioProfile | None = None
    visual_profile: VisualProfile
    render_profile: RenderProfile
    review_policy: ReviewPolicy
    character_profile: CharacterProfileDependency | None = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not PROJECT_KEY_PATTERN.fullmatch(value):
            raise ValueError("key chỉ được dùng chữ thường, số và dấu gạch ngang")
        return value

    @model_validator(mode="after")
    def validate_audio_provider(self) -> "ShortVideoProjectManifest":
        if (self.voice_profile is None) == (self.audio_profile is None):
            raise ValueError("Manifest phải khai báo đúng một voice_profile hoặc audio_profile")
        return self


def _find_secret_field(value: Any, path: tuple[str, ...] = ()) -> str:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold()
            current_path = (*path, str(raw_key))
            secret_suffixes = (
                "_api_key",
                "_cookie",
                "_cookies",
                "_credential",
                "_credentials",
                "_password",
                "_secret",
                "_token",
            )
            if key in SECRET_FIELD_NAMES or key.endswith(secret_suffixes):
                return ".".join(current_path)
            found = _find_secret_field(child, current_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_secret_field(child, (*path, str(index)))
            if found:
                return found
    return ""


def _dependency_status(
    manifest: ShortVideoProjectManifest,
    character_profiles: ShortVideoCharacterProfileRegistry,
) -> tuple[bool, list[dict], dict | None, dict | None]:
    dependencies: list[dict] = []
    pipeline_ok = manifest.pipeline_profile.id in SUPPORTED_PIPELINES
    dependencies.append(
        {
            "id": "pipeline",
            "available": pipeline_ok,
            "detail": manifest.pipeline_profile.id,
        }
    )
    audio_summary = None
    if manifest.audio_profile is not None:
        technical = vieneu_tts.managed_profile_status(
            manifest.key,
            manifest.audio_profile.profile_version,
        )
        audio_ready = bool(technical.get("available"))
        create_ready = audio_ready and manifest.audio_profile.status == "active"
        audio_summary = {
            **manifest.audio_profile.model_dump(mode="json"),
            "runtime_ready": bool((technical.get("runtime") or {}).get("available")),
            "reference_ready": bool(technical.get("reference_ready")),
            "create_ready": create_ready,
            "model_revision": deepcopy(
                (technical.get("runtime") or {}).get("model_revision") or {}
            ),
            "detail": (
                technical.get("detail")
                if audio_ready
                else technical.get("detail") or "Audio Profile chưa sẵn sàng."
            ),
        }
        dependencies.append(
            {
                "id": "audio_profile",
                "available": create_ready,
                "detail": (
                    "Audio Profile đang ở trạng thái draft; chỉ cho phép nghe thử."
                    if audio_ready and not create_ready
                    else audio_summary["detail"]
                ),
            }
        )
    else:
        voice_profile = manifest.voice_profile
        assert voice_profile is not None
        voice_ok = False
        voice_detail = ""
        try:
            voice = voice_config.get_voice(
                voice_profile.voice_id,
                include_inactive=False,
            )
            provider_id = str(voice.get("provider_id") or "").casefold()
            actual_name = str(voice.get("name") or "")
            voice_ok = provider_id == voice_profile.provider and (
                voice_profile.selectable or actual_name == voice_profile.voice_name
            )
            if not voice_ok:
                voice_detail = "Giọng không đúng provider hoặc tên đã khai báo."
        except ValueError as exc:
            if voice_profile.selectable:
                try:
                    selectable_voices = [
                        voice
                        for voice in voice_config.load_voice_config().get("voices") or []
                        if voice.get("status") == "active"
                        and str(voice.get("provider_id") or "").casefold()
                        == voice_profile.provider
                    ]
                    voice_ok = bool(selectable_voices)
                    voice_detail = (
                        f"{len(selectable_voices)} giọng OmniVoice đang hoạt động."
                        if voice_ok
                        else "Không có giọng OmniVoice đang hoạt động."
                    )
                except Exception:
                    voice_detail = "Không thể kiểm tra danh sách giọng OmniVoice."
            else:
                voice_detail = str(exc)
        except Exception as exc:
            logger.warning(
                "Short Video voice dependency check failed for project %s: %s",
                manifest.key,
                type(exc).__name__,
            )
            voice_detail = "Không thể kiểm tra dependency giọng đọc."
        dependencies.append(
            {
                "id": "voice",
                "available": voice_ok,
                "detail": voice_detail or voice_profile.voice_name,
            }
        )
    for provider in manifest.visual_profile.available_providers:
        dependencies.append(
            {
                "id": provider,
                "available": True,
                "deferred": True,
                "detail": (
                    "Kiểm tra khi job yêu cầu media; Muse vẫn on-demand."
                    if provider == "muse"
                    else "Kiểm tra khi job yêu cầu media; Google Flow vẫn on-demand."
                ),
            }
        )
    character_summary = None
    character_dependency = manifest.character_profile
    if character_dependency is not None:
        character_summary = character_profiles.get(
            character_dependency.key,
            character_dependency.profile_version,
        )
        profile_ready = bool(character_summary and character_summary.get("available"))
        dependency_ready = profile_ready or not character_dependency.required
        dependencies.append(
            {
                "id": "character_profile",
                "available": dependency_ready,
                "detail": (
                    f"{character_dependency.key} v{character_dependency.profile_version}"
                    if profile_ready
                    else "Character Profile chưa active hoặc chưa đủ ảnh hợp lệ."
                ),
            }
        )
    return (
        all(bool(item["available"]) for item in dependencies),
        dependencies,
        character_summary,
        audio_summary,
    )


class ShortVideoProjectRegistry:
    def __init__(
        self,
        directory: Path = SHORT_VIDEO_PROJECTS_CONFIG_DIR,
        character_profiles: ShortVideoCharacterProfileRegistry = character_profile_registry,
    ) -> None:
        self.directory = Path(directory)
        self.character_profiles = character_profiles
        self._lock = threading.RLock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._entries: list[dict] = []

    def _current_signature(self) -> tuple[tuple[str, int, int], ...]:
        project_signature = tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(self.directory.glob("*.json"))
            if path.is_file()
        ) if self.directory.is_dir() else ()
        character_signature = tuple(
            (f"character:{name}", mtime, size)
            for name, mtime, size in self.character_profiles.revision_signature()
        )
        audio_signature = tuple(
            (f"audio:{name}", mtime, size)
            for name, mtime, size in vieneu_tts.revision_signature()
        )
        return (*project_signature, *character_signature, *audio_signature)

    def _load(self) -> None:
        signature = self._current_signature()
        if signature == self._signature and self._entries:
            return
        entries: list[dict] = []
        by_key: dict[str, list[dict]] = {}
        for path_name, _mtime, _size in signature:
            if path_name.startswith("character:"):
                continue
            path = self.directory / path_name
            fallback_key = path.stem.split(".v", 1)[0]
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Manifest phải là JSON object.")
                secret_path = _find_secret_field(raw)
                if secret_path:
                    raise ValueError(
                        f"Manifest không được chứa trường bí mật: {secret_path}"
                    )
                manifest = ShortVideoProjectManifest.model_validate(raw)
                available, dependencies, character_summary, audio_summary = _dependency_status(
                    manifest,
                    self.character_profiles,
                )
                errors = [] if available else [
                    "Một hoặc nhiều dependency chưa sẵn sàng."
                ]
                entry = {
                    **manifest.model_dump(mode="json"),
                    "available": available,
                    "dependencies": dependencies,
                    "character_profile_summary": character_summary,
                    "audio_profile_summary": audio_summary,
                    "errors": errors,
                    "source": path.name,
                }
            except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                entry = {
                    "key": fallback_key,
                    "display_name": fallback_key,
                    "description": "",
                    "status": "archived",
                    "order": 10000,
                    "profile_version": "",
                    "available": False,
                    "dependencies": [],
                    "character_profile_summary": None,
                    "audio_profile_summary": None,
                    "errors": [str(exc)],
                    "source": path.name,
                }
            entries.append(entry)
            by_key.setdefault(str(entry["key"]), []).append(entry)

        for key, duplicates in by_key.items():
            if len(duplicates) <= 1:
                continue
            for entry in duplicates:
                entry["available"] = False
                entry["errors"] = [*entry.get("errors", []), f"Trùng project key: {key}"]

        entries.sort(key=lambda item: (int(item.get("order") or 0), str(item["key"])))
        self._entries = entries
        self._signature = signature

    def list_projects(self, *, include_archived: bool = True) -> list[dict]:
        with self._lock:
            self._load()
            projects = self._entries
            if not include_archived:
                projects = [
                    entry
                    for entry in projects
                    if entry.get("status") in {"active", "paused"}
                ]
            return deepcopy(projects)

    def get(self, key: str) -> dict | None:
        normalized = str(key or "").strip().casefold()
        with self._lock:
            self._load()
            matches = [entry for entry in self._entries if entry.get("key") == normalized]
            if len(matches) != 1:
                return None
            return deepcopy(matches[0])

    def snapshot_for_create(
        self,
        key: str,
        *,
        visual_provider: str = "",
        voice_id: str = "",
        audio_profile_id: str = "",
    ) -> dict:
        entry = self.get(key)
        if entry is None:
            raise KeyError("Dự án không khả dụng.")
        if entry.get("status") != "active":
            raise ValueError("Dự án hiện không nhận video mới.")
        if not entry.get("available"):
            raise ValueError("Dependency của dự án chưa sẵn sàng.")
        snapshot = {
            key: deepcopy(value)
            for key, value in entry.items()
            if key not in {
                "available",
                "dependencies",
                "errors",
                "source",
                "character_profile_summary",
                "audio_profile_summary",
            }
        }
        visual = snapshot.get("visual_profile") or {}
        selected_provider = str(
            visual_provider or visual.get("provider") or ""
        ).strip().casefold()
        allowed_providers = {
            str(provider).strip().casefold()
            for provider in visual.get("available_providers") or []
        }
        if selected_provider not in allowed_providers:
            raise ValueError("Engine hình ảnh không được project này hỗ trợ.")
        visual["provider"] = selected_provider
        snapshot["visual_profile"] = visual
        configured_audio = snapshot.get("audio_profile")
        if isinstance(configured_audio, dict):
            if voice_id:
                raise ValueError("voice_id chỉ dùng để đọc item OmniVoice cũ.")
            selected_profile_id = str(
                audio_profile_id or configured_audio.get("profile_id") or ""
            ).strip()
            if selected_profile_id != str(configured_audio.get("profile_id") or ""):
                raise ValueError("Audio Profile không thuộc project này.")
            if configured_audio.get("status") != "active":
                raise ValueError("Audio Profile đang ở trạng thái draft; chưa thể tạo video.")
            technical = vieneu_tts.managed_profile_status(
                str(snapshot.get("key") or ""),
                int(configured_audio.get("profile_version") or 1),
            )
            if not technical.get("available"):
                raise ValueError(str(technical.get("detail") or "Audio Profile chưa sẵn sàng."))
            voice_snapshot = {
                "voice_id": selected_profile_id,
                "voice_name": str(configured_audio.get("display_name") or selected_profile_id),
                "provider_id": str(configured_audio.get("provider") or ""),
                "voice_revision": int(configured_audio.get("profile_version") or 1),
                "config": {
                    "backend": configured_audio.get("backend"),
                    "precision": configured_audio.get("precision"),
                    "sample_rate": configured_audio.get("sample_rate"),
                },
            }
        else:
            configured_voice = snapshot.get("voice_profile") or {}
            selected_voice_id = str(
                voice_id or configured_voice.get("voice_id") or ""
            ).strip()
            if voice_id and not configured_voice.get("selectable"):
                if selected_voice_id != str(configured_voice.get("voice_id") or ""):
                    raise ValueError("Project này không cho phép thay đổi giọng đọc.")
            voice = voice_config.get_voice(selected_voice_id, include_inactive=False)
            if (
                str(voice.get("provider_id") or "").casefold()
                != str(configured_voice.get("provider") or "").casefold()
            ):
                raise ValueError("Giọng dự án phải thuộc provider OmniVoice.")
            if not voice_config.voice_allows_usage(voice, "single_narrator"):
                raise ValueError("Giọng đã chọn không được phép dùng cho video một người đọc.")
            snapshot["voice_profile"] = {
                **configured_voice,
                "voice_id": str(voice.get("id") or ""),
                "voice_name": str(voice.get("name") or ""),
            }
            voice_snapshot = voice_config.build_voice_snapshot(voice)
            voice_snapshot["config"] = {
                **(voice_snapshot.get("config") or {}),
                "pause_ms": int(configured_voice.get("title_pause_ms") or 0),
            }
        snapshot["resolved_voice_snapshot"] = voice_snapshot
        return snapshot

    def audio_profile_for_project(self, key: str) -> dict | None:
        entry = self.get(key)
        profile = (entry or {}).get("audio_profile")
        if not isinstance(profile, dict):
            return None
        summary = (entry or {}).get("audio_profile_summary") or {}
        return {
            **deepcopy(profile),
            "runtime_ready": bool(summary.get("runtime_ready")),
            "reference_ready": bool(summary.get("reference_ready")),
            "create_ready": bool(summary.get("create_ready")),
            "model_revision": deepcopy(summary.get("model_revision") or {}),
            "detail": str(summary.get("detail") or ""),
        }

    def audio_profile_for_preview(self, key: str, profile_id: str = "") -> dict:
        entry = self.get(key)
        if entry is None:
            raise KeyError("Dự án không khả dụng.")
        profile = entry.get("audio_profile")
        if not isinstance(profile, dict):
            raise ValueError("Project không dùng Audio Profile VieNeu.")
        selected = str(profile_id or profile.get("profile_id") or "").strip()
        if selected != str(profile.get("profile_id") or ""):
            raise ValueError("Audio Profile không thuộc project này.")
        status = vieneu_tts.managed_profile_status(
            str(entry.get("key") or ""),
            int(profile.get("profile_version") or 1),
        )
        if not status.get("available"):
            raise ValueError(str(status.get("detail") or "Audio Profile chưa sẵn sàng."))
        return {
            **deepcopy(profile),
            "runtime_revision": (status.get("runtime") or {}).get("runtime_revision") or "",
            "model_revision": deepcopy(
                (status.get("runtime") or {}).get("model_revision") or {}
            ),
            "runtime_lock_sha256": (status.get("runtime") or {}).get("lock_sha256") or "",
            "reference_sha256": status.get("reference_sha256") or "",
        }

    def snapshot_audio_profile(self, snapshot: dict, item_dir: Path) -> dict:
        profile = snapshot.get("audio_profile")
        if not isinstance(profile, dict):
            return snapshot
        snapshot["resolved_audio_profile"] = vieneu_tts.snapshot_profile(
            project_key=str(snapshot.get("key") or ""),
            profile=profile,
            item_dir=item_dir,
        )
        return snapshot

    def snapshot_character_profile(self, snapshot: dict, item_dir: Path) -> dict:
        dependency = snapshot.get("character_profile")
        if not isinstance(dependency, dict):
            return snapshot
        profile = self.character_profiles.get(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
        )
        if not profile or not profile.get("available"):
            if dependency.get("required"):
                raise ValueError("Character Profile bắt buộc chưa sẵn sàng.")
            snapshot.pop("character_profile", None)
            return snapshot
        profile_snapshot = self.character_profiles.snapshot_for_item(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
            item_dir,
        )
        snapshot["resolved_character_profile"] = profile_snapshot
        return snapshot

    def character_profile_for_project(self, key: str) -> dict | None:
        entry = self.get(key)
        dependency = (entry or {}).get("character_profile")
        if not isinstance(dependency, dict):
            return None
        return self.character_profiles.get(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
        )

    def character_profile_asset_path(self, key: str, asset_id: str) -> Path | None:
        entry = self.get(key)
        dependency = (entry or {}).get("character_profile")
        if not isinstance(dependency, dict):
            return None
        return self.character_profiles.asset_path(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
            asset_id,
        )

    def add_character_profile_asset(self, key: str, **asset: Any) -> dict:
        entry = self.get(key)
        dependency = (entry or {}).get("character_profile")
        if not isinstance(dependency, dict):
            raise ValueError("Project chưa khai báo Character Profile.")
        return self.character_profiles.add_reference_asset(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
            **asset,
        )

    def remove_character_profile_asset(self, key: str, asset_id: str) -> dict:
        entry = self.get(key)
        dependency = (entry or {}).get("character_profile")
        if not isinstance(dependency, dict):
            raise ValueError("Project chưa khai báo Character Profile.")
        return self.character_profiles.remove_reference_asset(
            str(dependency.get("key") or ""),
            int(dependency.get("profile_version") or 0),
            asset_id,
        )


registry = ShortVideoProjectRegistry()
