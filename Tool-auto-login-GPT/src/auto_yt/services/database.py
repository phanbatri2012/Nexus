import sqlite3
import datetime
import json
import os
import re
import unicodedata
import urllib.parse
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Fix the path to point correctly from where the app runs
# Since main.py is run from the project root usually, we can resolve relative to this file
_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parent.parent.parent.parent
DB_PATH = Path(os.environ.get("AUTO_YT_DB_PATH") or PROJECT_ROOT / "data" / "database.db")
TTS_V2_BACKUP_SUFFIX = ".pre_tts_v2.bak"
NULLABLE_PUBLICATION_CHANNEL_BACKUP_SUFFIX = ".pre_nullable_publication_channel.bak"
TRUST_READINESS_V1_MIGRATION = "trust_readiness_v1"
TRUST_AUTOMATION_RESTORE_V1_MIGRATION = "trust_automation_restore_v1"

VIDEO_STATUS_ACTIVE = "active"
VIDEO_STATUS_ERROR = "error"
VIDEO_STATUSES = {VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ERROR}
VIDEO_SOURCE_GENERATED = "generated"
VIDEO_SOURCE_COMMENT_IMPORT = "comment_import"

GENERATED_TITLE_LABELS = {"TIEU DE", "TIEU DE VIDEO"}
GENERATED_DESCRIPTION_LABELS = {"MO TA", "MO TA VIDEO", "MO TA VIDEO CHAPTERS"}
METADATA_FIELD_LABELS = GENERATED_TITLE_LABELS | GENERATED_DESCRIPTION_LABELS | {
    "URL SLUG",
    "SLUG",
    "HASHTAG",
    "HASHTAGS",
    "TAG",
    "TAGS",
    "THE TU KHOA",
    "THE",
    "BINH LUAN GHIM",
    "PINNED COMMENT",
    "PINNED_COMMENT",
    "CAU HOI",
    "CAU HOI KHAN GIA",
    "DAP AN DUNG",
    "CAU TRA LOI DUNG",
    "GIAI THICH",
    "QUIZ",
    "CHAPTER",
    "CHAPTERS",
}
METADATA_SECTION_PATTERN = re.compile(
    r"### \[(?:METADATA & QUIZ|METADATA)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
TITLE_SECTION_PATTERN = re.compile(
    r"### \[(?:TIÊU ĐỀ|TITLE|TIÊU ĐỀ VIDEO)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
SLUG_SECTION_PATTERN = re.compile(
    r"### \[(?:SLUG|URL SLUG)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
DESCRIPTION_SECTION_PATTERN = re.compile(
    r"### \[(?:MÔ TẢ|DESCRIPTION|MÔ TẢ VIDEO)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
HASHTAGS_SECTION_PATTERN = re.compile(
    r"### \[(?:HASHTAGS|HASHTAG)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
TAGS_SECTION_PATTERN = re.compile(
    r"### \[(?:TAGS|TAG|THẺ TỪ KHÓA|THE TU KHOA|THẺ)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
PINNED_COMMENT_SECTION_PATTERN = re.compile(
    r"### \[(?:BÌNH LUẬN GHIM|PINNED COMMENT|PINNED_COMMENT)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
QUIZ_SECTION_PATTERN = re.compile(
    r"### \[(?:QUIZ|QUIZ TƯƠNG TÁC)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
CHAPTERS_SECTION_PATTERN = re.compile(
    r"### \[(?:CHAPTERS|PHÂN ĐOẠN|CHAPTER)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)
THUMBNAIL_TEXT_SECTION_PATTERN = re.compile(
    r"### \[(?:THUMBNAIL_PROMPT_TEXT|THUMBNAIL TEXT|PROMPT THUMBNAIL TEXT)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL | re.IGNORECASE,
)
THUMBNAIL_NOTEXT_SECTION_PATTERN = re.compile(
    r"### \[(?:THUMBNAIL_PROMPT_NOTEXT|THUMBNAIL NOTEXT|PROMPT THUMBNAIL NOTEXT)\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL | re.IGNORECASE,
)


def _normalize_metadata_label(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        value.replace("Đ", "D").replace("đ", "d"),
    )
    without_accents = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^A-Z0-9]+", " ", without_accents.upper()).strip()


def _clean_generated_title(value: str) -> str:
    cleaned = value.strip().strip("#*` ").strip('"“”')
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_like_outro_or_cta(val: str) -> bool:
    v = str(val or "").lower()
    markers = [
        "hẹn gặp lại quý vị",
        "hẹn gặp lại các bạn",
        "nhấn like",
        "đăng ký kênh",
        "bật chuông thông báo",
        "hội viên",
        "chia sẻ góc nhìn",
        "để lại ý kiến dưới phần bình luận",
    ]
    return len(str(val or "")) > 200 or any(m in v for m in markers)


def _is_likely_slug(val: str) -> bool:
    v = str(val or "").strip()
    if not v or len(v) > 100 or " " in v or "\n" in v:
        return False
    return bool(re.match(r"^[a-z0-9\-_]+$", v))


def extract_generated_video_title(generated_script: str) -> str:
    if not generated_script:
        return ""

    title_match = TITLE_SECTION_PATTERN.search(generated_script)
    if title_match:
        content = title_match.group(1).strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        for line in lines:
            line_clean = line.strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in GENERATED_TITLE_LABELS:
                title = _clean_generated_title(inline_value)
                if title and not _looks_like_outro_or_cta(title):
                    return title
            cleaned = _clean_generated_title(line_clean)
            if cleaned and not _looks_like_outro_or_cta(cleaned):
                return cleaned

    # Fallback: Check if SLUG section contains a title if title section was empty or outro
    slug_match = SLUG_SECTION_PATTERN.search(generated_script)
    if slug_match:
        slug_content = slug_match.group(1).strip()
        for line in slug_content.splitlines():
            line_clean = line.strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            candidate = inline_value if separator and _normalize_metadata_label(label) in {"SLUG", "URL SLUG"} else line_clean
            cleaned = _clean_generated_title(candidate)
            if cleaned and len(cleaned.split()) >= 3 and not _is_likely_slug(cleaned) and not _looks_like_outro_or_cta(cleaned):
                return cleaned

    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    metadata = metadata_match.group(1) if metadata_match else generated_script
    lines = metadata.splitlines()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("#*` ")
        label, separator, inline_value = line.partition(":")
        if _normalize_metadata_label(label) not in GENERATED_TITLE_LABELS:
            continue

        if separator:
            title = _clean_generated_title(inline_value)
            if title and not _looks_like_outro_or_cta(title):
                return title

        for following_line in lines[index + 1:]:
            candidate = _clean_generated_title(following_line)
            if not candidate:
                continue
            if candidate.startswith("[") and candidate.endswith("]"):
                break
            candidate_label = candidate.partition(":")[0]
            if _normalize_metadata_label(candidate_label) in {
                "URL SLUG",
                "SLUG",
                "MO TA",
                "MO TA VIDEO",
                "DESCRIPTION",
                "HASHTAG",
                "HASHTAGS",
                "TAGS",
                "TAG",
                "THE TU KHOA",
                "BINH LUAN GHIM",
                "PINNED COMMENT",
                "QUIZ",
                "CHAPTERS",
                "PHAN DOAN",
                "THUMBNAIL",
            }:
                break
            if not _looks_like_outro_or_cta(candidate):
                return candidate
    return ""


def extract_generated_video_description(generated_script: str) -> str:
    if not generated_script:
        return ""

    desc_match = DESCRIPTION_SECTION_PATTERN.search(generated_script)
    if desc_match:
        content = desc_match.group(1).strip()
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        desc_lines = []
        for line in lines:
            line_clean = line.strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in GENERATED_DESCRIPTION_LABELS:
                if inline_value.strip():
                    desc_lines.append(inline_value.strip())
            else:
                desc_lines.append(line)
        result = "\n".join(desc_lines).strip()
        if result and not _is_likely_slug(result):
            return result


    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    metadata = metadata_match.group(1) if metadata_match else generated_script
    lines = metadata.splitlines()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("#*` ")
        label, separator, inline_value = line.partition(":")
        if _normalize_metadata_label(label) not in GENERATED_DESCRIPTION_LABELS:
            continue

        description_lines = []
        if separator and inline_value.strip():
            description_lines.append(inline_value.strip())

        for following_line in lines[index + 1:]:
            candidate = following_line.strip()
            if not candidate:
                continue
            if candidate.startswith("#"):
                break

            candidate_label, candidate_separator, _ = (
                candidate.strip("#*` ").partition(":")
            )
            normalized_label = _normalize_metadata_label(candidate_label)
            if candidate_separator and normalized_label in METADATA_FIELD_LABELS:
                break
            description_lines.append(candidate)

        return re.sub(r"\s+", " ", " ".join(description_lines)).strip()
    return ""


def extract_generated_video_hashtags(generated_script: str) -> str:
    if not generated_script:
        return ""
    hashtags_match = HASHTAGS_SECTION_PATTERN.search(generated_script)
    if hashtags_match:
        val = hashtags_match.group(1).strip()
        cleaned = re.sub(r"^[-*\s]*(?:hashtags?|thẻ\s+hashtag):\s*", "", val, flags=re.IGNORECASE).strip()
        hash_tokens = re.findall(r"(?<!\w)#[\w-]+", cleaned)
        if hash_tokens:
            return " ".join(hash_tokens[:10])
        if len(cleaned) <= 300 and not any(marker in cleaned for marker in ("[MC]:", "[KHACH_", "[HOST]:")):
            return cleaned
        return ""
    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    if metadata_match:
        lines = metadata_match.group(1).splitlines()
        for line in lines:
            line_clean = line.strip().strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in {"HASHTAG", "HASHTAGS"}:
                inline_tokens = re.findall(r"(?<!\w)#[\w-]+", inline_value)
                return " ".join(inline_tokens[:10]) if inline_tokens else inline_value.strip()
            if "#" in line and not line.startswith("###") and not line.upper().startswith("TAGS:"):
                matches = re.findall(r"(?<!\w)#[\w-]+", line)
                if matches:
                    return " ".join(matches[:10])
    return ""


def extract_generated_video_tags(generated_script: str) -> str:
    if not generated_script:
        return ""
    tags_match = TAGS_SECTION_PATTERN.search(generated_script)
    if tags_match:
        val = tags_match.group(1).strip()
        return re.sub(r"^[-*\s]*(?:tags?|thẻ(?:\s+từ\s+khóa)?):\s*", "", val, flags=re.IGNORECASE).strip()
    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    if metadata_match:
        lines = metadata_match.group(1).splitlines()
        for line in lines:
            line_clean = line.strip().strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in {"TAG", "TAGS", "THE TU KHOA", "THE"}:
                return inline_value.strip()
    return ""


def extract_generated_video_pinned_comment(generated_script: str) -> str:
    if not generated_script:
        return ""
    pinned_match = PINNED_COMMENT_SECTION_PATTERN.search(generated_script)
    if pinned_match:
        return pinned_match.group(1).strip()
    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    if metadata_match:
        lines = metadata_match.group(1).splitlines()
        for idx, line in enumerate(lines):
            line_clean = line.strip().strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in {"BINH LUAN GHIM", "PINNED COMMENT"}:
                pinned_lines = [inline_value.strip()] if inline_value.strip() else []
                for follow in lines[idx + 1:]:
                    f_clean = follow.strip()
                    if not f_clean:
                        continue
                    if f_clean.partition(":")[1] and _normalize_metadata_label(f_clean.partition(":")[0]) in METADATA_FIELD_LABELS:
                        break
                    pinned_lines.append(f_clean)
                return "\n".join(pinned_lines).strip()
    return ""


def _is_likely_quiz_content(text: str) -> bool:
    if not text:
        return False
    t_lower = text.lower()
    has_choices = ("a." in t_lower or "a)" in t_lower or "a -" in t_lower) and (
        "b." in t_lower or "b)" in t_lower or "b -" in t_lower
    )
    has_answer = "đáp án" in t_lower or "câu trả lời" in t_lower or "answer" in t_lower
    return has_choices or has_answer


def extract_generated_video_quiz(generated_script: str) -> str:
    if not generated_script:
        return ""
    quiz_match = QUIZ_SECTION_PATTERN.search(generated_script)
    if quiz_match:
        content = quiz_match.group(1).strip()
        if _is_likely_quiz_content(content):
            return content

    # Self-healing fallback: Check if quiz was placed in CHAPTERS section
    ch_match = CHAPTERS_SECTION_PATTERN.search(generated_script)
    if ch_match:
        ch_content = ch_match.group(1).strip()
        if _is_likely_quiz_content(ch_content):
            return ch_content

    if quiz_match:
        content = quiz_match.group(1).strip()
        if content:
            return content

    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    if metadata_match:
        lines = metadata_match.group(1).splitlines()
        for idx, line in enumerate(lines):
            line_clean = line.strip().strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in {"QUIZ", "CAU HOI", "CAU HOI KHAN GIA", "TRAC NGHIEM"}:
                quiz_lines = [inline_value.strip()] if inline_value.strip() else [line_clean]
                for follow in lines[idx + 1:]:
                    f_clean = follow.strip()
                    if not f_clean:
                        continue
                    if f_clean.partition(":")[1] and _normalize_metadata_label(f_clean.partition(":")[0]) in {"BINH LUAN GHIM", "CHAPTERS", "THUMBNAIL"}:
                        break
                    quiz_lines.append(f_clean)
                return "\n".join(quiz_lines).strip()
    return ""


def extract_generated_video_chapters(generated_script: str) -> str:
    if not generated_script:
        return ""
    ch_match = CHAPTERS_SECTION_PATTERN.search(generated_script)
    if ch_match:
        content = ch_match.group(1).strip()
        timestamp_lines = [
            line.strip()
            for line in content.splitlines()
            if re.match(r"^\s*(?:(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:-|–|—)\s*\S+", line)
        ]
        if len(timestamp_lines) >= 3 and not (
            "đáp án" in content.lower() and ("a." in content.lower() or "b." in content.lower())
        ):
            return content

    # Self-healing fallback: Scan for timestamp markers in the whole script
    all_timestamp_lines = [
        line.strip()
        for line in generated_script.splitlines()
        if re.match(r"^\s*(?:(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:-|–|—)\s*\S+", line)
    ]
    if len(all_timestamp_lines) >= 3:
        return "Nội dung chính trong video:\n\n" + "\n".join(all_timestamp_lines)

    return ""


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.replace("Đ", "D").replace("đ", "d"))
    without_accents = "".join(
        c for c in normalized if unicodedata.category(c) != "Mn"
    )
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", without_accents.lower()).strip("-")
    return re.sub(r"-+", "-", cleaned)


def extract_generated_video_slug(text: str, default_title: str = "") -> str:
    if not text:
        return _slugify(default_title) if default_title else ""

    slug_match = SLUG_SECTION_PATTERN.search(text)
    if slug_match:
        val = slug_match.group(1).strip()
        lines = [l.strip() for l in val.splitlines() if l.strip()]
        for line in lines:
            line_clean = line.strip("#*` ")
            label, separator, inline_value = line_clean.partition(":")
            if separator and _normalize_metadata_label(label) in {"SLUG", "URL SLUG"}:
                val_clean = inline_value.strip().strip('"“”\'` ')
                while ":" in val_clean:
                    val_clean = val_clean.partition(":")[2].strip().strip('"“”\'` ')
                s = _slugify(val_clean)
                if s:
                    return s
            s = _slugify(line_clean)
            if s:
                return s

    metadata_match = METADATA_SECTION_PATTERN.search(text)
    section = metadata_match.group(1) if metadata_match else text
    lines = section.splitlines()
    for raw_line in lines:
        line = raw_line.strip().strip("#*` ")
        label, separator, inline_value = line.partition(":")
        norm_label = _normalize_metadata_label(label)
        if norm_label in {"SLUG", "URL SLUG"}:
            val = inline_value.strip().strip('"“”\'` ')
            while ":" in val:
                val = val.partition(":")[2].strip().strip('"“”\'` ')
            slug = _slugify(val)
            if slug:
                return slug
    title = extract_generated_video_title(text) or default_title
    if not title:
        title = text.strip()
    return _slugify(title)


def resolve_render_filename(
    video_id: int,
    base_slug: str,
    renders_dir: Path | str | None = None,
) -> str:
    target_dir = Path(renders_dir) if renders_dir else Path("renders")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        own_artifact = conn.execute(
            "SELECT path FROM video_artifacts WHERE video_id = ? AND artifact_type = 'final_mp4' ORDER BY id DESC LIMIT 1",
            (video_id,),
        ).fetchone()
        if own_artifact:
            own_stem = Path(own_artifact["path"]).stem
            if own_stem == base_slug or own_stem.startswith(f"{base_slug}-"):
                return own_stem

        other_artifacts = conn.execute(
            "SELECT video_id, path FROM video_artifacts WHERE video_id != ? AND artifact_type = 'final_mp4'",
            (video_id,),
        ).fetchall()
        used_stems = {Path(row["path"]).stem for row in other_artifacts}

        candidate = base_slug
        candidate_file = target_dir / f"{candidate}.mp4"
        if candidate not in used_stems and not candidate_file.exists():
            return candidate

        index = 1
        while True:
            candidate = f"{base_slug}-{index}"
            candidate_file = target_dir / f"{candidate}.mp4"
            if candidate not in used_stems and not candidate_file.exists():
                return candidate
            index += 1
    finally:
        conn.close()


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        (value or "").replace("Đ", "D").replace("đ", "d"),
    )
    without_accents = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    searchable = re.sub(r"[^a-z0-9]+", " ", without_accents.casefold())
    return re.sub(r"\s+", " ", searchable).strip()


def build_video_search_text(
    original_url: str,
    original_title: str,
    generated_script: str,
    generated_title: str = "",
) -> str:
    resolved_generated_title = (
        generated_title or extract_generated_video_title(generated_script)
    )
    generated_description = extract_generated_video_description(generated_script)
    return normalize_search_text(
        " ".join(
            part
            for part in (
                original_url,
                original_title,
                resolved_generated_title,
                generated_description,
            )
            if part
        )
    )



VIDEO_PRODUCTION_BACKUP_SUFFIX = ".pre_video_production_v1.bak"

def _backup_database_before_video_production() -> None:
    """Create one consistent backup before adding production workflow tables."""
    if not DB_PATH.exists():
        return
    backup_path = DB_PATH.with_name(DB_PATH.name + VIDEO_PRODUCTION_BACKUP_SUFFIX)
    if backup_path.exists():
        return
    source = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        existing = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'video_artifacts'"
        ).fetchone()
        if existing:
            return
        destination = sqlite3.connect(str(backup_path), timeout=30.0)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

def _remove_orphan_video_dependencies(connection: sqlite3.Connection) -> dict:
    removed = {}
    for table in (
        "system_jobs",
        "audio_tasks",
        "audio_reviews",
        "video_publications",
    ):
        cursor = connection.execute(
            f"DELETE FROM {table} "
            "WHERE video_id IS NOT NULL "
            "AND NOT EXISTS ("
            f"SELECT 1 FROM videos WHERE videos.id = {table}.video_id"
            ")"
        )
        removed[table] = cursor.rowcount
    return removed


def remove_orphan_video_dependencies() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        removed = _remove_orphan_video_dependencies(conn)
        conn.execute("COMMIT")
        return removed
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def _backup_database_before_tts_v2() -> None:
    """Create one consistent backup immediately before the TTS v2 migration."""
    if not DB_PATH.exists():
        return
    source = sqlite3.connect(str(DB_PATH))
    try:
        existing_tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required_columns = {
            "videos": "tts_provider_id",
            "audio_tasks": "tts_provider_id",
            "system_jobs": "tts_provider_id",
        }
        needs_migration = any(
            table in existing_tables
            and column
            not in {
                row[1]
                for row in source.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for table, column in required_columns.items()
        )
        if not needs_migration:
            return
        backup_path = DB_PATH.with_name(f"{DB_PATH.name}{TTS_V2_BACKUP_SUFFIX}")
        if backup_path.exists():
            return
        backup = sqlite3.connect(str(backup_path))
        try:
            source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()


def _publication_channel_requires_migration(conn: sqlite3.Connection) -> bool:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("video_publications",),
    ).fetchone()
    if table_exists is None:
        return False
    channel_column = next(
        (
            row
            for row in conn.execute(
                "PRAGMA table_info(video_publications)"
            ).fetchall()
            if row[1] == "youtube_channel_id"
        ),
        None,
    )
    return channel_column is not None and bool(channel_column[3])


def _backup_database_before_nullable_publication_channel() -> None:
    """Create one consistent backup immediately before rebuilding publications."""
    if not DB_PATH.exists():
        return
    source = sqlite3.connect(str(DB_PATH))
    try:
        if not _publication_channel_requires_migration(source):
            return
        backup_path = DB_PATH.with_name(
            f"{DB_PATH.name}{NULLABLE_PUBLICATION_CHANNEL_BACKUP_SUFFIX}"
        )
        if backup_path.exists():
            return
        backup = sqlite3.connect(str(backup_path))
        try:
            source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()


def _migrate_nullable_publication_channel() -> None:
    """Allow unverified publications while preserving IDs and child records."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        if not _publication_channel_requires_migration(conn):
            return
        foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS video_publications_nullable_migration")
        conn.execute(
            '''
            CREATE TABLE video_publications_nullable_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                youtube_channel_id INTEGER,
                youtube_video_id TEXT NOT NULL UNIQUE,
                published_url TEXT NOT NULL,
                published_title TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO video_publications_nullable_migration (
                id, video_id, youtube_channel_id, youtube_video_id,
                published_url, published_title, published_at,
                created_at, updated_at
            )
            SELECT id, video_id, youtube_channel_id, youtube_video_id,
                   published_url, published_title, published_at,
                   created_at, updated_at
            FROM video_publications
            '''
        )
        conn.execute("DROP TABLE video_publications")
        conn.execute(
            "ALTER TABLE video_publications_nullable_migration "
            "RENAME TO video_publications"
        )
        conn.execute(
            "CREATE INDEX idx_video_publications_video "
            "ON video_publications(video_id)"
        )
        conn.execute(
            "CREATE INDEX idx_video_publications_channel "
            "ON video_publications(youtube_channel_id)"
        )
        conn.execute("COMMIT")
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _backup_database_before_tts_v2()
    _backup_database_before_nullable_publication_channel()
    _migrate_nullable_publication_channel()
    _backup_database_before_video_production()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            transcript TEXT NOT NULL,
            generated_script TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_published INTEGER DEFAULT 0,
            chat_url TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            generated_title TEXT DEFAULT '',
            search_text TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            voice_name TEXT DEFAULT '',
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
            audio_duration_seconds REAL,
            video_status TEXT NOT NULL DEFAULT 'active',
            source_type TEXT NOT NULL DEFAULT 'generated',
            description TEXT DEFAULT ''
        )
    ''')
    # Try adding the column if upgrading from older version
    try:
        c.execute("ALTER TABLE videos ADD COLUMN is_published INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN chat_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN prompt_version TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN generated_title TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN search_text TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN voice_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN audio_duration_seconds REAL")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute(
            "ALTER TABLE videos ADD COLUMN video_status "
            "TEXT NOT NULL DEFAULT 'active'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        c.execute(
            "ALTER TABLE videos ADD COLUMN source_type "
            "TEXT NOT NULL DEFAULT 'generated'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        "UPDATE videos SET video_status = ? "
        "WHERE video_status IS NULL OR video_status NOT IN (?, ?)",
        (VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ERROR),
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(video_status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_videos_source_type ON videos(source_type)"
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS audio_tasks (
            video_id INTEGER PRIMARY KEY,
            request_hash TEXT NOT NULL,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            audio_url TEXT DEFAULT '',
            error TEXT DEFAULT '',
            segments_json TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            voice_name TEXT DEFAULT '',
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_audio_tasks_request_hash '
        'ON audio_tasks(request_hash)'
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS audio_reviews (
            video_id INTEGER PRIMARY KEY,
            script_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            report_json TEXT DEFAULT '{}',
            reviewed_at TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tts_previews (
            id TEXT PRIMARY KEY,
            provider_task_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            text TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            voice_id TEXT NOT NULL,
            voice_name TEXT NOT NULL,
            tts_provider_id TEXT NOT NULL,
            voice_revision INTEGER NOT NULL DEFAULT 1,
            voice_snapshot_json TEXT NOT NULL DEFAULT '{}',
            audio_filename TEXT DEFAULT '',
            duration_seconds REAL,
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_tts_previews_expiry '
        'ON tts_previews(expires_at)'
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT DEFAULT '',
            progress TEXT DEFAULT '',
            payload_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            error TEXT DEFAULT '',
            video_id INTEGER,
            prompt_version TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            voice_name TEXT DEFAULT '',
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
            attempt INTEGER DEFAULT 0,
            recovery_count INTEGER DEFAULT 0,
            resume_from_step TEXT DEFAULT '',
            next_retry_at TEXT DEFAULT '',
            cancel_requested INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE SET NULL
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_system_jobs_queue '
        'ON system_jobs(job_type, status, created_at)'
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS youtube_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            thumbnail_url TEXT DEFAULT '',
            access_token_encrypted TEXT DEFAULT '',
            refresh_token_encrypted TEXT DEFAULT '',
            token_expiry TEXT DEFAULT '',
            scope TEXT DEFAULT '',
            oauth_client_id TEXT DEFAULT '',
            status TEXT DEFAULT 'connected',
            reply_instruction TEXT DEFAULT '',
            auto_mode TEXT DEFAULT 'draft_only',
            daily_reply_limit INTEGER DEFAULT 50,
            reply_interval_minutes INTEGER DEFAULT 5,
            quarter_hour_reply_limit INTEGER DEFAULT 3,
            hourly_reply_limit INTEGER DEFAULT 10,
            video_half_hour_reply_limit INTEGER DEFAULT 3,
            backlog_daily_reply_limit INTEGER DEFAULT 20,
            reply_window_start TEXT DEFAULT '08:00',
            reply_window_end TEXT DEFAULT '22:00',
            reply_paused INTEGER DEFAULT 0,
            auto_sync INTEGER DEFAULT 1,
            sync_interval_minutes INTEGER DEFAULT 10,
            last_sync_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS video_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            youtube_channel_id INTEGER,
            youtube_video_id TEXT NOT NULL UNIQUE,
            published_url TEXT NOT NULL,
            published_title TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_video_publications_video '
        'ON video_publications(video_id)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_video_publications_channel '
        'ON video_publications(youtube_channel_id)'
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS youtube_comments (
            comment_id TEXT PRIMARY KEY,
            thread_id TEXT DEFAULT '',
            publication_id INTEGER NOT NULL,
            parent_id TEXT DEFAULT '',
            author_name TEXT DEFAULT '',
            author_channel_id TEXT DEFAULT '',
            author_avatar_url TEXT DEFAULT '',
            text TEXT NOT NULL,
            like_count INTEGER DEFAULT 0,
            published_at TEXT DEFAULT '',
            source_updated_at TEXT DEFAULT '',
            can_reply INTEGER DEFAULT 1,
            total_reply_count INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'low',
            risk_reason TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            draft_reply TEXT DEFAULT '',
            reply_youtube_id TEXT DEFAULT '',
            reply_text TEXT DEFAULT '',
            reply_published_at TEXT DEFAULT '',
            auto_reply_priority INTEGER DEFAULT 0,
            auto_reply_reason TEXT DEFAULT '',
            is_hearted INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(publication_id) REFERENCES video_publications(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_youtube_comments_status '
        'ON youtube_comments(status, published_at)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_youtube_comments_publication '
        'ON youtube_comments(publication_id)'
    )
    for column_definition in (
        "auto_sync INTEGER DEFAULT 1",
        "sync_interval_minutes INTEGER DEFAULT 10",
        "reply_interval_minutes INTEGER DEFAULT 5",
        "quarter_hour_reply_limit INTEGER DEFAULT 3",
        "hourly_reply_limit INTEGER DEFAULT 10",
        "video_half_hour_reply_limit INTEGER DEFAULT 3",
        "backlog_daily_reply_limit INTEGER DEFAULT 20",
        "reply_window_start TEXT DEFAULT '08:00'",
        "reply_window_end TEXT DEFAULT '22:00'",
        "reply_paused INTEGER DEFAULT 0",
        "oauth_client_id TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE youtube_channels ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
    for column_definition in (
        "risk_level TEXT DEFAULT 'low'",
        "risk_reason TEXT DEFAULT ''",
        "reply_published_at TEXT DEFAULT ''",
        "auto_reply_priority INTEGER DEFAULT 0",
        "auto_reply_reason TEXT DEFAULT ''",
        "is_hearted INTEGER DEFAULT 0",
    ):
        try:
            c.execute(f"ALTER TABLE youtube_comments ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN segments_json TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN voice_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    for table_name in ("videos", "audio_tasks", "system_jobs"):
        for column_definition in (
            "tts_provider_id TEXT DEFAULT 'genmax'",
            "voice_revision INTEGER DEFAULT 1",
            "voice_snapshot_json TEXT DEFAULT '{}'",
        ):
            try:
                c.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_definition}"
                )
            except sqlite3.OperationalError:
                pass
    try:
        c.execute("ALTER TABLE system_jobs ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        "UPDATE videos SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    c.execute(
        "UPDATE audio_tasks SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    c.execute(
        "UPDATE system_jobs SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    for table_name in ("videos", "audio_tasks", "system_jobs"):
        rows = c.execute(
            f"SELECT rowid, voice_id, voice_name, tts_provider_id, voice_revision "
            f"FROM {table_name} WHERE COALESCE(voice_id, '') != '' "
            "AND COALESCE(voice_snapshot_json, '') IN ('', '{}')"
        ).fetchall()
        snapshots = [
            (
                json.dumps(
                    {
                        "voice_id": voice_id,
                        "voice_name": voice_name or "",
                        "provider_id": provider_id or "genmax",
                        "provider_voice_id": voice_id,
                        "voice_revision": int(revision or 1),
                        "config": {},
                    },
                    ensure_ascii=False,
                ),
                row_id,
            )
            for row_id, voice_id, voice_name, provider_id, revision in rows
        ]
        c.executemany(
            f"UPDATE {table_name} SET voice_snapshot_json = ? WHERE rowid = ?",
            snapshots,
        )
    for column_definition in (
        "recovery_count INTEGER DEFAULT 0",
        "resume_from_step TEXT DEFAULT ''",
        "next_retry_at TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE system_jobs ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
    c.execute(
        "SELECT id, generated_script FROM videos "
        "WHERE COALESCE(generated_title, '') = ''"
    )
    generated_title_updates = [
        (extract_generated_video_title(script), video_id)
        for video_id, script in c.fetchall()
    ]
    c.executemany(
        "UPDATE videos SET generated_title = ? WHERE id = ?",
        [update for update in generated_title_updates if update[0]],
    )
    c.execute(
        "SELECT id, url, title, generated_script, generated_title FROM videos"
    )
    search_text_updates = [
        (
            build_video_search_text(url, title, script, generated_title),
            video_id,
        )
        for video_id, url, title, script, generated_title in c.fetchall()
    ]
    c.executemany(
        "UPDATE videos SET search_text = ? WHERE id = ?",
        search_text_updates,
    )
    c.execute("""
        CREATE TABLE IF NOT EXISTS fb_crossposter_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_channel_id TEXT DEFAULT '',
            source_channel_title TEXT DEFAULT '',
            source_gpm_profile_id TEXT DEFAULT '',
            target_fb_page_id TEXT DEFAULT '',
            target_fb_page_name TEXT DEFAULT '',
            target_gpm_profile_id TEXT DEFAULT '',
            target_access_token TEXT DEFAULT '',
            target_access_token_encrypted TEXT DEFAULT '',
            daily_quota INTEGER DEFAULT 2,
            schedule_times_json TEXT DEFAULT '["11:30", "19:30"]',
            lead_time_minutes INTEGER DEFAULT 60,
            post_template TEXT DEFAULT '',
            sort_order_mode TEXT DEFAULT 'oldest_first',
            auto_sync_enabled INTEGER DEFAULT 0,
            auto_sync_type TEXT DEFAULT 'interval',
            auto_sync_interval_hours INTEGER DEFAULT 6,
            auto_sync_fixed_times_json TEXT DEFAULT '["06:00", "18:00"]',
            last_synced_at TEXT DEFAULT '',
            auto_publish_enabled INTEGER DEFAULT 0,
            convert_to_vertical INTEGER DEFAULT 0,
            default_tags_json TEXT DEFAULT '[]',
            upload_mode TEXT DEFAULT 'browser',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS fb_crossposter_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_page_id TEXT DEFAULT '',
            youtube_id TEXT NOT NULL,
            youtube_url TEXT NOT NULL,
            original_title TEXT NOT NULL,
            original_description TEXT DEFAULT '',
            original_tags_json TEXT DEFAULT '[]',
            thumbnail_url TEXT DEFAULT '',
            youtube_upload_date TEXT DEFAULT '',
            fb_title TEXT DEFAULT '',
            fb_description TEXT DEFAULT '',
            fb_description_source TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            scheduled_publish_time INTEGER DEFAULT 0,
            fb_post_id TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            file_size_bytes INTEGER DEFAULT 0,
            meta_published INTEGER,
            meta_video_status TEXT DEFAULT '',
            meta_scheduled_publish_time INTEGER DEFAULT 0,
            meta_status_json TEXT DEFAULT '{}',
            meta_verified_at TEXT DEFAULT '',
            meta_state TEXT DEFAULT '',
            meta_state_since TEXT DEFAULT '',
            meta_error_message TEXT DEFAULT '',
            upload_session_id TEXT DEFAULT '',
            upload_video_id TEXT DEFAULT '',
            upload_phase TEXT DEFAULT '',
            upload_retry_count INTEGER DEFAULT 0,
            upload_next_retry_at TEXT DEFAULT '',
            previous_fb_post_id TEXT DEFAULT '',
            cleanup_status TEXT DEFAULT '',
            repair_history_json TEXT DEFAULT '[]',
            checkpoint_phase TEXT DEFAULT '',
            checkpoint_data_json TEXT DEFAULT '{}',
            checkpoint_screenshot TEXT DEFAULT '',
            can_resume INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            UNIQUE(youtube_id, target_page_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_fb_crossposter_queue_status ON fb_crossposter_queue(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_fb_crossposter_queue_sort ON fb_crossposter_queue(sort_order)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_fb_queue_page_status ON fb_crossposter_queue(target_page_id, status, scheduled_publish_time)")

    try:
        c.execute(
            "ALTER TABLE fb_crossposter_settings "
            "ADD COLUMN target_access_token_encrypted TEXT DEFAULT ''"
        )
    except sqlite3.OperationalError:
        pass
    for column_definition in (
        "convert_to_vertical INTEGER DEFAULT 0",
        "default_tags_json TEXT DEFAULT '[]'",
        "upload_mode TEXT DEFAULT 'browser'",
    ):
        try:
            c.execute(
                f"ALTER TABLE fb_crossposter_settings ADD COLUMN {column_definition}"
            )
        except sqlite3.OperationalError:
            pass

    for column_definition in (
        "checkpoint_phase TEXT DEFAULT ''",
        "checkpoint_data_json TEXT DEFAULT '{}'",
        "checkpoint_screenshot TEXT DEFAULT ''",
        "can_resume INTEGER DEFAULT 0",
    ):
        try:
            c.execute(
                f"ALTER TABLE fb_crossposter_queue ADD COLUMN {column_definition}"
            )
        except sqlite3.OperationalError:
            pass
    _migrate_fb_crossposter_token_storage(conn)

    # Video Production columns
    for column_definition in (
        "production_snapshot_json TEXT NOT NULL DEFAULT '{}'",
        "render_status TEXT NOT NULL DEFAULT ''",
        "publish_status TEXT NOT NULL DEFAULT ''",
        "current_stage TEXT NOT NULL DEFAULT ''",
        "production_progress TEXT NOT NULL DEFAULT ''",
        "blocking_reason TEXT NOT NULL DEFAULT ''",
        "flow_project_url TEXT DEFAULT ''",
        "flow_scene_count INTEGER DEFAULT 0",
        "flow_completed_scenes INTEGER DEFAULT 0",
    ):
        try:
            c.execute(f"ALTER TABLE videos ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass

    # YouTube Channel publication columns
    for column_definition in (
        "publication_timezone TEXT DEFAULT 'Asia/Ho_Chi_Minh'",
        "publication_slots_json TEXT DEFAULT '[]'",
        "publication_daily_limit INTEGER DEFAULT 1",
        "publication_lead_minutes INTEGER DEFAULT 120",
        "publication_paused INTEGER DEFAULT 0",
        "public_upload_verified INTEGER DEFAULT 0",
        "gpm_profile_id TEXT DEFAULT ''",
        "gpm_profile_name TEXT DEFAULT ''",
        "gpm_proxy_info TEXT DEFAULT ''",
        "gpm_proxy_info_encrypted TEXT DEFAULT ''",
        "interaction_mode TEXT DEFAULT 'gpm_browser'",
        "auto_heart INTEGER DEFAULT 1",
    ):
        try:
            c.execute(f"ALTER TABLE youtube_channels ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
    _migrate_youtube_channel_proxy_storage(conn)

    # Video publication columns
    for column_definition in (
        "privacy_status TEXT NOT NULL DEFAULT 'public'",
        "processing_status TEXT NOT NULL DEFAULT 'succeeded'",
        "scheduled_at TEXT DEFAULT ''",
        "artifact_hash TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE video_publications ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass

    # New tables
    c.execute("""
        CREATE TABLE IF NOT EXISTS video_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            artifact_type TEXT NOT NULL,
            path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            mime_type TEXT DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            width INTEGER,
            height INTEGER,
            codecs_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            UNIQUE(video_id, artifact_type, content_hash)
        )
    """)
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_video_artifacts_video_type '
        'ON video_artifacts(video_id, artifact_type, status)'
    )

    c.execute("""
        CREATE TABLE IF NOT EXISTS flow_projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            project_url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (video_id) REFERENCES videos (id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS youtube_publish_workflows (
            id TEXT PRIMARY KEY,
            video_id INTEGER NOT NULL,
            youtube_channel_id INTEGER NOT NULL,
            artifact_id INTEGER NOT NULL,
            system_job_id TEXT DEFAULT '',
            status TEXT NOT NULL,
            stage TEXT NOT NULL,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            upload_session_encrypted TEXT DEFAULT '',
            upload_offset INTEGER NOT NULL DEFAULT 0,
            youtube_video_id TEXT DEFAULT '',
            publication_id INTEGER,
            caption_id TEXT DEFAULT '',
            scheduled_at TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE RESTRICT,
            FOREIGN KEY(artifact_id) REFERENCES video_artifacts(id) ON DELETE RESTRICT,
            FOREIGN KEY(publication_id) REFERENCES video_publications(id) ON DELETE SET NULL
        )
    """)
    try:
        c.execute("ALTER TABLE youtube_publish_workflows ADD COLUMN caption_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_publish_workflows_video_channel '
        'ON youtube_publish_workflows(video_id, youtube_channel_id, status)'
    )

    c.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    c.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (PUBLISH_PIPELINE_V1_MIGRATION, utc_now()),
    )
    c.execute("""
        CREATE TABLE IF NOT EXISTS channel_schedule_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_channel_id INTEGER NOT NULL,
            workflow_id TEXT NOT NULL UNIQUE,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE,
            FOREIGN KEY(workflow_id) REFERENCES youtube_publish_workflows(id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_schedule_reservations_active
        ON channel_schedule_reservations(youtube_channel_id, scheduled_at)
        WHERE status IN ('reserved', 'scheduled')
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS comfyui_workflow_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            workflow_json TEXT NOT NULL,
            node_mappings_json TEXT NOT NULL DEFAULT '{}',
            max_concurrency INTEGER NOT NULL DEFAULT 1,
            min_width INTEGER NOT NULL DEFAULT 1024,
            min_height INTEGER NOT NULL DEFAULT 576,
            timeout_seconds INTEGER NOT NULL DEFAULT 900,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    c.execute('''
        CREATE TABLE IF NOT EXISTS channel_trust_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_db_id INTEGER NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
            niche_keywords TEXT NOT NULL DEFAULT '[]',
            target_channels TEXT NOT NULL DEFAULT '[]',
            warmup_phase TEXT NOT NULL DEFAULT 'idle',
            phase_started_at TEXT DEFAULT '',
            daily_watch_target INTEGER DEFAULT 5,
            daily_search_target INTEGER DEFAULT 3,
            daily_like_target INTEGER DEFAULT 3,
            daily_comment_target INTEGER DEFAULT 0,
            daily_subscribe_target INTEGER DEFAULT 0,
            min_watch_minutes INTEGER DEFAULT 10,
            branding_checklist TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            total_videos_watched INTEGER DEFAULT 0,
            total_searches INTEGER DEFAULT 0,
            total_likes INTEGER DEFAULT 0,
            total_comments INTEGER DEFAULT 0,
            total_subscriptions INTEGER DEFAULT 0,
            trust_score_estimated INTEGER DEFAULT 0,
            legacy_trust_score_estimated INTEGER DEFAULT 0,
            mode TEXT NOT NULL DEFAULT 'automated',
            requires_review INTEGER NOT NULL DEFAULT 0,
            profile_readiness_json TEXT NOT NULL DEFAULT '{}',
            channel_readiness_json TEXT NOT NULL DEFAULT '{}',
            profile_readiness_pct INTEGER NOT NULL DEFAULT 0,
            channel_readiness_pct INTEGER NOT NULL DEFAULT 0,
            readiness_state TEXT NOT NULL DEFAULT 'needs_attention',
            profile_baseline_json TEXT NOT NULL DEFAULT '{}',
            approved_sources TEXT NOT NULL DEFAULT '[]',
            reminder_enabled INTEGER NOT NULL DEFAULT 0,
            reminder_time_local TEXT DEFAULT '',
            last_readiness_check_at TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            last_session_at TEXT DEFAULT '',
            last_attempt_at TEXT DEFAULT '',
            next_run_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(channel_db_id)
        )
    ''')

    for column_definition in (
        "min_watch_minutes INTEGER DEFAULT 10",
        "last_attempt_at TEXT DEFAULT ''",
        "next_run_at TEXT DEFAULT ''",
        "legacy_trust_score_estimated INTEGER DEFAULT 0",
        "mode TEXT NOT NULL DEFAULT 'guided'",
        "requires_review INTEGER NOT NULL DEFAULT 0",
        "profile_readiness_json TEXT NOT NULL DEFAULT '{}'",
        "channel_readiness_json TEXT NOT NULL DEFAULT '{}'",
        "profile_readiness_pct INTEGER NOT NULL DEFAULT 0",
        "channel_readiness_pct INTEGER NOT NULL DEFAULT 0",
        "readiness_state TEXT NOT NULL DEFAULT 'needs_attention'",
        "profile_baseline_json TEXT NOT NULL DEFAULT '{}'",
        "approved_sources TEXT NOT NULL DEFAULT '[]'",
        "reminder_enabled INTEGER NOT NULL DEFAULT 0",
        "reminder_time_local TEXT DEFAULT ''",
        "last_readiness_check_at TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE channel_trust_plans ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS trust_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES channel_trust_plans(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,
            target_url TEXT DEFAULT '',
            target_title TEXT DEFAULT '',
            duration_seconds REAL DEFAULT 0,
            detail_json TEXT DEFAULT '{}',
            success INTEGER DEFAULT 1,
            error_message TEXT DEFAULT '',
            activity_source TEXT NOT NULL DEFAULT 'system_verified',
            executed_at TEXT NOT NULL
        )
    ''')

    try:
        c.execute(
            "ALTER TABLE trust_activity_log ADD COLUMN "
            "activity_source TEXT NOT NULL DEFAULT 'system_verified'"
        )
    except sqlite3.OperationalError:
        pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS trust_guided_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES channel_trust_plans(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'ready',
            agenda_json TEXT NOT NULL DEFAULT '{}',
            checklist_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trust_guided_session_active
        ON trust_guided_sessions(plan_id)
        WHERE status IN ('ready', 'in_progress')
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_trust_guided_session_history
        ON trust_guided_sessions(plan_id, created_at DESC)
    ''')

    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_trust_activity_plan
        ON trust_activity_log(plan_id, executed_at DESC)
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_trust_activity_daily
        ON trust_activity_log(plan_id, success, executed_at, activity_type)
    ''')
    c.execute('''
        DELETE FROM trust_activity_log
        WHERE NOT EXISTS (
            SELECT 1 FROM channel_trust_plans
            WHERE channel_trust_plans.id = trust_activity_log.plan_id
        )
    ''')
    _migrate_trust_builder_guided_mode(conn)
    _migrate_trust_builder_automation_restore(conn)

    c.execute('''
        CREATE TABLE IF NOT EXISTS trust_safety_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT NOT NULL,
            entry_value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            reason TEXT DEFAULT '',
            is_custom INTEGER DEFAULT 0,
            is_enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entry_type, normalized_value)
        )
    ''')
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_trust_safety_lookup
        ON trust_safety_blacklist(entry_type, is_enabled, normalized_value)
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS trust_safety_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    _migrate_fb_crossposter_queue_composite_unique(conn)
    _migrate_fb_crossposter_meta_state(conn)
    _migrate_channel_schedule_reservations_partial_unique(conn)
    _remove_orphan_video_dependencies(conn)
    _clean_existing_scripts_with_sanitizer(conn)
    conn.commit()
    conn.close()


def _clean_existing_scripts_with_sanitizer(conn: sqlite3.Connection) -> None:
    try:
        from auto_yt.services.chatgpt_worker import sanitize_generated_script
        cursor = conn.cursor()
        cursor.execute("SELECT id, generated_script FROM videos WHERE length(generated_script) > 0")
        rows = cursor.fetchall()
        for video_id, script in rows:
            cleaned = sanitize_generated_script(script)
            if cleaned != script:
                cursor.execute(
                    "UPDATE videos SET generated_script = ? WHERE id = ?",
                    (cleaned, video_id),
                )
    except Exception as exc:
        import sys
        print(f"Warning: could not run script sanitizer migration: {exc}", file=sys.stderr)


def _migrate_fb_crossposter_queue_composite_unique(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fb_crossposter_queue'")
    row = c.fetchone()
    if row and row[0] and "youtube_id TEXT UNIQUE" in row[0]:
        c.execute("""
            CREATE TABLE fb_crossposter_queue_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_page_id TEXT DEFAULT '',
                youtube_id TEXT NOT NULL,
                youtube_url TEXT NOT NULL,
                original_title TEXT NOT NULL,
                original_description TEXT DEFAULT '',
                original_tags_json TEXT DEFAULT '[]',
                thumbnail_url TEXT DEFAULT '',
                youtube_upload_date TEXT DEFAULT '',
                fb_title TEXT DEFAULT '',
                fb_description TEXT DEFAULT '',
                fb_description_source TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                scheduled_publish_time INTEGER DEFAULT 0,
                fb_post_id TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                file_size_bytes INTEGER DEFAULT 0,
                meta_published INTEGER,
                meta_video_status TEXT DEFAULT '',
                meta_scheduled_publish_time INTEGER DEFAULT 0,
                meta_status_json TEXT DEFAULT '{}',
                meta_verified_at TEXT DEFAULT '',
                meta_state TEXT DEFAULT '',
                meta_state_since TEXT DEFAULT '',
                meta_error_message TEXT DEFAULT '',
                upload_session_id TEXT DEFAULT '',
                upload_video_id TEXT DEFAULT '',
                upload_phase TEXT DEFAULT '',
                upload_retry_count INTEGER DEFAULT 0,
                upload_next_retry_at TEXT DEFAULT '',
                previous_fb_post_id TEXT DEFAULT '',
                cleanup_status TEXT DEFAULT '',
                repair_history_json TEXT DEFAULT '[]',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(youtube_id, target_page_id)
            )
        """)
        c.execute("""
            INSERT OR IGNORE INTO fb_crossposter_queue_migrated (
                id, target_page_id, youtube_id, youtube_url, original_title, original_description,
                original_tags_json, thumbnail_url, youtube_upload_date, fb_title, fb_description,
                fb_description_source, status, scheduled_publish_time, fb_post_id, error_message,
                sort_order, file_size_bytes,
                created_at, updated_at
            )
            SELECT 
                id, target_page_id, youtube_id, youtube_url, original_title, original_description,
                original_tags_json, thumbnail_url, youtube_upload_date, fb_title, fb_description,
                fb_description_source, status, scheduled_publish_time, fb_post_id, error_message,
                sort_order, file_size_bytes,
                created_at, updated_at
            FROM fb_crossposter_queue
        """)
        c.execute("DROP TABLE fb_crossposter_queue")
        c.execute("ALTER TABLE fb_crossposter_queue_migrated RENAME TO fb_crossposter_queue")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fb_crossposter_queue_status ON fb_crossposter_queue(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fb_crossposter_queue_sort ON fb_crossposter_queue(sort_order)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fb_queue_page_status ON fb_crossposter_queue(target_page_id, status, scheduled_publish_time)")


def _migrate_fb_crossposter_meta_state(conn: sqlite3.Connection) -> None:
    """Add Meta verification fields and enforce one active slot per Fanpage."""
    c = conn.cursor()
    for column_definition in (
        "meta_published INTEGER",
        "meta_video_status TEXT DEFAULT ''",
        "meta_scheduled_publish_time INTEGER DEFAULT 0",
        "meta_status_json TEXT DEFAULT '{}'",
        "meta_verified_at TEXT DEFAULT ''",
        "meta_state TEXT DEFAULT ''",
        "meta_state_since TEXT DEFAULT ''",
        "meta_error_message TEXT DEFAULT ''",
        "upload_session_id TEXT DEFAULT ''",
        "upload_video_id TEXT DEFAULT ''",
        "upload_phase TEXT DEFAULT ''",
        "upload_retry_count INTEGER DEFAULT 0",
        "upload_next_retry_at TEXT DEFAULT ''",
        "previous_fb_post_id TEXT DEFAULT ''",
        "cleanup_status TEXT DEFAULT ''",
        "repair_history_json TEXT DEFAULT '[]'",
    ):
        try:
            c.execute(f"ALTER TABLE fb_crossposter_queue ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass

    active_statuses = (
        "scheduled",
        "downloading",
        "uploading",
        "verifying",
        "processing",
        "retryable",
        "meta_scheduled",
        "schedule_mismatch",
        "stalled",
    )
    placeholders = ",".join("?" for _ in active_statuses)
    duplicate_groups = c.execute(
        f"""
        SELECT COALESCE(target_page_id, '') AS page_id, scheduled_publish_time
        FROM fb_crossposter_queue
        WHERE scheduled_publish_time > 0 AND status IN ({placeholders})
        GROUP BY COALESCE(target_page_id, ''), scheduled_publish_time
        HAVING COUNT(*) > 1
        """,
        active_statuses,
    ).fetchall()
    for page_id, scheduled_time in duplicate_groups:
        duplicate_rows = c.execute(
            f"""
            SELECT id, COALESCE(fb_post_id, '')
            FROM fb_crossposter_queue
            WHERE COALESCE(target_page_id, '') = ?
              AND scheduled_publish_time = ?
              AND status IN ({placeholders})
            ORDER BY CASE WHEN COALESCE(fb_post_id, '') != '' THEN 0 ELSE 1 END, id
            """,
            (page_id, scheduled_time, *active_statuses),
        ).fetchall()
        for item_id, fb_post_id in duplicate_rows[1:]:
            if fb_post_id:
                c.execute(
                    """
                    UPDATE fb_crossposter_queue
                    SET status = 'error',
                        error_message = 'Trùng thời điểm đăng; cần đồng bộ lại trạng thái Meta'
                    WHERE id = ?
                    """,
                    (item_id,),
                )
            else:
                c.execute(
                    """
                    UPDATE fb_crossposter_queue
                    SET status = 'pending', scheduled_publish_time = 0,
                        error_message = 'Đã trả về hàng đợi vì trùng thời điểm đăng'
                    WHERE id = ?
                    """,
                    (item_id,),
                )

    c.execute("DROP INDEX IF EXISTS idx_fb_queue_unique_active_slot")
    c.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fb_queue_unique_active_slot
        ON fb_crossposter_queue(COALESCE(target_page_id, ''), scheduled_publish_time)
        WHERE scheduled_publish_time > 0
          AND status IN (
              'scheduled', 'downloading', 'uploading', 'verifying', 'processing',
              'retryable', 'meta_scheduled', 'schedule_mismatch', 'stalled'
          )
        """
    )


def _migrate_fb_crossposter_token_storage(conn: sqlite3.Connection) -> None:
    """Move legacy plaintext Page tokens into the user-bound DPAPI column."""
    from auto_yt.services.secret_store import encrypt_secret

    rows = conn.execute(
        "SELECT id, target_access_token, target_access_token_encrypted "
        "FROM fb_crossposter_settings"
    ).fetchall()
    for row_id, plaintext_token, encrypted_token in rows:
        plaintext = str(plaintext_token or "").strip()
        encrypted = str(encrypted_token or "").strip()
        if plaintext and not encrypted:
            encrypted = encrypt_secret(plaintext)
        if plaintext or encrypted != str(encrypted_token or ""):
            conn.execute(
                "UPDATE fb_crossposter_settings "
                "SET target_access_token = '', target_access_token_encrypted = ? "
                "WHERE id = ?",
                (encrypted, row_id),
            )


def _migrate_youtube_channel_proxy_storage(conn: sqlite3.Connection) -> None:
    """Move legacy plaintext GPM proxy credentials into a DPAPI column."""
    from auto_yt.services.secret_store import decrypt_secret, encrypt_secret

    rows = conn.execute(
        "SELECT id, gpm_proxy_info, gpm_proxy_info_encrypted FROM youtube_channels"
    ).fetchall()
    for row_id, plaintext_proxy, encrypted_proxy in rows:
        plaintext = str(plaintext_proxy or "").strip()
        encrypted = str(encrypted_proxy or "").strip()
        if encrypted:
            # Fail startup before workers run if an existing value cannot be recovered.
            decrypt_secret(encrypted)
        elif plaintext:
            encrypted = encrypt_secret(plaintext)
            if decrypt_secret(encrypted) != plaintext:
                raise RuntimeError("Không thể xác minh proxy GPM sau khi mã hóa DPAPI.")
        if plaintext or encrypted != str(encrypted_proxy or ""):
            conn.execute(
                "UPDATE youtube_channels "
                "SET gpm_proxy_info = '', gpm_proxy_info_encrypted = ? WHERE id = ?",
                (encrypted, row_id),
            )


def _migrate_trust_builder_guided_mode(conn: sqlite3.Connection) -> None:
    """Preserve legacy history while disabling automated engagement plans."""
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (TRUST_READINESS_V1_MIGRATION,),
    ).fetchone()
    if applied:
        return
    now = utc_now()
    conn.execute(
        """
        UPDATE channel_trust_plans
        SET legacy_trust_score_estimated = trust_score_estimated,
            trust_score_estimated = 0,
            mode = 'guided',
            requires_review = 1,
            readiness_state = 'needs_attention',
            status = CASE WHEN status = 'active' THEN 'paused' ELSE status END,
            next_run_at = '',
            updated_at = ?
        """,
        (now,),
    )
    conn.execute(
        """
        UPDATE trust_activity_log
        SET activity_source = 'legacy_automation'
        WHERE activity_source = 'system_verified'
        """
    )
    conn.execute(
        """
        UPDATE system_jobs
        SET status = 'canceled',
            progress = 'Đã hủy khi chuyển sang Guided Readiness',
            error = '',
            cancel_requested = 0,
            finished_at = ?,
            updated_at = ?
        WHERE job_type = 'trust_builder_session'
          AND status IN ('queued', 'running', 'retry_wait', 'paused')
        """,
        (now, now),
    )
    conn.execute(
        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (TRUST_READINESS_V1_MIGRATION, now),
    )


def _migrate_trust_builder_automation_restore(conn: sqlite3.Connection) -> None:
    """Restore legacy automation plans without restarting them or reviving jobs."""
    applied = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?",
        (TRUST_AUTOMATION_RESTORE_V1_MIGRATION,),
    ).fetchone()
    if applied:
        return
    now = utc_now()
    conn.execute(
        """
        UPDATE channel_trust_plans
        SET trust_score_estimated = legacy_trust_score_estimated,
            mode = 'automated',
            requires_review = 0,
            readiness_state = 'legacy_restored',
            status = 'paused',
            next_run_at = '',
            updated_at = ?
        WHERE legacy_trust_score_estimated > 0
        """,
        (now,),
    )
    conn.execute(
        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (TRUST_AUTOMATION_RESTORE_V1_MIGRATION, now),
    )


def _migrate_channel_schedule_reservations_partial_unique(conn: sqlite3.Connection) -> None:
    """Migrate channel_schedule_reservations to use partial unique index for active slots only."""
    c = conn.cursor()
    c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='channel_schedule_reservations'")
    row = c.fetchone()
    if row and row[0] and bool(re.search(r"UNIQUE\s*\(\s*youtube_channel_id\s*,\s*scheduled_at\s*\)", row[0], re.IGNORECASE)):
        c.execute("""
            CREATE TABLE IF NOT EXISTS channel_schedule_reservations_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_channel_id INTEGER NOT NULL,
                workflow_id TEXT NOT NULL UNIQUE,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE,
                FOREIGN KEY(workflow_id) REFERENCES youtube_publish_workflows(id) ON DELETE CASCADE
            )
        """)
        c.execute("""
            INSERT OR IGNORE INTO channel_schedule_reservations_migrated (
                id, youtube_channel_id, workflow_id, scheduled_at, status, created_at, updated_at
            )
            SELECT id, youtube_channel_id, workflow_id, scheduled_at, status, created_at, updated_at
            FROM channel_schedule_reservations
        """)
        c.execute("DROP TABLE channel_schedule_reservations")
        c.execute("ALTER TABLE channel_schedule_reservations_migrated RENAME TO channel_schedule_reservations")

    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_schedule_reservations_active
        ON channel_schedule_reservations(youtube_channel_id, scheduled_at)
        WHERE status IN ('reserved', 'scheduled')
    """)


def save_video(
    url: str,
    title: str,
    transcript: str,
    generated_script: str,
    chat_url: str = '',
    prompt_version: str = '',
    voice_id: str = '',
    voice_name: str = '',
    tts_provider_id: str = 'genmax',
    voice_revision: int = 1,
    voice_snapshot_json: str = '{}',
    production_snapshot_json: str = '{}',
) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    generated_title = extract_generated_video_title(generated_script)
    c.execute('''
        INSERT INTO videos (
            url, title, transcript, generated_script, created_at, chat_url,
            prompt_version, generated_title, search_text, voice_id, voice_name,
            tts_provider_id, voice_revision, voice_snapshot_json,
            production_snapshot_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        url,
        title,
        transcript,
        generated_script,
        datetime.datetime.now().isoformat(),
        chat_url,
        prompt_version,
        generated_title,
        build_video_search_text(url, title, generated_script, generated_title),
        voice_id,
        voice_name,
        tts_provider_id,
        max(1, int(voice_revision or 1)),
        voice_snapshot_json or '{}',
        production_snapshot_json or '{}',
    ))
    video_id = c.lastrowid
    conn.commit()
    conn.close()
    return video_id

def get_all_videos(
    limit: int = 10,
    offset: int = 0,
    is_published: int = None,
    prompt_version: str = None,
    search_query: str = None,
    video_status: str = None,
) -> dict:
    if video_status is not None and video_status not in VIDEO_STATUSES:
        raise ValueError("Trạng thái video không hợp lệ.")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Videos imported only to manage comments must not pollute the generated
    # video library. They remain available through publications/comments.
    count_scope_filters = ["COALESCE(source_type, ?) != ?"]
    count_scope_params = [VIDEO_SOURCE_GENERATED, VIDEO_SOURCE_COMMENT_IMPORT]
    if prompt_version is not None:
        count_scope_filters.append('prompt_version = ?')
        count_scope_params.append(prompt_version)
    normalized_query = normalize_search_text(search_query or '')
    if normalized_query:
        count_scope_filters.append(
            "(search_text LIKE ? OR EXISTS ("
            "SELECT 1 FROM video_publications "
            "WHERE video_publications.video_id = videos.id "
            "AND LOWER(video_publications.published_url) LIKE ?))"
        )
        count_scope_params.extend(
            (f'%{normalized_query}%', f'%{normalized_query}%')
        )
    scope_filters = list(count_scope_filters)
    scope_params = list(count_scope_params)
    if video_status is not None:
        scope_filters.append('video_status = ?')
        scope_params.append(video_status)
    scope_clause = (
        f" AND {' AND '.join(scope_filters)}" if scope_filters else ''
    )

    # Counts follow the selected prompt version while remaining independent of
    # the publication-status filter.
    active_scope_clause = (
        f" AND {' AND '.join(count_scope_filters)}"
        if count_scope_filters else ''
    )
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 1 '
        f'AND video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ACTIVE, *count_scope_params),
    )
    count_published = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 0 '
        f'AND video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ACTIVE, *count_scope_params),
    )
    count_unpublished = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ERROR, *count_scope_params),
    )
    count_error = c.fetchone()[0]

    filters = []
    params = []
    if is_published is not None:
        filters.append('is_published = ?')
        params.append(is_published)
    filters.extend(scope_filters)
    params.extend(scope_params)

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ''
    c.execute(f'SELECT COUNT(*) FROM videos{where_clause}', params)
    total = c.fetchone()[0]
    c.execute(
        'SELECT id, url, '
        "COALESCE(NULLIF(generated_title, ''), title) AS title, "
        'created_at, is_published, video_status, chat_url, '
        'prompt_version, voice_id, voice_name, tts_provider_id, '
        'audio_duration_seconds, render_status, publish_status, current_stage, '
        'production_progress, blocking_reason, '
        "COALESCE((SELECT status FROM audio_reviews WHERE video_id = videos.id), '') "
        'AS audio_review_status, '
        'SUBSTR(generated_script, 1, 300) as snippet, '
        "COALESCE((SELECT published_url FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS published_url, '
        "COALESCE((SELECT youtube_video_id FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS published_youtube_video_id, '
        "COALESCE((SELECT privacy_status FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS publication_privacy_status, '
        "COALESCE((SELECT processing_status FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS publication_processing_status, '
        "COALESCE((SELECT scheduled_at FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS publication_scheduled_at, '
        'COALESCE('
        'NULLIF((SELECT audio_url FROM audio_tasks WHERE video_id = videos.id), \'\'), '
        'CASE WHEN INSTR(generated_script, \'### [AUDIO]\') > 0 '
        'THEN TRIM(SUBSTR('
        'generated_script, '
        'INSTR(generated_script, \'### [AUDIO]\') + LENGTH(\'### [AUDIO]\')'
        ')) ELSE \'\' END'
        ') AS audio_url '
        f'FROM videos{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?',
        (*params, limit, offset),
    )

    rows = c.fetchall()
    conn.close()

    return {
        "total": total,
        "count_published": count_published,
        "count_unpublished": count_unpublished,
        "count_error": count_error,
        "items": [dict(row) for row in rows]
    }


def set_video_status(video_id: int, video_status: str) -> dict | None:
    if video_status not in VIDEO_STATUSES:
        raise ValueError("Trạng thái video không hợp lệ.")

    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "UPDATE videos SET video_status = ? WHERE id = ?",
            (video_status, video_id),
        )
        if video_status == VIDEO_STATUS_ERROR:
            conn.execute(
                '''
                UPDATE system_jobs
                SET status = 'canceled', progress = ?, cancel_requested = 0,
                    finished_at = ?, updated_at = ?
                WHERE video_id = ?
                  AND status IN ('queued', 'retry_wait', 'paused')
                ''',
                ("Đã bỏ qua vì video được đánh dấu Lỗi", now, now, video_id),
            )
            conn.execute(
                '''
                UPDATE system_jobs
                SET progress = ?, cancel_requested = 1, updated_at = ?
                WHERE video_id = ? AND status = 'running'
                ''',
                (
                    "Video đã được đánh dấu Lỗi; sẽ dừng tại điểm an toàn",
                    now,
                    video_id,
                ),
            )
        updated = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(updated)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def toggle_published(video_id: int, is_published: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    status_row = c.execute(
        "SELECT video_status FROM videos WHERE id = ?",
        (video_id,),
    ).fetchone()
    if status_row and status_row[0] == VIDEO_STATUS_ERROR:
        conn.close()
        raise ValueError(
            "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
        )
    if not is_published:
        publication_count = c.execute(
            "SELECT COUNT(*) FROM video_publications WHERE video_id = ?",
            (video_id,),
        ).fetchone()[0]
        if publication_count:
            conn.close()
            raise ValueError(
                "Video đang có link đã đăng. Hãy xóa liên kết trong menu "
                "Bình luận YouTube để đồng bộ toàn hệ thống."
            )
    c.execute('UPDATE videos SET is_published = ? WHERE id = ?', (is_published, video_id))
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def get_video(video_id: int) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def list_video_identity_candidates() -> list[dict]:
    """Return compact video identities used for exact YouTube-ID matching."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT id, url, title, chat_url, prompt_version, video_status,
               COALESCE(source_type, ?) AS source_type
        FROM videos
        ORDER BY id DESC
        ''',
        (VIDEO_SOURCE_GENERATED,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def reserve_comment_import_video(
    *,
    youtube_channel_id: int,
    youtube_video_id: str,
    published_url: str,
    title: str,
    description: str,
    published_at: str,
    prompt_version: str,
    existing_video_id: int | None = None,
) -> dict:
    """Atomically link or create one legacy video without overwriting context.

    The YouTube video ID is the durable deduplication key. Existing videos and
    Chat URLs always win; repeated bulk imports are therefore idempotent.
    """
    now = utc_now()
    normalized_youtube_id = str(youtube_video_id or "").strip()
    if not normalized_youtube_id:
        raise ValueError("Thiếu YouTube Video ID.")
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        channel = conn.execute(
            "SELECT id FROM youtube_channels WHERE id = ?",
            (youtube_channel_id,),
        ).fetchone()
        if channel is None:
            raise ValueError("Không tìm thấy kênh YouTube.")

        publication = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (normalized_youtube_id,),
        ).fetchone()
        created_video = False
        created_publication = False
        assigned_channel = False
        if publication is not None:
            publication_channel_id = publication["youtube_channel_id"]
            if (
                publication_channel_id is not None
                and int(publication_channel_id) != int(youtube_channel_id)
            ):
                raise ValueError("Video này đã được liên kết với một kênh khác.")
            if publication_channel_id is None:
                cursor = conn.execute(
                    '''
                    UPDATE video_publications
                    SET youtube_channel_id = ?, published_url = ?,
                        published_title = ?, published_at = ?, updated_at = ?
                    WHERE id = ? AND youtube_channel_id IS NULL
                    ''',
                    (
                        youtube_channel_id,
                        published_url,
                        title,
                        published_at,
                        now,
                        publication["id"],
                    ),
                )
                assigned_channel = cursor.rowcount > 0
            video_id = int(publication["video_id"])
        elif existing_video_id is not None:
            video = conn.execute(
                "SELECT id, video_status FROM videos WHERE id = ?",
                (existing_video_id,),
            ).fetchone()
            if video is None:
                raise ValueError("Không tìm thấy video nội bộ cần liên kết.")
            if video["video_status"] == VIDEO_STATUS_ERROR:
                raise ValueError("Video nội bộ đang ở trạng thái Lỗi.")
            video_id = int(video["id"])
        else:
            search_text = normalize_search_text(
                " ".join((published_url, title, description))
            )
            cursor = conn.execute(
                '''
                INSERT INTO videos (
                    url, title, transcript, generated_script, created_at,
                    is_published, chat_url, prompt_version, generated_title,
                    search_text, voice_id, voice_name, video_status,
                    source_type, description
                ) VALUES (?, ?, '', '', ?, 1, '', ?, ?, ?, '', '', ?, ?, ?)
                ''',
                (
                    published_url,
                    title,
                    now,
                    prompt_version,
                    title,
                    search_text,
                    VIDEO_STATUS_ACTIVE,
                    VIDEO_SOURCE_COMMENT_IMPORT,
                    description,
                ),
            )
            video_id = int(cursor.lastrowid)
            created_video = True

        if publication is None:
            conn.execute(
                '''
                INSERT INTO video_publications (
                    video_id, youtube_channel_id, youtube_video_id,
                    published_url, published_title, published_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    video_id,
                    youtube_channel_id,
                    normalized_youtube_id,
                    published_url,
                    title,
                    published_at,
                    now,
                    now,
                ),
            )
            created_publication = True
        conn.execute(
            '''
            UPDATE videos
            SET is_published = 1,
                prompt_version = CASE
                    WHEN TRIM(COALESCE(prompt_version, '')) = '' THEN ?
                    ELSE prompt_version
                END,
                description = CASE
                    WHEN TRIM(COALESCE(description, '')) = '' THEN ?
                    ELSE description
                END
            WHERE id = ?
            ''',
            (prompt_version, description, video_id),
        )
        video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        publication = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (normalized_youtube_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return {
            "video": dict(video),
            "publication": dict(publication),
            "created_video": created_video,
            "created_publication": created_publication,
            "assigned_channel": assigned_channel,
        }
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def update_comment_import_context(
    video_id: int,
    *,
    transcript: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Fill imported context fields without replacing non-empty saved data."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    assignments = []
    params: list = []
    if transcript is not None:
        assignments.append(
            "transcript = CASE WHEN TRIM(COALESCE(transcript, '')) = '' "
            "THEN ? ELSE transcript END"
        )
        params.append(transcript)
    if description is not None:
        assignments.append(
            "description = CASE WHEN TRIM(COALESCE(description, '')) = '' "
            "THEN ? ELSE description END"
        )
        params.append(description)
    if assignments:
        conn.execute(
            f"UPDATE videos SET {', '.join(assignments)} WHERE id = ?",
            (*params, video_id),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_video_chat_url_if_empty(video_id: int, chat_url: str) -> dict | None:
    """Persist a newly created Chat exactly once and never overwrite one."""
    normalized_url = str(chat_url or "").strip()
    if not normalized_url:
        raise ValueError("Chat URL không được để trống.")
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            '''
            UPDATE videos
            SET chat_url = ?
            WHERE id = ? AND TRIM(COALESCE(chat_url, '')) = ''
            ''',
            (normalized_url, video_id),
        )
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        conn.execute("COMMIT")
        return dict(row) if row else None
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def delete_video_with_dependencies(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            conn.execute("ROLLBACK")
            return None

        active_job = conn.execute(
            '''
            SELECT id FROM system_jobs
            WHERE video_id = ?
              AND status IN ('queued', 'running', 'retry_wait', 'paused')
            LIMIT 1
            ''',
            (video_id,),
        ).fetchone()
        if active_job is not None:
            raise ValueError(
                "Job tạo video đang chạy hoặc chờ xử lý. "
                "Hãy dừng job trước khi xóa."
            )

        active_audio = conn.execute(
            '''
            SELECT task_id FROM audio_tasks
            WHERE video_id = ? AND status IN ('pending', 'processing', 'queued')
            LIMIT 1
            ''',
            (video_id,),
        ).fetchone()
        if active_audio is not None:
            raise ValueError(
                "Video đang có job audio chạy hoặc chờ Genmax. "
                "Hãy đợi job kết thúc trước khi xóa."
            )

        system_jobs = conn.execute(
            "SELECT * FROM system_jobs WHERE video_id = ? ORDER BY created_at",
            (video_id,),
        ).fetchall()
        audio_task = conn.execute(
            "SELECT * FROM audio_tasks WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        audio_review = conn.execute(
            "SELECT * FROM audio_reviews WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        publications = conn.execute(
            "SELECT * FROM video_publications WHERE video_id = ? ORDER BY id",
            (video_id,),
        ).fetchall()

        conn.execute(
            "DELETE FROM youtube_comments WHERE publication_id IN "
            "(SELECT id FROM video_publications WHERE video_id = ?)",
            (video_id,),
        )
        conn.execute("DELETE FROM video_publications WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM system_jobs WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM audio_reviews WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM audio_tasks WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    decoded_jobs = [_decode_system_job(row) for row in system_jobs]
    return {
        "video": dict(video),
        "system_jobs": decoded_jobs,
        "system_job_ids": [job["id"] for job in decoded_jobs],
        "audio_task": dict(audio_task) if audio_task else None,
        "audio_review": dict(audio_review) if audio_review else None,
        "video_publications": [dict(row) for row in publications],
    }


def delete_video(video_id: int) -> bool:
    return delete_video_with_dependencies(video_id) is not None


def save_youtube_channel(
    *,
    channel_id: str,
    title: str,
    thumbnail_url: str = "",
    access_token_encrypted: str = "",
    refresh_token_encrypted: str = "",
    token_expiry: str = "",
    scope: str = "",
    oauth_client_id: str = "",
    gpm_profile_id: str = "",
    gpm_profile_name: str = "",
    gpm_proxy_info: str = "",
    interaction_mode: str = "gpm_browser",
    auto_heart: int = 1,
    **extra_fields,
) -> dict:
    from auto_yt.services.secret_store import encrypt_secret

    now = utc_now()
    encrypted_proxy = encrypt_secret(str(gpm_proxy_info or "").strip())
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        '''
        INSERT INTO youtube_channels (
            channel_id, title, thumbnail_url, access_token_encrypted,
            refresh_token_encrypted, token_expiry, scope, oauth_client_id, status,
            gpm_profile_id, gpm_profile_name, gpm_proxy_info,
            gpm_proxy_info_encrypted, interaction_mode, auto_heart,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'connected', ?, ?, '', ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title = excluded.title,
            thumbnail_url = excluded.thumbnail_url,
            access_token_encrypted = excluded.access_token_encrypted,
            refresh_token_encrypted = CASE
                WHEN excluded.refresh_token_encrypted = ''
                THEN youtube_channels.refresh_token_encrypted
                ELSE excluded.refresh_token_encrypted
            END,
            token_expiry = excluded.token_expiry,
            scope = excluded.scope,
            oauth_client_id = CASE
                WHEN excluded.refresh_token_encrypted = ''
                    AND youtube_channels.refresh_token_encrypted != ''
                THEN youtube_channels.oauth_client_id
                ELSE excluded.oauth_client_id
            END,
            gpm_profile_id = CASE
                WHEN excluded.gpm_profile_id != '' THEN excluded.gpm_profile_id
                ELSE youtube_channels.gpm_profile_id
            END,
            gpm_profile_name = CASE
                WHEN excluded.gpm_profile_name != '' THEN excluded.gpm_profile_name
                ELSE youtube_channels.gpm_profile_name
            END,
            gpm_proxy_info = '',
            gpm_proxy_info_encrypted = CASE
                WHEN excluded.gpm_proxy_info_encrypted != ''
                THEN excluded.gpm_proxy_info_encrypted
                ELSE youtube_channels.gpm_proxy_info_encrypted
            END,
            status = 'connected',
            updated_at = excluded.updated_at
        ''',
        (
            channel_id,
            title,
            thumbnail_url,
            access_token_encrypted,
            refresh_token_encrypted,
            token_expiry,
            scope,
            str(oauth_client_id or "").strip(),
            gpm_profile_id,
            gpm_profile_name,
            encrypted_proxy,
            interaction_mode,
            auto_heart,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    conn.close()
    return _public_youtube_channel(row)


PUBLISH_PIPELINE_V1_MIGRATION = "publish_pipeline_v1"


def _public_youtube_channel(row: sqlite3.Row | dict | None, *, include_tokens: bool = False) -> dict | None:
    if row is None:
        return None
    from auto_yt.services.secret_store import decrypt_secret

    channel = dict(row)
    encrypted_proxy = str(channel.pop("gpm_proxy_info_encrypted", "") or "").strip()
    legacy_proxy = str(channel.get("gpm_proxy_info") or "").strip()
    channel["gpm_proxy_info"] = decrypt_secret(encrypted_proxy) if encrypted_proxy else legacy_proxy
    channel["has_refresh_token"] = bool(channel.get("refresh_token_encrypted"))
    try:
        slots = json.loads(channel.get("publication_slots_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        slots = []
    channel["publication_slots"] = slots if isinstance(slots, list) else []
    if not include_tokens:
        channel.pop("access_token_encrypted", None)
        channel.pop("refresh_token_encrypted", None)
    return channel


def public_youtube_channel(channel: sqlite3.Row | dict | None) -> dict | None:
    """Return channel metadata without OAuth or proxy credentials."""
    if channel is None:
        return None
    from auto_yt.services.proxy_utils import parse_proxy_url

    public = dict(channel)
    proxy_info = str(public.pop("gpm_proxy_info", "") or "").strip()
    public.pop("gpm_proxy_info_encrypted", None)
    public.pop("access_token_encrypted", None)
    public.pop("refresh_token_encrypted", None)
    public["gpm_proxy_configured"] = bool(parse_proxy_url(proxy_info))
    return public


def get_youtube_channel(channel_db_id: int, *, include_tokens: bool = False) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE id = ?", (channel_db_id,)
    ).fetchone()
    conn.close()
    return _public_youtube_channel(row, include_tokens=include_tokens)


def get_youtube_channel_by_channel_id(
    channel_id: str,
    *,
    include_tokens: bool = False,
) -> dict | None:
    """Resolve a connected channel by its stable YouTube channel identifier."""
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?",
        (normalized_channel_id,),
    ).fetchone()
    conn.close()
    return _public_youtube_channel(row, include_tokens=include_tokens)


def list_youtube_channels() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM youtube_channels ORDER BY title COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [_public_youtube_channel(row) for row in rows]


def update_youtube_channel(channel_db_id: int, **changes) -> dict | None:
    allowed = {
        "title",
        "thumbnail_url",
        "access_token_encrypted",
        "refresh_token_encrypted",
        "token_expiry",
        "scope",
        "oauth_client_id",
        "status",
        "reply_instruction",
        "auto_mode",
        "daily_reply_limit",
        "reply_interval_minutes",
        "quarter_hour_reply_limit",
        "hourly_reply_limit",
        "video_half_hour_reply_limit",
        "backlog_daily_reply_limit",
        "reply_window_start",
        "reply_window_end",
        "reply_paused",
        "auto_sync",
        "sync_interval_minutes",
        "last_sync_at",
        "gpm_profile_id",
        "gpm_profile_name",
        "gpm_proxy_info",
        "interaction_mode",
        "auto_heart",
        "publication_timezone",
        "publication_slots_json",
        "publication_daily_limit",
        "publication_lead_minutes",
        "publication_paused",
        "public_upload_verified",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Unsupported YouTube channel fields: {sorted(invalid)}")
    if not changes:
        return get_youtube_channel(channel_db_id)
    if "gpm_proxy_info" in changes:
        from auto_yt.services.secret_store import encrypt_secret

        raw_proxy = str(changes.pop("gpm_proxy_info") or "").strip()
        changes["gpm_proxy_info"] = ""
        changes["gpm_proxy_info_encrypted"] = encrypt_secret(raw_proxy)
        allowed.add("gpm_proxy_info_encrypted")
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE youtube_channels SET {assignments} WHERE id = ?",
        (*changes.values(), channel_db_id),
    )
    conn.commit()
    conn.close()
    return get_youtube_channel(channel_db_id)


def delete_youtube_channel(channel_db_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        publication_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM video_publications WHERE youtube_channel_id = ?",
                (channel_db_id,),
            ).fetchall()
        ]
        if publication_ids:
            placeholders = ",".join("?" for _ in publication_ids)
            conn.execute(
                f"DELETE FROM youtube_comments WHERE publication_id IN ({placeholders})",
                publication_ids,
            )
        video_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT video_id FROM video_publications WHERE youtube_channel_id = ?",
                (channel_db_id,),
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM video_publications WHERE youtube_channel_id = ?",
            (channel_db_id,),
        )
        cursor = conn.execute("DELETE FROM youtube_channels WHERE id = ?", (channel_db_id,))
        for video_id in video_ids:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM video_publications WHERE video_id = ?", (video_id,)
            ).fetchone()[0]
            if remaining == 0:
                conn.execute("UPDATE videos SET is_published = 0 WHERE id = ?", (video_id,))
        conn.execute("COMMIT")
        return cursor.rowcount > 0
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def save_video_publication(
    *,
    video_id: int,
    youtube_channel_id: int | None,
    youtube_video_id: str,
    published_url: str,
    published_title: str = "",
    published_at: str = "",
    privacy_status: str = "public",
    processing_status: str = "succeeded",
    scheduled_at: str = "",
    artifact_hash: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute(
            "SELECT video_status FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise ValueError("Không tìm thấy video nội bộ.")
        if video[0] == VIDEO_STATUS_ERROR:
            raise ValueError(
                "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
            )
        if youtube_channel_id is not None:
            if conn.execute(
                "SELECT 1 FROM youtube_channels WHERE id = ?", (youtube_channel_id,)
            ).fetchone() is None:
                raise ValueError("Không tìm thấy kênh YouTube.")
        existing = conn.execute(
            "SELECT video_id, youtube_channel_id FROM video_publications "
            "WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if existing:
            existing_channel_id = existing[1]
            changes_video = int(existing[0]) != int(video_id)
            changes_assigned_channel = existing_channel_id is not None and (
                youtube_channel_id is None
                or int(existing_channel_id) != int(youtube_channel_id)
            )
            if changes_video or changes_assigned_channel:
                raise ValueError(
                    "Link video đã đăng này đã được gắn với video hoặc kênh khác."
                )
        conn.execute(
            '''
            INSERT INTO video_publications (
                video_id, youtube_channel_id, youtube_video_id, published_url,
                published_title, published_at, privacy_status, processing_status,
                scheduled_at, artifact_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_video_id) DO UPDATE SET
                video_id = excluded.video_id,
                youtube_channel_id = excluded.youtube_channel_id,
                published_url = excluded.published_url,
                published_title = excluded.published_title,
                published_at = excluded.published_at,
                privacy_status = excluded.privacy_status,
                processing_status = excluded.processing_status,
                scheduled_at = excluded.scheduled_at,
                artifact_hash = excluded.artifact_hash,
                updated_at = excluded.updated_at
            ''',
            (
                video_id,
                youtube_channel_id,
                youtube_video_id,
                published_url,
                published_title,
                published_at,
                privacy_status,
                processing_status,
                scheduled_at,
                artifact_hash,
                now,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE videos
            SET is_published = CASE WHEN EXISTS (
                SELECT 1 FROM video_publications
                WHERE video_id = ? AND privacy_status = 'public'
            ) THEN 1 ELSE 0 END
            WHERE id = ?
            """,
            (video_id, video_id),
        )
        row = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def list_video_publications(video_id: int | None = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["videos.video_status = ?"]
    params: list = [VIDEO_STATUS_ACTIVE]
    if video_id is not None:
        filters.append("publications.video_id = ?")
        params.append(video_id)
    where = f"WHERE {' AND '.join(filters)}"
    rows = conn.execute(
        f'''
        SELECT publications.*, channels.channel_id, channels.title AS channel_title,
               videos.chat_url, videos.prompt_version, videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM video_publications AS publications
        LEFT JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        {where}
        ORDER BY publications.created_at DESC
        ''',
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_video_publication_identities() -> list[dict]:
    """Return all publication IDs, including videos marked as error."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT publications.id, publications.video_id,
               publications.youtube_channel_id,
               publications.youtube_video_id,
               publications.published_url,
               videos.chat_url, videos.prompt_version, videos.video_status,
               COALESCE(videos.source_type, ?) AS source_type
        FROM video_publications AS publications
        JOIN videos ON videos.id = publications.video_id
        ORDER BY publications.id DESC
        ''',
        (VIDEO_SOURCE_GENERATED,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_video_publication_by_youtube_id(youtube_video_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        '''
        SELECT publications.*, videos.chat_url, videos.prompt_version,
               videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM video_publications AS publications
        JOIN videos ON videos.id = publications.video_id
        WHERE publications.youtube_video_id = ?
        ''',
        (youtube_video_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_video_publication(publication_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT video_id FROM video_publications WHERE id = ?", (publication_id,)
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return False
        conn.execute("DELETE FROM youtube_comments WHERE publication_id = ?", (publication_id,))
        conn.execute("DELETE FROM video_publications WHERE id = ?", (publication_id,))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM video_publications WHERE video_id = ?", (row[0],)
        ).fetchone()[0]
        if remaining == 0:
            conn.execute("UPDATE videos SET is_published = 0 WHERE id = ?", (row[0],))
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def upsert_youtube_comment(publication_id: int, comment: dict) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        '''
        INSERT INTO youtube_comments (
            comment_id, thread_id, publication_id, parent_id, author_name,
            author_channel_id, author_avatar_url, text, like_count,
            published_at, source_updated_at, can_reply, total_reply_count,
            risk_level, risk_reason, auto_reply_priority, auto_reply_reason,
            status, reply_youtube_id, reply_text, reply_published_at,
            synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comment_id) DO UPDATE SET
            thread_id = excluded.thread_id,
            publication_id = excluded.publication_id,
            author_name = excluded.author_name,
            author_channel_id = excluded.author_channel_id,
            author_avatar_url = excluded.author_avatar_url,
            text = excluded.text,
            like_count = excluded.like_count,
            published_at = excluded.published_at,
            source_updated_at = excluded.source_updated_at,
            can_reply = excluded.can_reply,
            total_reply_count = excluded.total_reply_count,
            risk_level = CASE
                WHEN youtube_comments.risk_level = 'reviewed' THEN 'reviewed'
                ELSE excluded.risk_level
            END,
            risk_reason = CASE
                WHEN youtube_comments.risk_level = 'reviewed' THEN youtube_comments.risk_reason
                ELSE excluded.risk_reason
            END,
            auto_reply_priority = excluded.auto_reply_priority,
            auto_reply_reason = excluded.auto_reply_reason,
            status = CASE
                WHEN ? != '' THEN 'replied'
                ELSE youtube_comments.status
            END,
            reply_youtube_id = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_youtube_id
            END,
            reply_text = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_text
            END,
            reply_published_at = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_published_at
            END,
            error = CASE WHEN ? != '' THEN '' ELSE youtube_comments.error END,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        ''',
        (
            comment["comment_id"],
            comment.get("thread_id", ""),
            publication_id,
            comment.get("parent_id", ""),
            comment.get("author_name", ""),
            comment.get("author_channel_id", ""),
            comment.get("author_avatar_url", ""),
            comment.get("text", ""),
            int(comment.get("like_count") or 0),
            comment.get("published_at", ""),
            comment.get("updated_at", ""),
            int(bool(comment.get("can_reply", True))),
            int(comment.get("total_reply_count") or 0),
            comment.get("risk_level", "low"),
            comment.get("risk_reason", ""),
            int(comment.get("auto_reply_priority") or 0),
            comment.get("auto_reply_reason", ""),
            "replied" if comment.get("existing_reply_id") else "new",
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_text", ""),
            comment.get("existing_reply_published_at", ""),
            now,
            now,
            now,
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_text", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_published_at", ""),
            comment.get("existing_reply_id", ""),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_comments WHERE comment_id = ?", (comment["comment_id"],)
    ).fetchone()
    conn.close()
    return dict(row)


def list_youtube_comments(
    *,
    channel_db_id: int | None = None,
    video_id: int | None = None,
    status: str | None = None,
    search_query: str = "",
    limit: int = 200,
) -> list[dict]:
    filters = ["videos.video_status = ?"]
    params: list = [VIDEO_STATUS_ACTIVE]
    if channel_db_id is not None:
        filters.append("publications.youtube_channel_id = ?")
        params.append(channel_db_id)
    if video_id is not None:
        filters.append("publications.video_id = ?")
        params.append(video_id)
    if status:
        filters.append("comments.status = ?")
        params.append(status)
    normalized_query = normalize_search_text(search_query)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    requested_limit = max(1, min(int(limit), 500))
    params.append(500 if normalized_query else requested_limit)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'''
        SELECT comments.*, publications.video_id, publications.youtube_channel_id,
               publications.youtube_video_id, publications.published_url,
               channels.title AS channel_title, videos.chat_url,
               videos.prompt_version, videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM youtube_comments AS comments
        JOIN video_publications AS publications ON publications.id = comments.publication_id
        JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        {where}
        ORDER BY comments.published_at DESC, comments.created_at DESC
        LIMIT ?
        ''',
        params,
    ).fetchall()
    conn.close()
    items = [dict(row) for row in rows]
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query
            in normalize_search_text(
                " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "text",
                        "author_name",
                        "video_title",
                        "published_url",
                    )
                )
            )
        ]
    return items[:requested_limit]


def get_youtube_comments(comment_ids: list[str]) -> list[dict]:
    if not comment_ids:
        return []
    placeholders = ",".join("?" for _ in comment_ids)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'''
        SELECT comments.*, publications.video_id, publications.youtube_channel_id,
               publications.youtube_video_id, publications.published_url,
               channels.title AS channel_title, channels.reply_instruction,
               channels.auto_mode, channels.daily_reply_limit,
               channels.reply_interval_minutes,
               channels.quarter_hour_reply_limit, channels.hourly_reply_limit,
               channels.video_half_hour_reply_limit,
               channels.backlog_daily_reply_limit,
               channels.reply_window_start, channels.reply_window_end,
               channels.reply_paused,
               videos.chat_url, videos.prompt_version,
               videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM youtube_comments AS comments
        JOIN video_publications AS publications ON publications.id = comments.publication_id
        JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        WHERE comments.comment_id IN ({placeholders})
          AND videos.video_status = ?
        ''',
        (*comment_ids, VIDEO_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    rows_by_id = {row["comment_id"]: dict(row) for row in rows}
    return [rows_by_id[comment_id] for comment_id in comment_ids if comment_id in rows_by_id]


def update_youtube_comment(comment_id: str, **changes) -> dict | None:
    allowed = {
        "status",
        "draft_reply",
        "reply_youtube_id",
        "reply_text",
        "error",
        "risk_level",
        "risk_reason",
        "reply_published_at",
        "auto_reply_priority",
        "auto_reply_reason",
        "is_hearted",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Unsupported YouTube comment fields: {sorted(invalid)}")
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"UPDATE youtube_comments SET {assignments} WHERE comment_id = ?",
        (*changes.values(), comment_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_comments WHERE comment_id = ?", (comment_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def count_channel_replies_since(channel_db_id: int, since_iso: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        '''
        SELECT COUNT(*)
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND comments.updated_at >= ?
        ''',
        (channel_db_id, since_iso),
    ).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def list_channel_reply_activity(channel_db_id: int, since_iso: str) -> list[dict]:
    """Return actual YouTube reply activity used by the publish scheduler."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT publications.video_id, comments.published_at AS comment_published_at,
               COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at)
                   AS replied_at
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at) >= ?
        ORDER BY replied_at DESC
        ''',
        (channel_db_id, since_iso),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_recent_channel_reply_texts(channel_db_id: int, limit: int = 20) -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        '''
        SELECT comments.reply_text
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND TRIM(comments.reply_text) != ''
        ORDER BY COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at) DESC
        LIMIT ?
        ''',
        (channel_db_id, max(1, min(int(limit), 50))),
    ).fetchall()
    conn.close()
    return [str(row[0]) for row in rows]


def is_managed_media_filename_referenced(filename: str) -> bool:
    normalized_filename = Path(filename).name
    if not normalized_filename or normalized_filename != filename:
        return False
    pattern = f"%{normalized_filename}%"
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            '''
            SELECT EXISTS (
                SELECT 1 FROM videos WHERE generated_script LIKE ?
                UNION ALL
                SELECT 1 FROM audio_tasks
                WHERE audio_url LIKE ? OR segments_json LIKE ?
                UNION ALL
                SELECT 1 FROM system_jobs
                WHERE payload_json LIKE ? OR result_json LIKE ?
            )
            ''',
            (pattern, pattern, pattern, pattern, pattern),
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()

def update_script(video_id: int, new_script: str) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT url, title FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False
    generated_title = extract_generated_video_title(new_script)
    c.execute(
        'UPDATE videos SET generated_script = ?, generated_title = ?, '
        'search_text = ? '
        'WHERE id = ?',
        (
            new_script,
            generated_title,
            build_video_search_text(row[0], row[1], new_script, generated_title),
            video_id,
        ),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_video_generation(
    video_id: int,
    generated_script: str,
    chat_url: str,
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT url, title FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False
    generated_title = extract_generated_video_title(generated_script)
    c.execute(
        '''
        UPDATE videos
        SET generated_script = ?, chat_url = ?, generated_title = ?,
            search_text = ?
        WHERE id = ?
        ''',
        (
            generated_script,
            chat_url,
            generated_title,
            build_video_search_text(
                row[0],
                row[1],
                generated_script,
                generated_title,
            ),
            video_id,
        ),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_audio_duration(video_id: int, duration_seconds: float | None) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'UPDATE videos SET audio_duration_seconds = ? WHERE id = ?',
        (duration_seconds, video_id),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_video_voice(
    video_id: int,
    voice_id: str,
    voice_name: str,
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        UPDATE videos
        SET voice_id = ?, voice_name = ?, tts_provider_id = ?,
            voice_revision = ?, voice_snapshot_json = ?
        WHERE id = ?
        ''',
        (
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
            video_id,
        ),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def get_audio_task(video_id: int) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM audio_tasks WHERE video_id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_audio_review(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM audio_reviews WHERE video_id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    review = dict(row)
    try:
        review["report"] = json.loads(review.get("report_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        review["report"] = {}
    review.pop("report_json", None)
    return review


def upsert_audio_review(
    video_id: int,
    script_hash: str,
    status: str,
    report: dict,
    reviewed_at: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_reviews (
            video_id, script_hash, status, report_json, reviewed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            script_hash = excluded.script_hash,
            status = excluded.status,
            report_json = excluded.report_json,
            reviewed_at = excluded.reviewed_at,
            updated_at = excluded.updated_at
        ''',
        (
            video_id,
            script_hash,
            status,
            json.dumps(report, ensure_ascii=False),
            reviewed_at,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_review(video_id)


def list_audio_reviews(limit: int | None = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list = [VIDEO_STATUS_ACTIVE]
    if limit is not None:
        params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT audio_reviews.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_reviews
        JOIN videos ON videos.id = audio_reviews.video_id
        WHERE videos.video_status = ?
        ORDER BY audio_reviews.updated_at DESC
        {limit_clause}
        ''',
        params,
    )
    rows = c.fetchall()
    conn.close()
    reviews = []
    for row in rows:
        review = dict(row)
        try:
            review["report"] = json.loads(review.get("report_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            review["report"] = {}
        review.pop("report_json", None)
        reviews.append(review)
    return reviews

def get_audio_task_by_request_hash(request_hash: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT audio_tasks.* FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE audio_tasks.request_hash = ? AND videos.video_status = ?
        ORDER BY
            CASE audio_tasks.status
                WHEN 'completed' THEN 0
                WHEN 'processing' THEN 1
                WHEN 'queued' THEN 2
                WHEN 'pending' THEN 3
                WHEN 'failed' THEN 4
                ELSE 5
            END,
            created_at ASC
        LIMIT 1
        ''',
        (request_hash, VIDEO_STATUS_ACTIVE),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_active_audio_tasks() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT audio_tasks.*
        FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE audio_tasks.status IN ('pending', 'processing', 'queued')
          AND videos.video_status = ?
        ''',
        (VIDEO_STATUS_ACTIVE,),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_audio_tasks(limit: int | None = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list = [VIDEO_STATUS_ACTIVE]
    if limit is not None:
        params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT audio_tasks.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE videos.video_status = ?
        ORDER BY audio_tasks.updated_at DESC
        {limit_clause}
        ''',
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def upsert_audio_task(
    video_id: int,
    request_hash: str,
    task_id: str,
    status: str,
    audio_url: str = '',
    error: str = '',
    segments_json: str = '',
    voice_id: str = '',
    voice_name: str = '',
    tts_provider_id: str = 'genmax',
    voice_revision: int = 1,
    voice_snapshot_json: str = '{}',
) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_tasks (
            video_id, request_hash, task_id, status, audio_url, error,
            segments_json, voice_id, voice_name, tts_provider_id,
            voice_revision, voice_snapshot_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            request_hash = excluded.request_hash,
            task_id = excluded.task_id,
            status = excluded.status,
            audio_url = excluded.audio_url,
            error = excluded.error,
            segments_json = excluded.segments_json,
            voice_id = CASE
                WHEN excluded.voice_id != '' THEN excluded.voice_id
                ELSE audio_tasks.voice_id
            END,
            voice_name = CASE
                WHEN excluded.voice_name != '' THEN excluded.voice_name
                ELSE audio_tasks.voice_name
            END,
            tts_provider_id = CASE
                WHEN excluded.tts_provider_id != '' THEN excluded.tts_provider_id
                ELSE audio_tasks.tts_provider_id
            END,
            voice_revision = excluded.voice_revision,
            voice_snapshot_json = CASE
                WHEN excluded.voice_snapshot_json NOT IN ('', '{}')
                THEN excluded.voice_snapshot_json
                ELSE audio_tasks.voice_snapshot_json
            END,
            updated_at = excluded.updated_at
        WHERE NOT (
            audio_tasks.status = 'completed'
            AND audio_tasks.request_hash = excluded.request_hash
            AND excluded.status != 'completed'
        )
        ''',
        (
            video_id,
            request_hash,
            task_id,
            status,
            audio_url,
            error,
            segments_json,
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or '{}',
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_task(video_id)


TTS_PREVIEW_MUTABLE_FIELDS = {
    "status",
    "audio_filename",
    "duration_seconds",
    "error",
}


def _decode_tts_preview(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    preview = dict(row)
    try:
        preview["voice_snapshot"] = json.loads(
            preview.get("voice_snapshot_json") or "{}"
        )
    except (TypeError, json.JSONDecodeError):
        preview["voice_snapshot"] = {}
    preview.pop("voice_snapshot_json", None)
    return preview


def create_tts_preview(
    *,
    preview_id: str,
    provider_task_id: str,
    request_hash: str,
    status: str,
    text: str,
    voice_id: str,
    voice_name: str,
    tts_provider_id: str,
    voice_revision: int,
    voice_snapshot_json: str,
    expires_at: str,
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        '''
        INSERT INTO tts_previews (
            id, provider_task_id, request_hash, status, text,
            character_count, voice_id, voice_name, tts_provider_id,
            voice_revision, voice_snapshot_json, created_at, updated_at,
            expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            preview_id,
            provider_task_id,
            request_hash,
            status,
            text,
            len(text),
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
            now,
            now,
            expires_at,
        ),
    )
    conn.commit()
    conn.close()
    return get_tts_preview(preview_id)


def get_tts_preview(preview_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tts_previews WHERE id = ?",
        (preview_id,),
    ).fetchone()
    conn.close()
    return _decode_tts_preview(row)


def list_active_tts_previews(provider_id: str = "") -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["status IN ('queued', 'processing')", "expires_at > ?"]
    params: list = [utc_now()]
    if provider_id:
        filters.append("tts_provider_id = ?")
        params.append(provider_id)
    rows = conn.execute(
        f"SELECT * FROM tts_previews WHERE {' AND '.join(filters)} "
        "ORDER BY created_at ASC",
        params,
    ).fetchall()
    conn.close()
    return [_decode_tts_preview(row) for row in rows]


def update_tts_preview(preview_id: str, **changes) -> dict | None:
    invalid_fields = set(changes) - TTS_PREVIEW_MUTABLE_FIELDS
    if invalid_fields:
        raise ValueError(f"Unsupported TTS preview fields: {sorted(invalid_fields)}")
    if not changes:
        return get_tts_preview(preview_id)
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE tts_previews SET {assignments} WHERE id = ?",
        (*changes.values(), preview_id),
    )
    conn.commit()
    conn.close()
    return get_tts_preview(preview_id)


def delete_expired_tts_previews(now: str | None = None) -> list[dict]:
    cutoff = now or utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM tts_previews WHERE expires_at <= ?",
            (cutoff,),
        ).fetchall()
        conn.execute(
            "DELETE FROM tts_previews WHERE expires_at <= ?",
            (cutoff,),
        )
        conn.execute("COMMIT")
        return [_decode_tts_preview(row) for row in rows]
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


SYSTEM_JOB_JSON_FIELDS = {"payload_json", "result_json"}
SYSTEM_JOB_MUTABLE_FIELDS = {
    "status",
    "title",
    "progress",
    "payload_json",
    "result_json",
    "error",
    "video_id",
    "prompt_version",
    "voice_id",
    "voice_name",
    "tts_provider_id",
    "voice_revision",
    "voice_snapshot_json",
    "attempt",
    "recovery_count",
    "resume_from_step",
    "next_retry_at",
    "cancel_requested",
    "started_at",
    "finished_at",
}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _decode_system_job(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    job = dict(row)
    for field in SYSTEM_JOB_JSON_FIELDS:
        try:
            job[field.removesuffix("_json")] = json.loads(job.get(field) or "{}")
        except (TypeError, json.JSONDecodeError):
            job[field.removesuffix("_json")] = {}
        job.pop(field, None)
    job["cancel_requested"] = bool(job.get("cancel_requested"))
    return job


def create_system_job(
    job_id: str,
    job_type: str,
    title: str,
    payload: dict,
    prompt_version: str = "",
    voice_id: str = "",
    voice_name: str = "",
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
    status: str = "queued",
    video_id: int | None = None,
) -> dict:
    now = utc_now()
    initial_progress = "Đang chạy" if status == "running" else "Đang chờ trong hàng đợi"
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO system_jobs (
            id, job_type, status, title, progress, payload_json,
            result_json, error, video_id, prompt_version, voice_id, voice_name,
            tts_provider_id, voice_revision, voice_snapshot_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, '{}', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            job_id,
            job_type,
            status,
            title,
            initial_progress,
            json.dumps(payload, ensure_ascii=False),
            video_id,
            prompt_version,
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_system_job(job_id)


def create_system_job_if_absent(
    *,
    job_id: str,
    job_type: str,
    title: str,
    payload: dict,
    video_id: int,
    prompt_version: str = "",
    dedupe_job_types: tuple[str, ...] | None = None,
) -> tuple[dict, bool]:
    """Atomically return an active video job or create the requested one."""
    job_types = tuple(dedupe_job_types or (job_type,))
    if not job_types:
        job_types = (job_type,)
    placeholders = ", ".join("?" for _ in job_types)
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            f'''
            SELECT * FROM system_jobs
            WHERE video_id = ?
              AND job_type IN ({placeholders})
              AND status IN ('queued', 'running', 'retry_wait', 'paused')
              AND cancel_requested = 0
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            ''',
            (video_id, *job_types),
        ).fetchone()
        if existing is not None:
            conn.execute("COMMIT")
            return _decode_system_job(existing), False

        conn.execute(
            '''
            INSERT INTO system_jobs (
                id, job_type, status, title, progress, payload_json,
                result_json, error, video_id, prompt_version,
                created_at, updated_at
            ) VALUES (?, ?, 'queued', ?, 'Đang chờ trong hàng đợi', ?, '{}', '', ?, ?, ?, ?)
            ''',
            (
                job_id,
                job_type,
                title,
                json.dumps(payload, ensure_ascii=False),
                video_id,
                prompt_version,
                now,
                now,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    created = get_system_job(job_id)
    if created is None:
        raise RuntimeError("Không thể đọc lại system job vừa tạo.")
    return created, True


def reclassify_active_system_job(
    job_id: str,
    *,
    expected_job_type: str,
    new_job_type: str,
    title: str,
    progress: str,
) -> dict | None:
    """Move one non-terminal job to another durable queue without changing its id."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_type, status FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if (
            row is None
            or row["job_type"] != expected_job_type
            or row["status"] not in {"queued", "running", "retry_wait", "paused"}
        ):
            conn.execute("COMMIT")
            return get_system_job(job_id) if row is not None else None
        conn.execute(
            '''
            UPDATE system_jobs
            SET job_type = ?, title = ?, progress = ?, updated_at = ?
            WHERE id = ?
            ''',
            (new_job_type, title, progress, utc_now(), job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(job_id)


def get_system_job(job_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM system_jobs WHERE id = ?", (job_id,))
    row = c.fetchone()
    conn.close()
    return _decode_system_job(row)


def list_system_jobs(
    limit: int | None = 100,
    job_type: str | None = None,
    video_id: int | None = None,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    filters = [
        "(system_jobs.video_id IS NULL OR videos.video_status = ?)"
    ]
    params: list = [VIDEO_STATUS_ACTIVE]
    if job_type:
        filters.append("system_jobs.job_type = ?")
        params.append(job_type)
    if video_id is not None:
        filters.append("system_jobs.video_id = ?")
        params.append(video_id)
    where_clause = f" WHERE {' AND '.join(filters)}"
    limit_clause = " LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT system_jobs.*,
               videos.url AS video_url,
               videos.title AS original_title,
               videos.generated_title AS generated_title
        FROM system_jobs
        LEFT JOIN videos ON videos.id = system_jobs.video_id
        {where_clause}
        ORDER BY system_jobs.created_at DESC
        {limit_clause}
        ''',
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [_decode_system_job(row) for row in rows]


def list_active_system_jobs(job_type: str | None = None) -> list[dict]:
    """Return every active job in stable ownership order without a UI limit."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["status IN ('queued', 'running', 'retry_wait', 'paused')"]
    params: list = []
    if job_type:
        filters.append("job_type = ?")
        params.append(job_type)
    rows = conn.execute(
        f'''
        SELECT * FROM system_jobs
        WHERE {' AND '.join(filters)}
        ORDER BY created_at ASC, id ASC
        ''',
        params,
    ).fetchall()
    conn.close()
    return [_decode_system_job(row) for row in rows]


def pause_queued_attention_jobs() -> int:
    """Keep verification jobs paused and normalize interrupted auto-login state."""
    candidates = list_active_system_jobs()
    paused = 0
    for job in candidates:
        result = dict(job.get("result") or {})
        if (
            job.get("status") == "paused"
            and result.get("automatic_login") == "pending"
        ):
            result["automatic_login"] = "interrupted"
            update_system_job(
                job["id"],
                progress=(
                    "Auto Login tự động bị gián đoạn khi hệ thống khởi động lại; "
                    "cần xác minh thủ công"
                ),
                result_json=result,
            )
            continue
        if not result.get("attention_required"):
            continue
        if job.get("status") not in {"queued", "retry_wait"}:
            continue
        update_system_job(
            job["id"],
            status="paused",
            progress="Cần xác minh phiên ChatGPT trước khi tiếp tục",
            next_retry_at="",
            started_at="",
        )
        paused += 1
    return paused


def fail_interrupted_system_jobs(job_type: str, message: str) -> int:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    now = utc_now()
    try:
        rows = conn.execute(
            "SELECT id, cancel_requested FROM system_jobs WHERE job_type = ? AND status IN ('running', 'queued', 'processing')",
            (job_type,),
        ).fetchall()
        count = len(rows)
        for row in rows:
            new_status = "canceled" if row["cancel_requested"] else "failed"
            conn.execute(
                "UPDATE system_jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (new_status, message, now, row["id"]),
            )
        conn.commit()
        return count
    finally:
        conn.close()


def has_active_system_job_for_video(job_type: str, video_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        '''
        SELECT 1 FROM system_jobs
        WHERE job_type = ? AND video_id = ?
          AND status IN ('queued', 'running', 'retry_wait', 'paused')
        LIMIT 1
        ''',
        (job_type, video_id),
    ).fetchone()
    conn.close()
    return row is not None


def update_system_job(job_id: str, **changes) -> dict | None:
    invalid_fields = set(changes) - SYSTEM_JOB_MUTABLE_FIELDS
    if invalid_fields:
        raise ValueError(f"Unsupported system job fields: {sorted(invalid_fields)}")
    if not changes:
        return get_system_job(job_id)

    encoded_changes = dict(changes)
    for field in SYSTEM_JOB_JSON_FIELDS:
        if field in encoded_changes and not isinstance(encoded_changes[field], str):
            encoded_changes[field] = json.dumps(
                encoded_changes[field],
                ensure_ascii=False,
            )
    encoded_changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in encoded_changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        f"UPDATE system_jobs SET {assignments} WHERE id = ?",
        (*encoded_changes.values(), job_id),
    )
    conn.commit()
    conn.close()
    return get_system_job(job_id)


def update_editable_video_job(
    job_id: str,
    *,
    title: str,
    payload: dict,
    prompt_version: str,
    voice_id: str,
    voice_name: str = "",
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        if row["job_type"] != "video_generation":
            raise ValueError("Chỉ có thể sửa job tạo video.")
        if row["status"] not in {"queued", "paused", "error", "canceled"}:
            raise ValueError("Không thể sửa job đang chạy hoặc đã hoàn thành.")
        if row["video_id"] is not None and row["status"] in {"queued", "paused"}:
            raise ValueError(
                "Job đã có checkpoint nên không thể sửa khi đang chờ tiếp tục."
            )

        changes = {
            "title": title,
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "prompt_version": prompt_version,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "tts_provider_id": tts_provider_id,
            "voice_revision": max(1, int(voice_revision or 1)),
            "voice_snapshot_json": voice_snapshot_json or "{}",
            "updated_at": utc_now(),
        }
        if row["status"] in {"error", "canceled"}:
            changes.update({
                "progress": "Đã cập nhật; sẵn sàng chạy lại",
                "result_json": "{}",
                "error": "",
                "video_id": None,
                "recovery_count": 0,
                "resume_from_step": "",
                "next_retry_at": "",
                "cancel_requested": 0,
            })
        assignments = ", ".join(f"{field} = ?" for field in changes)
        conn.execute(
            f"UPDATE system_jobs SET {assignments} WHERE id = ?",
            (*changes.values(), job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(job_id)


def delete_system_job(job_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        if row["status"] == "running":
            raise ValueError("Hãy dừng job đang chạy trước khi xóa.")
        conn.execute("DELETE FROM system_jobs WHERE id = ?", (job_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return _decode_system_job(row)


def claim_next_system_job(job_type: str) -> dict | None:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            '''
            SELECT * FROM system_jobs
            WHERE job_type = ? AND cancel_requested = 0
              AND status IN ('queued', 'retry_wait')
              AND (
                  ? NOT IN ('comment_publish', 'comment_sync') OR status = 'queued'
                  OR next_retry_at = '' OR next_retry_at <= ?
              )
              AND (
                  video_id IS NULL OR EXISTS (
                      SELECT 1 FROM videos
                      WHERE videos.id = system_jobs.video_id
                        AND videos.video_status = 'active'
                  )
              )
            ORDER BY created_at ASC
            LIMIT 1
            ''',
            (job_type, job_type, now),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        if (
            row["status"] == "retry_wait"
            and row["next_retry_at"]
            and row["next_retry_at"] > now
        ):
            conn.execute("COMMIT")
            return None
        conn.execute(
            '''
            UPDATE system_jobs
            SET status = 'running', progress = ?, attempt = attempt + 1,
                started_at = ?, finished_at = '', next_retry_at = '', updated_at = ?
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            ''',
            ("Đang khởi động", now, now, row["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(row["id"])


def recover_interrupted_system_jobs(job_type: str) -> int:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        '''
        UPDATE system_jobs
        SET status = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN 'canceled'
                ELSE 'queued'
            END,
            progress = CASE
                WHEN cancel_requested = 1 THEN 'Đã hủy khi ứng dụng khởi động lại'
                WHEN EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN 'Đã bỏ qua vì video ở trạng thái Lỗi'
                ELSE 'Đã khôi phục sau khi ứng dụng khởi động lại'
            END,
            finished_at = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN ? ELSE '' END,
            recovery_count = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN recovery_count
                ELSE recovery_count + 1
            END,
            next_retry_at = '',
            cancel_requested = 0,
            updated_at = ?
        WHERE job_type = ? AND status = 'running'
        ''',
        (now, now, job_type),
    )
    recovered = c.rowcount
    conn.commit()
    conn.close()
    return recovered


def schedule_system_job_recovery(
    job_id: str,
    resume_from_step: str,
    delay_seconds: float,
    error: str,
    result_json: dict | None = None,
) -> dict | None:
    retry_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=max(0.0, float(delay_seconds)))
    ).isoformat()
    changes = {
        "status": "retry_wait",
        "progress": (
            f"Chờ tự phục hồi từ bước {resume_from_step or 'gần nhất'}"
        ),
        "error": error,
        "resume_from_step": resume_from_step,
        "next_retry_at": retry_at,
        "cancel_requested": 0,
        "finished_at": "",
    }
    if result_json is not None:
        changes["result_json"] = result_json

    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT recovery_count FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        changes["recovery_count"] = int(row[0] or 0) + 1
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{field} = ?" for field in changes)
        encoded_values = []
        for field, value in changes.items():
            if field in SYSTEM_JOB_JSON_FIELDS and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            encoded_values.append(value)
        conn.execute(
            f"UPDATE system_jobs SET {assignments} WHERE id = ?",
            (*encoded_values, job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(job_id)


def has_claimable_system_jobs(job_type: str) -> bool:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT status, next_retry_at FROM system_jobs
        WHERE job_type = ? AND cancel_requested = 0
          AND status IN ('queued', 'retry_wait')
          AND (
              ? NOT IN ('comment_publish', 'comment_sync') OR status = 'queued'
              OR next_retry_at = '' OR next_retry_at <= ?
          )
          AND (
              video_id IS NULL OR EXISTS (
                  SELECT 1 FROM videos
                  WHERE videos.id = system_jobs.video_id
                    AND videos.video_status = 'active'
              )
          )
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (job_type, job_type, now),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return False
    return (
        row["status"] == "queued"
        or not row["next_retry_at"]
        or row["next_retry_at"] <= now
    )


def get_next_system_job_retry_delay(job_type: str) -> float | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT status, next_retry_at FROM system_jobs
        WHERE job_type = ? AND status IN ('queued', 'retry_wait')
          AND cancel_requested = 0
          AND (
              video_id IS NULL OR EXISTS (
                  SELECT 1 FROM videos
                  WHERE videos.id = system_jobs.video_id
                    AND videos.video_status = 'active'
              )
          )
        ORDER BY
            CASE WHEN ? IN ('comment_publish', 'comment_sync') AND status = 'queued' THEN 0 ELSE 1 END,
            CASE WHEN ? IN ('comment_publish', 'comment_sync') THEN next_retry_at ELSE created_at END ASC,
            created_at ASC
        LIMIT 1
        ''',
        (job_type, job_type, job_type),
    )
    row = c.fetchone()
    conn.close()
    if not row or row["status"] == "queued":
        return 0.0 if row else None
    if not row["next_retry_at"]:
        return None
    try:
        retry_at = datetime.datetime.fromisoformat(row["next_retry_at"])
    except (TypeError, ValueError):
        return 0.0
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (retry_at - now).total_seconds())


def force_stop_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    return update_system_job(
        job_id,
        status="canceled",
        progress="Đã dừng cưỡng bức ngay lập tức",
        error="Bị buộc dừng ngay lập tức bởi người dùng.",
        cancel_requested=0,
        finished_at=utc_now(),
    )


def request_cancel_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] in {"queued", "retry_wait", "paused"}:
        return update_system_job(
            job_id,
            status="canceled",
            progress="Đã hủy khỏi hàng đợi",
            cancel_requested=0,
            finished_at=utc_now(),
        )
    if job["status"] == "running":
        return update_system_job(
            job_id,
            progress="Đã nhận yêu cầu dừng; sẽ dừng tại điểm an toàn",
            cancel_requested=1,
        )
    return job


def pause_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] not in {"queued", "retry_wait"}:
        raise ValueError("Chỉ có thể tạm dừng job đang chờ.")
    return update_system_job(
        job_id,
        status="paused",
        progress="Đã tạm dừng trong hàng đợi",
    )


def resume_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] != "paused":
        raise ValueError("Chỉ có thể tiếp tục job đang tạm dừng.")
    changes = {
        "status": "queued",
        "progress": "Đang chờ sau khi tiếp tục",
        "next_retry_at": "",
    }
    if (job.get("result") or {}).get("attention_required"):
        changes.update({"result_json": {}, "error": "", "started_at": ""})
    return update_system_job(job_id, **changes)


def retry_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] not in {"error", "failed", "canceled"}:
        raise ValueError("Chỉ có thể chạy lại job lỗi hoặc đã hủy.")
    if job.get("video_id") is not None:
        video = get_video(int(job["video_id"]))
        if video and video.get("video_status") == VIDEO_STATUS_ERROR:
            raise ValueError(
                "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
            )
    return update_system_job(
        job_id,
        status="queued",
        progress="Đang chờ chạy lại",
        error="",
        result_json={},
        recovery_count=0,
        next_retry_at="",
        cancel_requested=0,
        finished_at="",
    )


def resume_system_job_from_checkpoint(
    job_id: str,
    resume_from_step: str,
) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["job_type"] != "video_generation":
        raise ValueError("Chỉ job tạo video mới có thể tiếp tục từ checkpoint.")
    if job["status"] not in {"error", "failed", "canceled"}:
        raise ValueError("Chỉ có thể tiếp tục job lỗi hoặc đã hủy.")
    if job.get("video_id") is None:
        raise ValueError("Job chưa gắn với video nên không thể tiếp tục checkpoint.")
    video = get_video(int(job["video_id"]))
    if video and video.get("video_status") == VIDEO_STATUS_ERROR:
        raise ValueError(
            "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
        )
    normalized_step = str(resume_from_step or "checkpoint").strip() or "checkpoint"
    return update_system_job(
        job_id,
        status="queued",
        progress=f"Đang chờ tiếp tục từ checkpoint: {normalized_step}",
        error="",
        recovery_count=0,
        resume_from_step=normalized_step,
        next_retry_at="",
        cancel_requested=0,
        finished_at="",
    )


def get_system_job_queue_position(job_id: str) -> int | None:
    job = get_system_job(job_id)
    if not job or job["status"] != "queued":
        return None
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        SELECT COUNT(*) FROM system_jobs
        WHERE job_type = ? AND status = 'queued' AND cancel_requested = 0
          AND created_at <= ?
        ''',
        (job["job_type"], job["created_at"]),
    )
    position = int(c.fetchone()[0])
    conn.close()
    return position

# Initialize tables when module is imported
init_db()


# =========================================================================
# Facebook Cross-Poster Database Methods
# =========================================================================


def _normalize_fb_crossposter_default_tags(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            value = parsed if isinstance(parsed, list) else re.split(r"[,\n]", value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = re.split(r"[,\n]", value)
    if not isinstance(value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        tag = re.sub(r"\s+", " ", str(raw_tag or "").strip().lstrip("#").strip())
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


def _load_fb_crossposter_settings(page_id: str | None = None) -> dict:
    """Load one settings row, including encrypted fields for trusted backend callers."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = None
        if page_id and page_id.strip():
            row = conn.execute(
                "SELECT * FROM fb_crossposter_settings WHERE target_fb_page_id = ? LIMIT 1",
                (page_id.strip(),)
            ).fetchone()
        
        if not row and not (page_id and page_id.strip()):
            row = conn.execute(
                "SELECT * FROM fb_crossposter_settings ORDER BY id ASC LIMIT 1"
            ).fetchone()

        if not row and not (page_id and page_id.strip()):
            conn.execute("INSERT OR IGNORE INTO fb_crossposter_settings (id, lead_time_minutes) VALUES (1, 60)")
            conn.commit()
            row = conn.execute("SELECT * FROM fb_crossposter_settings WHERE id = 1").fetchone()
        
        data = dict(row) if row else {
            "target_fb_page_id": str(page_id or "").strip(),
            "target_access_token": "",
            "target_access_token_encrypted": "",
        }
        if isinstance(data.get("schedule_times_json"), str):
            try:
                data["schedule_times"] = json.loads(data["schedule_times_json"])
            except Exception:
                data["schedule_times"] = ["11:30", "19:30"]
        else:
            data["schedule_times"] = ["11:30", "19:30"]

        if isinstance(data.get("auto_sync_fixed_times_json"), str):
            try:
                data["auto_sync_fixed_times"] = json.loads(data["auto_sync_fixed_times_json"])
            except Exception:
                data["auto_sync_fixed_times"] = ["06:00", "18:00"]
        else:
            data["auto_sync_fixed_times"] = ["06:00", "18:00"]

        data["default_tags"] = _normalize_fb_crossposter_default_tags(
            data.get("default_tags_json", "[]")
        )
        data["convert_to_vertical"] = bool(data.get("convert_to_vertical"))
        data["lead_time_minutes"] = int(data.get("lead_time_minutes") or 60)
        data["upload_mode"] = str(data.get("upload_mode") or "browser").strip() or "browser"
        return data
    finally:
        conn.close()


def _safe_fb_crossposter_settings(data: dict) -> dict:
    safe = dict(data or {})
    encrypted = str(safe.pop("target_access_token_encrypted", "") or "")
    legacy = str(safe.pop("target_access_token", "") or "")
    safe["target_access_token_configured"] = bool(encrypted or legacy)
    return safe


def get_fb_crossposter_settings(page_id: str | None = None) -> dict:
    """Return Cross-Poster settings without exposing the Page Access Token."""
    return _safe_fb_crossposter_settings(_load_fb_crossposter_settings(page_id))


def get_fb_crossposter_runtime_settings(page_id: str | None = None) -> dict:
    """Return settings with a decrypted token for backend-only Facebook operations."""
    from auto_yt.services.secret_store import decrypt_secret

    data = _load_fb_crossposter_settings(page_id)
    encrypted = str(data.pop("target_access_token_encrypted", "") or "")
    legacy = str(data.pop("target_access_token", "") or "")
    data["target_access_token"] = decrypt_secret(encrypted or legacy)
    data["target_access_token_configured"] = bool(encrypted or legacy)
    return data


def list_all_crossposter_campaigns() -> list[dict]:
    """List all configured Fanpage campaigns with their queue counts."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM fb_crossposter_settings ORDER BY id ASC").fetchall()
        campaigns = []
        for r in rows:
            d = _safe_fb_crossposter_settings(dict(r))
            pid = d.get("target_fb_page_id", "")
            d["page_id"] = pid
            d["page_name"] = d.get("target_fb_page_name") or pid or "Default Fanpage"
            stats = get_fb_crossposter_stats(target_page_id=pid)
            d["stats"] = stats
            try:
                d["schedule_times"] = json.loads(d.get("schedule_times_json") or "[]")
            except Exception:
                d["schedule_times"] = ["11:30", "19:30"]
            d["default_tags"] = _normalize_fb_crossposter_default_tags(
                d.get("default_tags_json", "[]")
            )
            d["convert_to_vertical"] = bool(d.get("convert_to_vertical"))
            d["upload_mode"] = str(d.get("upload_mode") or "browser").strip() or "browser"
            campaigns.append(d)
        return campaigns
    finally:
        conn.close()


def save_fb_crossposter_settings(settings: dict, page_id: str | None = None) -> dict:
    """Save or update FB Cross-Poster settings for a specific Fanpage."""
    from auto_yt.services.secret_store import encrypt_secret

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    now_iso = datetime.datetime.now().isoformat()
    target_page_id = (page_id or settings.get("target_fb_page_id") or "").strip()
    try:
        schedule_times = settings.get("schedule_times", ["11:30", "19:30"])
        schedule_times_json = json.dumps(schedule_times) if not isinstance(schedule_times, str) else schedule_times

        fixed_times = settings.get("auto_sync_fixed_times", ["06:00", "18:00"])
        fixed_times_json = json.dumps(fixed_times) if not isinstance(fixed_times, str) else fixed_times

        default_tags = _normalize_fb_crossposter_default_tags(
            settings.get("default_tags", [])
        )
        default_tags_json = json.dumps(default_tags, ensure_ascii=False)

        lead_time = int(settings.get("lead_time_minutes") or 60)
        upload_mode = str(settings.get("upload_mode") or "browser").strip() or "browser"

        existing = None
        if target_page_id:
            existing = conn.execute(
                "SELECT id, target_fb_page_id, last_synced_at, "
                "target_access_token_encrypted "
                "FROM fb_crossposter_settings WHERE target_fb_page_id = ?",
                (target_page_id,)
            ).fetchone()

        if not existing:
            first_row = conn.execute(
                "SELECT id, target_fb_page_id, last_synced_at, "
                "target_access_token_encrypted "
                "FROM fb_crossposter_settings ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if first_row and (not target_page_id or not first_row["target_fb_page_id"]):
                existing = first_row

        last_synced_at_val = settings.get("last_synced_at")
        if last_synced_at_val is None and existing:
            last_synced_at_val = existing["last_synced_at"] if existing["last_synced_at"] is not None else ""
        elif last_synced_at_val is None:
            last_synced_at_val = ""

        supplied_token = str(settings.get("target_access_token") or "").strip()
        encrypted_token = (
            encrypt_secret(supplied_token)
            if supplied_token
            else str(existing["target_access_token_encrypted"] or "") if existing else ""
        )

        values = (
            settings.get("source_channel_id", ""),
            settings.get("source_channel_title", ""),
            settings.get("source_gpm_profile_id", ""),
            target_page_id,
            settings.get("target_fb_page_name", ""),
            settings.get("target_gpm_profile_id", ""),
            encrypted_token,
            int(settings.get("daily_quota", 2)),
            schedule_times_json,
            lead_time,
            settings.get("post_template", ""),
            settings.get("sort_order_mode", "oldest_first"),
            1 if settings.get("auto_sync_enabled") else 0,
            settings.get("auto_sync_type", "interval"),
            int(settings.get("auto_sync_interval_hours", 6)),
            fixed_times_json,
            last_synced_at_val,
            1 if settings.get("auto_publish_enabled") else 0,
            1 if settings.get("convert_to_vertical") else 0,
            default_tags_json,
            upload_mode,
            now_iso,
        )

        if existing:
            conn.execute("""
                UPDATE fb_crossposter_settings SET
                    source_channel_id = ?,
                    source_channel_title = ?,
                    source_gpm_profile_id = ?,
                    target_fb_page_id = ?,
                    target_fb_page_name = ?,
                    target_gpm_profile_id = ?,
                    target_access_token = '',
                    target_access_token_encrypted = ?,
                    daily_quota = ?,
                    schedule_times_json = ?,
                    lead_time_minutes = ?,
                    post_template = ?,
                    sort_order_mode = ?,
                    auto_sync_enabled = ?,
                    auto_sync_type = ?,
                    auto_sync_interval_hours = ?,
                    auto_sync_fixed_times_json = ?,
                    last_synced_at = ?,
                    auto_publish_enabled = ?,
                    convert_to_vertical = ?,
                    default_tags_json = ?,
                    upload_mode = ?,
                    updated_at = ?
                WHERE id = ?
            """, (*values, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO fb_crossposter_settings (
                    source_channel_id, source_channel_title, source_gpm_profile_id,
                    target_fb_page_id, target_fb_page_name, target_gpm_profile_id,
                    target_access_token, target_access_token_encrypted,
                    daily_quota, schedule_times_json, lead_time_minutes, post_template,
                    sort_order_mode, auto_sync_enabled, auto_sync_type,
                    auto_sync_interval_hours, auto_sync_fixed_times_json,
                    last_synced_at, auto_publish_enabled, convert_to_vertical,
                    default_tags_json, upload_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
        conn.commit()
    finally:
        conn.close()
    return get_fb_crossposter_settings(page_id=target_page_id)


def upsert_fb_crossposter_queue_items(items: list[dict], target_page_id: str = "") -> dict:
    """Upsert YouTube videos into fb_crossposter_queue isolated by target_page_id.
    Preserves status for existing records. Returns count of inserted vs existing.
    """
    if not items:
        return {"inserted": 0, "existing": 0}

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    now_iso = datetime.datetime.now().isoformat()
    inserted_count = 0
    existing_count = 0
    target_pid = (target_page_id or "").strip()
    seen_in_batch: set[str] = set()

    try:
        for idx, item in enumerate(items):
            yt_id = item.get("youtube_id")
            if not yt_id:
                continue
            if yt_id in seen_in_batch:
                continue
            seen_in_batch.add(yt_id)
            tags = item.get("original_tags", [])
            tags_json = json.dumps(tags, ensure_ascii=False) if isinstance(tags, list) else str(tags)
            original_description = str(item.get("original_description") or "")

            existing = conn.execute(
                "SELECT id FROM fb_crossposter_queue WHERE youtube_id = ? AND target_page_id = ?",
                (yt_id, target_pid)
            ).fetchone()

            if existing:
                existing_count += 1
                conn.execute("""
                    UPDATE fb_crossposter_queue SET
                        original_title = ?,
                        original_description = CASE WHEN ? != '' THEN ? ELSE original_description END,
                        original_tags_json = CASE WHEN ? != '[]' THEN ? ELSE original_tags_json END,
                        fb_title = CASE 
                            WHEN fb_title = original_title OR fb_title = '' OR fb_title IS NULL OR status = 'scheduled' 
                            THEN ? 
                            ELSE fb_title 
                        END,
                        fb_description = CASE 
                            WHEN COALESCE(fb_description_source, '') != 'manual'
                            THEN ?
                            ELSE fb_description
                        END,
                        fb_description_source = CASE
                            WHEN COALESCE(fb_description_source, '') = 'manual' THEN 'manual'
                            ELSE 'auto'
                        END,
                        thumbnail_url = CASE WHEN ? != '' THEN ? ELSE thumbnail_url END,
                        youtube_upload_date = ?,
                        sort_order = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'scheduled')
                """, (
                    item.get("original_title", ""),
                    original_description,
                    original_description,
                    tags_json,
                    tags_json,
                    item.get("fb_title") or item.get("original_title", ""),
                    item.get("fb_description", ""),
                    item.get("thumbnail_url", ""),
                    item.get("thumbnail_url", ""),
                    item.get("youtube_upload_date", ""),
                    item.get("sort_order", idx + 1),
                    now_iso,
                    existing[0]
                ))
            else:
                try:
                    conn.execute("""
                        INSERT INTO fb_crossposter_queue (
                            target_page_id, youtube_id, youtube_url, original_title, original_description,
                            original_tags_json, thumbnail_url, youtube_upload_date,
                            fb_title, fb_description, fb_description_source, status, sort_order,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """, (
                        target_pid,
                        yt_id,
                        item.get("youtube_url", f"https://www.youtube.com/watch?v={yt_id}"),
                        item.get("original_title", ""),
                        item.get("original_description", ""),
                        tags_json,
                        item.get("thumbnail_url", ""),
                        item.get("youtube_upload_date", ""),
                        item.get("fb_title", item.get("original_title", "")),
                        item.get("fb_description", ""),
                        item.get("fb_description_source", "auto"),
                        item.get("sort_order", idx + 1),
                        now_iso,
                        now_iso
                    ))
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    existing_count += 1
        conn.commit()
    finally:
        conn.close()

    return {"inserted": inserted_count, "existing": existing_count}


def get_fb_crossposter_queue(
    target_page_id: str = "",
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
    search: str = ""
) -> dict:
    """List queue items with pagination, filtering by target_page_id, and search."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        where_clauses = []
        params = []

        if target_page_id and target_page_id.strip():
            where_clauses.append("target_page_id = ?")
            params.append(target_page_id.strip())

        if status and status != "all":
            where_clauses.append("status = ?")
            params.append(status)

        if search.strip():
            where_clauses.append("(original_title LIKE ? OR fb_title LIKE ? OR youtube_id LIKE ?)")
            term = f"%{search.strip()}%"
            params.extend([term, term, term])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        total_row = conn.execute(
            f"SELECT COUNT(*) as total FROM fb_crossposter_queue {where_sql}",
            params
        ).fetchone()
        total_items = total_row["total"] if total_row else 0

        offset = max(0, (page - 1) * page_size)
        query_params = list(params) + [page_size, offset]

        rows = conn.execute(f"""
            SELECT * FROM fb_crossposter_queue
            {where_sql}
            ORDER BY 
                CASE WHEN scheduled_publish_time > 0 THEN 0 ELSE 1 END,
                CASE WHEN scheduled_publish_time > 0 THEN scheduled_publish_time ELSE 9999999999 END ASC,
                sort_order ASC,
                id ASC
            LIMIT ? OFFSET ?
        """, query_params).fetchall()

        items = []
        for row in rows:
            d = dict(row)
            try:
                d["original_tags"] = json.loads(d.get("original_tags_json") or "[]")
            except Exception:
                d["original_tags"] = []
            items.append(d)

        return {
            "items": items,
            "total": total_items,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_items + page_size - 1) // page_size)
        }
    finally:
        conn.close()


def get_fb_crossposter_queue_item(item_id: int) -> dict | None:
    """Get a single queue item by ID."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM fb_crossposter_queue WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["original_tags"] = json.loads(d.get("original_tags_json") or "[]")
        except Exception:
            d["original_tags"] = []
        return d
    finally:
        conn.close()


def update_fb_crossposter_queue_item(item_id: int, fields: dict) -> bool:
    """Update specific fields of a queue item."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        allowed = {
            "fb_title", "fb_description", "status", "scheduled_publish_time",
            "fb_post_id", "error_message", "sort_order", "file_size_bytes", "target_page_id",
            "original_title", "original_description", "original_tags_json", "thumbnail_url",
            "fb_description_source", "meta_published", "meta_video_status",
            "meta_scheduled_publish_time", "meta_status_json", "meta_verified_at",
            "meta_state", "meta_state_since", "meta_error_message",
            "upload_session_id", "upload_video_id", "upload_phase",
            "upload_retry_count", "upload_next_retry_at", "previous_fb_post_id",
            "cleanup_status", "repair_history_json",
            "checkpoint_phase", "checkpoint_data_json", "checkpoint_screenshot", "can_resume",
        }
        updates = []
        values = []
        for k, v in fields.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                values.append(v)
        
        if not updates:
            return False

        updates.append("updated_at = ?")
        values.append(datetime.datetime.now().isoformat())
        values.append(item_id)

        sql = f"UPDATE fb_crossposter_queue SET {', '.join(updates)} WHERE id = ?"
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(sql, values)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValueError("Thời điểm đăng này đã được một video khác giữ trên cùng Fanpage") from exc
    finally:
        conn.close()


def update_fb_checkpoint(
    item_id: int,
    phase: str,
    *,
    status: str | None = None,
    checkpoint_data: dict | None = None,
    screenshot_path: str | None = None,
    can_resume: bool = True,
    error_message: str | None = None,
) -> bool:
    """Record a checkpoint phase update with optional asset metadata and error screenshot."""
    fields: dict[str, Any] = {
        "checkpoint_phase": str(phase or "").strip(),
        "can_resume": 1 if can_resume else 0,
    }
    if status is not None:
        fields["status"] = status
    if checkpoint_data is not None:
        fields["checkpoint_data_json"] = json.dumps(checkpoint_data, ensure_ascii=False)
    if screenshot_path is not None:
        fields["checkpoint_screenshot"] = str(screenshot_path or "").strip()
    if error_message is not None:
        fields["error_message"] = str(error_message or "").strip()
        fields["meta_error_message"] = str(error_message or "").strip()
    return update_fb_crossposter_queue_item(item_id, fields)


def reset_fb_checkpoint(item_id: int) -> bool:
    """Reset checkpoint state back to fresh pending/scheduled state."""
    return update_fb_crossposter_queue_item(item_id, {
        "checkpoint_phase": "",
        "checkpoint_data_json": "{}",
        "checkpoint_screenshot": "",
        "can_resume": 0,
        "error_message": "",
        "meta_error_message": "",
        "meta_state": "",
        "status": "pending",
    })


def append_fb_recovery_history(item_id: int, event: str, details: dict | None = None) -> None:
    """Append a bounded, non-secret recovery audit event to one queue item."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT repair_history_json FROM fb_crossposter_queue WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Không tìm thấy video ID #{item_id} trong hàng đợi")
        try:
            history = json.loads(row[0] or "[]")
        except (TypeError, ValueError):
            history = []
        if not isinstance(history, list):
            history = []
        history.append({
            "event": str(event or "unknown"),
            "at": utc_now(),
            "details": details or {},
        })
        conn.execute(
            "UPDATE fb_crossposter_queue SET repair_history_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(history[-50:], ensure_ascii=False), datetime.datetime.now().isoformat(), item_id),
        )
        conn.commit()
    finally:
        conn.close()


def reserve_next_fb_queue_slot(
    item_id: int,
    *,
    minimum_lead_minutes: int = 30,
    clear_meta_object: bool = False,
    max_horizon_days: int = 70,
) -> int:
    """Atomically reserve the next configured free slot for an item on its Fanpage."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        item = conn.execute(
            "SELECT * FROM fb_crossposter_queue WHERE id = ?",
            (item_id,),
        ).fetchone()
        if item is None:
            raise ValueError(f"Không tìm thấy video ID #{item_id} trong hàng đợi")
        page_id = str(item["target_page_id"] or "").strip()
        settings = conn.execute(
            "SELECT * FROM fb_crossposter_settings WHERE target_fb_page_id = ? ORDER BY id LIMIT 1",
            (page_id,),
        ).fetchone()
        try:
            configured_times = json.loads(
                (settings["schedule_times_json"] if settings else "") or '["11:30", "19:30"]'
            )
        except (TypeError, ValueError):
            configured_times = ["11:30", "19:30"]
        valid_times: list[datetime.time] = []
        for value in configured_times if isinstance(configured_times, list) else []:
            try:
                hour, minute = (int(part) for part in str(value).split(":", 1))
                valid_times.append(datetime.time(hour=hour, minute=minute))
            except (TypeError, ValueError):
                continue
        if not valid_times:
            raise ValueError("Fanpage chưa có khung giờ đăng hợp lệ")
        valid_times.sort()
        daily_quota = max(1, int((settings["daily_quota"] if settings else 0) or len(valid_times)))
        valid_times = valid_times[:daily_quota]
        configured_lead = int((settings["lead_time_minutes"] if settings else 0) or 0)
        lead_minutes = max(30, int(minimum_lead_minutes), configured_lead)
        now = datetime.datetime.now()
        earliest_ts = int((now + datetime.timedelta(minutes=lead_minutes)).timestamp())

        occupied_rows = conn.execute(
            """
            SELECT scheduled_publish_time, meta_scheduled_publish_time
            FROM fb_crossposter_queue
            WHERE id != ? AND COALESCE(target_page_id, '') = ?
              AND (
                    status IN (
                        'scheduled', 'downloading', 'uploading', 'verifying', 'processing',
                        'retryable', 'meta_scheduled', 'schedule_mismatch', 'stalled'
                    )
                    OR COALESCE(fb_post_id, '') != ''
                  )
            """,
            (item_id, page_id),
        ).fetchall()
        occupied = {
            int(value)
            for row in occupied_rows
            for value in (row["scheduled_publish_time"], row["meta_scheduled_publish_time"])
            if int(value or 0) > 0
        }

        reserved_ts = 0
        horizon_limit = max(1, min(int(max_horizon_days), 366))
        for day_offset in range(horizon_limit):
            target_date = now.date() + datetime.timedelta(days=day_offset)
            for slot_time in valid_times:
                candidate_ts = int(datetime.datetime.combine(target_date, slot_time).timestamp())
                if candidate_ts <= earliest_ts:
                    continue
                if any(abs(candidate_ts - occupied_ts) < 900 for occupied_ts in occupied):
                    continue
                reserved_ts = candidate_ts
                break
            if reserved_ts:
                break
        if not reserved_ts and horizon_limit < 366:
            for day_offset in range(horizon_limit, 367):
                target_date = now.date() + datetime.timedelta(days=day_offset)
                for slot_time in valid_times:
                    candidate_ts = int(datetime.datetime.combine(target_date, slot_time).timestamp())
                    if candidate_ts <= earliest_ts:
                        continue
                    if any(abs(candidate_ts - occupied_ts) < 900 for occupied_ts in occupied):
                        continue
                    reserved_ts = candidate_ts
                    break
                if reserved_ts:
                    break
        if not reserved_ts:
            raise RuntimeError("Không tìm được slot Facebook trống trong 366 ngày tới")

        previous_video_id = str(item["fb_post_id"] or "")
        assignments = [
            "scheduled_publish_time = ?",
            "status = 'scheduled'",
            "error_message = ''",
            "updated_at = ?",
        ]
        values: list[object] = [reserved_ts, datetime.datetime.now().isoformat()]
        if clear_meta_object:
            assignments.extend([
                "previous_fb_post_id = ?",
                "fb_post_id = ''",
                "meta_published = NULL",
                "meta_video_status = ''",
                "meta_scheduled_publish_time = 0",
                "meta_status_json = '{}'",
                "meta_verified_at = ''",
                "meta_state = ''",
                "meta_state_since = ''",
                "meta_error_message = ''",
                "cleanup_status = 'deleted'",
                "upload_session_id = ''",
                "upload_video_id = ''",
                "upload_phase = ''",
                "upload_retry_count = 0",
                "upload_next_retry_at = ''",
            ])
            values.append(previous_video_id)
        values.append(item_id)
        conn.execute(
            f"UPDATE fb_crossposter_queue SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        conn.commit()
        return reserved_ts
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            conn.rollback()
        raise ValueError("Slot vừa được video khác giữ; vui lòng thử lại") from exc
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def adopt_fb_meta_schedule(item_id: int) -> int:
    """Make the verified Meta schedule authoritative for one queue item."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT meta_scheduled_publish_time FROM fb_crossposter_queue WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Không tìm thấy video ID #{item_id} trong hàng đợi")
        meta_schedule = int(row["meta_scheduled_publish_time"] or 0)
        if meta_schedule <= int(datetime.datetime.now().timestamp()):
            raise ValueError("Meta không có lịch tương lai hợp lệ để đồng bộ")
        conn.execute(
            """
            UPDATE fb_crossposter_queue
            SET scheduled_publish_time = ?, status = 'meta_scheduled',
                meta_state = 'meta_scheduled', meta_error_message = '', error_message = '',
                updated_at = ?
            WHERE id = ?
            """,
            (meta_schedule, datetime.datetime.now().isoformat(), item_id),
        )
        conn.commit()
        return meta_schedule
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:
            conn.rollback()
        raise ValueError("Lịch Meta đang trùng slot được video khác giữ") from exc
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def delete_fb_crossposter_queue_item(item_id: int) -> bool:
    """Delete a queue item by ID."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        cur = conn.execute("DELETE FROM fb_crossposter_queue WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_fb_crossposter_queue(target_page_id: str = "", only_pending: bool = False) -> int:
    """Clear queue items for a target_page_id."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        where_clauses = []
        params = []
        if target_page_id and target_page_id.strip():
            where_clauses.append("target_page_id = ?")
            params.append(target_page_id.strip())
        if only_pending:
            where_clauses.append("status IN ('pending', 'error')")
        
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        cur = conn.execute(f"DELETE FROM fb_crossposter_queue {where_sql}", params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def recalculate_fb_queue_schedule(
    daily_quota: int,
    times_list: list[str],
    target_page_id: str = "",
    start_date: datetime.date | None = None,
    sort_order_mode: str = "oldest_first"
) -> int:
    """Recalculate scheduled_publish_time for pending/scheduled items of a specific Fanpage with smart collision prevention."""
    if daily_quota <= 0 or not times_list:
        return 0

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        target_pid = (target_page_id or "").strip()
        where_sql = """
            WHERE status IN ('pending', 'scheduled') 
              AND (fb_post_id IS NULL OR fb_post_id = '')
              AND (upload_video_id IS NULL OR upload_video_id = '')
              AND (checkpoint_phase IS NULL OR checkpoint_phase != 'CP8_SUBMITTED')
              AND status != 'meta_scheduled'
              AND (meta_state IS NULL OR meta_state != 'scheduled')
        """
        params = []
        if target_pid:
            where_sql += " AND target_page_id = ?"
            params.append(target_pid)
        else:
            where_sql += " AND (target_page_id = '' OR target_page_id IS NULL)"

        if str(sort_order_mode).strip().lower() == "newest_first":
            order_by_sql = """
                CASE WHEN youtube_upload_date != '' AND youtube_upload_date IS NOT NULL THEN youtube_upload_date ELSE '' END DESC,
                sort_order DESC,
                id DESC
            """
        else:
            order_by_sql = """
                CASE WHEN youtube_upload_date != '' AND youtube_upload_date IS NOT NULL THEN youtube_upload_date ELSE '99999999' END ASC,
                sort_order ASC,
                id ASC
            """

        rows = conn.execute(f"""
            SELECT id FROM fb_crossposter_queue
            {where_sql}
            ORDER BY {order_by_sql}
        """, params).fetchall()

        if not rows:
            return 0

        # Phase 1: Reset scheduled_publish_time to 0 for items being recalculated
        # to prevent intermediate UNIQUE constraint collisions on idx_fb_queue_unique_active_slot
        placeholders = ",".join("?" for _ in rows)
        conn.execute(
            f"UPDATE fb_crossposter_queue SET scheduled_publish_time = 0 WHERE id IN ({placeholders})",
            [r[0] for r in rows],
        )

        now = datetime.datetime.now()
        now_ts = int(now.timestamp())
        current_date = start_date or now.date()
        time_slots = sorted(times_list)
        slot_count = len(time_slots)
        effective_slots_per_day = min(daily_quota, slot_count)

        # 1. Collect all occupied slots (published, in-flight, or already uploaded to Meta Cloud with future publish time, excluding rows to be rescheduled)
        row_ids = [r[0] for r in rows]
        occ_params = [now_ts]
        id_exclusion_sql = f"AND id NOT IN ({placeholders})"
        occ_params.extend(row_ids)

        occ_where = f"WHERE scheduled_publish_time > ? {id_exclusion_sql} AND ((fb_post_id IS NOT NULL AND fb_post_id != '') OR status IN ('scheduled', 'downloading', 'uploading', 'verifying', 'processing', 'retryable', 'meta_scheduled', 'schedule_mismatch', 'stalled', 'published'))"
        if target_pid:
            occ_where += " AND target_page_id = ?"
            occ_params.append(target_pid)
        else:
            occ_where += " AND (target_page_id = '' OR target_page_id IS NULL)"

        occupied_rows = conn.execute(f"""
            SELECT scheduled_publish_time FROM fb_crossposter_queue
            {occ_where}
        """, occ_params).fetchall()

        occupied_slots: set[int] = {r[0] for r in occupied_rows if r[0] and r[0] > 0}

        slot_index = 0
        date_offset = 0
        updated_count = 0

        for row in rows:
            item_id = row[0]

            # Find next free, non-colliding time slot
            while True:
                target_date = current_date + datetime.timedelta(days=date_offset)
                t_str = time_slots[slot_index % slot_count]
                try:
                    hour, minute = map(int, t_str.split(":"))
                    candidate_dt = datetime.datetime.combine(target_date, datetime.time(hour, minute))
                    candidate_ts = int(candidate_dt.timestamp())
                except Exception:
                    candidate_ts = int((now + datetime.timedelta(hours=2)).timestamp())

                # If candidate slot is in the past or < 10 mins in future, advance to next slot
                if candidate_ts <= (now_ts + 600):
                    slot_index += 1
                    if slot_index % effective_slots_per_day == 0:
                        date_offset += 1
                        slot_index = 0
                    continue

                # Check collision with already occupied slots (within 15 minutes window)
                is_colliding = any(abs(candidate_ts - occ_ts) < 900 for occ_ts in occupied_slots)
                if is_colliding:
                    slot_index += 1
                    if slot_index % effective_slots_per_day == 0:
                        date_offset += 1
                        slot_index = 0
                    continue

                # Found free slot!
                scheduled_ts = candidate_ts
                occupied_slots.add(scheduled_ts)
                slot_index += 1
                if slot_index % effective_slots_per_day == 0:
                    date_offset += 1
                    slot_index = 0
                break

            conn.execute("""
                UPDATE fb_crossposter_queue
                SET scheduled_publish_time = ?, status = 'scheduled', updated_at = ?
                WHERE id = ?
            """, (scheduled_ts, datetime.datetime.now().isoformat(), item_id))
            updated_count += 1

        conn.commit()
        return updated_count
    finally:
        conn.close()


def get_fb_crossposter_stats(target_page_id: str = "") -> dict:
    """Get summary counts of queue statuses filtered by target_page_id."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        where_sql = ""
        params = []
        if target_page_id and target_page_id.strip():
            where_sql = "WHERE target_page_id = ?"
            params.append(target_page_id.strip())

        rows = conn.execute(f"""
            SELECT status, COUNT(*) as count
            FROM fb_crossposter_queue
            {where_sql}
            GROUP BY status
        """, params).fetchall()

        counts = {
            "total": 0,
            "pending": 0,
            "scheduled": 0,
            "downloading": 0,
            "uploading": 0,
            "verifying": 0,
            "meta_scheduled": 0,
            "processing": 0,
            "retryable": 0,
            "schedule_mismatch": 0,
            "stalled": 0,
            "meta_failed": 0,
            "missing": 0,
            "published": 0,
            "skipped": 0,
            "error": 0
        }
        for row in rows:
            st = row["status"]
            cnt = row["count"]
            if st in counts:
                counts[st] = cnt
            counts["total"] += cnt

        # Next upcoming scheduled item
        next_where = "status IN ('scheduled', 'meta_scheduled') AND scheduled_publish_time > ?"
        next_params = [int(datetime.datetime.now().timestamp())]
        if target_page_id and target_page_id.strip():
            next_where += " AND target_page_id = ?"
            next_params.append(target_page_id.strip())

        next_item = conn.execute(f"""
            SELECT id, fb_title, original_title, scheduled_publish_time, target_page_id
            FROM fb_crossposter_queue
            WHERE {next_where}
            ORDER BY scheduled_publish_time ASC
            LIMIT 1
        """, next_params).fetchone()

        counts["next_scheduled"] = dict(next_item) if next_item else None
        return counts
    finally:
        conn.close()


def get_next_queue_items_for_pre_schedule(target_page_id: str, count: int) -> list[dict]:
    """Retrieve the next K items from the queue for pre-scheduling (including previously failed/missing items)."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        now_ts = int(datetime.datetime.now().timestamp())
        where_sql = "WHERE status IN ('pending', 'scheduled', 'error', 'failed', 'missing', 'checkpoint_paused') AND (fb_post_id IS NULL OR fb_post_id = '' OR status = 'missing')"
        params: list[object] = []
        if target_page_id and target_page_id.strip():
            where_sql += " AND target_page_id = ?"
            params.append(target_page_id.strip())
        params.extend([now_ts, now_ts, max(1, count)])

        rows = conn.execute(f"""
            SELECT * FROM fb_crossposter_queue
            {where_sql}
            ORDER BY 
                CASE WHEN scheduled_publish_time > ? THEN 0 ELSE 1 END,
                CASE WHEN scheduled_publish_time > ? THEN scheduled_publish_time ELSE 9999999999 END ASC,
                sort_order ASC,
                id ASC
            LIMIT ?
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_fb_queue_items_for_schedule_ahead(days_ahead: int, target_page_id: str = "") -> list[dict]:
    """Retrieve queue items for pre-scheduling based on days_ahead and daily quota."""
    settings = get_fb_crossposter_settings(target_page_id)
    daily_quota = settings.get("daily_quota", 2)
    count = max(1, int(days_ahead) * int(daily_quota))
    return get_next_queue_items_for_pre_schedule(target_page_id, count)


def reset_fb_crossposter_queue_errors(target_page_id: str = "") -> int:
    """Return retryable errors, missing items, and stale paused checkpoints to the unscheduled queue."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        where_sql = "WHERE (status IN ('error', 'failed', 'missing', 'checkpoint_paused') OR can_resume = 1) AND (fb_post_id IS NULL OR fb_post_id = '' OR status = 'missing')"
        params = []
        if target_page_id and target_page_id.strip():
            where_sql += " AND target_page_id = ?"
            params.append(target_page_id.strip())
        
        cursor = conn.execute(
            f"""
            UPDATE fb_crossposter_queue 
            SET status = 'pending', 
                scheduled_publish_time = 0,
                checkpoint_phase = '', 
                checkpoint_data_json = '{{}}',
                checkpoint_screenshot = '',
                can_resume = 0,
                error_message = '',
                meta_error_message = '',
                meta_state = ''
            {where_sql}
            """,
            params,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def fix_fb_queue_schedule_collisions(target_page_id: str = "") -> dict:
    """Detect and automatically resolve schedule slot collisions for one or all Fanpages."""
    target_pid = (target_page_id or "").strip()
    campaigns = []
    if target_pid:
        campaigns = [target_pid]
    else:
        all_camps = list_all_crossposter_campaigns()
        if all_camps:
            campaigns = [c.get("page_id", "") for c in all_camps]
        else:
            campaigns = [""]

    total_recalculated = 0
    for pid in campaigns:
        settings = get_fb_crossposter_settings(pid)
        daily_quota = settings.get("daily_quota", 2)
        schedule_times = settings.get("schedule_times", ["11:30", "19:30"])
        sort_order_mode = settings.get("sort_order_mode", "oldest_first")
        updated = recalculate_fb_queue_schedule(
            daily_quota, schedule_times, target_page_id=pid, sort_order_mode=sort_order_mode
        )
        total_recalculated += updated

    return {
        "success": True,
        "recalculated_count": total_recalculated,
        "campaigns_processed": len(campaigns),
    }


def delete_fb_crossposter_campaign(page_id: str) -> bool:
    """Delete a Fanpage campaign from settings and delete all its queue items."""
    if not page_id:
        return False
    target_pid = page_id.strip()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute("DELETE FROM fb_crossposter_settings WHERE target_fb_page_id = ?", (target_pid,))
        conn.execute("DELETE FROM fb_crossposter_queue WHERE target_page_id = ?", (target_pid,))
        conn.commit()
        return True
    finally:
        conn.close()




def _decode_json_field(value: str, fallback):
    try:
        decoded = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return decoded if isinstance(decoded, type(fallback)) else fallback


# ==============================================================================
# Video Artifacts
# ==============================================================================

def _decode_video_artifact(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    artifact = dict(row)
    artifact["codecs"] = _decode_json_field(artifact.pop("codecs_json", "{}"), {})
    artifact["metadata"] = _decode_json_field(
        artifact.pop("metadata_json", "{}"), {}
    )
    return artifact


def upsert_video_artifact(
    *,
    video_id: int,
    artifact_type: str,
    path: str,
    content_hash: str,
    status: str,
    mime_type: str = "",
    size_bytes: int = 0,
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
    codecs: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO video_artifacts (
            video_id, artifact_type, path, content_hash, mime_type, size_bytes,
            duration_seconds, width, height, codecs_json, status, metadata_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id, artifact_type, content_hash) DO UPDATE SET
            path = excluded.path,
            mime_type = excluded.mime_type,
            size_bytes = excluded.size_bytes,
            duration_seconds = excluded.duration_seconds,
            width = excluded.width,
            height = excluded.height,
            codecs_json = excluded.codecs_json,
            status = excluded.status,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            video_id,
            artifact_type,
            path,
            content_hash,
            mime_type,
            max(0, int(size_bytes or 0)),
            duration_seconds,
            width,
            height,
            json.dumps(codecs or {}, ensure_ascii=False),
            status,
            json.dumps(metadata or {}, ensure_ascii=False),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT * FROM video_artifacts
        WHERE video_id = ? AND artifact_type = ? AND content_hash = ?
        """,
        (video_id, artifact_type, content_hash),
    ).fetchone()
    conn.close()
    return _decode_video_artifact(row)


def get_video_artifact(artifact_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM video_artifacts WHERE id = ?", (artifact_id,)
    ).fetchone()
    conn.close()
    return _decode_video_artifact(row)


def get_latest_video_artifact(
    video_id: int,
    artifact_type: str,
    status: str | None = None,
) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["video_id = ?", "artifact_type = ?"]
    params: list = [video_id, artifact_type]
    if status:
        filters.append("status = ?")
        params.append(status)
    row = conn.execute(
        f"SELECT * FROM video_artifacts WHERE {' AND '.join(filters)} "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        params,
    ).fetchone()
    conn.close()
    return _decode_video_artifact(row)


def list_video_artifacts(
    video_id: int,
    artifact_type: str | None = None,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if artifact_type:
        if artifact_type.endswith("%") or artifact_type.endswith(":"):
            prefix = artifact_type.rstrip("%")
            if not prefix.endswith("%"):
                prefix = f"{prefix}%"
            rows = conn.execute(
                "SELECT * FROM video_artifacts WHERE video_id = ? AND artifact_type LIKE ? ORDER BY id",
                (video_id, prefix),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM video_artifacts WHERE video_id = ? AND artifact_type = ? ORDER BY id",
                (video_id, artifact_type),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM video_artifacts WHERE video_id = ? ORDER BY id",
            (video_id,),
        ).fetchall()
    conn.close()
    return [_decode_video_artifact(row) for row in rows]


def delete_video_artifact(artifact_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cursor = conn.execute("DELETE FROM video_artifacts WHERE id = ?", (artifact_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_video_ids_with_ready_final_mp4(video_ids: list[int] | set[int]) -> set[int]:
    if not video_ids:
        return set()
    conn = sqlite3.connect(str(DB_PATH))
    placeholders = ",".join("?" for _ in video_ids)
    rows = conn.execute(
        f"SELECT DISTINCT video_id FROM video_artifacts WHERE video_id IN ({placeholders}) AND artifact_type = 'final_mp4' AND status = 'ready'",
        tuple(video_ids),
    ).fetchall()
    conn.close()
    return {int(row[0]) for row in rows}


# ==============================================================================
# YouTube Publish Workflows & Scheduling
# ==============================================================================

PUBLISH_WORKFLOW_MUTABLE_FIELDS = {
    "system_job_id",
    "status",
    "stage",
    "snapshot_json",
    "upload_session_encrypted",
    "upload_offset",
    "youtube_video_id",
    "publication_id",
    "caption_id",
    "scheduled_at",
    "error",
}
PUBLISH_WORKFLOW_REUSABLE_STATUSES = {
    "queued",
    "running",
    "retry_wait",
    "waiting",
    "waiting_for_browser",
    "needs_review",
    "processing",
    "ready_to_schedule",
    "uploaded_private",
    "scheduled",
    "done",
    "published",
    "error",
    "canceled",
}


def _decode_publish_workflow(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    workflow = dict(row)
    workflow["snapshot"] = _decode_json_field(
        workflow.pop("snapshot_json", "{}"), {}
    )
    return workflow


def reserve_youtube_publish_workflow(
    *,
    video_id: int,
    youtube_channel_id: int,
    artifact_id: int,
    snapshot: dict,
    workflow_id: str | None = None,
    system_job_id: str = "",
) -> tuple[dict, bool]:
    now = utc_now()
    if not workflow_id:
        workflow_id = f"yt-pub-{video_id}-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in PUBLISH_WORKFLOW_REUSABLE_STATUSES)
        existing = conn.execute(
            f"""
            SELECT * FROM youtube_publish_workflows
            WHERE video_id = ? AND youtube_channel_id = ?
              AND status IN ({placeholders})
            ORDER BY created_at DESC LIMIT 1
            """,
            (
                video_id,
                youtube_channel_id,
                *sorted(PUBLISH_WORKFLOW_REUSABLE_STATUSES),
            ),
        ).fetchone()
        if existing is not None:
            conn.execute("COMMIT")
            return _decode_publish_workflow(existing), False
        conn.execute(
            """
            INSERT INTO youtube_publish_workflows (
                id, video_id, youtube_channel_id, artifact_id, system_job_id, status, stage,
                snapshot_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', 'upload', ?, ?, ?)
            """,
            (
                workflow_id,
                video_id,
                youtube_channel_id,
                artifact_id,
                system_job_id,
                json.dumps(snapshot, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM youtube_publish_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return _decode_publish_workflow(row), True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_youtube_publish_workflow(workflow_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_publish_workflows WHERE id = ?", (workflow_id,)
    ).fetchone()
    conn.close()
    return _decode_publish_workflow(row)


def get_latest_youtube_publish_workflow(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT * FROM youtube_publish_workflows
        WHERE video_id = ? ORDER BY created_at DESC LIMIT 1
        """,
        (video_id,),
    ).fetchone()
    conn.close()
    return _decode_publish_workflow(row)


def get_youtube_publish_workflow_by_job(system_job_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_publish_workflows WHERE system_job_id = ? ORDER BY created_at DESC LIMIT 1",
        (system_job_id,),
    ).fetchone()
    conn.close()
    return _decode_publish_workflow(row)


def get_youtube_publish_workflow_for_video(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_publish_workflows WHERE video_id = ? ORDER BY created_at DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    conn.close()
    return _decode_publish_workflow(row)


def get_youtube_publish_workflows_by_job_ids(job_ids: list[str]) -> dict[str, dict]:
    if not job_ids:
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in job_ids)
    rows = conn.execute(
        f"SELECT * FROM youtube_publish_workflows WHERE system_job_id IN ({placeholders})",
        tuple(job_ids),
    ).fetchall()
    conn.close()
    return {
        row["system_job_id"]: _decode_publish_workflow(row)
        for row in rows
        if row["system_job_id"]
    }


def update_youtube_publish_workflow(workflow_id: str, **changes) -> dict | None:
    invalid = set(changes) - PUBLISH_WORKFLOW_MUTABLE_FIELDS
    if invalid:
        raise ValueError(f"Unsupported publish workflow fields: {sorted(invalid)}")
    if not changes:
        return get_youtube_publish_workflow(workflow_id)
    encoded = dict(changes)
    if "snapshot_json" in encoded and not isinstance(encoded["snapshot_json"], str):
        encoded["snapshot_json"] = json.dumps(
            encoded["snapshot_json"], ensure_ascii=False
        )
    encoded["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in encoded)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE youtube_publish_workflows SET {assignments} WHERE id = ?",
        (*encoded.values(), workflow_id),
    )
    conn.commit()
    conn.close()
    return get_youtube_publish_workflow(workflow_id)


def reserve_channel_schedule(
    *,
    youtube_channel_id: int,
    workflow_id: str,
    scheduled_at: str,
) -> dict | None:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "canceled":
                try:
                    conn.execute(
                        """
                        UPDATE channel_schedule_reservations
                        SET scheduled_at = ?, status = 'reserved', updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (scheduled_at, now, workflow_id),
                    )
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK")
                    return None
                existing = conn.execute(
                    "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchone()
            conn.execute("COMMIT")
            return dict(existing)
        try:
            conn.execute(
                """
                INSERT INTO channel_schedule_reservations (
                    youtube_channel_id, workflow_id, scheduled_at, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'reserved', ?, ?)
                """,
                (youtube_channel_id, workflow_id, scheduled_at, now, now),
            )
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return None
        row = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def list_channel_schedule_reservations(
    youtube_channel_id: int,
    *,
    statuses: tuple[str, ...] = ("reserved", "scheduled"),
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT r.*, w.video_id, w.status as workflow_status, v.title as video_title
        FROM channel_schedule_reservations r
        LEFT JOIN youtube_publish_workflows w ON r.workflow_id = w.id
        LEFT JOIN videos v ON w.video_id = v.id
        WHERE r.youtube_channel_id = ? AND r.status IN ({placeholders})
        ORDER BY r.scheduled_at ASC
        """,
        (youtube_channel_id, *statuses),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def cleanup_stale_channel_schedule_reservations(
    conn: sqlite3.Connection | None = None,
    youtube_channel_id: int | None = None,
    max_age_seconds: int = 3600,
) -> int:
    """Clean up stale 'reserved' records where the parent workflow/job failed, canceled, or expired."""
    close_after = False
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
        close_after = True
    try:
        now = utc_now()
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        channel_clause = "AND r.youtube_channel_id = ?" if youtube_channel_id is not None else ""
        channel_clause_simple = "AND youtube_channel_id = ?" if youtube_channel_id is not None else ""
        params: list[Any] = [youtube_channel_id] if youtube_channel_id is not None else []

        # 1. Cancel reservations where workflow or job is in error/canceled/failed
        cur = conn.execute(
            f"""
            UPDATE channel_schedule_reservations
            SET status = 'canceled', updated_at = ?
            WHERE status = 'reserved'
              {channel_clause_simple}
              AND workflow_id IN (
                  SELECT w.id FROM youtube_publish_workflows w
                  LEFT JOIN system_jobs j ON w.system_job_id = j.id
                  WHERE w.status IN ('error', 'canceled', 'failed')
                     OR j.status IN ('error', 'cancelled', 'failed')
              )
            """,
            (now, *params),
        )
        cleaned_count = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        # 2. Cancel reservations where updated_at is older than max_age_seconds and workflow is not actively running
        stale_rows = conn.execute(
            f"""
            SELECT r.workflow_id, r.updated_at, r.created_at, w.status as w_status, j.status as j_status
            FROM channel_schedule_reservations r
            LEFT JOIN youtube_publish_workflows w ON r.workflow_id = w.id
            LEFT JOIN system_jobs j ON w.system_job_id = j.id
            WHERE r.status = 'reserved' {channel_clause}
            """,
            params,
        ).fetchall()
        for row in stale_rows:
            wf_id = row["workflow_id"] if isinstance(row, sqlite3.Row) else row[0]
            upd_str = (row["updated_at"] or row["created_at"]) if isinstance(row, sqlite3.Row) else (row[1] or row[2])
            w_st = str((row["w_status"] if isinstance(row, sqlite3.Row) else row[3]) or "")
            j_st = str((row["j_status"] if isinstance(row, sqlite3.Row) else row[4]) or "")
            is_active = (w_st in ("running", "processing", "browser_upload", "uploading") or j_st in ("running", "processing"))
            if not is_active and upd_str:
                try:
                    upd_dt = datetime.datetime.fromisoformat(str(upd_str).replace("Z", "+00:00"))
                    if upd_dt.tzinfo is None:
                        upd_dt = upd_dt.replace(tzinfo=datetime.timezone.utc)
                    if (now_dt - upd_dt).total_seconds() > max_age_seconds:
                        conn.execute(
                            "UPDATE channel_schedule_reservations SET status = 'canceled', updated_at = ? WHERE workflow_id = ?",
                            (now, wf_id),
                        )
                        cleaned_count += 1
                except Exception:
                    pass
        return cleaned_count
    finally:
        if close_after:
            conn.close()


def get_channel_schedule_reservation(workflow_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
        (workflow_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_channel_schedule_reservation(
    workflow_id: str,
    status: str,
) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        UPDATE channel_schedule_reservations
        SET status = ?, updated_at = ? WHERE workflow_id = ?
        """,
        (status, utc_now(), workflow_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
        (workflow_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_active_youtube_publish_workflow(channel_db_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        """
        SELECT 1 FROM youtube_publish_workflows
        WHERE youtube_channel_id = ?
          AND status IN ('queued', 'running', 'retry_wait', 'waiting',
                         'processing', 'ready_to_schedule')
        LIMIT 1
        """,
        (channel_db_id,),
    ).fetchone()
    conn.close()
    return row is not None


def reserve_youtube_publication_slot(
    workflow_id: str,
    now_utc: datetime.datetime | None = None,
) -> str:
    from auto_yt.services.publication_scheduler import (
        find_next_publication_slot,
        _parse_utc,
    )
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        workflow = conn.execute(
            "SELECT * FROM youtube_publish_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        channel = conn.execute(
            "SELECT * FROM youtube_channels WHERE id = ?",
            (workflow["youtube_channel_id"],),
        ).fetchone()
        if channel is None:
            raise ValueError(f"Channel not found: {workflow['youtube_channel_id']}")

        lead_minutes = int(channel["publication_lead_minutes"] or 120)
        now = now_utc or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        earliest = now.astimezone(datetime.timezone.utc) + datetime.timedelta(
            minutes=max(1, lead_minutes)
        )
        reservation = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if reservation is not None and reservation["status"] == "scheduled":
            conn.execute("COMMIT")
            return str(reservation["scheduled_at"])
        if reservation is not None and reservation["status"] == "reserved":
            try:
                reserved_at = datetime.datetime.fromisoformat(
                    str(reservation["scheduled_at"] or "").replace("Z", "+00:00")
                )
                if reserved_at.tzinfo is None:
                    reserved_at = reserved_at.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                reserved_at = None
            if reserved_at is not None and reserved_at >= earliest:
                conn.execute(
                    "UPDATE youtube_publish_workflows SET scheduled_at = ?, updated_at = ? WHERE id = ?",
                    (reservation["scheduled_at"], utc_now(), workflow_id),
                )
                conn.execute("COMMIT")
                return str(reservation["scheduled_at"])
            conn.execute(
                "UPDATE channel_schedule_reservations SET status = 'canceled', updated_at = ? WHERE workflow_id = ?",
                (utc_now(), workflow_id),
            )

        # Auto-clean any stale or dead reservations for this channel
        cleanup_stale_channel_schedule_reservations(conn, channel["id"])

        res_rows = conn.execute(
            """
            SELECT r.scheduled_at 
            FROM channel_schedule_reservations r
            LEFT JOIN youtube_publish_workflows w ON r.workflow_id = w.id
            LEFT JOIN system_jobs j ON w.system_job_id = j.id
            WHERE r.youtube_channel_id = ? 
              AND r.workflow_id != ? 
              AND r.status IN ('reserved', 'scheduled')
              AND (
                  r.status = 'scheduled'
                  OR (
                      r.status = 'reserved'
                      AND COALESCE(w.status, '') NOT IN ('error', 'canceled', 'failed')
                      AND COALESCE(j.status, '') NOT IN ('error', 'cancelled', 'failed')
                  )
              )
            """,
            (channel["id"], workflow_id),
        ).fetchall()
        pub_rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(scheduled_at, ''), NULLIF(published_at, ''))
            FROM video_publications
            WHERE youtube_channel_id = ?
              AND COALESCE(NULLIF(scheduled_at, ''), NULLIF(published_at, '')) IS NOT NULL
              AND processing_status != 'failed'
            """,
            (channel["id"],),
        ).fetchall()

        occupied_set = set()
        for r in res_rows:
            if r[0]:
                parsed = _parse_utc(r[0])
                if parsed:
                    occupied_set.add(parsed.replace(second=0, microsecond=0).isoformat())
        for r in pub_rows:
            if r[0]:
                parsed = _parse_utc(r[0])
                if parsed:
                    occupied_set.add(parsed.replace(second=0, microsecond=0).isoformat())
        occupied = sorted(occupied_set)

        timezone_name = channel["publication_timezone"] or "Asia/Ho_Chi_Minh"
        try:
            slots = json.loads(channel["publication_slots_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            slots = []
        daily_limit = int(channel["publication_daily_limit"] or 1)

        slot = find_next_publication_slot(
            timezone_name=timezone_name,
            slots=slots,
            daily_limit=daily_limit,
            lead_minutes=lead_minutes,
            occupied_utc=occupied,
            now_utc=now_utc,
        )

        if reservation is None:
            conn.execute(
                """
                INSERT INTO channel_schedule_reservations (
                    youtube_channel_id, workflow_id, scheduled_at, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'reserved', ?, ?)
                """,
                (channel["id"], workflow_id, slot, utc_now(), utc_now()),
            )
        else:
            conn.execute(
                """
                UPDATE channel_schedule_reservations
                SET scheduled_at = ?, status = 'reserved', updated_at = ?
                WHERE workflow_id = ?
                """,
                (slot, utc_now(), workflow_id),
            )
        conn.execute(
            "UPDATE youtube_publish_workflows SET scheduled_at = ?, updated_at = ? WHERE id = ?",
            (slot, utc_now(), workflow_id),
        )
        conn.execute("COMMIT")
        return slot
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_fb_items_for_meta_reconciliation(
    target_page_id: str = "",
    limit: int = 100,
) -> list[dict]:
    """Return Meta-backed items whose remote state can affect local scheduling truth."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        now_ts = int(datetime.datetime.now().timestamp())
        where_sql = """
            WHERE (
                    status IN (
                        'verifying', 'processing', 'retryable', 'meta_scheduled',
                        'schedule_mismatch', 'stalled', 'meta_failed', 'error'
                    )
                    OR checkpoint_phase = 'CP8_SUBMITTED'
                    OR (
                        status = 'published'
                        AND (
                            scheduled_publish_time > ?
                            OR COALESCE(meta_verified_at, '') = ''
                        )
                    )
                    OR (
                        status = 'scheduled'
                        AND scheduled_publish_time > 0
                        AND scheduled_publish_time <= ?
                    )
                    OR (COALESCE(fb_post_id, '') != '' OR COALESCE(upload_video_id, '') != '')
                  )
        """
        future_horizon_ts = now_ts + (30 * 86400)
        params: list[object] = [now_ts, future_horizon_ts]
        target_pid = (target_page_id or "").strip()
        if target_pid:
            where_sql += " AND target_page_id = ?"
            params.append(target_pid)
        params.append(max(1, min(int(limit), 500)))
        rows = conn.execute(
            f"""
            SELECT * FROM fb_crossposter_queue
            {where_sql}
            ORDER BY COALESCE(NULLIF(meta_verified_at, ''), created_at) ASC, id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def cancel_youtube_publication_reservation(workflow_id: str) -> dict | None:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        reservation = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if reservation is not None and reservation["status"] == "reserved":
            conn.execute(
                "UPDATE channel_schedule_reservations SET status = 'canceled', updated_at = ? WHERE workflow_id = ?",
                (now, workflow_id),
            )
            conn.execute(
                "UPDATE youtube_publish_workflows SET scheduled_at = '', updated_at = ? WHERE id = ?",
                (now, workflow_id),
            )
        row = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row) if row else None
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def complete_youtube_schedule(
    workflow_id: str,
    publication_id: int,
    scheduled_at: str,
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        reservation = conn.execute(
            "SELECT * FROM channel_schedule_reservations WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        if (
            reservation is None
            or reservation["status"] != "reserved"
            or str(reservation["scheduled_at"]) != str(scheduled_at)
        ):
            raise ValueError("Khung giờ đăng không còn thuộc workflow này.")
        conn.execute(
            "UPDATE channel_schedule_reservations SET status = 'scheduled', updated_at = ? WHERE workflow_id = ?",
            (now, workflow_id),
        )
        conn.execute(
            """
            UPDATE video_publications
            SET privacy_status = 'private', processing_status = 'succeeded',
                scheduled_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (scheduled_at, now, publication_id),
        )
        conn.execute(
            """
            UPDATE youtube_publish_workflows
            SET status = 'scheduled', stage = 'scheduled', scheduled_at = ?,
                upload_session_encrypted = '', error = '', updated_at = ?
            WHERE id = ?
            """,
            (scheduled_at, now, workflow_id),
        )
        row = conn.execute(
            "SELECT * FROM youtube_publish_workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        conn.execute("COMMIT")
        if row is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        return _decode_publish_workflow(row)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ==============================================================================
# Video Production State
# ==============================================================================

VIDEO_PRODUCTION_MUTABLE_FIELDS = {
    "production_snapshot_json",
    "render_status",
    "publish_status",
    "current_stage",
    "production_progress",
    "blocking_reason",
    "flow_project_url",
    "flow_scene_count",
    "flow_completed_scenes",
}


def update_video_production(video_id: int, **changes) -> dict | None:
    invalid = set(changes) - VIDEO_PRODUCTION_MUTABLE_FIELDS
    if invalid:
        raise ValueError(f"Unsupported video production fields: {sorted(invalid)}")
    if not changes:
        return get_video(video_id)
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"UPDATE videos SET {assignments} WHERE id = ?",
        (*changes.values(), video_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


update_video_production_state = update_video_production


def list_completed_audio_video_ids_with_production_snapshot() -> list[int]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        """
        SELECT videos.id
        FROM videos
        JOIN audio_tasks ON audio_tasks.video_id = videos.id
        WHERE videos.video_status = ?
          AND audio_tasks.status = 'completed'
          AND TRIM(COALESCE(videos.production_snapshot_json, '')) NOT IN ('', '{}')
        ORDER BY videos.id
        """,
        (VIDEO_STATUS_ACTIVE,),
    ).fetchall()
    conn.close()
    return [int(row[0]) for row in rows]


# ==============================================================================
# Video Publications & Channels Helpers
# ==============================================================================

def get_video_publication(publication_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM video_publications WHERE id = ?", (publication_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_channel_pending_publications(channel_db_id: int) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT publications.*
        FROM video_publications AS publications
        JOIN videos ON videos.id = publications.video_id
        WHERE publications.youtube_channel_id = ?
          AND publications.privacy_status != 'public'
          AND videos.video_status = ?
        ORDER BY publications.id
        """,
        (channel_db_id, VIDEO_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_video_publication(publication_id: int, **changes) -> dict | None:
    allowed = {
        "published_url",
        "published_title",
        "published_at",
        "privacy_status",
        "processing_status",
        "scheduled_at",
        "artifact_hash",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Unsupported publication fields: {sorted(invalid)}")
    if not changes:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM video_publications WHERE id = ?", (publication_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"UPDATE video_publications SET {assignments} WHERE id = ?",
        (*changes.values(), publication_id),
    )
    publication_video = conn.execute(
        "SELECT video_id FROM video_publications WHERE id = ?",
        (publication_id,),
    ).fetchone()
    if publication_video is not None:
        video_id = int(publication_video[0])
        conn.execute(
            """
            UPDATE videos
            SET is_published = CASE WHEN EXISTS (
                SELECT 1 FROM video_publications
                WHERE video_id = ? AND privacy_status = 'public'
            ) THEN 1 ELSE 0 END
            WHERE id = ?
            """,
            (video_id, video_id),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM video_publications WHERE id = ?", (publication_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


get_youtube_channels = list_youtube_channels


def get_editable_video_job(identifier: int | str) -> dict | None:
    if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
        vid = get_video(int(identifier))
        if vid:
            return vid
    job = get_system_job(str(identifier))
    if job:
        return job
    return None


def upsert_youtube_channel(
    channel_id: str,
    title: str,
    thumbnail_url: str = "",
    gpm_profile_id: str = "",
    gpm_profile_name: str = "",
    gpm_proxy_info: str = "",
    interaction_mode: str = "gpm_browser",
    auto_heart: int = 1,
    **extra_fields,
) -> dict:
    from auto_yt.services.secret_store import encrypt_secret

    now = utc_now()
    encrypted_proxy = encrypt_secret(str(gpm_proxy_info or "").strip())
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        "SELECT id FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE youtube_channels SET
                title = ?,
                thumbnail_url = ?,
                gpm_profile_id = ?,
                gpm_profile_name = ?,
                gpm_proxy_info = '',
                gpm_proxy_info_encrypted = ?,
                interaction_mode = ?,
                auto_heart = ?,
                updated_at = ?
            WHERE channel_id = ?
            """,
            (
                title,
                thumbnail_url,
                gpm_profile_id,
                gpm_profile_name,
                encrypted_proxy,
                interaction_mode,
                auto_heart,
                now,
                channel_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO youtube_channels (
                channel_id, title, thumbnail_url, access_token_encrypted,
                refresh_token_encrypted, token_expiry, scope, status,
                gpm_profile_id, gpm_profile_name, gpm_proxy_info,
                gpm_proxy_info_encrypted,
                interaction_mode, auto_heart, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', '', '', 'connected', ?, ?, '', ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                title,
                thumbnail_url,
                gpm_profile_id,
                gpm_profile_name,
                encrypted_proxy,
                interaction_mode,
                auto_heart,
                now,
                now,
            ),
        )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    conn.close()
    return _public_youtube_channel(row)


# ==============================================================================
# ComfyUI Profiles
# ==============================================================================

def _decode_comfyui_profile(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    profile = dict(row)
    profile["workflow"] = _decode_json_field(profile.pop("workflow_json", "{}"), {})
    profile["node_mappings"] = _decode_json_field(
        profile.pop("node_mappings_json", "{}"), {}
    )
    return profile


def save_comfyui_workflow_profile(
    *,
    profile_id: str,
    name: str,
    base_url: str,
    workflow: dict,
    node_mappings: dict,
    max_concurrency: int = 1,
    min_width: int = 1024,
    min_height: int = 576,
    timeout_seconds: int = 900,
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO comfyui_workflow_profiles (
            id, name, base_url, workflow_json, node_mappings_json,
            max_concurrency, min_width, min_height, timeout_seconds,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            base_url = excluded.base_url,
            workflow_json = excluded.workflow_json,
            node_mappings_json = excluded.node_mappings_json,
            max_concurrency = excluded.max_concurrency,
            min_width = excluded.min_width,
            min_height = excluded.min_height,
            timeout_seconds = excluded.timeout_seconds,
            updated_at = excluded.updated_at
        """,
        (
            profile_id,
            name,
            base_url,
            json.dumps(workflow, ensure_ascii=False),
            json.dumps(node_mappings, ensure_ascii=False),
            max(1, int(max_concurrency)),
            max(256, int(min_width)),
            max(144, int(min_height)),
            max(30, int(timeout_seconds)),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM comfyui_workflow_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    conn.close()
    return _decode_comfyui_profile(row)


def get_comfyui_workflow_profile(profile_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM comfyui_workflow_profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    conn.close()
    return _decode_comfyui_profile(row)


def list_comfyui_workflow_profiles() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM comfyui_workflow_profiles ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [_decode_comfyui_profile(row) for row in rows]


def delete_comfyui_workflow_profile(profile_id: str) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cursor = conn.execute(
        "DELETE FROM comfyui_workflow_profiles WHERE id = ?", (profile_id,)
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


# ==============================================================================
# CHANNEL TRUST PLANS & ACTIVITY LOGS (TRUST BUILDER)
# ==============================================================================

def _decode_trust_plan(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["niche_keywords"] = _decode_json_field(data.get("niche_keywords"), [])
    data["target_channels"] = _decode_json_field(data.get("target_channels"), [])
    data["branding_checklist"] = _decode_json_field(data.get("branding_checklist"), {})
    data["profile_readiness"] = _decode_json_field(data.pop("profile_readiness_json", None), {})
    data["channel_readiness"] = _decode_json_field(data.pop("channel_readiness_json", None), {})
    data["profile_baseline"] = _decode_json_field(data.pop("profile_baseline_json", None), {})
    data["approved_sources"] = _decode_json_field(data.get("approved_sources"), [])
    data["total_videos_watched"] = int(data.get("total_videos_watched") or 0)
    data["total_searches"] = int(data.get("total_searches") or 0)
    data["total_likes"] = int(data.get("total_likes") or 0)
    data["total_comments"] = int(data.get("total_comments") or 0)
    data["total_subscriptions"] = int(data.get("total_subscriptions") or 0)
    data["trust_score_estimated"] = int(data.get("trust_score_estimated") or 0)
    data["legacy_trust_score_estimated"] = int(data.get("legacy_trust_score_estimated") or 0)
    data["profile_readiness_pct"] = int(data.get("profile_readiness_pct") or 0)
    data["channel_readiness_pct"] = int(data.get("channel_readiness_pct") or 0)
    data["requires_review"] = bool(data.get("requires_review"))
    data["reminder_enabled"] = bool(data.get("reminder_enabled"))
    data["daily_watch_target"] = int(5 if data.get("daily_watch_target") is None else data["daily_watch_target"])
    data["daily_search_target"] = int(3 if data.get("daily_search_target") is None else data["daily_search_target"])
    data["daily_like_target"] = int(3 if data.get("daily_like_target") is None else data["daily_like_target"])
    data["daily_comment_target"] = int(0 if data.get("daily_comment_target") is None else data["daily_comment_target"])
    data["daily_subscribe_target"] = int(0 if data.get("daily_subscribe_target") is None else data["daily_subscribe_target"])
    data["min_watch_minutes"] = int(10 if data.get("min_watch_minutes") is None else data["min_watch_minutes"])
    return data


def _decode_trust_activity_log(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["detail_json"] = _decode_json_field(data.get("detail_json"), {})
    data["duration_seconds"] = float(data.get("duration_seconds") or 0.0)
    data["success"] = bool(data.get("success", 1))
    return data


def create_channel_trust_plan(
    channel_db_id: int,
    niche_keywords: list[str] | None = None,
    target_channels: list[str] | None = None,
    daily_watch_target: int = 5,
    daily_search_target: int = 3,
    daily_like_target: int = 3,
    daily_comment_target: int = 0,
    daily_subscribe_target: int = 0,
    min_watch_minutes: int = 10,
    branding_checklist: dict | None = None,
    approved_sources: list[str] | None = None,
    status: str = "draft",
    warmup_phase: str = "idle",
) -> dict:
    now = utc_now()
    keywords_json = json.dumps(niche_keywords or [], ensure_ascii=False)
    channels_json = json.dumps(target_channels or [], ensure_ascii=False)
    branding_json = json.dumps(branding_checklist or {}, ensure_ascii=False)
    approved_sources_json = json.dumps(approved_sources or [], ensure_ascii=False)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        INSERT INTO channel_trust_plans (
            channel_db_id, niche_keywords, target_channels,
            daily_watch_target, daily_search_target, daily_like_target,
            daily_comment_target, daily_subscribe_target, min_watch_minutes,
            branding_checklist, approved_sources, status, warmup_phase, mode,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'automated', ?, ?)
        """,
        (
            channel_db_id, keywords_json, channels_json,
            daily_watch_target, daily_search_target, daily_like_target,
            daily_comment_target, daily_subscribe_target, min_watch_minutes,
            branding_json, approved_sources_json, status, warmup_phase,
            now, now
        )
    )
    plan_id = cursor.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT * FROM channel_trust_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    conn.close()
    return _decode_trust_plan(row)


def get_channel_trust_plan(plan_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT p.*, c.channel_id as youtube_channel_ucid, c.title as channel_title, c.thumbnail_url as channel_thumbnail,
               c.gpm_profile_id, c.gpm_profile_name, c.gpm_proxy_info,
               c.publication_timezone
        FROM channel_trust_plans p
        LEFT JOIN youtube_channels c ON p.channel_db_id = c.id
        WHERE p.id = ?
        """,
        (plan_id,)
    ).fetchone()
    conn.close()
    return _decode_trust_plan(row)


def get_channel_trust_plan_by_channel_id(channel_db_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT p.*, c.channel_id as youtube_channel_ucid, c.title as channel_title, c.thumbnail_url as channel_thumbnail,
               c.gpm_profile_id, c.gpm_profile_name, c.gpm_proxy_info,
               c.publication_timezone
        FROM channel_trust_plans p
        LEFT JOIN youtube_channels c ON p.channel_db_id = c.id
        WHERE p.channel_db_id = ?
        """,
        (channel_db_id,)
    ).fetchone()
    conn.close()
    return _decode_trust_plan(row)


def list_channel_trust_plans() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.*, c.channel_id as youtube_channel_ucid, c.title as channel_title, c.thumbnail_url as channel_thumbnail,
               c.gpm_profile_id, c.gpm_profile_name, c.gpm_proxy_info,
               c.publication_timezone
        FROM channel_trust_plans p
        LEFT JOIN youtube_channels c ON p.channel_db_id = c.id
        ORDER BY p.updated_at DESC
        """
    ).fetchall()
    conn.close()
    return [_decode_trust_plan(row) for row in rows]


def update_channel_trust_plan(plan_id: int, **changes) -> dict | None:
    if not changes:
        return get_channel_trust_plan(plan_id)

    valid_fields = {
        "niche_keywords", "target_channels", "warmup_phase", "phase_started_at",
        "daily_watch_target", "daily_search_target", "daily_like_target",
        "daily_comment_target", "daily_subscribe_target", "min_watch_minutes",
        "branding_checklist", "status", "total_videos_watched", "total_searches",
        "total_likes", "total_comments", "total_subscriptions", "trust_score_estimated",
        "error_message", "last_session_at", "last_attempt_at", "next_run_at",
        "legacy_trust_score_estimated", "mode", "requires_review",
        "profile_readiness", "channel_readiness", "profile_readiness_pct",
        "channel_readiness_pct", "readiness_state", "profile_baseline",
        "approved_sources", "reminder_enabled", "reminder_time_local",
        "last_readiness_check_at"
    }

    set_clauses = []
    params = []
    for key, value in changes.items():
        if key not in valid_fields:
            continue
        if key in ("niche_keywords", "target_channels") and isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        elif key in {"branding_checklist", "profile_readiness", "channel_readiness", "profile_baseline"} and isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
            key = f"{key}_json" if key in {"profile_readiness", "channel_readiness", "profile_baseline"} else key
        elif key == "approved_sources" and isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        set_clauses.append(f"{key} = ?")
        params.append(value)

    if not set_clauses:
        return get_channel_trust_plan(plan_id)

    set_clauses.append("updated_at = ?")
    params.append(utc_now())
    params.append(plan_id)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"UPDATE channel_trust_plans SET {', '.join(set_clauses)} WHERE id = ?",
        params
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT p.*, c.title as channel_title, c.thumbnail_url as channel_thumbnail,
               c.gpm_profile_id, c.gpm_profile_name, c.gpm_proxy_info,
               c.publication_timezone
        FROM channel_trust_plans p
        LEFT JOIN youtube_channels c ON p.channel_db_id = c.id
        WHERE p.id = ?
        """,
        (plan_id,)
    ).fetchone()
    conn.close()
    return _decode_trust_plan(row)


def delete_channel_trust_plan(plan_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM trust_guided_sessions WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM trust_activity_log WHERE plan_id = ?", (plan_id,))
        cursor = conn.execute(
            "DELETE FROM channel_trust_plans WHERE id = ?", (plan_id,)
        )
        conn.execute("COMMIT")
        return cursor.rowcount > 0
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def create_trust_activity_log(
    plan_id: int,
    activity_type: str,
    target_url: str = "",
    target_title: str = "",
    duration_seconds: float = 0.0,
    detail_json: dict | None = None,
    success: bool = True,
    error_message: str = "",
    activity_source: str = "system_verified",
) -> dict:
    now = utc_now()
    detail_str = json.dumps(detail_json or {}, ensure_ascii=False)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        INSERT INTO trust_activity_log (
            plan_id, activity_type, target_url, target_title,
            duration_seconds, detail_json, success, error_message,
            activity_source, executed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id, activity_type, target_url, target_title,
            duration_seconds, detail_str, 1 if success else 0, error_message,
            activity_source, now
        )
    )
    log_id = cursor.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT * FROM trust_activity_log WHERE id = ?", (log_id,)
    ).fetchone()
    conn.close()
    return _decode_trust_activity_log(row)


def list_trust_activity_logs(
    plan_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM trust_activity_log
        WHERE plan_id = ?
        ORDER BY executed_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (plan_id, limit, offset)
    ).fetchall()
    conn.close()
    return [_decode_trust_activity_log(row) for row in rows]


GUIDED_SESSION_STATUSES = {"ready", "in_progress", "completed", "abandoned"}


def _decode_guided_session(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data["agenda"] = _decode_json_field(data.pop("agenda_json", None), {})
    data["checklist"] = _decode_json_field(data.pop("checklist_json", None), {})
    return data


def create_trust_guided_session(plan_id: int, agenda: dict) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT * FROM trust_guided_sessions
            WHERE plan_id = ? AND status IN ('ready', 'in_progress')
            ORDER BY created_at ASC LIMIT 1
            """,
            (plan_id,),
        ).fetchone()
        if existing:
            conn.execute("COMMIT")
            return _decode_guided_session(existing)
        cursor = conn.execute(
            """
            INSERT INTO trust_guided_sessions (
                plan_id, status, agenda_json, checklist_json, notes,
                started_at, completed_at, created_at, updated_at
            ) VALUES (?, 'ready', ?, '{}', '', '', '', ?, ?)
            """,
            (plan_id, json.dumps(agenda, ensure_ascii=False), now, now),
        )
        session_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM trust_guided_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        conn.execute("COMMIT")
        return _decode_guided_session(row)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_trust_guided_session(session_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM trust_guided_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    conn.close()
    return _decode_guided_session(row)


def list_trust_guided_sessions(
    plan_id: int,
    *,
    limit: int = 30,
    offset: int = 0,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM trust_guided_sessions
        WHERE plan_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (plan_id, max(1, min(int(limit), 200)), max(0, int(offset))),
    ).fetchall()
    conn.close()
    return [_decode_guided_session(row) for row in rows]


def update_trust_guided_session(session_id: int, **changes) -> dict | None:
    valid_fields = {"status", "checklist", "notes", "started_at", "completed_at"}
    invalid = set(changes) - valid_fields
    if invalid:
        raise ValueError(f"Unsupported guided session fields: {sorted(invalid)}")
    if "status" in changes and changes["status"] not in GUIDED_SESSION_STATUSES:
        raise ValueError("Trạng thái guided session không hợp lệ.")
    if "checklist" in changes:
        changes["checklist_json"] = json.dumps(changes.pop("checklist") or {}, ensure_ascii=False)
    if not changes:
        return get_trust_guided_session(session_id)
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE trust_guided_sessions SET {assignments} WHERE id = ?",
        (*changes.values(), session_id),
    )
    conn.commit()
    conn.close()
    return get_trust_guided_session(session_id)


def get_trust_activity_stats(
    plan_id: int,
    *,
    start_at: str = "",
    end_at: str = "",
) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["plan_id = ?", "success = 1"]
    params: list = [plan_id]
    if start_at:
        filters.append("executed_at >= ?")
        params.append(start_at)
    if end_at:
        filters.append("executed_at < ?")
        params.append(end_at)
    where_clause = " AND ".join(filters)
    
    # Total counts by activity_type
    counts_rows = conn.execute(
        """
        SELECT activity_type, COUNT(*) as count, SUM(duration_seconds) as total_duration
        FROM trust_activity_log
        WHERE {where_clause}
        GROUP BY activity_type
        """.format(where_clause=where_clause),
        params,
    ).fetchall()
    
    # Total distinct active days
    days_row = conn.execute(
        """
        SELECT COUNT(DISTINCT substr(executed_at, 1, 10)) as active_days
        FROM trust_activity_log
        WHERE {where_clause}
        """.format(where_clause=where_clause),
        params,
    ).fetchone()
    
    conn.close()

    stats = {
        "watch_count": 0,
        "search_count": 0,
        "like_count": 0,
        "comment_count": 0,
        "subscribe_count": 0,
        "total_watch_seconds": 0.0,
        "active_days": int(days_row["active_days"]) if days_row else 0,
    }
    for row in counts_rows:
        atype = str(row["activity_type"]).lower()
        count = int(row["count"] or 0)
        dur = float(row["total_duration"] or 0.0)
        if atype == "watch":
            stats["watch_count"] = count
            stats["total_watch_seconds"] = dur
        elif atype == "search":
            stats["search_count"] = count
        elif atype == "like":
            stats["like_count"] = count
        elif atype == "comment":
            stats["comment_count"] = count
        elif atype == "subscribe":
            stats["subscribe_count"] = count

    return stats


def get_trust_daily_activity_stats(
    plan_id: int,
    timezone_name: str,
    *,
    now_utc: datetime.datetime | None = None,
) -> dict:
    clean_timezone = str(timezone_name or "Asia/Ho_Chi_Minh").strip()
    try:
        timezone = ZoneInfo(clean_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    current_utc = now_utc or datetime.datetime.now(datetime.timezone.utc)
    if current_utc.tzinfo is None:
        current_utc = current_utc.replace(tzinfo=datetime.timezone.utc)
    local_day = current_utc.astimezone(timezone).date()
    local_start = datetime.datetime.combine(local_day, datetime.time.min, tzinfo=timezone)
    local_end = local_start + datetime.timedelta(days=1)
    return get_trust_activity_stats(
        plan_id,
        start_at=local_start.astimezone(datetime.timezone.utc).isoformat(),
        end_at=local_end.astimezone(datetime.timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Trust Safety Shield & Blacklist Engine
# ---------------------------------------------------------------------------


def normalize_safety_key(text: str) -> str:
    unquoted = urllib.parse.unquote(str(text or ""))
    return re.sub(r"[\s\W_]+", "", unquoted).casefold()


def strip_safety_diacritics(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "d")


def create_safety_blacklist_entry(
    entry_type: str,
    entry_value: str,
    reason: str = "",
    is_custom: int = 1,
    is_enabled: int = 1,
) -> dict:
    clean_type = str(entry_type or "channel").strip().lower()
    clean_val = str(entry_value or "").strip()
    if not clean_val:
        raise ValueError("Giá trị quy tắc chặn không được để trống.")
    norm_val = normalize_safety_key(clean_val)
    if not norm_val:
        norm_val = clean_val.casefold()
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        INSERT INTO trust_safety_blacklist (
            entry_type, entry_value, normalized_value, reason, is_custom, is_enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_type, normalized_value) DO UPDATE SET
            entry_value = excluded.entry_value,
            reason = excluded.reason,
            is_enabled = excluded.is_enabled,
            updated_at = excluded.updated_at
        """,
        (clean_type, clean_val, norm_val, reason.strip(), is_custom, is_enabled, now, now),
    )
    entry_id = cursor.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT * FROM trust_safety_blacklist WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM trust_safety_blacklist WHERE entry_type = ? AND normalized_value = ?",
            (clean_type, norm_val),
        ).fetchone()
    conn.close()
    return dict(row) if row else {}


def upsert_safety_blacklist_bulk(entries: list[dict]) -> int:
    if not entries:
        return 0
    now = utc_now()
    count = 0
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in entries:
            etype = str(item.get("entry_type") or "channel").strip().lower()
            eval_val = str(item.get("entry_value") or "").strip()
            if not eval_val:
                continue
            norm_val = normalize_safety_key(eval_val) or eval_val.casefold()
            reason = str(item.get("reason") or "").strip()
            is_custom = int(item.get("is_custom") or 0)
            is_enabled = 1 if item.get("is_enabled", True) else 0
            conn.execute(
                """
                INSERT INTO trust_safety_blacklist (
                    entry_type, entry_value, normalized_value, reason, is_custom, is_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_type, normalized_value) DO UPDATE SET
                    entry_value = excluded.entry_value,
                    reason = CASE WHEN excluded.reason != '' THEN excluded.reason ELSE trust_safety_blacklist.reason END,
                    updated_at = excluded.updated_at
                """,
                (etype, eval_val, norm_val, reason, is_custom, is_enabled, now, now),
            )
            count += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return count


def list_safety_blacklist_entries(
    entry_type: str = "",
    search: str = "",
    is_custom: int | None = None,
    enabled_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = []
    params: list = []
    if entry_type:
        filters.append("entry_type = ?")
        params.append(entry_type)
    if is_custom is not None:
        filters.append("is_custom = ?")
        params.append(is_custom)
    if enabled_only:
        filters.append("is_enabled = 1")
    if search:
        s_norm = f"%{search.strip().lower()}%"
        filters.append("(entry_value LIKE ? OR normalized_value LIKE ? OR reason LIKE ?)")
        params.extend([s_norm, s_norm, s_norm])
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = conn.execute(
        f"""
        SELECT * FROM trust_safety_blacklist
        {where_sql}
        ORDER BY is_custom DESC, updated_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_safety_blacklist_entry(entry_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM trust_safety_blacklist WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_safety_blacklist_entry(entry_id: int, **changes) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    set_clauses = []
    params = []
    if "is_enabled" in changes:
        set_clauses.append("is_enabled = ?")
        params.append(1 if changes["is_enabled"] else 0)
    if "reason" in changes:
        set_clauses.append("reason = ?")
        params.append(str(changes["reason"]).strip())
    if not set_clauses:
        conn.close()
        return get_safety_blacklist_entry(entry_id)
    set_clauses.append("updated_at = ?")
    params.append(utc_now())
    params.append(entry_id)
    conn.execute(
        f"UPDATE trust_safety_blacklist SET {', '.join(set_clauses)} WHERE id = ?",
        params,
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM trust_safety_blacklist WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_safety_blacklist_entry(entry_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cursor = conn.execute("DELETE FROM trust_safety_blacklist WHERE id = ?", (entry_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def get_safety_config() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT key, value FROM trust_safety_config").fetchall()
    conn.close()
    config = {
        "shield_enabled": True,
        "remote_sync_url": "https://raw.githubusercontent.com/Auto-YT/safety-shield/main/vietnam_blacklist.json",
        "last_synced_at": "",
        "auto_sync_interval_hours": 24,
    }
    for r in rows:
        k = r["key"]
        v = r["value"]
        if k == "shield_enabled":
            config["shield_enabled"] = v.lower() in {"1", "true", "yes"}
        elif k == "remote_sync_url":
            config["remote_sync_url"] = v
        elif k == "last_synced_at":
            config["last_synced_at"] = v
        elif k == "auto_sync_interval_hours":
            try:
                config["auto_sync_interval_hours"] = int(v)
            except ValueError:
                pass
    return config


def update_safety_config(**kwargs) -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    now = utc_now()
    for k, v in kwargs.items():
        val_str = str(v)
        if isinstance(v, bool):
            val_str = "1" if v else "0"
        conn.execute(
            """
            INSERT INTO trust_safety_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (k, val_str, now),
        )
    conn.commit()
    conn.close()
    return get_safety_config()


def get_all_active_safety_rules() -> dict[str, list[dict]]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM trust_safety_blacklist WHERE is_enabled = 1"
    ).fetchall()
    conn.close()
    result = {"channels": [], "keywords": [], "regex_patterns": []}
    for r in rows:
        d = dict(r)
        etype = str(d.get("entry_type") or "").lower()
        if etype == "channel":
            result["channels"].append(d)
        elif etype == "keyword":
            result["keywords"].append(d)
        elif etype in {"regex_pattern", "regex"}:
            result["regex_patterns"].append(d)
    return result
