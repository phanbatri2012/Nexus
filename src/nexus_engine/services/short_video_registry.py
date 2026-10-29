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
from nexus_engine.services import voice_config


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
    title_pause_ms: int = Field(ge=0, le=5000)
    single_task: Literal[True]


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
    voice_profile: VoiceProfile
    visual_profile: VisualProfile
    render_profile: RenderProfile
    review_policy: ReviewPolicy

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not PROJECT_KEY_PATTERN.fullmatch(value):
            raise ValueError("key chỉ được dùng chữ thường, số và dấu gạch ngang")
        return value


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


def _dependency_status(manifest: ShortVideoProjectManifest) -> tuple[bool, list[dict]]:
    dependencies: list[dict] = []
    pipeline_ok = manifest.pipeline_profile.id in SUPPORTED_PIPELINES
    dependencies.append(
        {
            "id": "pipeline",
            "available": pipeline_ok,
            "detail": manifest.pipeline_profile.id,
        }
    )
    voice_ok = False
    voice_detail = ""
    try:
        voice = voice_config.get_voice(
            manifest.voice_profile.voice_id,
            include_inactive=False,
        )
        provider_id = str(voice.get("provider_id") or "").casefold()
        actual_name = str(voice.get("name") or "")
        voice_ok = (
            provider_id == manifest.voice_profile.provider
            and actual_name == manifest.voice_profile.voice_name
        )
        if not voice_ok:
            voice_detail = "Giọng không đúng provider hoặc tên đã khai báo."
    except ValueError as exc:
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
            "detail": voice_detail or manifest.voice_profile.voice_name,
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
    return all(bool(item["available"]) for item in dependencies), dependencies


class ShortVideoProjectRegistry:
    def __init__(self, directory: Path = SHORT_VIDEO_PROJECTS_CONFIG_DIR) -> None:
        self.directory = Path(directory)
        self._lock = threading.RLock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._entries: list[dict] = []

    def _current_signature(self) -> tuple[tuple[str, int, int], ...]:
        if not self.directory.is_dir():
            return ()
        return tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(self.directory.glob("*.json"))
            if path.is_file()
        )

    def _load(self) -> None:
        signature = self._current_signature()
        if signature == self._signature and self._entries:
            return
        entries: list[dict] = []
        by_key: dict[str, list[dict]] = {}
        for path_name, _mtime, _size in signature:
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
                available, dependencies = _dependency_status(manifest)
                errors = [] if available else [
                    "Một hoặc nhiều dependency chưa sẵn sàng."
                ]
                entry = {
                    **manifest.model_dump(mode="json"),
                    "available": available,
                    "dependencies": dependencies,
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

    def snapshot_for_create(self, key: str, *, visual_provider: str = "") -> dict:
        entry = self.get(key)
        if entry is None:
            raise KeyError("Dự án không khả dụng.")
        if entry.get("status") != "active":
            raise ValueError("Dự án hiện không nhận video mới.")
        if not entry.get("available"):
            raise ValueError("Dependency của dự án chưa sẵn sàng.")
        voice = voice_config.get_voice(
            str((entry.get("voice_profile") or {}).get("voice_id") or ""),
            include_inactive=False,
        )
        if str(voice.get("provider_id") or "").casefold() != "omnivoice":
            raise ValueError("Giọng dự án phải thuộc provider OmniVoice.")
        snapshot = {
            key: deepcopy(value)
            for key, value in entry.items()
            if key not in {"available", "dependencies", "errors", "source"}
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
        voice_snapshot = voice_config.build_voice_snapshot(voice)
        voice_snapshot["config"] = {
            **(voice_snapshot.get("config") or {}),
            "pause_ms": int(
                (snapshot.get("voice_profile") or {}).get("title_pause_ms") or 0
            ),
        }
        snapshot["resolved_voice_snapshot"] = voice_snapshot
        return snapshot


registry = ShortVideoProjectRegistry()
