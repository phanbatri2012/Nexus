"""Trust Safety Shield & Blacklist Engine for YouTube Trust Builder.

Protects channel warmup workflows from interacting with anti-state, subversive,
hostile, or toxic channels and content violating Vietnamese cybersecurity standards.
Supports 3-tier defense (Channel Blacklist, Lexical Filter, Action Gatekeeper),
offline bundled fallback, and periodic/1-click remote blacklist sync.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from auto_yt.services import database as db, security_logging

logger = logging.getLogger(__name__)

# Bundled fallback core blacklist (pre-loaded offline)
DEFAULT_CORE_CHANNELS: list[dict[str, str]] = [
    {"handle": "@viettan", "name": "Việt Tân", "reason": "Tổ chức phản động / chống phá"},
    {"handle": "@viet_tan", "name": "Việt Tân", "reason": "Tổ chức phản động / chống phá"},
    {"handle": "@N10TV", "name": "N10Tv", "reason": "Kênh chống phá / kích động"},
    {"handle": "@truongquochuy", "name": "Trương Quốc Huy", "reason": "Kênh chống phá / kích động"},
    {"handle": "@kumahuy", "name": "KumaHuy", "reason": "Kênh chống phá / kích động"},
    {"handle": "@thoibaode", "name": "Thoibao.de", "reason": "Xuyên tạc chính trị / chống phá"},
    {"handle": "@letrungkhoa", "name": "Lê Trung Khoa", "reason": "Xuyên tạc chính trị / chống phá"},
    {"handle": "@RFAVietnamese", "name": "Đài Á Châu Tự Do", "reason": "Truyền thông định kiến / chống phá"},
    {"handle": "@rfatiengviet", "name": "RFA Tiếng Việt", "reason": "Truyền thông định kiến / chống phá"},
    {"handle": "@VOATiengViet", "name": "VOA Tiếng Việt", "reason": "Truyền thông định kiến / chống phá"},
    {"handle": "@BBCNewsTiengViet", "name": "BBC News Tiếng Việt", "reason": "Truyền thông định kiến"},
    {"handle": "@bbcvietnamese", "name": "BBC Tiếng Việt", "reason": "Truyền thông định kiến"},
    {"handle": "@chantroimoi", "name": "Chân Trời Mới Media", "reason": "Tổ chức phản động / chống phá"},
    {"handle": "@radiodlsn", "name": "Đáp Lời Sông Núi", "reason": "Tổ chức phản động / chống phá"},
    {"handle": "@daploisongnui", "name": "Đáp Lời Sông Núi", "reason": "Tổ chức phản động / chống phá"},
    {"handle": "@luatkhoatappin", "name": "Luật Khoa tạp chí", "reason": "Xuyên tạc thể chế"},
    {"handle": "@luatkhoa", "name": "Luật Khoa", "reason": "Xuyên tạc thể chế"},
    {"handle": "@nguoivietdaily", "name": "Người Việt Daily News", "reason": "Truyền thông chống phá hải ngoại"},
    {"handle": "@saigontv", "name": "Saigon TV", "reason": "Truyền thông chống phá hải ngoại"},
    {"handle": "@calitoday", "name": "Cali Today News", "reason": "Truyền thông chống phá hải ngoại"},
    {"handle": "@baocalitoday", "name": "Báo Cali Today", "reason": "Truyền thông chống phá hải ngoại"},
    {"handle": "@chauxuannguyen", "name": "Châu Xuân Nguyễn", "reason": "Kênh chống phá"},
    {"handle": "@nhatkyech", "name": "Nhật Ký Yêu Nước", "reason": "Kênh kích động / chống phá"},
    {"handle": "@tiengdanso", "name": "Tiếng Dân News", "reason": "Kênh chống phá"},
    {"handle": "@hoangchidung", "name": "Hoàng Chí Dũng", "reason": "Kênh chống phá"},
    {"handle": "@nguoibuongio", "name": "Người Buôn Gió", "reason": "Kênh chống phá / xuyên tạc"},
    {"handle": "@vietlivetv", "name": "Viet Live TV", "reason": "Truyền thông chống phá hải ngoại"},
]

DEFAULT_CORE_KEYWORDS: list[dict[str, str]] = [
    {"keyword": "việt cộng", "reason": "Thuật ngữ miệt thị / thù địch"},
    {"keyword": "viet cong", "reason": "Thuật ngữ miệt thị / thù địch"},
    {"keyword": "đảng cướp", "reason": "Xuyên tạc chính quyền"},
    {"keyword": "xứ vẹm", "reason": "Miệt thị quốc gia"},
    {"keyword": "bò đỏ", "reason": "Kích động chia rẽ / thù địch"},
    {"keyword": "quốc hận", "reason": "Tuyên truyền chống phá"},
    {"keyword": "đu càng", "reason": "Kích động thù hằn"},
    {"keyword": "lật đổ chế độ", "reason": "Kích động bạo loạn / lật đổ"},
    {"keyword": "tam quyền phân lập", "reason": "Tuyên truyền chống phá thể chế"},
    {"keyword": "đa nguyên đa đảng", "reason": "Tuyên truyền chống phá thể chế"},
    {"keyword": "chính quyền bù nhìn", "reason": "Xuyên tạc lịch sử"},
    {"keyword": "chế độ độc tài", "reason": "Xuyên tạc chính trị"},
    {"keyword": "bán nước", "reason": "Xuyên tạc chính quyền"},
    {"keyword": "buôn dân", "reason": "Xuyên tạc chính quyền"},
    {"keyword": "tự chuyển hóa", "reason": "Kích động chống phá"},
    {"keyword": "bạo loạn lật đổ", "reason": "Kích động bạo loạn"},
    {"keyword": "cờ vàng ba sọc", "reason": "Biểu tượng chống phá"},
    {"keyword": "hội cờ vàng", "reason": "Tổ chức chống phá hải ngoại"},
    {"keyword": "ba que xỏ lá", "reason": "Kích động chia rẽ dân tộc"},
]

DEFAULT_CORE_PATTERNS: list[dict[str, str]] = [
    {"pattern": r"\b(cs|dang)\s*c(u|ướ)p\b", "reason": "Xuyên tạc chính quyền"},
    {"pattern": r"\b(lat|lật)\s*do\s*che\s*do\b", "reason": "Kích động lật đổ"},
    {"pattern": r"\bquoc\s*han\s*30/?4\b", "reason": "Tuyên truyền chống phá"},
    {"pattern": r"\bco\s*vang\s*ba\s*soc\b", "reason": "Biểu tượng chống phá"},
    {"pattern": r"\bxu\s*vem\b", "reason": "Miệt thị thể chế"},
]


def ensure_default_safety_rules_seeded() -> None:
    """Seed bundled default core rules into SQLite if database table is empty."""
    try:
        existing = db.list_safety_blacklist_entries(limit=1)
        if existing:
            return
        logger.info("Khởi tạo danh mục Core Safety Blacklist mặc định vào Database...")
        entries_to_insert = []
        for ch in DEFAULT_CORE_CHANNELS:
            entries_to_insert.append({
                "entry_type": "channel",
                "entry_value": ch["handle"],
                "reason": ch["reason"],
                "is_custom": 0,
                "is_enabled": True,
            })
            if ch["name"] and ch["name"] != ch["handle"]:
                entries_to_insert.append({
                    "entry_type": "channel",
                    "entry_value": ch["name"],
                    "reason": ch["reason"],
                    "is_custom": 0,
                    "is_enabled": True,
                })
        for kw in DEFAULT_CORE_KEYWORDS:
            entries_to_insert.append({
                "entry_type": "keyword",
                "entry_value": kw["keyword"],
                "reason": kw["reason"],
                "is_custom": 0,
                "is_enabled": True,
            })
        for pt in DEFAULT_CORE_PATTERNS:
            entries_to_insert.append({
                "entry_type": "regex_pattern",
                "entry_value": pt["pattern"],
                "reason": pt["reason"],
                "is_custom": 0,
                "is_enabled": True,
            })
        db.upsert_safety_blacklist_bulk(entries_to_insert)
        logger.info("Đã nạp thành công %d quy tắc Core Safety Blacklist ban đầu.", len(entries_to_insert))
    except Exception as exc:
        logger.warning("Không thể nạp Core Safety Blacklist ban đầu: %s", exc)


def is_safe_for_interaction(
    title: str = "",
    channel_name: str = "",
    channel_url: str = "",
    channel_handle: str = "",
) -> tuple[bool, str]:
    """Evaluate candidate video and channel against the VN Safety Shield.
    
    Returns (is_safe: bool, reason: str). If not safe, reason explains the blocked rule.
    """
    config = db.get_safety_config()
    if not config.get("shield_enabled", True):
        return True, ""

    rules = db.get_all_active_safety_rules()
    channels = rules.get("channels") or []
    keywords = rules.get("keywords") or []
    patterns = rules.get("regex_patterns") or []

    # If DB is empty, use bundled constants
    if not channels and not keywords and not patterns:
        ensure_default_safety_rules_seeded()
        rules = db.get_all_active_safety_rules()
        channels = rules.get("channels") or []
        keywords = rules.get("keywords") or []
        patterns = rules.get("regex_patterns") or []

    # Prepare candidate normalized keys
    norm_title = db.normalize_safety_key(title)
    unaccent_title = db.strip_safety_diacritics(norm_title)

    norm_cname = db.normalize_safety_key(channel_name)
    unaccent_cname = db.strip_safety_diacritics(norm_cname)

    norm_curl = db.normalize_safety_key(channel_url)
    unaccent_curl = db.strip_safety_diacritics(norm_curl)

    norm_handle = db.normalize_safety_key(channel_handle)
    unaccent_handle = db.strip_safety_diacritics(norm_handle)

    # 1. Check Channel Blacklist
    for ch_rule in channels:
        val = str(ch_rule.get("entry_value") or "")
        norm_val = str(ch_rule.get("normalized_value") or db.normalize_safety_key(val))
        unaccent_val = db.strip_safety_diacritics(norm_val)
        reason = str(ch_rule.get("reason") or "Kênh thuộc danh mục đen an toàn quốc gia")

        # Match handle or name directly
        if norm_val and (
            norm_val == norm_cname
            or norm_val == norm_handle
            or (len(norm_val) >= 4 and norm_val in norm_cname)
            or (len(norm_val) >= 4 and norm_val in norm_curl)
            or (len(norm_val) >= 4 and norm_val in norm_handle)
        ):
            return False, f"Chặn Kênh: '{val}' ({reason})"

        # Unaccented matching
        if unaccent_val and (
            unaccent_val == unaccent_cname
            or unaccent_val == unaccent_handle
            or (len(unaccent_val) >= 4 and unaccent_val in unaccent_cname)
            or (len(unaccent_val) >= 4 and unaccent_val in unaccent_curl)
        ):
            return False, f"Chặn Kênh: '{val}' ({reason})"

    # 2. Check Lexical Keyword Blacklist in Title and Channel Name
    for kw_rule in keywords:
        val = str(kw_rule.get("entry_value") or "")
        norm_val = str(kw_rule.get("normalized_value") or db.normalize_safety_key(val))
        unaccent_val = db.strip_safety_diacritics(norm_val)
        reason = str(kw_rule.get("reason") or "Nội dung chứa từ khóa nhạy cảm / chống phá")

        if norm_val and (norm_val in norm_title or norm_val in norm_cname):
            return False, f"Chặn Từ Khóa: '{val}' ({reason})"

        if unaccent_val and (unaccent_val in unaccent_title or unaccent_val in unaccent_cname):
            return False, f"Chặn Từ Khóa: '{val}' ({reason})"

    # 3. Check Regex Patterns in Title
    raw_combined = f"{title} {channel_name}".strip()
    for pt_rule in patterns:
        pattern_str = str(pt_rule.get("entry_value") or "")
        reason = str(pt_rule.get("reason") or "Khớp mẫu biểu thức vi phạm an toàn")
        if not pattern_str:
            continue
        try:
            if re.search(pattern_str, raw_combined, re.IGNORECASE):
                return False, f"Khớp Mẫu Cấm: '{pattern_str}' ({reason})"
        except Exception:
            continue

    return True, ""


def sync_safety_blacklist_from_remote(remote_url: str | None = None) -> dict[str, Any]:
    """Fetch the latest safety blacklist JSON from remote subscription URL and merge into DB."""
    config = db.get_safety_config()
    url = remote_url or config.get("remote_sync_url", "")
    if not url:
        return {"success": False, "message": "Chưa cấu hình URL đồng bộ từ xa."}

    logger.info("Đang đồng bộ Core Safety Blacklist từ URL: %s", url)
    entries_to_upsert: list[dict[str, Any]] = []

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Auto-YT-SafetyShield/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            payload_bytes = response.read()
            data = json.loads(payload_bytes.decode("utf-8"))

        if not isinstance(data, dict):
            raise ValueError("Dữ liệu Blacklist tải về không đúng định dạng JSON Object.")

        channels = data.get("channels") or []
        for ch in channels:
            if isinstance(ch, dict):
                handle = str(ch.get("handle") or ch.get("name") or "").strip()
                reason = str(ch.get("reason") or "Remote Sync Core Blacklist").strip()
                if handle:
                    entries_to_upsert.append({
                        "entry_type": "channel",
                        "entry_value": handle,
                        "reason": reason,
                        "is_custom": 0,
                        "is_enabled": True,
                    })
            elif isinstance(ch, str) and ch.strip():
                entries_to_upsert.append({
                    "entry_type": "channel",
                    "entry_value": ch.strip(),
                    "reason": "Remote Sync Channel",
                    "is_custom": 0,
                    "is_enabled": True,
                })

        keywords = data.get("keywords") or []
        for kw in keywords:
            if isinstance(kw, dict):
                val = str(kw.get("keyword") or "").strip()
                reason = str(kw.get("reason") or "Remote Sync Keyword").strip()
                if val:
                    entries_to_upsert.append({
                        "entry_type": "keyword",
                        "entry_value": val,
                        "reason": reason,
                        "is_custom": 0,
                        "is_enabled": True,
                    })
            elif isinstance(kw, str) and kw.strip():
                entries_to_upsert.append({
                    "entry_type": "keyword",
                    "entry_value": kw.strip(),
                    "reason": "Remote Sync Keyword",
                    "is_custom": 0,
                    "is_enabled": True,
                })

        patterns = data.get("regex_patterns") or []
        for pt in patterns:
            if isinstance(pt, dict):
                val = str(pt.get("pattern") or "").strip()
                reason = str(pt.get("reason") or "Remote Sync Pattern").strip()
                if val:
                    entries_to_upsert.append({
                        "entry_type": "regex_pattern",
                        "entry_value": val,
                        "reason": reason,
                        "is_custom": 0,
                        "is_enabled": True,
                    })
            elif isinstance(pt, str) and pt.strip():
                entries_to_upsert.append({
                    "entry_type": "regex_pattern",
                    "entry_value": pt.strip(),
                    "reason": "Remote Sync Pattern",
                    "is_custom": 0,
                    "is_enabled": True,
                })

        upserted_count = db.upsert_safety_blacklist_bulk(entries_to_upsert)
        now_str = db.utc_now()
        db.update_safety_config(last_synced_at=now_str)

        logger.info(
            "Đồng bộ Blacklist thành công: Đã xử lý %d mục (%s)",
            upserted_count,
            now_str,
        )
        return {
            "success": True,
            "message": f"Đồng bộ thành công {upserted_count} quy tắc an toàn.",
            "total_synced": upserted_count,
            "last_synced_at": now_str,
            "version": str(data.get("version") or "latest"),
        }

    except urllib.error.URLError as url_err:
        logger.warning("Không thể kết nối đến Remote Blacklist URL (%s): %s. Giữ nguyên danh mục local.", url, url_err)
        ensure_default_safety_rules_seeded()
        return {
            "success": False,
            "offline_fallback": True,
            "message": f"Không thể tải từ URL từ xa ({security_logging.redact_sensitive(url_err)}). Đã duy trì bộ lọc an toàn cục bộ.",
        }
    except Exception as exc:
        safe_msg = security_logging.redact_sensitive(exc)
        logger.error("Lỗi khi xử lý dữ liệu đồng bộ Blacklist: %s", safe_msg)
        ensure_default_safety_rules_seeded()
        return {
            "success": False,
            "offline_fallback": True,
            "message": f"Lỗi đồng bộ: {safe_msg}. Đã duy trì bộ lọc an toàn cục bộ.",
        }
