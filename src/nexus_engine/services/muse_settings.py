"""Global configuration for the on-demand Muse AI Studio integration."""

from __future__ import annotations

import json
import logging
import threading

from nexus_engine.paths import MUSE_SETTINGS_PATH
from nexus_engine.services.secret_store import write_private_text


DEFAULT_MUSE_STUDIO_PATH = r"E:\yt\Tool\Muse_AI_Studio"
DEFAULT_MUSE_SETTINGS = {
    "muse_studio_path": DEFAULT_MUSE_STUDIO_PATH,
}

logger = logging.getLogger(__name__)
_settings_lock = threading.RLock()


def normalize_muse_settings(value: object) -> dict:
    settings = value if isinstance(value, dict) else {}
    return {
        "muse_studio_path": str(
            settings.get("muse_studio_path") or DEFAULT_MUSE_STUDIO_PATH
        ).strip()[:1024],
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
    with _settings_lock:
        write_private_text(
            MUSE_SETTINGS_PATH,
            json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        )
    return normalized
