from __future__ import annotations

import json
import re
import shutil
import uuid
from copy import deepcopy

from auto_yt.paths import DATA_DIR
from auto_yt.services.secret_store import write_private_text


SCHEMA_VERSION = 2
GENMAX_PROVIDER_ID = "genmax"
OMNIVOICE_PROVIDER_ID = "omnivoice"
DEFAULT_VOICE_ID = "e1d9617c-045c-4072-8d17-9be0ec113723"
DEFAULT_VOICE_NAME = "Giọng mặc định hiện tại"
VOICE_CONFIG_PATH = DATA_DIR / "voices.json"
MAX_VOICE_OPTIONS = 200
MAX_PROVIDER_OPTIONS = 20
MAX_VOICE_ID_LENGTH = 128
MAX_PROVIDER_VOICE_ID_LENGTH = 256
VOICE_STATUSES = {"active", "archived", "unavailable"}
CASTING_GENDERS = {"any", "male", "female"}
CASTING_AGE_GROUPS = {"any", "young", "adult", "senior"}
CASTING_USAGE_MODES = {"single_narrator", "multi_host", "multi_guest"}
SYSTEM_VOICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SECRET_CONFIG_KEYS = {"api_key", "token", "secret", "password", "credential"}

# These values are copied into every new OmniVoice snapshot.  Keeping them in
# the snapshot makes queued jobs reproducible and deliberately changes the
# request hash whenever the production generation policy changes.
OMNIVOICE_ENGINE_REVISION = 3
OMNIVOICE_DEFAULT_VOICE_CONFIG = {
    "engine_revision": OMNIVOICE_ENGINE_REVISION,
    "target_words_per_minute": 145,
    "pause_ms": 150,
    "max_chunk_chars": 220,
    "min_words_per_minute": 90,
    "max_words_per_minute": 320,
    "num_step": 32,
    "denoise": False,
}


def normalize_casting_profile(value: object) -> dict:
    profile = value if isinstance(value, dict) else {}
    gender = str(profile.get("gender") or "any").strip().casefold()
    age_group = str(profile.get("age_group") or "any").strip().casefold()
    if gender not in CASTING_GENDERS:
        raise ValueError("Giới tính casting phải là any, male hoặc female.")
    if age_group not in CASTING_AGE_GROUPS:
        raise ValueError("Nhóm tuổi casting phải là any, young, adult hoặc senior.")

    def normalize_tags(key: str, default: list[str]) -> list[str]:
        raw_values = profile.get(key, default)
        if not isinstance(raw_values, list):
            raise ValueError(f"Thuộc tính casting '{key}' phải là danh sách.")
        values: list[str] = []
        for raw_value in raw_values:
            cleaned = str(raw_value or "").strip().casefold()
            if not cleaned or len(cleaned) > 40 or not re.fullmatch(r"[a-z0-9_-]+", cleaned):
                raise ValueError(f"Giá trị casting '{key}' không hợp lệ.")
            if cleaned not in values:
                values.append(cleaned)
        if len(values) > 20:
            raise ValueError(f"Thuộc tính casting '{key}' có quá nhiều giá trị.")
        return values

    roles = normalize_tags("roles", ["guest"])
    raw_usage_modes = profile.get("usage_modes")
    if raw_usage_modes is None:
        # Preserve legacy intent without allowing opted-out voices into guest
        # casting. Existing voices must be explicitly promoted to multi-host or
        # assigned to a guest pool before Radar can use them in those roles.
        usage_modes = (
            ["multi_guest"]
            if bool(profile.get("auto_cast", False))
            else ["single_narrator"]
        )
        if "mc" in roles or "any" in roles:
            usage_modes.append("multi_host")
    else:
        usage_modes = normalize_tags("usage_modes", [])
        invalid_modes = set(usage_modes) - CASTING_USAGE_MODES
        if invalid_modes:
            raise ValueError("Phạm vi sử dụng voice không hợp lệ.")

    voice_pools = [
        normalize_voice_pool_id(pool_id)
        for pool_id in normalize_tags("voice_pools", [])
    ]
    return {
        "gender": gender,
        "age_group": age_group,
        "languages": normalize_tags("languages", ["vi"]),
        "roles": roles,
        "styles": normalize_tags("styles", []),
        "usage_modes": usage_modes,
        "voice_pools": voice_pools,
        # Retain the old response field for older clients. The permission is
        # now derived from usage_modes so both representations cannot diverge.
        "auto_cast": "multi_guest" in usage_modes,
    }


