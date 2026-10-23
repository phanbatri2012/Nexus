"""Evidence-based deterministic voice casting for multi-speaker videos."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

from nexus_engine.dialogue_parser import parse_dialogue_turns
from nexus_engine.services import voice_config
from nexus_engine.services.voice_config import normalize_casting_profile


CASTING_VERSION = 2
PROFILE_CONFIDENCE_THRESHOLD = 0.75
MANIFEST_GENDERS = {"any", "male", "female"}
MANIFEST_AGE_GROUPS = {"any", "young", "adult", "senior"}


class AutoCastingAttentionRequired(RuntimeError):
    """Raised when a safe distinct cast cannot be produced automatically."""


class CastingManifestValidationError(ValueError):
    """Raised when ChatGPT did not return a valid casting manifest."""


def normalize_speaker_key(value: str) -> str:
    """Normalize parser/config aliases to the canonical dialogue role key."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    key = re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")
    compact = key.replace("_", "")
    if compact == "MC":
        return "MC"
    guest_match = re.fullmatch(r"(?:GUEST|KHACH)(\d*)", compact)
    if guest_match:
        return f"KHACH_{int(guest_match.group(1) or '1')}"
    return key or "SPEAKER"


def script_speaker_keys(script: str) -> list[str]:
    result: list[str] = []
    for turn in parse_dialogue_turns(script):
        key = normalize_speaker_key(turn.get("role") or "")
        if key not in result:
            result.append(key)
    return result


def _extract_json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise CastingManifestValidationError("Casting manifest phải là JSON object.")
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CastingManifestValidationError("Casting manifest không phải JSON hợp lệ.") from exc
    if not isinstance(parsed, dict):
        raise CastingManifestValidationError("Casting manifest phải là JSON object.")
    return parsed


