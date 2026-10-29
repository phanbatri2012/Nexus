"""Central path constants for the nexus_engine project."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
SHORT_VIDEO_PROJECTS_CONFIG_DIR = CONFIG_DIR / "short_video_projects"
DATA_DIR = Path(
    os.environ.get("NEXUS_DATA_DIR")
    or os.environ.get("AUTO_YT_DATA_DIR")
    or PROJECT_ROOT / "data"
)
CHROME_USER_DATA_DIR = DATA_DIR / "chrome_user_data"

ACCOUNT_PATH = DATA_DIR / "account.json"
GOOGLE_FLOW_ACCOUNT_PATH = DATA_DIR / "google_flow_account.json"
PROMPTS_PATH = DATA_DIR / "prompts.json"
MUSE_SETTINGS_PATH = DATA_DIR / "muse_settings.json"
ACCOUNT_EXAMPLE_PATH = CONFIG_DIR / "account.example.json"
SESSION_PATH = DATA_DIR / "session_chatgpt.json"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
AUDIO_DIR = DATA_DIR / "audio"
VIDEO_ARTIFACTS_DIR = DATA_DIR / "video_artifacts"
SCENES_DIR = VIDEO_ARTIFACTS_DIR / "scenes"
CAPTIONS_DIR = VIDEO_ARTIFACTS_DIR / "captions"
RENDERS_DIR = VIDEO_ARTIFACTS_DIR / "renders"
VISUAL_PLANS_DIR = VIDEO_ARTIFACTS_DIR / "visual_plans"
SEGMENTS_DIR = VIDEO_ARTIFACTS_DIR / "segments"
FLOW_ARTIFACTS_DIR = VIDEO_ARTIFACTS_DIR / "flow"
FLOW_REFERENCE_STAGING_DIR = FLOW_ARTIFACTS_DIR / "staging"
MUSE_ARTIFACTS_DIR = VIDEO_ARTIFACTS_DIR / "muse"
SHORT_VIDEO_DIR = DATA_DIR / "short_video"
PROMPT_ASSETS_DIR = DATA_DIR / "prompt_assets"
BACKGROUND_VIDEOS_DIR = DATA_DIR / "background_videos"
ANIMATED_ICONS_DIR = DATA_DIR / "animated_icons"
LOGS_DIR = DATA_DIR / "logs"
DIAGNOSTICS_DIR = LOGS_DIR / "diagnostics"
REFERENCES_DIR = DATA_DIR / "references"
LOCAL_VAULT_DIR = DATA_DIR / "references_vault"




def gpt_profile_dir(profile_name: str = "PROFILE_GPT_1") -> Path:
    """Return the Chrome user-data dir for a specific GPT profile."""
    return CHROME_USER_DATA_DIR / profile_name


def google_flow_profile_dir(profile_name: str = "PROFILE_GOOGLE_FLOW_1") -> Path:
    """Return the isolated Chrome user-data dir for Google Flow."""
    return CHROME_USER_DATA_DIR / profile_name
