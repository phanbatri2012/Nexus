"""Unit and integration tests for visual research and reference image pipeline."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from auto_yt.services.media_fetcher import (
    clean_query_to_core_entity,
    extract_queries_from_response,
    fetch_reference_images_for_queries,
    search_bing_images,
    search_wikipedia_pageimages,
    search_wikimedia_commons,
)
from auto_yt.services.chatgpt_worker import (
    adapt_thumbnail_prompt_for_references,
)


def test_clean_query_to_core_entity_strips_fillers_and_titles():
    # Case 1: The exact problematic query from user's screenshot
    q1 = "Đại tướng Võ Nguyên Giáp chiến dịch Điện Biên Phủ 1954 chân dung ảnh tư liệu lịch sử"
    candidates1 = clean_query_to_core_entity(q1)
    assert len(candidates1) >= 2
    assert "Võ Nguyên Giáp" in candidates1[0]
    assert "chân dung ảnh tư liệu lịch sử" not in candidates1[0]
    assert "Đại tướng" not in candidates1[0]

    # Case 2: Second query from screenshot
    q2 = "Tướng Henri Navarre chiến dịch Điện Biên Phủ 1954 chân dung ảnh tư liệu lịch sử"
    candidates2 = clean_query_to_core_entity(q2)
    assert len(candidates2) >= 2
    assert "Henri Navarre" in candidates2[0]
    assert "Tướng" not in candidates2[0]

    # Case 3: English / military equipment
    q3 = "Xe tăng T-34 chiến dịch năm 1945 ảnh tư liệu"
    candidates3 = clean_query_to_core_entity(q3)
    assert any("T-34" in c for c in candidates3)


def test_extract_queries_from_response_json():
    sample_json = """{
  "queries": [
    "Võ Nguyên Giáp",
    "Henri Navarre"
  ]
}"""
    queries = extract_queries_from_response(sample_json)
    assert queries == ["Võ Nguyên Giáp", "Henri Navarre"]


def test_search_wikipedia_pageimages():
    results = search_wikipedia_pageimages("Võ Nguyên Giáp", limit=1)
    assert len(results) > 0
    assert results[0]["url"].startswith("http")
    assert "wikipedia" in results[0]["source"]


def test_search_bing_images():
    results = search_bing_images("Henri Navarre", limit=2)
    assert len(results) > 0
    assert results[0]["url"].startswith("http")
    assert results[0]["source"] == "bing"


def test_search_wikimedia_commons_clean_query():
    results = search_wikimedia_commons("Henri Navarre", limit=1)
    assert len(results) > 0
    assert results[0]["url"].startswith("http")


def test_fetch_reference_images_for_queries_end_to_end(tmp_path: Path):
    queries = [
        "Đại tướng Võ Nguyên Giáp chiến dịch Điện Biên Phủ 1954 chân dung ảnh tư liệu lịch sử",
        "Tướng Henri Navarre chiến dịch Điện Biên Phủ 1954 chân dung ảnh tư liệu lịch sử"
    ]
    downloaded = fetch_reference_images_for_queries(queries, output_dir=tmp_path, max_images=2)
    assert len(downloaded) == 2
    for img_path in downloaded:
        p = Path(img_path)
        assert p.exists()
        assert p.stat().st_size > 1024


def test_adapt_thumbnail_prompt_for_references():
    prompt = (
        "Bạn là giám đốc nghệ thuật. Dựa trên nội dung câu chuyện ở trên và CÁC HÌNH ẢNH TƯ LIỆU LỊCH SỬ VỪA ĐƯỢC ĐÍNH KÈM, "
        "hãy thực hiện 2 bước sau..."
    )
    # When references are present, phrasing is preserved
    with_refs = adapt_thumbnail_prompt_for_references(prompt, has_references=True)
    assert "CÁC HÌNH ẢNH TƯ LIỆU LỊCH SỬ VỪA ĐƯỢC ĐÍNH KÈM" in with_refs

    # When references are missing, phrasing is gracefully removed
    without_refs = adapt_thumbnail_prompt_for_references(prompt, has_references=False)
    assert "CÁC HÌNH ẢNH TƯ LIỆU LỊCH SỬ VỪA ĐƯỢC ĐÍNH KÈM" not in without_refs
    assert "Bạn là giám đốc nghệ thuật. Dựa trên nội dung câu chuyện ở trên hãy thực hiện 2 bước sau..." in without_refs or "hãy thực hiện 2 bước sau" in without_refs