def validate_casting_manifest(script: str, value: object) -> dict:
    """Validate and canonicalize a manifest against roles present in the script."""
    payload = _extract_json_object(value)
    speakers = payload.get("speakers")
    if not isinstance(speakers, list) or not speakers:
        raise CastingManifestValidationError("Casting manifest thiếu danh sách speakers.")
    expected = script_speaker_keys(script)
    expected_set = set(expected)
    normalized: dict[str, dict] = {}
    for raw_entry in speakers:
        if not isinstance(raw_entry, dict):
            raise CastingManifestValidationError("Mỗi speaker phải là một JSON object.")
        key = normalize_speaker_key(
            str(raw_entry.get("speaker_key") or raw_entry.get("role") or "")
        )
        if key not in expected_set:
            raise CastingManifestValidationError(
                f"Role '{key}' không tồn tại trong kịch bản."
            )
        if key in normalized:
            raise CastingManifestValidationError(f"Role '{key}' bị lặp trong manifest.")
        gender = str(raw_entry.get("gender") or "").strip().casefold()
        age_group = str(raw_entry.get("age_group") or "").strip().casefold()
        language = str(raw_entry.get("language") or "").strip().casefold()
        styles = raw_entry.get("styles")
        try:
            confidence = float(raw_entry.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise CastingManifestValidationError(
                f"Confidence của role '{key}' không hợp lệ."
            ) from exc
        if gender not in MANIFEST_GENDERS:
            raise CastingManifestValidationError(f"Gender của role '{key}' không hợp lệ.")
        if age_group not in MANIFEST_AGE_GROUPS:
            raise CastingManifestValidationError(f"Age group của role '{key}' không hợp lệ.")
        if not language or len(language) > 35:
            raise CastingManifestValidationError(f"Language của role '{key}' không hợp lệ.")
        if not isinstance(styles, list) or any(
            not isinstance(style, str) or not style.strip() for style in styles
        ):
            raise CastingManifestValidationError(f"Styles của role '{key}' không hợp lệ.")
        if not 0.0 <= confidence <= 1.0:
            raise CastingManifestValidationError(
                f"Confidence của role '{key}' phải nằm trong 0..1."
            )
        normalized[key] = {
            "speaker_key": key,
            "name": str(raw_entry.get("name") or key).strip() or key,
            "gender": gender,
            "age_group": age_group,
            "language": language,
            "styles": list(dict.fromkeys(style.strip().casefold() for style in styles)),
            "confidence": confidence,
        }
    missing = [key for key in expected if key not in normalized]
    if missing:
        raise CastingManifestValidationError(
            "Casting manifest thiếu role: " + ", ".join(missing)
        )
    return {"speakers": [normalized[key] for key in expected]}


def _provided_manifest_map(value: object) -> dict[str, dict]:
    entries: Iterable[Any]
    if isinstance(value, dict):
        entries = value.get("speakers") if isinstance(value.get("speakers"), list) else []
    elif isinstance(value, list):
        entries = value
    else:
        entries = []
    result: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = normalize_speaker_key(
            str(entry.get("speaker_key") or entry.get("role") or entry.get("name") or "")
        )
        result[key] = dict(entry)
    return result


def _cast_setting_for_key(cast_settings: dict, key: str) -> dict:
    for raw_key, value in cast_settings.items():
        if normalize_speaker_key(raw_key) == key and isinstance(value, dict):
            return value
    return {}


def build_speaker_manifest(
    script: str,
    *,
    provided_manifest: object = None,
    cast_settings: object = None,
) -> list[dict]:
    provided = _provided_manifest_map(provided_manifest)
    cast = cast_settings if isinstance(cast_settings, dict) else {}
    manifest: list[dict] = []
    for key in script_speaker_keys(script):
        supplied = provided.get(key, {})
        role_cast = _cast_setting_for_key(cast, key)
        desired = role_cast.get("casting_profile") if isinstance(role_cast, dict) else {}
        desired = desired if isinstance(desired, dict) else {}
        confidence_value = supplied.get("confidence", desired.get("confidence", 0.35))
        try:
            confidence = min(1.0, max(0.0, float(confidence_value)))
        except (TypeError, ValueError):
            confidence = 0.35
        gender = str(supplied.get("gender") or desired.get("gender") or "any").casefold()
        age_group = str(
            supplied.get("age_group") or desired.get("age_group") or "any"
        ).casefold()
        language = str(
            supplied.get("language") or desired.get("language") or "vi"
        ).casefold()
        styles = supplied.get("styles") or desired.get("styles") or []
        if not isinstance(styles, list):
            styles = []
        manifest.append(
            {
                "speaker_key": key,
                "speaker_name": str(supplied.get("name") or key).strip(),
                "role": "mc" if key == "MC" else "guest",
                "gender": gender if gender in MANIFEST_GENDERS else "any",
                "age_group": age_group if age_group in MANIFEST_AGE_GROUPS else "any",
                "language": language or "vi",
                "styles": [
                    str(item).strip().casefold() for item in styles if str(item).strip()
                ],
                "confidence": confidence,
                "profile_resolved": bool(
                    confidence >= PROFILE_CONFIDENCE_THRESHOLD and gender != "any"
                ),
            }
        )
    return manifest


def manifest_hash(manifest: list[dict]) -> str:
    canonical = [
        {
            key: entry.get(key)
            for key in (
                "speaker_key",
                "speaker_name",
                "role",
                "gender",
                "age_group",
                "language",
                "styles",
                "confidence",
            )
        }
        for entry in manifest
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def profile_signature(manifest_entry: dict) -> str:
    payload = {
        key: manifest_entry.get(key)
        for key in ("gender", "age_group", "language", "styles", "role")
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _effective_requirement(manifest: dict) -> dict:
    confidence = float(manifest.get("confidence") or 0.0)
    resolved = confidence >= PROFILE_CONFIDENCE_THRESHOLD
    return {
        **manifest,
        "gender": str(manifest.get("gender") or "any") if resolved else "any",
        "age_group": str(manifest.get("age_group") or "any") if resolved else "any",
    }


def _score_voice(
    manifest: dict,
    voice: dict,
    *,
    guest_voice_pool: str = "",
) -> tuple[float, dict] | None:
    profile = normalize_casting_profile(voice.get("casting_profile"))
    if not voice_config.voice_allows_usage(
        voice,
        "multi_guest",
        voice_pool=guest_voice_pool,
    ):
        return None
    desired = _effective_requirement(manifest)
    desired_gender = str(desired.get("gender") or "any")
    desired_age = str(desired.get("age_group") or "any")
    language = str(desired.get("language") or "vi")
    role = str(desired.get("role") or "guest")
    if desired_gender != "any" and profile["gender"] != desired_gender:
        return None
    if desired_age != "any" and profile["age_group"] != desired_age:
        return None
    if profile["languages"] and language not in profile["languages"] and "any" not in profile["languages"]:
        return None
    if profile["roles"] and role not in profile["roles"] and "any" not in profile["roles"]:
        return None
    breakdown = {
        "base": 20.0,
        "gender": 35.0 if desired_gender != "any" else 0.0,
        "age_group": 25.0 if desired_age != "any" else 0.0,
        "language": 15.0 if language in profile["languages"] else 0.0,
        "role": 10.0 if role in profile["roles"] else 0.0,
        "styles": min(
            15.0,
            5.0 * len(set(desired.get("styles") or []) & set(profile["styles"])),
        ),
    }
    return sum(breakdown.values()), breakdown


def _eligible_guest_voices(
    voices: list[dict], *, guest_voice_pool: str, used: set[str]
) -> list[dict]:
    return [
        voice
        for voice in voices
        if str(voice.get("id") or "") not in used
        and voice_config.voice_allows_usage(
            voice, "multi_guest", voice_pool=guest_voice_pool
        )
    ]


def _rendezvous_rank(seed: str, voice_id: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{seed}|{voice_id}".encode("utf-8")).digest(), "big"
    )


def list_candidates(
    manifest_entry: dict,
    voices: list[dict],
    *,
    guest_voice_pool: str,
    used_voice_ids: set[str] | None = None,
) -> list[dict]:
    candidates: list[dict] = []
    for voice in voices:
        voice_id = str(voice.get("id") or "")
        if voice_id in (used_voice_ids or set()):
            continue
        scored = _score_voice(
            manifest_entry, voice, guest_voice_pool=guest_voice_pool
        )
        if scored is None:
            continue
        score, breakdown = scored
        candidates.append(
            {
                "voice_id": voice_id,
                "voice_name": str(voice.get("name") or voice_id),
                "match_score": round(score, 2),
                "breakdown": breakdown,
                "casting_profile": normalize_casting_profile(
                    voice.get("casting_profile")
                ),
            }
        )
    return sorted(candidates, key=lambda item: (-item["match_score"], item["voice_name"]))


def assign_cast(
    manifest: list[dict],
    voices: list[dict],
    *,
    mc_voice_id: str,
    registry: dict[str, dict] | None = None,
    guest_voice_pool: str = "",
    enforce_usage_policy: bool = False,
    source_voice_profile: dict | None = None,
    source_video_id: str = "",
    prompt_version: str = "",
    casting_version: int = CASTING_VERSION,
) -> list[dict]:
    voice_by_id = {str(voice.get("id") or ""): voice for voice in voices}
    mc_id = str(mc_voice_id or "").strip()
    if not mc_id or mc_id not in voice_by_id or voice_by_id[mc_id].get("status") != "active":
        raise AutoCastingAttentionRequired("Giọng MC cố định không còn hoạt động.")
    normalized_pool = voice_config.normalize_voice_pool_id(guest_voice_pool)
    if enforce_usage_policy:
        if not normalized_pool:
            raise AutoCastingAttentionRequired("Radar chưa được gán nhóm giọng khách.")
        if not voice_config.voice_allows_usage(voice_by_id[mc_id], "multi_host"):
            raise AutoCastingAttentionRequired(
                "Giọng MC cố định chưa được cấp quyền 'MC đa giọng'."
            )

    unresolved_guests = [
        entry
        for entry in manifest
        if entry.get("role") != "mc"
        and normalize_speaker_key(entry.get("speaker_key") or "") != "MC"
        and _effective_requirement(entry).get("gender") == "any"
    ]
    effective_manifest = [dict(entry) for entry in manifest]
    source_gender = str((source_voice_profile or {}).get("gender") or "any")
    source_confidence = float((source_voice_profile or {}).get("confidence") or 0.0)
    if len(unresolved_guests) == 1 and source_gender in {"male", "female"} and source_confidence >= 0.85:
        target_key = normalize_speaker_key(unresolved_guests[0].get("speaker_key") or "")
        for entry in effective_manifest:
            if normalize_speaker_key(entry.get("speaker_key") or "") == target_key:
                entry["gender"] = source_gender
                entry["confidence"] = source_confidence
                entry["profile_resolved"] = True
                entry["decision_source"] = "source_voice_fallback"
                break

    current_manifest_hash = manifest_hash(manifest)
    assignments: list[dict] = []
    used = {mc_id}
    registry = registry or {}
    for speaker in effective_manifest:
        key = normalize_speaker_key(str(speaker.get("speaker_key") or ""))
        if speaker.get("role") == "mc" or key == "MC":
            assignments.append(
                {
                    **speaker,
                    "speaker_key": key,
                    "role_key": "MC",
                    "voice_id": mc_id,
                    "match_score": 100.0,
                    "score_breakdown": {"fixed_mc": 100.0},
                    "source": "fixed_mc",
                    "usage_mode": "multi_host",
                    "voice_pool": "",
                    "casting_version": casting_version,
                    "manifest_hash": current_manifest_hash,
                    "approval_status": "auto",
                }
            )
            continue

        requirement = _effective_requirement(speaker)
        eligible_pool = _eligible_guest_voices(
            voices, guest_voice_pool=normalized_pool, used=used
        )
        if requirement["gender"] == "any":
            pool_genders = {
                normalize_casting_profile(voice.get("casting_profile"))["gender"]
                for voice in eligible_pool
            }
            if pool_genders not in ({"male"}, {"female"}):
                raise AutoCastingAttentionRequired(
                    f"Chưa đủ bằng chứng chọn âm sắc giọng cho {speaker.get('speaker_name') or key}; cần duyệt thủ công."
                )

        stable = registry.get(key) or {}
        stable_voice_id = str(stable.get("voice_id") or "")
        stable_scored = (
            _score_voice(
                speaker,
                voice_by_id[stable_voice_id],
                guest_voice_pool=normalized_pool,
            )
            if stable_voice_id in voice_by_id
            and stable_voice_id not in used
            and str(stable.get("profile_signature") or "") == profile_signature(speaker)
            else None
        )
        if stable_scored is not None:
            selected_id = stable_voice_id
            score, breakdown = stable_scored
            source = "registry"
        else:
            candidates: list[tuple[float, int, str, dict]] = []
            seed = "|".join(
                (str(source_video_id), str(prompt_version), key, str(casting_version))
            )
            for voice in eligible_pool:
                voice_id = str(voice.get("id") or "")
                scored = _score_voice(
                    speaker, voice, guest_voice_pool=normalized_pool
                )
                if scored is None:
                    continue
                voice_score, voice_breakdown = scored
                candidates.append(
                    (
                        voice_score,
                        _rendezvous_rank(seed, voice_id),
                        voice_id,
                        voice_breakdown,
                    )
                )
            if not candidates:
                raise AutoCastingAttentionRequired(
                    f"Không có giọng phù hợp và khác biệt cho {speaker.get('speaker_name') or key}."
                )
            score, _rank, selected_id, breakdown = max(
                candidates, key=lambda item: (item[0], item[1])
            )
            source = str(speaker.get("decision_source") or "manifest_profile")
        used.add(selected_id)
        assignments.append(
            {
                **speaker,
                "speaker_key": key,
                "role_key": key,
                "voice_id": selected_id,
                "match_score": round(float(score), 2),
                "score_breakdown": breakdown,
                "source": source,
                "usage_mode": "multi_guest",
                "voice_pool": normalized_pool,
                "casting_version": casting_version,
                "manifest_hash": current_manifest_hash,
                "approval_status": "auto",
            }
        )
    return assignments


def validate_cast_policy(
    assignments: list[dict],
    voices: list[dict],
    *,
    mc_voice_id: str,
    guest_voice_pool: str,
    manifest: list[dict] | None = None,
) -> None:
    """Fail closed when a stored Radar cast no longer satisfies its policy/profile."""
    voice_by_id = {str(voice.get("id") or ""): voice for voice in voices}
    mc_id = str(mc_voice_id or "").strip()
    pool_id = voice_config.normalize_voice_pool_id(guest_voice_pool)
    if not pool_id:
        raise AutoCastingAttentionRequired("Radar chưa được gán nhóm giọng khách.")
    mc_voice = voice_by_id.get(mc_id)
    if not mc_voice or not voice_config.voice_allows_usage(mc_voice, "multi_host"):
        raise AutoCastingAttentionRequired(
            "Giọng MC cố định không hoạt động hoặc chưa được cấp quyền 'MC đa giọng'."
        )

    manifest_by_key = {
        normalize_speaker_key(item.get("speaker_key") or ""): item
        for item in (manifest or [])
    }
    expected_hash = manifest_hash(manifest or []) if manifest else ""
    used: set[str] = set()
    mc_count = 0
    guest_count = 0
    for assignment in assignments:
        role_key = normalize_speaker_key(
            str(assignment.get("role_key") or assignment.get("speaker_key") or "")
        )
        voice_id = str(assignment.get("voice_id") or "")
        if not voice_id or voice_id in used:
            raise AutoCastingAttentionRequired("Casting Radar có giọng trống hoặc bị trùng.")
        used.add(voice_id)
        if role_key == "MC":
            mc_count += 1
            if voice_id != mc_id:
                raise AutoCastingAttentionRequired("Casting Radar không dùng đúng giọng MC cố định.")
            continue
        guest_count += 1
        voice = voice_by_id.get(voice_id)
        if not voice or not voice_config.voice_allows_usage(
            voice, "multi_guest", voice_pool=pool_id
        ):
            raise AutoCastingAttentionRequired(
                f"Giọng của {role_key or 'khách'} không còn được phép trong nhóm '{pool_id}'."
            )
        requirement = manifest_by_key.get(role_key)
        if requirement:
            if int(assignment.get("casting_version") or 1) >= CASTING_VERSION:
                if str(assignment.get("manifest_hash") or "") != expected_hash:
                    raise AutoCastingAttentionRequired(
                        f"Casting của {role_key} đã cũ so với manifest hiện tại."
                    )
            if (
                str(assignment.get("approval_status") or "") != "approved"
                and _score_voice(requirement, voice, guest_voice_pool=pool_id) is None
            ):
                raise AutoCastingAttentionRequired(
                    f"Giọng của {role_key} không còn khớp profile nhân vật."
                )
    if mc_count != 1 or guest_count < 1:
        raise AutoCastingAttentionRequired("Casting Radar thiếu MC hoặc giọng khách.")
