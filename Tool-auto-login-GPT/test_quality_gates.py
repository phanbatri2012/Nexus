import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt import main
from auto_yt.services import database, youtube_publisher
from auto_yt.services.metadata_validation import MetadataQualityGate
from auto_yt.services.script_quality import ScriptQualityGate


def _script(body: str, *, title: str = "Bài học về lòng tin") -> str:
    return (
        "### [INTRO]\n"
        "Câu chuyện hôm nay bắt đầu từ một gia đình luôn coi trọng lòng tin.\n\n"
        "### [BODY]\n"
        f"{body}\n\n"
        "### [OUTRO]\n"
        "Bài học cuối cùng nhắc mỗi người sống chân thành và có trách nhiệm.\n\n"
        f"### [TIÊU ĐỀ]\n{title}\n\n"
        "### [SLUG]\nbai-hoc-ve-long-tin\n\n"
        "### [MÔ TẢ]\n"
        "Câu chuyện về lòng tin trong gia đình cho thấy sự chân thành và trách nhiệm "
        "giúp mọi người cùng nhau vượt qua những thời điểm khó khăn.\n\n"
        "### [HASHTAGS]\n#LongTin #GiaDinh\n\n"
        "### [TAGS]\nlòng tin, gia đình, trách nhiệm"
    )


