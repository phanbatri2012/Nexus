import unittest
from unittest.mock import MagicMock
from auto_yt.services.google_flow_worker import (
    GoogleFlowWorker,
    _upgrade_google_cdn_image_url,
)


class TestGoogleFlowWorkerMedia(unittest.TestCase):
    def test_upgrade_google_cdn_image_url(self):
        # flow.google.com asb URL
        url = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQwmjl7k34g17zPgVmPwmVauSNF2TcC6A26pkwNRmVRzvHTrOvnEs=s1600-rw"
        upgraded = _upgrade_google_cdn_image_url(url)
        self.assertEqual(
            upgraded,
            "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQwmjl7k34g17zPgVmPwmVauSNF2TcC6A26pkwNRmVRzvHTrOvnEs=s0",
        )

        # googleusercontent URL
        url_lh3 = "https://lh3.googleusercontent.com/abc123xyz=w512-h288-p"
        self.assertEqual(_upgrade_google_cdn_image_url(url_lh3), "https://lh3.googleusercontent.com/abc123xyz=s0")

        # non-google CDN or blob url
        blob = "blob:https://flow.google.com/12345"
        self.assertEqual(_upgrade_google_cdn_image_url(blob), blob)

    def test_media_key_normalization(self):
        url1 = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw?foo=bar"
        url2 = "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s0"
        key1 = GoogleFlowWorker._media_key(url1)
        key2 = GoogleFlowWorker._media_key(url2)
        self.assertEqual(key1, key2)
        self.assertEqual(key1, "https://flow.google.com/asb/anqvlobn17tbpb92bsvfonbq")

    def test_find_new_media_candidate_with_asb_and_references(self):
        mock_page = MagicMock()
        worker = GoogleFlowWorker(mock_page)

        # Baseline contains reference thumbnail
        ref_url = "https://flow-content.google/image/a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7"
        ref_key = worker._media_key(ref_url)
        baseline_keys = {ref_key, "asset:a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7"}

        # Active reference filenames
        worker._active_reference_filenames = {"thumb_4c1bfc9c426b.png"}
        worker._active_reference_keys = baseline_keys

        candidates = [
            {
                "src": ref_url,
                "urls": [ref_url],
                "assetId": "a3c3f570-42cd-4e1b-bf08-3b1ca2df88e7",
                "labelText": "thumb_4c1bfc9c426b.png",
                "turnRole": "",
                "width": 1280,
                "height": 720,
            },
            {
                "src": "https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw",
                "urls": ["https://flow.google.com/asb/ANqvLObN17TBPB92BsvfONBQ=s1600-rw"],
                "assetId": "64f26af1-0efc-4c94-9519-724f8cbcaf6e",
                "labelText": "Vietnamese documentary photograph...",
                "turnRole": "agent",
                "width": 1920,
                "height": 1080,
            },
        ]

        selected = worker._find_new_media_candidate(
            candidates,
            baseline_keys,
            exclude_uploaded_images=True,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["assetId"], "64f26af1-0efc-4c94-9519-724f8cbcaf6e")
        self.assertEqual(selected["turnRole"], "agent")


if __name__ == "__main__":
    unittest.main()
