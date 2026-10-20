import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from auto_yt.services.gpm_service import (
    TIMEZONE_LANGUAGE_CONFIG,
    resolve_market_language_config,
    patch_gpm_profile_language,
    start_gpm_profile,
)
from auto_yt.services.trust_builder_service import (
    generate_contextual_comment,
    LANGUAGE_COMMENT_TEMPLATES,
)
from auto_yt.services.youtube_comments import (
    build_comment_reply_prompt,
    _get_market_lang_info,
)
from auto_yt.services.chatgpt_worker import validate_prompt_text


def test_resolve_market_language_config():
    # Spanish - Spain
    es_cfg = resolve_market_language_config("Europe/Madrid")
    assert es_cfg["primary_lang"] == "es-ES"
    assert es_cfg["lang_code"] == "es"
    assert "es-ES" in es_cfg["accept_languages"]

    # Spanish - Mexico
    mx_cfg = resolve_market_language_config("America/Mexico_City")
    assert mx_cfg["primary_lang"] == "es-MX"
    assert mx_cfg["lang_code"] == "es"

    # German
    de_cfg = resolve_market_language_config("Europe/Berlin")
    assert de_cfg["primary_lang"] == "de-DE"
    assert de_cfg["lang_code"] == "de"

    # French
    fr_cfg = resolve_market_language_config("Europe/Paris")
    assert fr_cfg["primary_lang"] == "fr-FR"
    assert fr_cfg["lang_code"] == "fr"

    # Japanese
    ja_cfg = resolve_market_language_config("Asia/Tokyo")
    assert ja_cfg["primary_lang"] == "ja-JP"
    assert ja_cfg["lang_code"] == "ja"

    # Korean
    ko_cfg = resolve_market_language_config("Asia/Seoul")
    assert ko_cfg["primary_lang"] == "ko-KR"
    assert ko_cfg["lang_code"] == "ko"

    # Portuguese - Brazil
    pt_cfg = resolve_market_language_config("America/Sao_Paulo")
    assert pt_cfg["primary_lang"] == "pt-BR"
    assert pt_cfg["lang_code"] == "pt"

    # Vietnam
    vi_cfg = resolve_market_language_config("Asia/Ho_Chi_Minh")
    assert vi_cfg["primary_lang"] == "vi-VN"
    assert vi_cfg["lang_code"] == "vi"

    # Fallback Americas
    assert resolve_market_language_config("America/Unknown")["primary_lang"] == "en-US"
    # Fallback Europe
    assert resolve_market_language_config("Europe/Unknown")["primary_lang"] == "en-GB"


def test_patch_gpm_profile_language():
    with tempfile.TemporaryDirectory() as tmpdir:
        profile_dir = Path(tmpdir)
        default_dir = profile_dir / "Default"
        default_dir.mkdir(parents=True)
        prefs_file = default_dir / "Preferences"

        initial_prefs = {
            "browser": {"has_seen_welcome_page": True},
            "intl": {"selected_languages": "en-US,en", "accept_languages": "en-US,en"},
        }
        prefs_file.write_text(json.dumps(initial_prefs), encoding="utf-8")

        # Patch to Spain
        success = patch_gpm_profile_language(
            profile_id="test-profile-123",
            timezone_name="Europe/Madrid",
            custom_profile_dir=profile_dir,
        )
        assert success is True

        # Check backup created
        bak_file = prefs_file.with_suffix(".bak")
        assert bak_file.exists()
        bak_data = json.loads(bak_file.read_text(encoding="utf-8"))
        assert bak_data["intl"]["accept_languages"] == "en-US,en"

        # Check new preferences
        new_data = json.loads(prefs_file.read_text(encoding="utf-8"))
        assert "es-ES" in new_data["intl"]["selected_languages"]
        assert "es-ES" in new_data["intl"]["accept_languages"]


def test_start_gpm_profile_adds_lang_flag():
    with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None), \
         patch("auto_yt.services.gpm_service._request_gpm_api") as mock_api, \
         patch("auto_yt.services.gpm_service.patch_gpm_profile_language") as mock_patch:
        
        mock_api.return_value = {
            "success": True,
            "data": {
                "remote_debugging_port": 12345,
                "websocket_debugging_url": "ws://127.0.0.1:12345/devtools/browser/abc",
            },
        }

        start_gpm_profile(
            "profile-spain",
            market_timezone="Europe/Madrid",
            addition_args="--disable-gpu",
        )

        mock_patch.assert_called_once_with("profile-spain", "Europe/Madrid", api_url=None)
        mock_api.assert_called_once()
        _, kwargs = mock_api.call_args
        params = kwargs.get("params", {})
        assert "--lang=es-ES" in params.get("addition_args", "")
        assert "--disable-gpu" in params.get("addition_args", "")


def test_generate_contextual_comment_multi_language():
    # Spanish
    es_comment = generate_contextual_comment("Video de prueba", market_timezone="Europe/Madrid")
    assert es_comment in LANGUAGE_COMMENT_TEMPLATES["es"]

    # German
    de_comment = generate_contextual_comment("Test Video", market_timezone="Europe/Berlin")
    assert de_comment in LANGUAGE_COMMENT_TEMPLATES["de"]

    # French
    fr_comment = generate_contextual_comment("Vidéo test", market_timezone="Europe/Paris")
    assert fr_comment in LANGUAGE_COMMENT_TEMPLATES["fr"]

    # Japanese
    ja_comment = generate_contextual_comment("テスト動画", market_timezone="Asia/Tokyo")
    assert ja_comment in LANGUAGE_COMMENT_TEMPLATES["ja"]

    # Korean
    ko_comment = generate_contextual_comment("테스트 영상", market_timezone="Asia/Seoul")
    assert ko_comment in LANGUAGE_COMMENT_TEMPLATES["ko"]

    # Portuguese
    pt_comment = generate_contextual_comment("Vídeo de teste", market_timezone="America/Sao_Paulo")
    assert pt_comment in LANGUAGE_COMMENT_TEMPLATES["pt"]

    # Vietnamese
    vi_comment = generate_contextual_comment("Video thử nghiệm", market_timezone="Asia/Ho_Chi_Minh")
    assert vi_comment in LANGUAGE_COMMENT_TEMPLATES["vi"]


def test_build_comment_reply_prompt_localization():
    comments = [
        {"comment_id": "c1", "author_name": "Juan", "text": "¿Cómo puedo configurar esto?"},
        {"comment_id": "c2", "author_name": "Hans", "text": "Sehr hilfreiches Video, danke!"},
    ]

    prompt = build_comment_reply_prompt(
        comments,
        instruction="Ngắn gọn, súc tích",
        market_timezone="Europe/Madrid",
    )

    # Validate syntax and corruption
    validate_prompt_text(prompt)

    # Check that language rule and target market are present
    assert "QUY TẮC NGÔN NGỮ:" in prompt
    assert "Europe/Madrid" in prompt
    assert "es-ES" in prompt
    assert "UNTRUSTED_COMMENTS_START" in prompt
    assert "c1" in prompt
    assert "c2" in prompt