def normalize_voice_pool_id(value: object) -> str:
    pool_id = str(value or "").strip().casefold()
    if not pool_id:
        return ""
    if len(pool_id) > 40 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", pool_id):
        raise ValueError(
            "Mã nhóm giọng chỉ gồm chữ thường không dấu, số, '_' hoặc '-'."
        )
    return pool_id


def voice_allows_usage(
    voice: dict,
    usage_mode: str,
    *,
    voice_pool: str = "",
    require_active: bool = True,
) -> bool:
    if usage_mode not in CASTING_USAGE_MODES:
        raise ValueError("Phạm vi sử dụng voice không hợp lệ.")
    if require_active and str(voice.get("status") or "active") != "active":
        return False
    profile = normalize_casting_profile(voice.get("casting_profile"))
    if usage_mode not in profile["usage_modes"]:
        return False
    normalized_pool = normalize_voice_pool_id(voice_pool)
    return not normalized_pool or normalized_pool in profile["voice_pools"]


def _normalize_voice_id(value: object) -> str:
    voice_id = str(value or "").strip()
    try:
        return str(uuid.UUID(voice_id))
    except (ValueError, AttributeError):
        pass

    if (
        not voice_id
        or len(voice_id) > MAX_VOICE_ID_LENGTH
        or not SYSTEM_VOICE_ID_PATTERN.fullmatch(voice_id)
    ):
        raise ValueError(
            "Voice ID phải là UUID hoặc mã giọng hệ thống chỉ gồm chữ, số, '_', '.' và '-'."
        )
    return voice_id


def _normalize_provider_voice_id(value: object) -> str:
    provider_voice_id = str(value or "").strip()
    if (
        not provider_voice_id
        or len(provider_voice_id) > MAX_PROVIDER_VOICE_ID_LENGTH
        or any(character in provider_voice_id for character in ("/", "\\", "\x00"))
    ):
        raise ValueError("Mã giọng của nhà cung cấp không hợp lệ.")
    return provider_voice_id


def _default_providers() -> list[dict]:
    return [
        {
            "id": GENMAX_PROVIDER_ID,
            "type": "cloud",
            "display_name": "Genmax",
            "enabled": True,
            "health": "unknown",
            "capabilities": {
                "remote_voices": True,
                "voice_clone": False,
                "billable": True,
                "parallel_jobs": True,
            },
            "config": {},
        },
        {
            "id": OMNIVOICE_PROVIDER_ID,
            "type": "local",
            "display_name": "OmniVoice",
            "enabled": True,
            "health": "unknown",
            "capabilities": {
                "remote_voices": True,
                "voice_clone": True,
                "billable": False,
                "parallel_jobs": False,
            },
            "config": {
                "base_url": "http://127.0.0.1:8011",
                "idle_unload_seconds": 900,
            },
        },
    ]


def _default_config() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "default_voice_id": DEFAULT_VOICE_ID,
        # Kept as an alias while older clients are migrated.
        "active_voice_id": DEFAULT_VOICE_ID,
        "providers": _default_providers(),
        "voices": [
            {
                "id": DEFAULT_VOICE_ID,
                "name": DEFAULT_VOICE_NAME,
                "provider_id": GENMAX_PROVIDER_ID,
                "provider_voice_id": DEFAULT_VOICE_ID,
                "status": "active",
                "revision": 1,
                "config": {},
                "casting_profile": normalize_casting_profile({}),
            }
        ],
    }


def _legacy_to_v2(data: dict) -> dict:
    legacy_voices = data.get("voices") if isinstance(data, dict) else None
    if not isinstance(legacy_voices, list) or not legacy_voices:
        return _default_config()
    default_voice_id = str(
        data.get("default_voice_id") or data.get("active_voice_id") or ""
    ).strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "default_voice_id": default_voice_id,
        "active_voice_id": default_voice_id,
        "providers": _default_providers(),
        "voices": [
            {
                "id": raw_voice.get("id", ""),
                "name": raw_voice.get("name", ""),
                "provider_id": GENMAX_PROVIDER_ID,
                "provider_voice_id": raw_voice.get("id", ""),
                "status": "active",
                "revision": 1,
                "config": {},
                "casting_profile": normalize_casting_profile({}),
            }
            for raw_voice in legacy_voices
            if isinstance(raw_voice, dict)
        ],
    }


