import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from auto_yt.services import youtube_service
from auto_yt.services.proxy_utils import ProxyConfigurationError
from youtube_transcript_api._errors import RequestBlocked, TranscriptsDisabled


class YouTubeServiceTests(unittest.TestCase):
    def test_configures_system_trust_store(self):
        with patch.object(youtube_service.truststore, "inject_into_ssl") as inject:
            youtube_service._configure_system_trust_store()

        inject.assert_called_once_with()

    def test_title_request_keeps_tls_verification_enabled(self):
        response = MagicMock()
        response.text = "<title>Tiêu đề kiểm thử - YouTube</title>"

        with patch.object(
            youtube_service.curl_requests,
            "get",
            return_value=response,
        ) as get:
            title = youtube_service.get_video_title(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(title, "Tiêu đề kiểm thử")
        self.assertNotIn("verify", get.call_args.kwargs)
        self.assertEqual(
            get.call_args.kwargs["timeout"],
            youtube_service.YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status.assert_called_once_with()

    def test_title_decodes_html_entities(self):
        response = MagicMock()
        response.text = "<title>Nội dung &quot;đặc biệt&quot; - YouTube</title>"

        with patch.object(
            youtube_service.curl_requests,
            "get",
            return_value=response,
        ):
            title = youtube_service.get_video_title(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(title, 'Nội dung "đặc biệt"')

    def test_transcript_download_keeps_tls_verification_enabled(self):
        class FakeYoutubeDL:
            last_options = {}

            def __init__(self, options):
                type(self).last_options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                self.assert_download_disabled = not download
                return {
                    "automatic_captions": {
                        "vi": [{
                            "ext": "json3",
                            "url": "https://www.youtube.com/api/timedtext",
                        }]
                    }
                }

        response = MagicMock()
        response.json.return_value = {
            "events": [{"segs": [{"utf8": "Nội dung "}, {"utf8": "mới"}]}]
        }

        with (
            patch.object(youtube_service, "YoutubeDL", FakeYoutubeDL),
            patch.object(
                youtube_service.curl_requests,
                "get",
                return_value=response,
            ) as get,
        ):
            transcript = youtube_service.get_video_transcript(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(transcript, "Nội dung mới")
        self.assertNotIn("nocheckcertificate", FakeYoutubeDL.last_options)
        self.assertNotIn("verify", get.call_args.kwargs)
        response.raise_for_status.assert_called_once_with()

    def test_transcript_falls_back_to_transcript_api_when_ytdlp_is_rate_limited(self):
        class RateLimitedYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download=False):
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        class TranscriptSnippet:
            def __init__(self, text):
                self.text = text

        transcript = MagicMock()
        transcript.language_code = "vi"
        transcript.is_generated = False
        transcript.is_translatable = False
        transcript.fetch.return_value = [
            TranscriptSnippet("Nội dung dự phòng"),
            TranscriptSnippet("vẫn đầy đủ"),
        ]
        transcript_api = MagicMock()
        transcript_api.list.return_value = [transcript]

        with (
            patch.object(youtube_service, "YoutubeDL", RateLimitedYoutubeDL),
            patch.object(
                youtube_service,
                "YouTubeTranscriptApi",
                return_value=transcript_api,
            ),
        ):
            transcript = youtube_service.get_video_transcript(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(transcript, "Nội dung dự phòng vẫn đầy đủ")
        transcript_api.list.assert_called_once_with("video-id")

    def test_transcript_selector_accepts_vietnamese_locale_variant(self):
        manual_locale = MagicMock()
        manual_locale.language_code = "vi-VN"
        manual_locale.is_generated = False
        manual_locale.is_translatable = False
        generated_english = MagicMock()
        generated_english.language_code = "en"
        generated_english.is_generated = True
        generated_english.is_translatable = False

        selected = youtube_service._select_transcript(
            [generated_english, manual_locale]
        )

        self.assertIs(selected, manual_locale)

    def test_transcript_uses_required_proxy_for_ytdlp_and_caption_download(self):
        class ProxyYoutubeDL:
            last_options = {}

            def __init__(self, options):
                type(self).last_options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                return {
                    "automatic_captions": {
                        "vi-VN": [{
                            "ext": "json3",
                            "url": "https://www.youtube.com/api/timedtext",
                        }]
                    }
                }

        response = MagicMock()
        response.json.return_value = {
            "events": [{"segs": [{"utf8": "Nội dung qua proxy"}]}]
        }
        with (
            patch.object(youtube_service, "YoutubeDL", ProxyYoutubeDL),
            patch.object(
                youtube_service.curl_requests,
                "get",
                return_value=response,
            ) as get,
        ):
            transcript = youtube_service.get_video_transcript(
                "https://www.youtube.com/watch?v=video-id",
                proxy="127.0.0.1:8123",
                require_proxy=True,
            )

        expected_proxy = "http://127.0.0.1:8123"
        self.assertEqual(transcript, "Nội dung qua proxy")
        self.assertEqual(ProxyYoutubeDL.last_options["proxy"], expected_proxy)
        self.assertEqual(get.call_args.kwargs["proxy"], expected_proxy)

    def test_required_proxy_fails_closed_before_any_provider_runs(self):
        with (
            patch.object(youtube_service, "_get_transcript_with_ytdlp") as ytdlp,
            patch.object(youtube_service, "_get_transcript_with_transcript_api") as api,
            self.assertRaises(ProxyConfigurationError),
        ):
            youtube_service.get_video_transcript(
                "https://www.youtube.com/watch?v=video-id",
                require_proxy=True,
            )

        ytdlp.assert_not_called()
        api.assert_not_called()

    def test_request_block_is_classified_as_retryable(self):
        with (
            patch.object(
                youtube_service,
                "_get_transcript_with_ytdlp",
                side_effect=RuntimeError("HTTP Error 429"),
            ),
            patch.object(
                youtube_service,
                "_get_transcript_with_transcript_api",
                side_effect=RequestBlocked("video-id"),
            ),
        ):
            with self.assertRaises(
                youtube_service.TranscriptTemporarilyUnavailable
            ) as raised:
                youtube_service.get_video_transcript(
                    "https://www.youtube.com/watch?v=video-id"
                )

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "transcript_request_blocked")

    def test_disabled_transcript_is_terminal(self):
        with (
            patch.object(
                youtube_service,
                "_get_transcript_with_ytdlp",
                side_effect=ValueError("no captions"),
            ),
            patch.object(
                youtube_service,
                "_get_transcript_with_transcript_api",
                side_effect=TranscriptsDisabled("video-id"),
            ),
        ):
            with self.assertRaises(youtube_service.TranscriptUnavailable) as raised:
                youtube_service.get_video_transcript(
                    "https://www.youtube.com/watch?v=video-id"
                )

        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "transcripts_disabled")


if __name__ == "__main__":
    unittest.main()
