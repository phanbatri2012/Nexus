import json
import sys
from pathlib import Path
from unittest.mock import patch

from PIL import Image

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from auto_yt.default_prompts import DEFAULT_PROMPTS_DATA
from auto_yt.services import chatgpt_projects, chatgpt_worker, media_fetcher


def test_extract_queries_from_json_block():
    response = """Dưới đây là từ khóa:
```json
{
  "queries": [
    "Đại tướng Võ Nguyên Giáp 1954",
    "Pháo cao xạ 37mm Điện Biên Phủ"
  ]
}
```
Chúc bạn thành công!"""
    queries = media_fetcher.extract_queries_from_response(response)
    assert len(queries) == 2
    assert "Đại tướng Võ Nguyên Giáp 1954" in queries[0]
    assert "Pháo cao xạ 37mm" in queries[1]


def test_extract_queries_from_raw_json():
    response = '{"queries": ["T-54 Tank 1975", "Independence Palace"]}'
    queries = media_fetcher.extract_queries_from_response(response)
    assert queries == ["T-54 Tank 1975", "Independence Palace"]


def test_extract_queries_from_bullet_list_fallback():
    response = """Dưới đây là các thực thể:
- Đại tướng Võ Nguyên Giáp năm 1954
- Xe tăng T54 số hiệu 390
- Cổng Dinh Độc Lập
"""
    queries = media_fetcher.extract_queries_from_response(response)
    assert len(queries) >= 2
    assert any("Võ Nguyên Giáp" in q for q in queries)
    assert any("Xe tăng" in q for q in queries)


def test_extract_queries_empty_response():
    assert media_fetcher.extract_queries_from_response("") == []
    assert media_fetcher.extract_queries_from_response("   ") == []


def test_download_and_sanitize_image_local_png(tmp_path):
    # Create a dummy RGBA image
    test_img = Image.new("RGBA", (1200, 800), color=(255, 0, 0, 128))
    src_png = tmp_path / "source.png"
    test_img.save(src_png, format="PNG")

    dest_jpg = tmp_path / "output.jpg"
    result_path = media_fetcher.download_and_sanitize_image(str(src_png), dest_jpg, max_dimension=1024)

    assert result_path is not None
    assert dest_jpg.exists()

    with Image.open(dest_jpg) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"
        assert max(img.size) <= 1024


def test_download_and_sanitize_image_rejects_tiny_images(tmp_path):
    # Create a tiny 50x50 image
    tiny_img = Image.new("RGB", (50, 50), color=(0, 255, 0))
    src_file = tmp_path / "tiny.jpg"
    tiny_img.save(src_file)

    dest_file = tmp_path / "out.jpg"
    result = media_fetcher.download_and_sanitize_image(str(src_file), dest_file, min_dimension=300)
    assert result is None
    assert not dest_file.exists()


def test_image_to_base64(tmp_path):
    img = Image.new("RGB", (350, 350), color=(100, 100, 100))
    img_path = tmp_path / "test.jpg"
    img.save(img_path)

    b64 = media_fetcher.image_to_base64(img_path)
    assert b64 is not None
    assert len(b64) > 100

    assert media_fetcher.image_to_base64(tmp_path / "non_existent.jpg") is None


def test_pipeline_normalization_with_visual_research():
    raw_pipeline = {
        "title": True,
        "visual_research": True,
        "thumbnail_with_text": True,
    }
    normalized = chatgpt_projects.normalize_prompt_pipeline(raw_pipeline)
    assert normalized.get("visual_research") is True

    # Default should be False
    default_normalized = chatgpt_projects.normalize_prompt_pipeline({})
    assert default_normalized.get("visual_research") is False


def test_pipeline_validation_accepts_visual_research():
    valid_pipeline = {
        "visual_research": True,
        "audio": True,
    }
    res = chatgpt_projects.validate_prompt_pipeline(valid_pipeline)
    assert res["visual_research"] is True


def test_default_prompts_have_visual_research():
    default_version = DEFAULT_PROMPTS_DATA["versions"]["default"]
    assert "visual_research" in default_version["prompts"]
    assert "visual_research" in default_version["pipeline"]

    monologue_version = DEFAULT_PROMPTS_DATA["versions"]["monologue_default"]
    assert "visual_research" in monologue_version["prompts"]
    assert "visual_research" in monologue_version["pipeline"]


def test_get_video_reference_base64_helper(tmp_path):
    with patch("auto_yt.services.chatgpt_worker.REFERENCES_DIR", tmp_path):
        # When no folder exists
        assert chatgpt_worker._get_video_reference_base64(9999) is None

        # Create video folder with reference images
        vid_dir = tmp_path / "video_9999"
        vid_dir.mkdir(parents=True)
        img1 = Image.new("RGB", (400, 400), color=(255, 0, 0))
        img1.save(vid_dir / "ref_1.jpg")

        b64_json = chatgpt_worker._get_video_reference_base64(9999)
        assert b64_json is not None
        items = json.loads(b64_json)
        assert isinstance(items, list)
        assert len(items) == 1