def test_script_gate_auto_fixes_only_high_confidence_local_artifacts():
    body = (
        "Bổ sung đoạn chuyển ý giữa các luận điểm để nhịp kể tự nhiên hơn và giúp "
        "người nghe hiểu rõ mối liên hệ giữa nguyên nhân với kết quả.\n\n"
        "Người cha nhận ra lỗi lầm.? Ông chủ động xin lỗi và cùng gia đình xây dựng "
        "lại niềm tin bằng những hành động cụ thể mỗi ngày."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"
    assert "Bổ sung đoạn chuyển ý" not in result["cleaned_script"]
    assert "lỗi lầm?" in result["cleaned_script"]
    fix_codes = {item["code"] for item in result["fixes"]}
    assert "editorial_artifact_removed" in fix_codes
    assert "mixed_terminal_punctuation" in fix_codes


def test_script_gate_auto_fixes_high_confidence_source_reference():
    body = (
        "Theo số liệu được ghi nhận trong kịch bản, gia đình đã trải qua nhiều năm "
        "xa cách trước khi các thành viên quyết định ngồi lại và lắng nghe nhau."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"
    assert result["can_approve"]
    assert "Theo số liệu, gia đình" in result["cleaned_script"]
    assert "trong kịch bản" not in result["cleaned_script"]
    assert "narration_self_reference_rewritten" in {
        item["code"] for item in result["fixes"]
    }


def test_script_gate_auto_fixes_narration_and_embedded_script_references():
    body = (
        "Theo nội dung được thuật lại trong kịch bản, một thông điệp gửi tới đối phương nêu rõ yêu cầu. "
        "Một lời kể khác được nhắc đến trong kịch bản cho rằng chỉ huy từng liên lạc với gia đình."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"
    assert result["can_approve"]
    assert "Theo nội dung được thuật lại, một thông điệp" in result["cleaned_script"]
    assert "Một lời kể khác cho rằng chỉ huy" in result["cleaned_script"]
    assert "trong kịch bản" not in result["cleaned_script"]
    assert "narration_self_reference_rewritten" in {
        item["code"] for item in result["fixes"]
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "Như đã đề cập ở phần trước, gia đình lựa chọn đối thoại để hóa giải hiểu lầm.",
            "Trước đó, gia đình lựa chọn đối thoại",
        ),
        (
            "Theo yêu cầu, câu chuyện tập trung vào cách gia đình xây dựng lại lòng tin.",
            "Câu chuyện tập trung vào cách gia đình",
        ),
        (
            "Dữ liệu được cung cấp cho thấy các thành viên đã giữ lời hứa với nhau.",
            "Dữ liệu hiện có cho thấy các thành viên",
        ),
        (
            "Prompt yêu cầu bổ sung một câu chuyển ý. Gia đình sau đó cùng nhau sửa chữa sai lầm.",
            "Gia đình sau đó cùng nhau sửa chữa sai lầm.",
        ),
    ],
)
def test_script_gate_auto_fixes_common_process_references(body, expected):
    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"
    assert expected in result["cleaned_script"]
    assert "narration_self_reference_rewritten" in {
        item["code"] for item in result["fixes"]
    }


def test_script_gate_keeps_ambiguous_self_reference_for_review():
    body = (
        "Trong kịch bản này, nhân vật người cha đại diện cho trách nhiệm và lòng tin "
        "mà gia đình đã đánh mất sau nhiều năm xa cách."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "review"
    assert "narration_self_reference" in {
        item["code"] for item in result["review_required"]
    }
    assert body in result["cleaned_script"]


def test_script_gate_does_not_flag_legitimate_scriptwriting_topic():
    body = (
        "Bỏ lời cha dặn ngoài tai, người con vội vàng đưa ra quyết định khiến cả "
        "gia đình phải đối mặt với hậu quả. Nghề biên kịch đòi hỏi khả năng quan "
        "sát con người và chuyển trải nghiệm đời sống thành câu chuyện có ý nghĩa."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"


def test_script_gate_does_not_flag_legitimate_conversational_requirement():
    body = (
        "Chú nghĩ lúc đó cháu đã hiểu rằng họ không còn muốn nghe lời giải thích, "
        "mà chỉ muốn cháu làm theo yêu cầu. Và điều khiến chú đau lòng nhất là "
        "người trực tiếp gây ra nỗi sợ ấy lại chính là chồng cháu."
    )

    result = ScriptQualityGate().run(_script(body))

    assert result["decision"] == "pass"
    assert result["can_approve"]
    assert "narration_self_reference" not in {
        item["code"] for item in result["review_required"]
    }


@pytest.mark.parametrize("status", ["pending", "blocked"])
def test_unapproved_audio_review_is_not_reported_as_tts_failure(status):
    review = {
        "video_id": 42,
        "status": status,
        "script_hash": "script-hash",
        "report": {
            "decision": "review" if status == "pending" else "block",
            "can_approve": False,
            "can_manual_approve": status == "pending",
        },
    }

    with (
        patch.object(
            main,
            "_automatically_approve_audio_review",
            return_value=review,
        ),
        patch.object(main, "_ensure_audio_task") as ensure_audio_task,
    ):
        result_review, task, error = main._auto_review_and_create_audio(42)

    assert result_review == review
    assert task is None
    assert error == ""
    ensure_audio_task.assert_not_called()




def test_script_gate_blocks_duplicate_sections_and_is_idempotent():
    original = _script(
        "Mọi thành viên lựa chọn đối thoại thẳng thắn để giải quyết hiểu lầm."
    )
    duplicated = original.replace(
        "### [OUTRO]",
        "### [BODY]\nMột phần thân bài bị tạo thêm ngoài cấu trúc.\n\n### [OUTRO]",
    )
    gate = ScriptQualityGate()

    blocked = gate.run(duplicated)
    once = gate.run(original)["cleaned_script"]
    twice = gate.run(once)["cleaned_script"]

    assert blocked["decision"] == "block"
    assert "duplicate_section" in {item["code"] for item in blocked["errors"]}
    assert once == twice


def test_metadata_gate_normalizes_duplicates_and_binds_to_script_hash():
    script = _script(
        "Các thành viên giữ lời hứa, chia sẻ trách nhiệm và từng bước lấy lại lòng tin."
    )
    metadata = {
        "title": "  Bài học về lòng tin  ",
        "slug": "bai-hoc-ve-long-tin",
        "description": (
            "Câu chuyện về lòng tin trong gia đình cho thấy sự chân thành và trách "
            "nhiệm giúp mọi người cùng nhau vượt qua những thời điểm khó khăn."
        ),
        "hashtags": "#LongTin #longtin #GiaDinh",
        "tags": ["lòng tin", "LÒNG TIN", "gia đình"],
    }

    first = MetadataQualityGate().run(metadata, script_text=script, require_slug=True)
    changed_script = script.replace("giữ lời hứa", "thực hiện lời hứa")
    second = MetadataQualityGate().run(
        metadata,
        script_text=changed_script,
        require_slug=True,
    )

    assert first["decision"] == "pass"
    assert first["cleaned_metadata"]["tags"] == ["lòng tin", "gia đình"]
    assert first["cleaned_metadata"]["hashtags"] == "#LongTin #GiaDinh"
    assert first["metadata_hash"] == second["metadata_hash"]
    assert first["script_hash"] != second["script_hash"]


def test_metadata_gate_blocks_placeholders_and_reviews_content_mismatch():
    script = _script(
        "Các thành viên giữ lời hứa, chia sẻ trách nhiệm và từng bước lấy lại lòng tin."
    )
    blocked = MetadataQualityGate().run(
        {
            "title": "{{TITLE_PLACEHOLDER}}",
            "description": "TODO: điền phần mô tả đầy đủ trước khi xuất bản video này.",
        },
        script_text=script,
    )
    mismatched = MetadataQualityGate().run(
        {
            "title": "Khám phá thiên hà xa xôi ngoài hệ Mặt Trời",
            "description": (
                "Hành trình khám phá thiên hà, kính viễn vọng và những hành tinh xa "
                "xôi mang đến các phát hiện khoa học đáng kinh ngạc cho nhân loại."
            ),
        },
        script_text=script,
    )

    assert blocked["decision"] == "block"
    assert "unresolved_placeholder" in {
        item["code"] for item in blocked["errors"]
    }
    assert mismatched["decision"] == "review"
    assert "title_content_mismatch" in {
        item["code"] for item in mismatched["review_required"]
    }


def test_metadata_review_is_invalidated_when_metadata_changes():
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.object(database, "DB_PATH", Path(temp_dir) / "database.db"):
            database.init_db()
            script = _script(
                "Các thành viên giữ lời hứa, chia sẻ trách nhiệm và từng bước lấy lại lòng tin."
            )
            video_id = database.save_video(
                "https://youtube.test/quality-gate",
                "Nguồn kiểm thử",
                "Transcript",
                script,
            )
            approved = main._automatically_approve_metadata_review(video_id)

            changed_script = script.replace(
                "Bài học về lòng tin",
                "Giá trị của lòng tin",
            )
            database.update_script(video_id, changed_script)
            refreshed = main._prepare_metadata_review(video_id)

            assert approved["status"] == "approved"
            assert refreshed["status"] == "pending"
            assert approved["metadata_hash"] != refreshed["metadata_hash"]


def test_high_confidence_script_fix_flows_through_metadata_automatically():
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.object(database, "DB_PATH", Path(temp_dir) / "database.db"):
            database.init_db()
            video_id = database.save_video(
                "https://youtube.test/automatic-quality-gates",
                "Nguồn kiểm thử",
                "Transcript",
                _script(
                    "Theo một số thông tin được đề cập trong kịch bản gốc, gia đình "
                    "đã chủ động đối thoại và từng bước xây dựng lại lòng tin."
                ),
            )

            metadata_review = main._automatically_approve_metadata_review(video_id)
            audio_review = database.get_audio_review(video_id)
            updated_video = database.get_video(video_id)

            assert audio_review["status"] == "approved"
            assert metadata_review["status"] == "approved"
            assert "trong kịch bản gốc" not in updated_video["generated_script"]
            assert "Theo một số thông tin, gia đình" in updated_video["generated_script"]
            assert "narration_self_reference_rewritten" in {
                item["code"] for item in audio_review["report"]["fixes"]
            }


def test_upload_blocks_unapproved_review_and_accepts_matching_manual_approval():
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch.object(database, "DB_PATH", Path(temp_dir) / "database.db"):
            database.init_db()
            script = _script(
                "Các thành viên giữ lời hứa, chia sẻ trách nhiệm và từng bước lấy lại lòng tin.",
                title="Khám phá thiên hà xa xôi ngoài hệ Mặt Trời",
            )
            video_id = database.save_video(
                "https://youtube.test/metadata-upload-gate",
                "Nguồn kiểm thử",
                "Transcript",
                script,
            )
            video = database.get_video(video_id)
            settings = {"description_template": "{description}", "made_for_kids": False}

            with pytest.raises(youtube_publisher.YouTubePublishError, match="quality gate"):
                youtube_publisher.build_upload_metadata(video, settings)

            review = main._prepare_metadata_review(video_id)
            database.upsert_metadata_review(
                video_id=video_id,
                script_hash=review["script_hash"],
                metadata_hash=review["metadata_hash"],
                status="approved",
                report=review["report"],
                reviewed_at=database.utc_now(),
            )

            metadata = youtube_publisher.build_upload_metadata(video, settings)

            assert metadata["snippet"]["title"] == (
                "Khám phá thiên hà xa xôi ngoài hệ Mặt Trời"
            )
