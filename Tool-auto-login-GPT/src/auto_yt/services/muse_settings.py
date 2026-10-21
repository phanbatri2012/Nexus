"""Global configuration for the on-demand Muse browser integration."""

from __future__ import annotations

import json
import logging
import threading

from auto_yt.paths import MUSE_SETTINGS_PATH
from auto_yt.services.secret_store import write_private_text


DEFAULT_MUSE_SETTINGS = {
    "gpm_profile_id": "",
}
MUSE_GPM_PROFILE_STORAGE_KEY = "muse_gpm_profile_id"

logger = logging.getLogger(__name__)
_settings_lock = threading.RLock()


def normalize_muse_settings(value: object) -> dict:
    settings = value if isinstance(value, dict) else {}
    return {
        "gpm_profile_id": str(
            settings.get(MUSE_GPM_PROFILE_STORAGE_KEY)
            or settings.get("gpm_profile_id")
            or ""
        ).strip()[:120],
    }


def load_muse_settings() -> dict:
    with _settings_lock:
        if not MUSE_SETTINGS_PATH.is_file():
            return dict(DEFAULT_MUSE_SETTINGS)
        try:
            value = json.loads(MUSE_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Không thể đọc cấu hình Muse; dùng mặc định: %s", type(exc).__name__)
            return dict(DEFAULT_MUSE_SETTINGS)
        return normalize_muse_settings(value)


def save_muse_settings(value: object) -> dict:
    normalized = normalize_muse_settings(value)
    storage_value = {
        MUSE_GPM_PROFILE_STORAGE_KEY: normalized["gpm_profile_id"],
    }
    with _settings_lock:
        write_private_text(
            MUSE_SETTINGS_PATH,
            json.dumps(storage_value, ensure_ascii=False, indent=2) + "\n",
        )
    return normalized
