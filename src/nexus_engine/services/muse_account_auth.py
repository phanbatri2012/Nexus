"""Encrypted per-account credentials and recovery guards for Muse relogin."""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from nexus_engine.paths import MUSE_ACCOUNT_CREDENTIALS_PATH
from nexus_engine.services.secret_store import (
    DPAPI_PREFIX,
    SecretStorageError,
    decrypt_secret,
    encrypt_secret,
    write_private_text,
)


logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOGIN_METHODS = frozenset({"password", "mailbox"})
RECOVERY_FAILURE_COOLDOWN_SECONDS = 300.0
SECRET_FIELDS = ("password", "two_factor_secret", "mailbox_url")

_document_lock = threading.RLock()
_login_lock = threading.Lock()
_cooldown_lock = threading.Lock()
_last_recovery_failure_at = 0.0


def _read_document() -> dict:
    if not MUSE_ACCOUNT_CREDENTIALS_PATH.exists():
        return {}
    try:
        value = json.loads(MUSE_ACCOUNT_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Không thể đọc cấu hình Muse auto-login: %s", type(exc).__name__)
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_payload(payload: dict) -> dict:
    email = str(payload.get("email") or "").strip()
    method = str(payload.get("method") or "").strip().lower()
    password = str(payload.get("password") or "")
    two_factor_secret = str(payload.get("two_factor_secret") or "").strip()
    mailbox_url = str(payload.get("mailbox_url") or "").strip()
    if not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Email Muse không hợp lệ.")
    if method not in LOGIN_METHODS:
        raise ValueError("Phương thức đăng nhập Muse không hợp lệ.")
    if method == "password":
        if not password:
            raise ValueError("Cần nhập mật khẩu Muse.")
        if mailbox_url:
            raise ValueError("Không gửi link hộp thư khi dùng mật khẩu.")
        mailbox_url = ""
    else:
        if not mailbox_url:
            raise ValueError("Cần nhập link hộp thư Tuan89.")
        if password or two_factor_secret:
            raise ValueError("Không gửi mật khẩu hoặc 2FA khi dùng hộp thư.")
        password = ""
        two_factor_secret = ""
    return {
        "email": email,
        "method": method,
        "password": password,
        "two_factor_secret": two_factor_secret,
        "mailbox_url": mailbox_url,
    }


def validate_credentials(payload: dict) -> dict:
    return _normalize_payload(payload)


def save_credentials(account_id: str, payload: dict) -> dict:
    normalized = _normalize_payload(payload)
    stored = {
        "email": normalized["email"],
        "method": normalized["method"],
        "updated_at": int(time.time()),
    }
    for field in SECRET_FIELDS:
        value = normalized[field]
        if value:
            stored[f"{field}_encrypted"] = encrypt_secret(value)
    with _document_lock:
        document = _read_document()
        document[str(account_id)] = stored
        write_private_text(
            MUSE_ACCOUNT_CREDENTIALS_PATH,
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        )
    return get_status(account_id)


def _raw_entry(account_id: str) -> dict:
    with _document_lock:
        value = _read_document().get(str(account_id))
    return dict(value) if isinstance(value, dict) else {}


def load_credentials(account_id: str) -> dict:
    raw = _raw_entry(account_id)
    if not raw:
        return {}
    if any(field in raw for field in SECRET_FIELDS):
        raise SecretStorageError("Muse credential store contains plaintext secrets.")
    email = str(raw.get("email") or "").strip()
    method = str(raw.get("method") or "").strip().lower()
    if not EMAIL_PATTERN.fullmatch(email) or method not in LOGIN_METHODS:
        raise SecretStorageError("Muse credential metadata is invalid.")
    credentials = {
        "email": email,
        "method": method,
        "password": "",
        "two_factor_secret": "",
        "mailbox_url": "",
    }
    required = "password" if method == "password" else "mailbox_url"
    for field in SECRET_FIELDS:
        stored_value = str(raw.get(f"{field}_encrypted") or "")
        if stored_value and not stored_value.startswith(DPAPI_PREFIX):
            raise SecretStorageError("Muse credential is not protected by DPAPI.")
        credentials[field] = decrypt_secret(stored_value) if stored_value else ""
    if not credentials[required]:
        raise SecretStorageError("Muse credential store is incomplete.")
    return credentials


def get_status(account_id: str) -> dict:
    raw = _raw_entry(account_id)
    method = str(raw.get("method") or "").strip().lower()
    required_field = "password" if method == "password" else "mailbox_url"
    required_value = str(raw.get(f"{required_field}_encrypted") or "")
    configured = (
        method in LOGIN_METHODS
        and bool(EMAIL_PATTERN.fullmatch(str(raw.get("email") or "").strip()))
        and required_value.startswith(DPAPI_PREFIX)
        and not any(field in raw for field in SECRET_FIELDS)
    )
    return {
        "auto_login_configured": configured,
        "auto_login_method": method if configured else "",
        "login_email": str(raw.get("email") or "").strip() if configured else "",
        "totp_configured": bool(
            configured
            and str(raw.get("two_factor_secret_encrypted") or "").startswith(DPAPI_PREFIX)
        ),
    }


def list_statuses() -> dict[str, dict]:
    with _document_lock:
        account_ids = list(_read_document())
    return {str(account_id): get_status(str(account_id)) for account_id in account_ids}


def delete_credentials(account_id: str) -> bool:
    with _document_lock:
        document = _read_document()
        removed = document.pop(str(account_id), None) is not None
        if document:
            write_private_text(
                MUSE_ACCOUNT_CREDENTIALS_PATH,
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            )
        else:
            MUSE_ACCOUNT_CREDENTIALS_PATH.unlink(missing_ok=True)
    return removed


def acquire_login_slot() -> bool:
    return _login_lock.acquire(blocking=False)


def release_login_slot() -> None:
    if _login_lock.locked():
        _login_lock.release()


def recovery_cooldown_remaining() -> float:
    with _cooldown_lock:
        if _last_recovery_failure_at <= 0:
            return 0.0
        elapsed = time.monotonic() - _last_recovery_failure_at
    return max(0.0, RECOVERY_FAILURE_COOLDOWN_SECONDS - elapsed)


def record_recovery_failure() -> None:
    global _last_recovery_failure_at
    with _cooldown_lock:
        _last_recovery_failure_at = time.monotonic()


def clear_recovery_failure() -> None:
    global _last_recovery_failure_at
    with _cooldown_lock:
        _last_recovery_failure_at = 0.0
