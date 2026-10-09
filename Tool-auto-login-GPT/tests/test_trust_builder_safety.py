import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from auto_yt.services import database as db
from auto_yt.services.trust_builder_safety import (
    ensure_default_safety_rules_seeded,
    is_safe_for_interaction,
    sync_safety_blacklist_from_remote,
    validate_regex_pattern,
    validate_remote_sync_url,
)


class TestTrustBuilderSafetyShield(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_db_path = Path(self.temp_dir.name) / "test_safety.db"
        self.db_patch = patch.object(db, "DB_PATH", self.temp_db_path)
        self.db_patch.start()
        db.init_db()
        # Seed default built-in safety rules
        ensure_default_safety_rules_seeded()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_strip_diacritics(self):
        self.assertEqual(db.strip_safety_diacritics("Việt Tân").casefold(), "viet tan")
        self.assertEqual(db.strip_safety_diacritics("Cách Mạng Trắng").casefold(), "cach mang trang")
        self.assertEqual(db.strip_safety_diacritics("Củ Đậu Story").casefold(), "cu dau story")
        self.assertEqual(db.strip_safety_diacritics("Đài Á Châu Tự Do").casefold(), "dai a chau tu do")

    def test_normalize_channel_key(self):
        self.assertEqual(db.normalize_safety_key("@viettan"), "viettan")
        self.assertEqual(db.normalize_safety_key("@C%E1%BB%A7%C4%90%E1%BA%ADuStory"), "củđậustory")
        self.assertEqual(
            db.strip_safety_diacritics(db.normalize_safety_key("@C%E1%BB%A7%C4%90%E1%BA%ADuStory")),
            "cudaustory",
        )
        self.assertEqual(db.normalize_safety_key("Kênh Việt Tân Official!"), "kênhviệttânofficial")

    def test_default_safety_rules_seeded(self):
        rules = db.get_all_active_safety_rules()
        self.assertGreater(len(rules.get("channels", [])), 10)
        self.assertGreater(len(rules.get("keywords", [])), 5)
        config = db.get_safety_config()
        self.assertTrue(config["shield_enabled"])
        self.assertEqual(config["auto_sync_interval_hours"], 24)

    def test_is_safe_for_interaction_blocked_channel_handle(self):
        # Hostile channel handle
        safe, reason = is_safe_for_interaction(
            channel_name="Unrelated Cooking Name",
            channel_handle="@viettan",
            title="Video nấu ăn",
        )
        self.assertFalse(safe)
        self.assertTrue("viet" in reason.lower() or "phản động" in reason.lower())

        # Subversive handle via channel_url
        safe, reason = is_safe_for_interaction(
            channel_name="Some Channel",
            channel_url="https://www.youtube.com/@nguoibuongio",
            title="Bản tin thường ngày",
        )
        self.assertFalse(safe)
        self.assertTrue("nguoibuongio" in reason.lower() or "chống phá" in reason.lower() or len(reason) > 0)

    def test_is_safe_for_interaction_blocked_channel_name(self):
        # Hostile channel display name
        safe, reason = is_safe_for_interaction(
            channel_name="Đài Á Châu Tự Do (RFA Vietnamese)",
            channel_handle="@randomhandle123",
            title="Bản tin tổng hợp",
        )
        self.assertFalse(safe)
        self.assertTrue(len(reason) > 0)

        # Subversive news name
        safe, reason = is_safe_for_interaction(
            channel_name="Tiếng Dân News Official",
            channel_handle="@tdn_channel",
            title="Chuyện đời thường",
        )
        self.assertFalse(safe)
        self.assertTrue(len(reason) > 0)

    def test_is_safe_for_interaction_blocked_keywords_in_title(self):
        # Subversive keyword in video title
        safe, reason = is_safe_for_interaction(
            channel_name="Tin Tức Mới",
            channel_handle="@tintucmoi",
            title="Kêu gọi cách mạng trắng lật đổ chế độ toàn diện",
        )
        self.assertFalse(safe)
        self.assertIn("từ khóa", reason.lower())

        # Hostile keyword in title
        safe, reason = is_safe_for_interaction(
            channel_name="Tin Tức Mới",
            channel_handle="@tintucmoi",
            title="Tổ chức bạo loạn lật đổ chính quyền nhân dân tại địa phương",
        )
        self.assertFalse(safe)
        self.assertIn("từ khóa", reason.lower())

    def test_is_safe_for_interaction_allowed_clean_content(self):
        # Legitimate channel and title
        safe, reason = is_safe_for_interaction(
            channel_name="VTV24 Đài Truyền Hình Việt Nam",
            channel_handle="@VTV24",
            title="Chuyển động 24h - Bản tin thời sự trưa hôm nay",
        )
        self.assertTrue(safe)
        self.assertEqual(reason, "")

        # Cooking channel
        safe, reason = is_safe_for_interaction(
            channel_name="Ẩm Thực Mẹ Làm",
            channel_handle="@AmThucMeLam",
            title="Cách nấu canh chua cá lóc miền Tây chuẩn vị",
        )
        self.assertTrue(safe)
        self.assertEqual(reason, "")

    def test_custom_user_blacklist_rule(self):
        # Add custom channel handle to blacklist
        db.create_safety_blacklist_entry(
            entry_type="channel",
            entry_value="spammy_channel_xyz",
            reason="Kênh spam nội dung rác",
            is_custom=1,
            is_enabled=True,
        )
        safe, reason = is_safe_for_interaction(
            channel_name="Spam Video Studio",
            channel_handle="@spammy_channel_xyz",
            title="Video câu view",
        )
        self.assertFalse(safe)
        self.assertIn("spammy_channel_xyz", reason.lower())

    def test_disabled_shield_allows_interaction(self):
        # Disable shield in config
        db.update_safety_config(shield_enabled=False)
        safe, reason = is_safe_for_interaction(
            channel_name="Việt Tân",
            channel_handle="@viettan",
            title="Video cách mạng trắng",
        )
        self.assertTrue(safe)
        self.assertEqual(reason, "")

    @patch("urllib.request.urlopen")
    def test_sync_safety_blacklist_from_remote_success(self, mock_urlopen):
        # Mock remote JSON response
        mock_response = MagicMock()
        mock_response.read.return_value = """{
            "version": "2026.10.07",
            "channels": [
                {"handle": "@new_reactionary_org", "name": "Tổ chức chống đối mới", "reason": "Tổ chức khủng bố mới"}
            ],
            "keywords": [
                {"keyword": "bạo loạn lật đổ", "reason": "Kêu gọi bạo loạn"}
            ]
        }""".encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = sync_safety_blacklist_from_remote(
            remote_url="https://raw.githubusercontent.com/test/blacklist.json",
        )
        self.assertTrue(res["success"])
        self.assertGreater(res["total_synced"], 0)

        # Verify new rules take effect immediately
        safe, reason = is_safe_for_interaction(
            channel_name="Unrelated",
            channel_handle="@new_reactionary_org",
            title="Normal video",
        )
        self.assertFalse(safe)

    def test_sync_safety_blacklist_offline_fallback(self):
        # Remote fails (invalid URL) -> should gracefully handle without throwing
        res = sync_safety_blacklist_from_remote(
            remote_url="http://127.0.0.1:99999/non_existent_manifest.json",
        )
        self.assertFalse(res["success"])
        self.assertIn("message", res)
        # Built-in rules should still exist and protect
        rules = db.get_all_active_safety_rules()
        self.assertGreater(len(rules.get("channels", [])), 10)

    def test_remote_sync_rejects_private_or_non_allowlisted_hosts(self):
        with self.assertRaises(ValueError):
            validate_remote_sync_url("http://127.0.0.1/internal.json")
        with self.assertRaises(ValueError):
            validate_remote_sync_url("https://example.com/blacklist.json")

    def test_regex_validation_rejects_nested_quantifiers(self):
        with self.assertRaises(ValueError):
            validate_regex_pattern("(a+)+$")
        self.assertEqual(validate_regex_pattern(r"\btrusted\s+term\b"), r"\btrusted\s+term\b")


if __name__ == "__main__":
    unittest.main()
