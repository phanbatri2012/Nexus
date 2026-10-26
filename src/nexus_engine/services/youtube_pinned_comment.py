"""Durable post-publication workflow for posting and pinning one channel comment."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import urllib.parse
from collections.abc import Callable

from nexus_engine.services import database as db
from nexus_engine.services import gpm_service
from nexus_engine.services import gpm_youtube_automation
from nexus_engine.services import security_logging
from nexus_engine.services import youtube_comments
from nexus_engine.services.channel_scanner_service import (
    BrowserAutomationBlocked,
    BrowserAutomationTemporarilyUnavailable,
)
from nexus_engine.services.proxy_utils import parse_proxy_url


JOB_TYPE = "youtube_pinned_comment"
PUBLIC_DELAY = dt.timedelta(hours=1)
MAX_EXECUTION_ATTEMPTS = 6
RETRY_WINDOW = dt.timedelta(hours=24)
RETRY_DELAYS_SECONDS = (300, 900, 3_600, 14_400, 43_200)
MAX_SCHEDULE_REBASES = 3
MAX_JOB_AGE = dt.timedelta(days=30)


class PinnedCommentAttentionRequired(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "pinned_comment_attention")
        super().__init__(message)


class PinnedCommentTransientError(RuntimeError):
    pass


def _parse_datetime(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def _proxy_identity(raw_proxy: str) -> tuple[str, str, int, str, str] | None:
    normalized = parse_proxy_url(raw_proxy)
    if not normalized:
        return None
    try:
        parsed = urllib.parse.urlsplit(normalized)
        if not parsed.hostname or not parsed.port:
            return None
        return (
            parsed.scheme.casefold(),
            parsed.hostname.casefold(),
            int(parsed.port),
            urllib.parse.unquote(parsed.username or ""),
            urllib.parse.unquote(parsed.password or ""),
        )
    except ValueError:
        return None


def delayed_run_at(anchor: str | dt.datetime) -> str:
    parsed = anchor if isinstance(anchor, dt.datetime) else _parse_datetime(anchor)
    if parsed is None:
        raise ValueError("Thời điểm Public/đặt lịch không hợp lệ.")
    return _iso(parsed + PUBLIC_DELAY)


def resolve_public_at(
    publication: dict,
    remote: dict | None = None,
    *,
    observed_at: str | None = None,
) -> str:
    """Choose a conservative public anchor, ignoring upload timestamps before a schedule."""
    observed = _parse_datetime(observed_at) or _utc_now()
    remote = remote or {}
    scheduled_candidates = [
        _parse_datetime(remote.get("scheduled_publish_at")),
        _parse_datetime(publication.get("scheduled_at")),
    ]
    usable_schedules = [
        value
        for value in scheduled_candidates
        if value is not None and value <= observed
    ]
    if str(publication.get("privacy_status") or "").strip().lower() != "public":
        return _iso(max(usable_schedules) if usable_schedules else observed)
    candidates = [
        _parse_datetime(remote.get("published_at")),
        _parse_datetime(publication.get("published_at")),
        *usable_schedules,
    ]
    usable = [value for value in candidates if value is not None and value <= observed]
    return _iso(max(usable) if usable else observed)


def enqueue_for_publication(
    publication_id: int,
    *,
    public_at: str = "",
    scheduled_at: str = "",
    reschedule_existing: bool = False,
) -> tuple[dict, bool]:
    anchor = str(public_at or scheduled_at or "").strip()
    if not anchor:
        raise ValueError("Thiếu thời điểm Public hoặc lịch đăng YouTube.")
    return db.ensure_youtube_pinned_comment_job(
        publication_id=int(publication_id),
        run_at=delayed_run_at(anchor),
        public_at=str(public_at or ""),
        scheduled_at=str(scheduled_at or ""),
        reschedule_existing=reschedule_existing,
    )


def _pause_for_attention(job: dict, code: str, message: str) -> dict:
    safe_error = security_logging.redact_sensitive(str(message or ""))
    latest = db.get_system_job(str(job["id"])) or job
    result = dict(latest.get("result") or {})
    result.update(
        {
            "attention_required": str(code or "pinned_comment_attention"),
            "stage": str(result.get("stage") or "attention_required"),
        }
    )
    updated = db.update_system_job(
        str(job["id"]),
        status="paused",
        progress=safe_error or "Bình luận ghim cần được kiểm tra thủ công",
        result_json=result,
        error=safe_error,
        next_retry_at="",
        finished_at="",
        cancel_requested=0,
    )
    publication_id = int((latest.get("payload") or {}).get("publication_id") or 0)
    if publication_id and db.get_video_publication(publication_id):
        db.update_video_publication(
            publication_id,
            pinned_comment_status="attention_required",
        )
    return updated or latest


def _begin_execution_attempt(job: dict) -> tuple[dict, dict, dt.datetime]:
    latest = db.get_system_job(str(job["id"])) or job
    result = dict(latest.get("result") or {})
    now = _utc_now()
    first_attempt = _parse_datetime(result.get("first_attempt_at")) or now
    deadline = _parse_datetime(result.get("retry_deadline_at")) or (
        first_attempt + RETRY_WINDOW
    )
    attempts = max(0, int(result.get("execution_attempts") or 0))
    if attempts >= MAX_EXECUTION_ATTEMPTS or now >= deadline:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_retry_exhausted",
            "Đã hết ngân sách 6 lần/24 giờ cho bình luận ghim.",
        )
    result.update(
        {
            "execution_attempts": attempts + 1,
            "first_attempt_at": _iso(first_attempt),
            "retry_deadline_at": _iso(deadline),
            "stage": str(result.get("stage") or "preflight"),
        }
    )
    latest = db.update_system_job(
        str(job["id"]),
        result_json=result,
        progress="Đang kiểm tra video trước khi đăng bình luận ghim",
    ) or latest
    return latest, result, deadline


def _reschedule_for_remote_timing(
    job: dict,
    publication: dict,
    remote: dict,
    *,
    target_public_at: str,
) -> dict:
    latest = db.get_system_job(str(job["id"])) or job
    payload = dict(latest.get("payload") or {})
    created_at = _parse_datetime(latest.get("created_at")) or _utc_now()
    rebase_count = max(0, int(payload.get("schedule_rebase_count") or 0))
    if rebase_count >= MAX_SCHEDULE_REBASES or _utc_now() - created_at >= MAX_JOB_AGE:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_schedule_changed",
            "Lịch Public đã thay đổi quá giới hạn; cần kiểm tra thủ công.",
        )
    payload.update(
        {
            "public_at": target_public_at if remote.get("privacy_status") == "public" else "",
            "scheduled_at": str(remote.get("scheduled_publish_at") or ""),
            "target_run_at": delayed_run_at(target_public_at),
            "schedule_rebase_count": rebase_count + 1,
        }
    )
    result = dict(latest.get("result") or {})
    for key in ("execution_attempts", "first_attempt_at", "retry_deadline_at"):
        result.pop(key, None)
    result["stage"] = "waiting_for_public"
    db.update_video_publication(
        int(publication["id"]),
        privacy_status=str(remote.get("privacy_status") or publication.get("privacy_status") or ""),
        scheduled_at=str(remote.get("scheduled_publish_at") or ""),
    )
    return db.update_system_job(
        str(job["id"]),
        status="retry_wait",
        progress="YouTube đã đổi thời điểm Public; đã hẹn lại bình luận ghim",
        payload_json=payload,
        result_json=result,
        next_retry_at=payload["target_run_at"],
        started_at="",
        finished_at="",
        error="",
    ) or latest


def _raise_youtube_failure(exc: youtube_comments.YouTubeCommentsError) -> None:
    if exc.reason in {
        "commentsDisabled",
        "forbidden",
        "insufficientPermissions",
        "ineligibleAccount",
    }:
        raise PinnedCommentAttentionRequired(
            f"youtube_{exc.reason or 'comment_forbidden'}", str(exc)
        ) from exc
    if exc.transient:
        raise PinnedCommentTransientError(str(exc)) from exc
    raise PinnedCommentAttentionRequired(
        f"youtube_{exc.reason or 'comment_configuration'}", str(exc)
    ) from exc


def execute_job(
    job: dict,
    *,
    access_token_provider: Callable[[int], tuple[dict, str]],
) -> dict:
    payload = dict(job.get("payload") or {})
    publication_id = int(payload.get("publication_id") or 0)
    publication = db.get_video_publication(publication_id) if publication_id else None
    if not publication:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_publication_missing",
            "Publication không còn tồn tại; đã dừng job bình luận ghim.",
        )
    video = db.get_video(int(publication["video_id"]))
    if not video or video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_video_unavailable",
            "Video nội bộ không còn sẵn sàng để đăng bình luận ghim.",
        )
    channel_db_id = int(publication.get("youtube_channel_id") or 0)
    if not channel_db_id:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_channel_missing",
            "Publication chưa được gắn với kênh YouTube.",
        )

    channel = db.get_youtube_channel(channel_db_id)
    if not channel:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_channel_missing",
            "Không tìm thấy cấu hình kênh YouTube của publication.",
        )
    expected_channel_id = str(channel.get("channel_id") or "").strip()
    profile_id = str(channel.get("gpm_profile_id") or "").strip()
    raw_proxy = str(channel.get("gpm_proxy_info") or "").strip()
    if not expected_channel_id:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_channel_identity_missing",
            "Kênh YouTube chưa có Channel ID để đối chiếu.",
        )
    if not profile_id:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_missing",
            "Kênh chưa được gán GPM profile để ghim bình luận.",
        )
    if profile_id.startswith("local_"):
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_required",
            "Bình luận ghim bắt buộc dùng GPM profile riêng của kênh.",
        )
    if not parse_proxy_url(raw_proxy):
        raise PinnedCommentAttentionRequired(
            "pinned_comment_proxy_missing",
            "Kênh chưa có proxy hợp lệ; job đã fail closed.",
        )

    latest, result, _deadline = _begin_execution_attempt(job)
    payload = dict(latest.get("payload") or payload)
    try:
        profile_detail = gpm_service.get_gpm_profile_detail(profile_id)
    except gpm_service.GpmProfileNotFoundError as exc:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_missing",
            "GPM profile của kênh không còn tồn tại.",
        ) from exc
    except gpm_service.GpmConnectionError as exc:
        raise PinnedCommentTransientError(
            "GPM Local API tạm thời không phản hồi khi kiểm tra proxy profile."
        ) from exc
    actual_proxy = str(profile_detail.get("raw_proxy") or "").strip()
    actual_proxy_identity = _proxy_identity(actual_proxy)
    if not actual_proxy_identity:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_proxy_missing",
            "GPM profile không có proxy hợp lệ; browser trực tiếp đã bị chặn.",
        )
    if actual_proxy_identity != _proxy_identity(raw_proxy):
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_proxy_mismatch",
            "Proxy thực tế của GPM profile không khớp proxy API của kênh.",
        )
    try:
        channel, access_token = access_token_provider(channel_db_id)
    except youtube_comments.YouTubeCommentsError as exc:
        _raise_youtube_failure(exc)
        raise AssertionError("unreachable")
    youtube_video_id = str(
        publication.get("youtube_video_id") or payload.get("youtube_video_id") or ""
    ).strip()
    try:
        remote = youtube_comments.get_video_details(
            access_token,
            youtube_video_id,
            proxy=raw_proxy,
        )
    except youtube_comments.YouTubeCommentsError as exc:
        _raise_youtube_failure(exc)
        raise AssertionError("unreachable")
    if str(remote.get("channel_id") or "") != expected_channel_id:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_channel_mismatch",
            "Video YouTube không thuộc kênh được gán cho publication.",
        )

    now = _utc_now()
    remote_privacy = str(remote.get("privacy_status") or "").strip().lower()
    remote_schedule = _parse_datetime(remote.get("scheduled_publish_at"))
    if remote_privacy != "public":
        if remote_schedule and remote_schedule > now:
            return _reschedule_for_remote_timing(
                latest,
                publication,
                remote,
                target_public_at=_iso(remote_schedule),
            )
        raise PinnedCommentTransientError(
            "YouTube chưa xác nhận video ở trạng thái Public."
        )

    public_anchor = str(payload.get("public_at") or "").strip()
    if not public_anchor:
        anchor_publication = {
            **publication,
            "scheduled_at": str(payload.get("scheduled_at") or publication.get("scheduled_at") or ""),
        }
        public_anchor = resolve_public_at(
            anchor_publication,
            remote,
            observed_at=_iso(now),
        )
    due_at = _parse_datetime(delayed_run_at(public_anchor))
    if due_at and due_at > now:
        return _reschedule_for_remote_timing(
            latest,
            publication,
            remote,
            target_public_at=public_anchor,
        )

    comment_text = str(payload.get("comment_text") or "").strip()
    if not comment_text:
        comment_text = db.extract_generated_video_pinned_comment(
            str(video.get("generated_script") or "")
        )
        try:
            comment_text = youtube_comments.validate_top_level_comment(comment_text)
        except youtube_comments.YouTubeCommentsError as exc:
            raise PinnedCommentAttentionRequired(
                "pinned_comment_metadata_missing", str(exc)
            ) from exc
        payload.update(
            {
                "comment_text": comment_text,
                "comment_text_hash": hashlib.sha256(
                    comment_text.encode("utf-8")
                ).hexdigest(),
                "public_at": public_anchor,
            }
        )
        result["stage"] = "reconciling_comment"
        latest = db.update_system_job(
            str(job["id"]),
            payload_json=payload,
            result_json=result,
            progress="Đã khóa nội dung; đang đối soát bình luận trên YouTube",
        ) or latest

    comment_id = str(publication.get("pinned_comment_id") or "").strip()
    comment_result: dict = {}
    if not comment_id:
        try:
            comment_result = youtube_comments.find_channel_top_level_comment(
                access_token,
                youtube_video_id,
                expected_channel_id,
                comment_text,
                proxy=raw_proxy,
            ) or {}
            if not comment_result:
                comment_result = youtube_comments.publish_top_level_comment(
                    access_token,
                    youtube_video_id,
                    comment_text,
                    proxy=raw_proxy,
                )
        except youtube_comments.YouTubeCommentsError as exc:
            _raise_youtube_failure(exc)
            raise AssertionError("unreachable")
        comment_id = str(comment_result.get("comment_id") or "").strip()
        if not comment_id:
            raise PinnedCommentTransientError(
                "Chưa đối soát được Comment ID sau khi đăng."
            )
        posted_at = str(comment_result.get("published_at") or db.utc_now())
        db.update_video_publication(
            publication_id,
            privacy_status="public",
            published_at=public_anchor,
            scheduled_at="",
            pinned_comment_status="posted",
            pinned_comment_id=comment_id,
            pinned_comment_posted_at=posted_at,
        )
        result.update(
            {
                "stage": "posted",
                "comment_id": comment_id,
                "comment_existing": bool(comment_result.get("existing")),
            }
        )
        latest = db.update_system_job(
            str(job["id"]),
            result_json=result,
            progress="Đã đăng bình luận; đang ghim qua GPM profile",
        ) or latest

    video_url = str(publication.get("published_url") or "").strip()
    if not video_url:
        video_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    try:
        pin_result = asyncio.run(
            gpm_youtube_automation.pin_top_level_comment_via_gpm(
                profile_id,
                video_url=video_url,
                comment_id=comment_id,
                expected_channel_id=expected_channel_id,
            )
        )
    except gpm_youtube_automation.GpmAutomationIdentityError as exc:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_identity", str(exc)
        ) from exc
    except BrowserAutomationBlocked as exc:
        raise PinnedCommentAttentionRequired(
            "pinned_comment_gpm_blocked", str(exc)
        ) from exc
    except (BrowserAutomationTemporarilyUnavailable, gpm_youtube_automation.GpmAutomationError) as exc:
        raise PinnedCommentTransientError(str(exc)) from exc

    if not pin_result.get("pinned"):
        raise PinnedCommentTransientError(
            "GPM chưa xác minh được trạng thái ghim của bình luận."
        )
    pinned_at = db.utc_now()
    db.update_video_publication(
        publication_id,
        pinned_comment_status="pinned",
        pinned_comment_id=comment_id,
        pinned_comment_pinned_at=pinned_at,
    )
    result.update(
        {
            "stage": "pinned",
            "comment_id": comment_id,
            "pinned_at": pinned_at,
            "already_pinned": bool(pin_result.get("already_pinned")),
        }
    )
    return db.update_system_job(
        str(job["id"]),
        status="done",
        progress="Đã đăng và ghim bình luận metadata",
        result_json=result,
        error="",
        next_retry_at="",
        finished_at=db.utc_now(),
        cancel_requested=0,
    ) or latest


def handle_job_failure(job: dict, exc: Exception) -> dict:
    if isinstance(exc, PinnedCommentAttentionRequired):
        return _pause_for_attention(job, exc.code, str(exc))
    if not isinstance(exc, PinnedCommentTransientError):
        return _pause_for_attention(
            job,
            "pinned_comment_unexpected_error",
            security_logging.safe_user_error(
                "youtube_pinned_comment",
                exc,
                "Job bình luận ghim gặp lỗi không xác định.",
            ),
        )

    latest = db.get_system_job(str(job["id"])) or job
    result = dict(latest.get("result") or {})
    attempts = max(0, int(result.get("execution_attempts") or 0))
    deadline = _parse_datetime(result.get("retry_deadline_at"))
    now = _utc_now()
    if attempts >= MAX_EXECUTION_ATTEMPTS or not deadline or now >= deadline:
        return _pause_for_attention(
            latest,
            "pinned_comment_retry_exhausted",
            "Đã hết ngân sách 6 lần/24 giờ cho bình luận ghim.",
        )
    delay_index = attempts - 1
    if delay_index < 0 or delay_index >= len(RETRY_DELAYS_SECONDS):
        return _pause_for_attention(
            latest,
            "pinned_comment_retry_exhausted",
            "Đã hết ngân sách retry cho bình luận ghim.",
        )
    delay_seconds = RETRY_DELAYS_SECONDS[delay_index]
    if now + dt.timedelta(seconds=delay_seconds) > deadline:
        return _pause_for_attention(
            latest,
            "pinned_comment_retry_deadline",
            "Đã quá thời hạn tự phục hồi 24 giờ cho bình luận ghim.",
        )
    safe_error = security_logging.safe_user_error(
        "youtube_pinned_comment_transient",
        exc,
        "Lỗi tạm thời khi đăng hoặc ghim bình luận.",
    )
    return db.schedule_system_job_recovery(
        str(job["id"]),
        resume_from_step=str(result.get("stage") or "pinned_comment"),
        delay_seconds=delay_seconds,
        error=safe_error,
        result_json=result,
        progress=f"Lỗi tạm thời; sẽ thử lại sau {delay_seconds // 60} phút",
    ) or latest


def reset_manual_retry(job: dict) -> dict:
    payload = dict(job.get("payload") or {})
    payload["schedule_rebase_count"] = 0
    result = dict(job.get("result") or {})
    for key in (
        "attention_required",
        "execution_attempts",
        "first_attempt_at",
        "retry_deadline_at",
    ):
        result.pop(key, None)
    publication_id = int(payload.get("publication_id") or 0)
    publication = db.get_video_publication(publication_id) if publication_id else None
    if publication:
        db.update_video_publication(
            publication_id,
            pinned_comment_status=(
                "posted" if str(publication.get("pinned_comment_id") or "").strip() else "waiting"
            ),
        )
    updated = db.update_system_job(
        str(job["id"]),
        status="queued",
        progress="Đang chờ chạy lại bình luận ghim theo yêu cầu",
        payload_json=payload,
        result_json=result,
        error="",
        recovery_count=0,
        next_retry_at="",
        cancel_requested=0,
        started_at="",
        finished_at="",
    )
    if not updated:
        raise ValueError("Không tìm thấy job bình luận ghim.")
    return updated
