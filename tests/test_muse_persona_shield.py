import unittest
from nexus_engine.services.muse_pipeline import (
    neutralize_persona_names,
    _prepare_balanced_submission,
    _required_semantic_anchors,
    _build_refusal_fallback,
)


class TestMusePersonaShield(unittest.TestCase):
    def setUp(self):
        self.all_references = [
            {"set_name": "võ nguyên giáp", "aliases": ["võ nguyên giáp"], "sha256": "hash_vng"},
            {"set_name": "Hồ Chí Minh", "aliases": ["hồ chí minh", "bác hồ"], "sha256": "hash_hcm"},
            {"set_name": "Hoàng đan", "aliases": ["hoàng đan"], "sha256": "hash_hd"},
            {"set_name": "Lê duẩn", "aliases": ["lê duẩn"], "sha256": "hash_ld"},
        ]
        self.selected_references = [
            {"set_name": "võ nguyên giáp", "aliases": ["võ nguyên giáp"], "sha256": "hash_vng"}
        ]

    def test_neutralize_titled_attached_persona(self):
        text = "Đại tướng Võ Nguyên Giáp đứng bên bản đồ chiến dịch."
        shielded = neutralize_persona_names(
            text,
            selected_references=self.selected_references,
            all_references=self.all_references,
        )
        self.assertNotIn("Võ Nguyên Giáp", shielded)
        self.assertNotIn("Đại tướng", shielded)
        self.assertIn("referenced senior military commander", shielded)

    def test_neutralize_titled_unattached_persona(self):
        text = "Trung tướng Hoàng Minh Thảo nghiên cứu tài liệu tác chiến."
        shielded = neutralize_persona_names(
            text,
            selected_references=self.selected_references,
            all_references=self.all_references,
        )
        self.assertNotIn("Hoàng Minh Thảo", shielded)
        self.assertNotIn("Trung tướng", shielded)
        self.assertIn("accompanying military officer", shielded)

    def test_neutralize_political_leaders(self):
        text = "Chủ tịch Hồ Chí Minh cùng Tổng bí thư Lê Duẩn chủ trì phiên họp."
        shielded = neutralize_persona_names(
            text,
            selected_references=[],
            all_references=self.all_references,
        )
        self.assertNotIn("Hồ Chí Minh", shielded)
        self.assertNotIn("Lê Duẩn", shielded)
        self.assertIn("accompanying historical official", shielded)

    def test_neutralize_anchors(self):
        anchor_attached = neutralize_persona_names(
            "Đại tướng Võ Nguyên Giáp",
            selected_references=self.selected_references,
            all_references=self.all_references,
            is_anchor=True,
        )
        self.assertEqual(anchor_attached, "Senior military commander matching reference image")

        anchor_unattached = neutralize_persona_names(
            "Trung tướng Hoàng Minh Thảo",
            selected_references=self.selected_references,
            all_references=self.all_references,
            is_anchor=True,
        )
        self.assertEqual(anchor_unattached, "Accompanying military officer")

        normal_object = neutralize_persona_names(
            "Bản đồ Tây Nguyên",
            selected_references=self.selected_references,
            all_references=self.all_references,
            is_anchor=True,
        )
        self.assertEqual(normal_object, "Bản đồ Tây Nguyên")

    def test_prepare_balanced_submission_persona_shield(self):
        scene = {
            "index": 5,
            "setting": "Phòng tham mưu phục dựng",
            "era": "Đầu năm 1975",
            "subject": "Đại tướng Võ Nguyên Giáp và Trung tướng Hoàng Minh Thảo",
            "visual_anchor": "Võ Nguyên Giáp đứng bên mép bàn, Hoàng Minh Thảo đối diện",
            "must_show": [
                "Đại tướng Võ Nguyên Giáp",
                "Trung tướng Hoàng Minh Thảo",
                "Bản đồ Tây Nguyên",
            ],
            "prompt": "Subject: Đại tướng Võ Nguyên Giáp và Trung tướng Hoàng Minh Thảo. Action: Nghiên cứu bản đồ.",
        }
        prompt, refs, metadata = _prepare_balanced_submission(
            scene,
            "image",
            scene["prompt"],
            self.selected_references,
            all_references=self.all_references,
        )
        self.assertEqual(metadata["prompt_strategy"], "persona_shielded")
        self.assertNotIn("Võ Nguyên Giáp", prompt)
        self.assertNotIn("Hoàng Minh Thảo", prompt)
        self.assertIn("the referenced senior military commander", prompt)
        self.assertIn("the accompanying military officer", prompt)

    def test_required_semantic_anchors_sanitization(self):
        scene = {
            "index": 5,
            "setting": "Phòng tham mưu phục dựng",
            "era": "Đầu năm 1975",
            "safe_visual_anchor": "Võ Nguyên Giáp và Hoàng Minh Thảo cùng nghiên cứu",
            "must_show": [
                "Đại tướng Võ Nguyên Giáp",
                "Trung tướng Hoàng Minh Thảo",
                "Bản đồ Tây Nguyên",
            ],
        }
        setting, era, safe_context, must_show = _required_semantic_anchors(
            scene,
            selected_references=self.selected_references,
            all_references=self.all_references,
        )
        self.assertNotIn("Võ Nguyên Giáp", safe_context)
        self.assertNotIn("Hoàng Minh Thảo", safe_context)
        for anchor in must_show:
            self.assertNotIn("Võ Nguyên Giáp", anchor)
            self.assertNotIn("Hoàng Minh Thảo", anchor)

    def test_fallback_does_not_contain_persona_names(self):
        scene = {
            "index": 5,
            "setting": "Phòng tham mưu phục dựng",
            "era": "Đầu năm 1975",
            "safe_visual_anchor": "Võ Nguyên Giáp và Hoàng Minh Thảo cùng thảo luận",
            "must_show": [
                "Đại tướng Võ Nguyên Giáp",
                "Trung tướng Hoàng Minh Thảo",
                "Bản đồ tác chiến",
            ],
        }
        fallback_prompt, fallback_refs = _build_refusal_fallback(
            scene,
            "image",
            self.selected_references,
            all_references=self.all_references,
        )
        self.assertNotIn("Võ Nguyên Giáp", fallback_prompt)
        self.assertNotIn("Hoàng Minh Thảo", fallback_prompt)
        self.assertEqual(fallback_refs, [])


if __name__ == "__main__":
    unittest.main()
