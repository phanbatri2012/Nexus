"""Shared runtime signals for unattended ChatGPT browser jobs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


ATTENTION_REQUIRED_MARKER = "###CHATGPT_ATTENTION_REQUIRED###"
CHATGPT_LOGIN_REQUIRED_MESSAGE = "Phiên đăng nhập ChatGPT đã hết hạn."
logger = logging.getLogger(__name__)


_profile_lock = threading.Lock()
_state_lock = threading.Lock()
_operation = ""
_prompt_version = ""
_video_id: int | None = None
_finish_callback: Callable[[], None] | None = None


class ChatGPTAttentionRequiredError(RuntimeError):
    """Raised when a person must repair the saved ChatGPT browser session."""


class ChatGPTPreSendTransientError(RuntimeError):
    """Raised when a transient ChatGPT failure occurs before prompt submission."""


def is_chatgpt_login_required(error: BaseException | str) -> bool:
    """Return whether Playwright positively identified an expired login."""
    return str(error or "").strip().startswith(CHATGPT_LOGIN_REQUIRED_MESSAGE)


def encode_attention_error(message: str) -> str:
    """Encode an attention error so a subprocess boundary preserves its type."""
    return f"{ATTENTION_REQUIRED_MARKER}{str(message).strip()}"


def decode_attention_error(message: str) -> str | None:
    """Return the user-facing message when stderr contains the marker."""
    value = str(message or "")
    if ATTENTION_REQUIRED_MARKER not in value:
        return None
    return value.rsplit(ATTENTION_REQUIRED_MARKER, 1)[1].strip()


def try_start_operation(
    operation: str,
    prompt_version: str = "",
    video_id: int | None = None,
) -> bool:
    """Acquire the single authenticated ChatGPT profile without waiting."""
    global _operation, _prompt_version, _video_id
    if not _profile_lock.acquire(blocking=False):
        return False
    with _state_lock:
        _operation = str(operation or "").strip()
        _prompt_version = str(prompt_version or "").strip()
        _video_id = int(video_id) if video_id is not None else None
    return True


def finish_operation() -> None:
    """Release the shared ChatGPT profile after a successful acquisition."""
    global _operation, _prompt_version, _video_id
    with _state_lock:
        _operation = ""
        _prompt_version = ""
        _video_id = None
    if _profile_lock.locked():
        _profile_lock.release()
    with _state_lock:
        callback = _finish_callback
    if callback is not None:
        try:
            callback()
        except Exception:
            # Releasing the profile must never fail because a queue wake-up did.
            logger.exception("ChatGPT release callback failed.")


def set_finish_callback(callback: Callable[[], None] | None) -> None:
    """Register the process-level queue wake-up invoked after every release."""
    global _finish_callback
    with _state_lock:
        _finish_callback = callback


def is_busy() -> bool:
    return _profile_lock.locked()


def get_operation() -> str:
    with _state_lock:
        return _operation


def get_state() -> tuple[str, str]:
    with _state_lock:
        return _operation, _prompt_version


def set_video_id(video_id: int) -> None:
    global _video_id
    with _state_lock:
        _video_id = int(video_id)


def get_video_id() -> int | None:
    with _state_lock:
        return _video_id