def _validate_public_config(config: object, label: str) -> dict:
    if not isinstance(config, dict):
        raise ValueError(f"Cấu hình {label} phải là một object.")
    for key in config:
        normalized_key = str(key).strip().casefold()
        if any(secret_key in normalized_key for secret_key in SECRET_CONFIG_KEYS):
            raise ValueError("Không được lưu thông tin bí mật trong voices.json.")
    try:
        json.dumps(config, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cấu hình {label} không thể lưu thành JSON.") from exc
    return deepcopy(config)


def validate_voice_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Voice configuration must be an object.")
    if int(data.get("schema_version") or 1) < SCHEMA_VERSION:
        data = _legacy_to_v2(data)

    raw_providers = data.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raw_providers = _default_providers()
    if len(raw_providers) > MAX_PROVIDER_OPTIONS:
        raise ValueError(f"Chỉ được cấu hình tối đa {MAX_PROVIDER_OPTIONS} nhà cung cấp.")

    providers = []
    provider_ids = set()
    for raw_provider in raw_providers:
        if not isinstance(raw_provider, dict):
            raise ValueError("Thông tin nhà cung cấp TTS không hợp lệ.")
        provider_id = str(raw_provider.get("id") or "").strip().casefold()
        if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError("Provider ID không hợp lệ.")
        if provider_id in provider_ids:
            raise ValueError("Provider ID không được trùng nhau.")
        provider_ids.add(provider_id)
        display_name = str(raw_provider.get("display_name") or "").strip()
        if not display_name:
            raise ValueError("Tên nhà cung cấp TTS không được để trống.")
        providers.append(
            {
                "id": provider_id,
                "type": str(raw_provider.get("type") or "external").strip(),
                "display_name": display_name,
                "enabled": bool(raw_provider.get("enabled", True)),
                "health": str(raw_provider.get("health") or "unknown").strip(),
                "capabilities": _validate_public_config(
                    raw_provider.get("capabilities") or {}, "capability"
                ),
                "config": _validate_public_config(
                    raw_provider.get("config") or {}, "nhà cung cấp"
                ),
            }
        )

    raw_voices = data.get("voices")
    if not isinstance(raw_voices, list) or not raw_voices:
        raise ValueError("Phải có ít nhất một giọng đọc.")
    if len(raw_voices) > MAX_VOICE_OPTIONS:
        raise ValueError(f"Chỉ được cấu hình tối đa {MAX_VOICE_OPTIONS} giọng đọc.")

    voices = []
    seen_ids = set()
    seen_provider_keys = set()
    for raw_voice in raw_voices:
        if not isinstance(raw_voice, dict):
            raise ValueError("Thông tin giọng đọc không hợp lệ.")
        voice_name = str(raw_voice.get("name", "")).strip()
        if not voice_name:
            raise ValueError("Tên giọng đọc không được để trống.")
        try:
            voice_id = _normalize_voice_id(raw_voice.get("id", ""))
            provider_voice_id = _normalize_provider_voice_id(
                raw_voice.get("provider_voice_id") or raw_voice.get("id")
            )
        except ValueError as exc:
            raise ValueError(f"Giọng '{voice_name}' không hợp lệ. {exc}") from exc
        provider_id = str(raw_voice.get("provider_id") or GENMAX_PROVIDER_ID).strip().casefold()
        if provider_id not in provider_ids:
            raise ValueError(f"Nhà cung cấp của giọng '{voice_name}' không tồn tại.")
        status = str(raw_voice.get("status") or "active").strip().casefold()
        if status not in VOICE_STATUSES:
            raise ValueError(f"Trạng thái giọng '{voice_name}' không hợp lệ.")
        try:
            revision = max(1, int(raw_voice.get("revision") or 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Revision của giọng '{voice_name}' không hợp lệ.") from exc
        if voice_id in seen_ids:
            raise ValueError("Voice ID nội bộ không được trùng nhau.")
        provider_key = (provider_id, provider_voice_id.casefold())
        if provider_key in seen_provider_keys:
            raise ValueError("Mã giọng trong cùng nhà cung cấp không được trùng nhau.")
        seen_ids.add(voice_id)
        seen_provider_keys.add(provider_key)
        voices.append(
            {
                "id": voice_id,
                "name": voice_name,
                "provider_id": provider_id,
                "provider_voice_id": provider_voice_id,
                "status": status,
                "revision": revision,
                "config": _validate_public_config(raw_voice.get("config") or {}, "giọng"),
                "casting_profile": normalize_casting_profile(
                    raw_voice.get("casting_profile")
                ),
            }
        )

    default_voice_id = _normalize_voice_id(
        data.get("default_voice_id") or data.get("active_voice_id") or ""
    )
    default_voice = next((voice for voice in voices if voice["id"] == default_voice_id), None)
    if default_voice is None:
        raise ValueError("Giọng mặc định phải nằm trong danh sách giọng đọc.")
    if default_voice["status"] != "active":
        raise ValueError("Giọng mặc định phải ở trạng thái hoạt động.")
    return {
        "schema_version": SCHEMA_VERSION,
        "default_voice_id": default_voice_id,
        "active_voice_id": default_voice_id,
        "providers": providers,
        "voices": voices,
    }


def _backup_legacy_config(raw_data: dict) -> None:
    if int(raw_data.get("schema_version") or 1) >= SCHEMA_VERSION:
        return
    backup_path = VOICE_CONFIG_PATH.with_name(f"{VOICE_CONFIG_PATH.name}.v1.bak")
    if VOICE_CONFIG_PATH.exists() and not backup_path.exists():
        shutil.copy2(VOICE_CONFIG_PATH, backup_path)


def load_voice_config() -> dict:
    if not VOICE_CONFIG_PATH.exists():
        return save_voice_config(_default_config())
    try:
        raw_data = json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))
        config = validate_voice_config(raw_data)
        if int(raw_data.get("schema_version") or 1) < SCHEMA_VERSION:
            _backup_legacy_config(raw_data)
            save_voice_config(config)
        return config
    except (OSError, json.JSONDecodeError, ValueError):
        return _default_config()


def save_voice_config(data: dict) -> dict:
    config = validate_voice_config(data)
    VOICE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_private_text(
        VOICE_CONFIG_PATH,
        json.dumps(config, ensure_ascii=False, indent=2),
    )
    return config


def get_provider(provider_id: str) -> dict:
    normalized_id = str(provider_id or "").strip().casefold()
    for provider in load_voice_config()["providers"]:
        if provider["id"] == normalized_id:
            return provider
    raise ValueError("Nhà cung cấp TTS không tồn tại trong cấu hình.")


def get_voice(voice_id: str = "", *, include_inactive: bool = True) -> dict:
    config = load_voice_config()
    selected_id = voice_id.strip() if voice_id else config["default_voice_id"]
    for voice in config["voices"]:
        if voice["id"] == selected_id:
            if not include_inactive and voice["status"] != "active":
                raise ValueError("Giọng đọc đã chọn hiện không hoạt động.")
            return voice
    raise ValueError("Giọng đọc đã chọn không có trong cấu hình.")


def build_voice_snapshot(voice: dict) -> dict:
    voice_id = str(voice.get("id") or "").strip()
    provider_id = str(
        voice.get("provider_id") or GENMAX_PROVIDER_ID
    ).strip().casefold()
    raw_config = deepcopy(voice.get("config") or {})
    if provider_id == OMNIVOICE_PROVIDER_ID:
        effective_config = {
            **OMNIVOICE_DEFAULT_VOICE_CONFIG,
            **raw_config,
        }
        effective_config["engine_revision"] = max(
            OMNIVOICE_ENGINE_REVISION,
            int(effective_config.get("engine_revision") or OMNIVOICE_ENGINE_REVISION),
        )
        effective_config["denoise"] = False
    else:
        effective_config = raw_config
    return {
        "voice_id": voice_id,
        "voice_name": str(voice.get("name") or "Giọng đã lưu").strip(),
        # A missing provider denotes a pre-v2 row, which was always Genmax.
        "provider_id": provider_id,
        "provider_voice_id": str(
            voice.get("provider_voice_id") or voice_id
        ).strip(),
        "voice_revision": int(voice.get("revision") or 1),
        "config": effective_config,
        "casting_profile": normalize_casting_profile(
            voice.get("casting_profile")
        ),
    }
