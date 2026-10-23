import urllib.error
import unittest
from unittest.mock import patch, MagicMock

from nexus_engine.services import youtube_comments
import nexus_engine.main as main_module


class TestYouTubeOAuthProxySync(unittest.TestCase):
    def test_exchange_authorization_code_wraps_url_error(self):
        with patch("nexus_engine.services.youtube_comments.create_proxy_opener") as mock_opener:
            mock_instance = MagicMock()
            mock_instance.open.side_effect = urllib.error.URLError("Connection refused [WinError 10060]")
            mock_opener.return_value = mock_instance

            with self.assertRaises(youtube_comments.YouTubeCommentsError) as ctx:
                youtube_comments.exchange_authorization_code(
                    code="fake_code",
                    config={"client_id": "c1", "client_secret": "s1", "redirect_uri": "http://localhost"},
                    proxy="1.2.3.4:8080",
                )
            self.assertIn("Không thể kết nối máy chủ Google qua proxy", str(ctx.exception))

    def test_exchange_authorization_code_wraps_timeout_error(self):
        with patch("nexus_engine.services.youtube_comments.create_proxy_opener") as mock_opener:
            mock_instance = MagicMock()
            mock_instance.open.side_effect = TimeoutError("Timed out")
            mock_opener.return_value = mock_instance

            with self.assertRaises(youtube_comments.YouTubeCommentsError) as ctx:
                youtube_comments.exchange_authorization_code(
                    code="fake_code",
                    config={"client_id": "c1", "client_secret": "s1", "redirect_uri": "http://localhost"},
                    proxy="1.2.3.4:8080",
                )
            self.assertIn("Không thể kết nối máy chủ Google qua proxy", str(ctx.exception))

    @patch("nexus_engine.main.db")
    @patch("nexus_engine.main.gpm_service")
    @patch("nexus_engine.main.youtube_comments")
    def test_start_youtube_oauth_syncs_fresh_proxy_from_gpm(self, mock_yt, mock_gpm, mock_db):
        mock_db.get_youtube_channel_by_channel_id.return_value = {
            "id": 1,
            "channel_id": "UC123",
            "title": "Test Channel",
            "gpm_profile_id": "gpm-1",
            "gpm_profile_name": "Old Name",
            "gpm_proxy_info": "1.1.1.1:8080:old:old",
        }
        mock_gpm.get_gpm_profile_detail.return_value = {
            "name": "New Name",
            "raw_proxy": "2.2.2.2:9090:new:new",
        }
        mock_yt.load_oauth_configs.return_value = [{"client_id": "client-1"}]
        mock_yt.create_oauth_state.return_value = "state-123"
        mock_yt.create_pkce_pair.return_value = ("verifier", "challenge")
        mock_yt.build_authorization_url.return_value = "https://accounts.google.com/o/oauth2/auth"

        res = main_module.start_youtube_oauth(
            client_id="client-1",
            expected_channel_id="UC123",
            gpm_profile_id="gpm-1",
        )

        self.assertEqual(res["gpm_profile_name"], "New Name")
        self.assertEqual(res["gpm_proxy_display"], "2.2.2.2:9090")
        mock_db.update_youtube_channel.assert_called_with(
            1,
            gpm_profile_id="gpm-1",
            gpm_profile_name="New Name",
            gpm_proxy_info="2.2.2.2:9090:new:new",
        )
