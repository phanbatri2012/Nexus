"""Versioned character profiles and immutable Short Video reference snapshots."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nexus_engine.paths import (
    SHORT_VIDEO_CHARACTER_PROFILES_CONFIG_DIR,
    SHORT_VIDEO_CHARACTER_PROFILES_DIR,
)


PROFILE_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ERA_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
ASSET_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MIN_IMAGE_DIMENSION = 512
RECOMMENDED_LONG_EDGE = 1200
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


class CharacterReferenceAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    file: str = Field(min_length=1, max_length=500)
    role: Literal["primary_portrait", "half_body", "full_body", "profile_context"]
    pose: str = Field(min_length=1, max_length=300)
    primary: bool = False
    source_label: str = Field(min_length=1, max_length=500)
    source_year: str = Field(min_length=1, max_length=100)
    rights_note: str = Field(min_length=1, max_length=1000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not ASSET_ID_PATTERN.fullmatch(value):
            raise ValueError("asset id chỉ được dùng chữ thường, số, gạch ngang hoặc gạch dưới")
        return value

    @field_validator("file")
    @classmethod
    def validate_relative_file(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        candidate = Path(normalized)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("file ảnh phải là đường dẫn tương đối an toàn")
        return normalized


class CharacterEraProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    year_from: int = Field(ge=1800, le=2100)
    year_to: int = Field(ge=1800, le=2100)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    appearance_prompt: str = Field(min_length=20, max_length=3000)
    context_prompt: str = Field(min_length=20, max_length=3000)
    references: list[CharacterReferenceAsset] = Field(default_factory=list, max_length=3)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not ERA_KEY_PATTERN.fullmatch(value):
            raise ValueError("era key chỉ được dùng chữ thường, số và gạch dưới")
        return value

    @model_validator(mode="after")
    def validate_years(self) -> "CharacterEraProfile":
        if self.year_to < self.year_from:
            raise ValueError("year_to không được nhỏ hơn year_from")
        return self


class CharacterAppearancePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_scene_ratio: float = Field(ge=0, le=1)
    target_scene_ratio: float = Field(ge=0, le=1)
    max_scene_ratio: float = Field(ge=0, le=1)
    allowed_shots: list[
        Literal["medium", "half_body", "full_body", "three_quarter", "rear_view"]
    ] = Field(min_length=1, max_length=10)
    forbid: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_ratios(self) -> "CharacterAppearancePolicy":
        if not self.min_scene_ratio <= self.target_scene_ratio <= self.max_scene_ratio:
            raise ValueError("scene ratio phải thỏa min <= target <= max")
        return self


class CharacterProfileManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    status: Literal["draft", "active", "archived"]
    profile_version: int = Field(ge=1, le=10000)
    identity_prompt: str = Field(min_length=20, max_length=4000)
    style_prompt: str = Field(min_length=20, max_length=4000)
    appearance_policy: CharacterAppearancePolicy
    eras: list[CharacterEraProfile] = Field(min_length=1, max_length=20)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not PROFILE_KEY_PATTERN.fullmatch(value):
            raise ValueError("profile key chỉ được dùng chữ thường, số và dấu gạch ngang")
        return value

    @model_validator(mode="after")
    def validate_unique_entries(self) -> "CharacterProfileManifest":
        era_keys = [era.key for era in self.eras]
        if len(era_keys) != len(set(era_keys)):
            raise ValueError("Character Profile có era key trùng nhau")
        asset_ids = [asset.id for era in self.eras for asset in era.references]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("Character Profile có asset id trùng nhau")
        return self


def _find_secret_field(value: Any, path: tuple[str, ...] = ()) -> str:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold()
            current_path = (*path, str(raw_key))
            if key in SECRET_FIELD_NAMES or key.endswith(
                (
                    "_api_key",
                    "_cookie",
                    "_cookies",
                    "_credential",
                    "_credentials",
                    "_password",
                    "_secret",
                    "_token",
                )
            ):
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ShortVideoCharacterProfileRegistry:
    def __init__(
        self,
        config_directory: Path = SHORT_VIDEO_CHARACTER_PROFILES_CONFIG_DIR,
        assets_directory: Path = SHORT_VIDEO_CHARACTER_PROFILES_DIR,
    ) -> None:
        self.config_directory = Path(config_directory)
        self.assets_directory = Path(assets_directory)
        self._lock = threading.RLock()
        self._signature: tuple[tuple[str, int, int], ...] = ()
        self._entries: list[dict] = []

    def _current_signature(self) -> tuple[tuple[str, int, int], ...]:
        paths: list[Path] = []
        if self.config_directory.is_dir():
            paths.extend(self.config_directory.glob("*.json"))
        if self.assets_directory.is_dir():
            paths.extend(path for path in self.assets_directory.rglob("*") if path.is_file())
        signature: list[tuple[str, int, int]] = []
        for path in sorted(paths, key=lambda value: str(value).casefold()):
            try:
                stat = path.stat()
                relative_root = (
                    self.config_directory
                    if path.parent == self.config_directory
                    else self.assets_directory
                )
                signature.append(
                    (str(path.relative_to(relative_root)).replace("\\", "/"), stat.st_mtime_ns, stat.st_size)
                )
            except (OSError, ValueError):
                continue
        return tuple(signature)

    def _inspect_asset(self, root: Path, asset: CharacterReferenceAsset) -> tuple[dict, list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        source = (root / asset.file).resolve()
        try:
            source.relative_to(root.resolve())
        except ValueError:
            errors.append(f"Ảnh {asset.id} nằm ngoài thư mục profile.")
            return {}, errors, warnings
        if source.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
            errors.append(f"Ảnh {asset.id} có định dạng không hỗ trợ.")
            return {}, errors, warnings
        if not source.is_file():
            errors.append(f"Thiếu ảnh {asset.id}.")
            return {}, errors, warnings
        try:
            size_bytes = source.stat().st_size
            if size_bytes <= 0 or size_bytes > MAX_IMAGE_BYTES:
                errors.append(f"Ảnh {asset.id} phải lớn hơn 0 và không quá 20 MB.")
                return {}, errors, warnings
            with Image.open(source) as image:
                width, height = image.size
                image.verify()
            if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
                errors.append(f"Ảnh {asset.id} phải có mỗi chiều tối thiểu 512 px.")
                return {}, errors, warnings
            if max(width, height) < RECOMMENDED_LONG_EDGE:
                warnings.append(f"Ảnh {asset.id} có cạnh dài dưới 1.200 px.")
            return {
                "source_path": source,
                "sha256": _sha256(source),
                "width": width,
                "height": height,
                "size_bytes": size_bytes,
            }, errors, warnings
        except (OSError, ValueError) as exc:
            errors.append(f"Không thể đọc ảnh {asset.id}: {type(exc).__name__}.")
            return {}, errors, warnings

    def _build_entry(self, manifest: CharacterProfileManifest, source_name: str) -> dict:
        root = (
            self.assets_directory / manifest.key / f"v{manifest.profile_version}"
        ).resolve()
        errors: list[str] = []
        warnings: list[str] = []
        assets: dict[str, dict] = {}
        total_references = 0
        era_readiness: dict[str, dict] = {}
        for era in manifest.eras:
            era_errors: list[str] = []
            reference_count = len(era.references)
            total_references += reference_count
            if reference_count < 2 or reference_count > 3:
                era_errors.append("Mỗi thời kỳ cần từ 2 đến 3 ảnh.")
            if sum(bool(asset.primary) for asset in era.references) != 1:
                era_errors.append("Mỗi thời kỳ phải có đúng một ảnh primary.")
            roles = {asset.role for asset in era.references}
            if era.references and "primary_portrait" not in roles:
                era_errors.append("Mỗi thời kỳ cần một ảnh primary_portrait.")
            if era.references and not roles.intersection({"half_body", "full_body"}):
                era_errors.append("Mỗi thời kỳ cần một ảnh bán thân hoặc toàn thân.")
            for asset in era.references:
                metadata, asset_errors, asset_warnings = self._inspect_asset(root, asset)
                era_errors.extend(asset_errors)
                warnings.extend(asset_warnings)
                if metadata:
                    assets[asset.id] = {
                        **asset.model_dump(mode="json", exclude={"file"}),
                        "era_key": era.key,
                        **metadata,
                    }
            errors.extend(f"{era.label}: {message}" for message in era_errors)
            era_readiness[era.key] = {
                "available": not era_errors,
                "reference_count": reference_count,
                "errors": era_errors,
            }
        minimum_references = len(manifest.eras) * 2
        maximum_references = len(manifest.eras) * 3
        if total_references < minimum_references or total_references > maximum_references:
            errors.append(
                "Character Profile cần tổng cộng từ "
                f"{minimum_references} đến {maximum_references} ảnh."
            )
        if manifest.status != "active":
            errors.append(f"Character Profile đang ở trạng thái {manifest.status}.")
        return {
            "manifest": manifest,
            "source": source_name,
            "root": root,
            "assets": assets,
            "available": manifest.status == "active" and not errors,
            "errors": errors,
            "warnings": warnings,
            "era_readiness": era_readiness,
        }

    def _load(self) -> None:
        signature = self._current_signature()
        if signature == self._signature and self._entries:
            return
        entries: list[dict] = []
        if self.config_directory.is_dir():
            manifest_paths = sorted(self.config_directory.glob("*.json"))
        else:
            manifest_paths = []
        for path in manifest_paths:
            fallback_key = path.stem.split(".v", 1)[0]
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Character Profile manifest phải là JSON object.")
                secret_path = _find_secret_field(raw)
                if secret_path:
                    raise ValueError(f"Manifest không được chứa trường bí mật: {secret_path}")
                manifest = CharacterProfileManifest.model_validate(raw)
                entry = self._build_entry(manifest, path.name)
            except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                entry = {
                    "manifest": None,
                    "fallback_key": fallback_key,
                    "fallback_version": 0,
                    "source": path.name,
                    "root": None,
                    "assets": {},
                    "available": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "era_readiness": {},
                }
            entries.append(entry)

        grouped: dict[tuple[str, int], list[dict]] = {}
        for entry in entries:
            manifest = entry.get("manifest")
            identity = (
                (manifest.key, manifest.profile_version)
                if manifest is not None
                else (entry.get("fallback_key") or "", int(entry.get("fallback_version") or 0))
            )
            grouped.setdefault(identity, []).append(entry)
        for identity, duplicates in grouped.items():
            if len(duplicates) <= 1:
                continue
            for entry in duplicates:
                entry["available"] = False
                entry["errors"] = [
                    *entry.get("errors", []),
                    f"Trùng Character Profile key/version: {identity[0]} v{identity[1]}",
                ]
        self._entries = entries
        self._signature = signature

    @staticmethod
    def _public_entry(entry: dict) -> dict:
        manifest = entry.get("manifest")
        if manifest is None:
            return {
                "key": entry.get("fallback_key") or "",
                "profile_version": int(entry.get("fallback_version") or 0),
                "display_name": entry.get("fallback_key") or "",
                "status": "draft",
                "available": False,
                "errors": deepcopy(entry.get("errors") or []),
                "warnings": [],
                "eras": [],
                "source": entry.get("source") or "",
            }
        data = manifest.model_dump(mode="json", exclude={"identity_prompt", "style_prompt"})
        public_eras: list[dict] = []
        for era in manifest.eras:
            readiness = (entry.get("era_readiness") or {}).get(era.key) or {}
            public_eras.append(
                {
                    "key": era.key,
                    "label": era.label,
                    "year_from": era.year_from,
                    "year_to": era.year_to,
                    "available": bool(readiness.get("available")),
                    "reference_count": int(readiness.get("reference_count") or 0),
                    "errors": deepcopy(readiness.get("errors") or []),
                    "assets": [
                        {
                            "id": asset.id,
                            "role": asset.role,
                            "pose": asset.pose,
                            "primary": asset.primary,
                            "source_label": asset.source_label,
                            "source_year": asset.source_year,
                            "rights_note": asset.rights_note,
                            "available": asset.id in (entry.get("assets") or {}),
                            "width": int((entry.get("assets") or {}).get(asset.id, {}).get("width") or 0),
                            "height": int((entry.get("assets") or {}).get(asset.id, {}).get("height") or 0),
                        }
                        for asset in era.references
                    ],
                }
            )
        data["eras"] = public_eras
        data.update(
            {
                "available": bool(entry.get("available")),
                "errors": deepcopy(entry.get("errors") or []),
                "warnings": deepcopy(entry.get("warnings") or []),
                "source": entry.get("source") or "",
            }
        )
        return data

    def _find(self, key: str, profile_version: int) -> dict | None:
        normalized = str(key or "").strip().casefold()
        self._load()
        matches = [
            entry
            for entry in self._entries
            if entry.get("manifest") is not None
            and entry["manifest"].key == normalized
            and entry["manifest"].profile_version == int(profile_version)
        ]
        return matches[0] if len(matches) == 1 else None

    def list_profiles(self) -> list[dict]:
        with self._lock:
            self._load()
            return [self._public_entry(entry) for entry in self._entries]

    def revision_signature(self) -> tuple[tuple[str, int, int], ...]:
        with self._lock:
            return self._current_signature()

    def get(self, key: str, profile_version: int) -> dict | None:
        with self._lock:
            entry = self._find(key, profile_version)
            return self._public_entry(entry) if entry is not None else None

    def asset_path(self, key: str, profile_version: int, asset_id: str) -> Path | None:
        with self._lock:
            entry = self._find(key, profile_version)
            if entry is None:
                return None
            asset = (entry.get("assets") or {}).get(str(asset_id or ""))
            return Path(asset["source_path"]) if asset else None

    def add_reference_asset(
        self,
        key: str,
        profile_version: int,
        *,
        era_key: str,
        original_filename: str,
        content: bytes,
        role: str,
        pose: str,
        source_label: str,
        source_year: str,
        rights_note: str,
    ) -> dict:
        with self._lock:
            entry = self._find(key, profile_version)
            if entry is None or entry.get("manifest") is None:
                raise ValueError("Không tìm thấy Character Profile đã khai báo.")
            manifest: CharacterProfileManifest = entry["manifest"]
            if manifest.status != "draft":
                raise ValueError("Chỉ được thay đổi ảnh khi Character Profile ở trạng thái draft.")
            era = next((item for item in manifest.eras if item.key == era_key), None)
            if era is None:
                raise ValueError("Thời kỳ hình ảnh không thuộc Character Profile.")
            if len(era.references) >= 3:
                raise ValueError("Mỗi thời kỳ chỉ được có tối đa 3 ảnh.")
            if not content or len(content) > MAX_IMAGE_BYTES:
                raise ValueError("Ảnh phải lớn hơn 0 và không quá 20 MB.")
            original_suffix = Path(str(original_filename or "")).suffix.casefold()
            if original_suffix not in SUPPORTED_IMAGE_SUFFIXES:
                raise ValueError("Chỉ chấp nhận ảnh PNG, JPEG hoặc WebP.")
            try:
                with Image.open(io.BytesIO(content)) as image:
                    width, height = image.size
                    image_format = str(image.format or "").upper()
                    image.verify()
            except (OSError, ValueError) as exc:
                raise ValueError("File tải lên không phải ảnh hợp lệ.") from exc
            suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image_format)
            if suffix is None:
                raise ValueError("Chỉ chấp nhận ảnh PNG, JPEG hoặc WebP.")
            if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
                raise ValueError("Ảnh phải có mỗi chiều tối thiểu 512 px.")

            normalized_role = str(role or "").strip()
            primary = normalized_role == "primary_portrait"
            if primary and any(reference.primary for reference in era.references):
                raise ValueError("Thời kỳ này đã có ảnh chân dung chính.")
            asset_id = f"{era.key}_{normalized_role}_{uuid.uuid4().hex[:10]}"
            relative_file = f"{era.key}/{asset_id}{suffix}"
            reference = CharacterReferenceAsset.model_validate(
                {
                    "id": asset_id,
                    "file": relative_file,
                    "role": normalized_role,
                    "pose": str(pose or "").strip(),
                    "primary": primary,
                    "source_label": str(source_label or "").strip(),
                    "source_year": str(source_year or "").strip(),
                    "rights_note": str(rights_note or "").strip(),
                }
            )
            manifest_data = manifest.model_dump(mode="json")
            target_era = next(item for item in manifest_data["eras"] if item["key"] == era.key)
            target_era["references"].append(reference.model_dump(mode="json"))
            updated = CharacterProfileManifest.model_validate(manifest_data)
            destination = Path(entry["root"]) / relative_file
            manifest_path = self.config_directory / str(entry["source"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{asset_id}.",
                suffix=suffix,
                dir=destination.parent,
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                _atomic_write_json(manifest_path, updated.model_dump(mode="json"))
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            self._signature = ()
            profile = self.get(key, profile_version)
            if profile is None:
                raise ValueError("Không thể tải lại Character Profile sau khi lưu ảnh.")
            return profile

    def remove_reference_asset(
        self,
        key: str,
        profile_version: int,
        asset_id: str,
    ) -> dict:
        with self._lock:
            entry = self._find(key, profile_version)
            if entry is None or entry.get("manifest") is None:
                raise ValueError("Không tìm thấy Character Profile đã khai báo.")
            manifest: CharacterProfileManifest = entry["manifest"]
            if manifest.status != "draft":
                raise ValueError("Chỉ được thay đổi ảnh khi Character Profile ở trạng thái draft.")
            manifest_data = manifest.model_dump(mode="json")
            removed: dict | None = None
            for era in manifest_data["eras"]:
                references = era.get("references") or []
                for index, reference in enumerate(references):
                    if reference.get("id") == asset_id:
                        removed = references.pop(index)
                        break
                if removed is not None:
                    break
            if removed is None:
                raise ValueError("Không tìm thấy ảnh trong Character Profile.")
            updated = CharacterProfileManifest.model_validate(manifest_data)
            manifest_path = self.config_directory / str(entry["source"])
            source = (Path(entry["root"]) / str(removed.get("file") or "")).resolve()
            try:
                source.relative_to(Path(entry["root"]).resolve())
            except ValueError as exc:
                raise ValueError("Đường dẫn ảnh không an toàn.") from exc
            _atomic_write_json(manifest_path, updated.model_dump(mode="json"))
            if source.is_file():
                trash = Path(entry["root"]) / ".trash"
                trash.mkdir(parents=True, exist_ok=True)
                archived = trash / f"{asset_id}-{uuid.uuid4().hex[:8]}{source.suffix.casefold()}"
                os.replace(source, archived)
            self._signature = ()
            profile = self.get(key, profile_version)
            if profile is None:
                raise ValueError("Không thể tải lại Character Profile sau khi gỡ ảnh.")
            return profile

    def snapshot_for_item(
        self,
        key: str,
        profile_version: int,
        item_dir: Path,
    ) -> dict:
        with self._lock:
            entry = self._find(key, profile_version)
            if entry is None:
                raise ValueError("Không tìm thấy Character Profile đã khai báo.")
            if not entry.get("available"):
                detail = " ".join(str(value) for value in entry.get("errors") or [])
                raise ValueError(detail or "Character Profile chưa sẵn sàng.")
            manifest: CharacterProfileManifest = entry["manifest"]
            final_dir = Path(item_dir) / "references"
            planned: list[tuple[CharacterEraProfile, CharacterReferenceAsset, dict, Path]] = []
            for era in manifest.eras:
                for asset in era.references:
                    metadata = entry["assets"][asset.id]
                    suffix = Path(metadata["source_path"]).suffix.casefold()
                    target = final_dir / era.key / f"{metadata['sha256']}{suffix}"
                    planned.append((era, asset, metadata, target))

            if final_dir.exists():
                if not all(
                    target.is_file() and _sha256(target) == metadata["sha256"]
                    for _era, _asset, metadata, target in planned
                ):
                    raise ValueError("Snapshot Character Profile hiện có không toàn vẹn.")
            else:
                item_dir = Path(item_dir)
                item_dir.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix=".references-", dir=item_dir))
                try:
                    for era, _asset, metadata, target in planned:
                        staged = temporary / era.key / target.name
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(metadata["source_path"], staged)
                        if _sha256(staged) != metadata["sha256"]:
                            raise ValueError("Hash ảnh Character Profile thay đổi khi snapshot.")
                    os.replace(temporary, final_dir)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)

            eras: list[dict] = []
            for era in manifest.eras:
                references: list[dict] = []
                for asset in era.references:
                    metadata = entry["assets"][asset.id]
                    suffix = Path(metadata["source_path"]).suffix.casefold()
                    target = final_dir / era.key / f"{metadata['sha256']}{suffix}"
                    references.append(
                        {
                            **asset.model_dump(mode="json", exclude={"file"}),
                            "sha256": metadata["sha256"],
                            "width": metadata["width"],
                            "height": metadata["height"],
                            "snapshot_path": target.relative_to(item_dir).as_posix(),
                        }
                    )
                eras.append(
                    {
                        **era.model_dump(mode="json", exclude={"references"}),
                        "references": references,
                    }
                )
            return {
                **manifest.model_dump(mode="json", exclude={"eras"}),
                "eras": eras,
                "snapshot_hash": hashlib.sha256(
                    "".join(
                        metadata["sha256"]
                        for _era, _asset, metadata, _target in planned
                    ).encode("ascii")
                ).hexdigest(),
            }


character_profile_registry = ShortVideoCharacterProfileRegistry()
