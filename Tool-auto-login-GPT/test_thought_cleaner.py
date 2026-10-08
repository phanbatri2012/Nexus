import re
import sys
sys.path.insert(0, 'src')
sys.stdout.reconfigure(encoding='utf-8')

from auto_yt.services.chatgpt_worker import (
    THINKING_INDICATOR_PATTERN,
    _is_pure_thinking_indicator,
    split_outline_parts,
)

def test_thinking_indicators():
    indicators = [
        "Worked for 2m 21s",
        "worked for 15s",
        "worked for 1 minute 30 seconds",
        "thought for 10 seconds",
        "Thinking...",
        "thinking",
        "đang suy nghĩ...",
        "đã dừng suy nghĩ",
        "đã suy nghĩ trong 2 phút 21 giây",
        "stopped thinking",
    ]
    for ind in indicators:
        assert _is_pure_thinking_indicator(ind), f"Failed to detect thinking indicator: {ind}"
        print(f"[PASSED] Detected thinking indicator: {ind}")

    non_indicators = [
        "[PHAN]\nThái Lan và Việt Nam không có chung đường biên giới...",
        "Phần 1: Bối cảnh chiến trường Cánh Đồng Chum",
        "Tôi sẽ kể câu chuyện này...",
    ]
    for non in non_indicators:
        assert not _is_pure_thinking_indicator(non), f"False positive thinking indicator: {non}"
        print(f"[PASSED] Correctly allowed real content: {non[:40]}...")

def test_video_235_real_outline_parsing():
    # Real outline format generated in Video 235 as captured in screenshot
    real_chatgpt_response = """
[PHAN]
Thái Lan và Việt Nam không có chung đường biên giới, nhưng trong lịch sử từng nhiều lần đối đầu hoặc đứng ở những phía khác nhau trong các cuộc xung đột tại bán đảo Đông Dương. Một trong những cuộc đụng độ đáng chú ý nhất ở thế kỷ hai mươi diễn ra tại Cánh Đồng Chum – Xiêng Khoảng của Lào trong giai đoạn 1971–1972, nơi các đơn vị Thái Lan tham chiến cùng lực lượng Chính phủ Hoàng gia Lào và quân đặc biệt Vàng Pao được Mỹ hậu thuẫn, đối đầu với quân tình nguyện Việt Nam và lực lượng cách mạng Lào.

Đầu thập niên 1970, chiến sự trên bán đảo Đông Dương ngày càng khốc liệt. Trước đó, cuộc hành quân Lam Sơn 719 năm 1971 của Quân lực Việt Nam Cộng hòa sang khu vực Đường 9 – Nam Lào...

Tháng 7 năm 1971, lợi dụng mùa mưa, lực lượng Chính phủ Hoàng gia Lào, quân đặc biệt Vàng Pao và các đơn vị Thái Lan được Mỹ hậu thuẫn mở cuộc lấn chiếm Cánh Đồng Chum...

[PHAN]
Để giành lại Cánh Đồng Chum trong mùa khô 1971–1972, liên quân Việt – Lào mở Chiến dịch Cánh Đồng Chum – Mường Sủi, còn được gọi là Chiến dịch Z, từ ngày 18 tháng 12 năm 1971 đến ngày 6 tháng 4 năm 1972. Quân tình nguyện Việt Nam có hai sư đoàn bộ binh 312 và 316...

[PHAN]
Bước sang mùa mưa năm 1972, từ tháng 5 đến tháng 11 năm 1972, chiến trường Cánh Đồng Chum – Xiêng Khoảng bước vào giai đoạn phòng ngự ác liệt khi đối phương mở đợt tiến công lớn...

[PHAN]
Chiến thắng tại Cánh Đồng Chum – Xiêng Khoảng trong cả hai giai đoạn tiến công và phòng ngự năm 1971–1972 đã đánh bại một bước thử nghiệm quan trọng của chiến lược “Đông Dương hóa chiến tranh”...
"""
    parts = split_outline_parts(real_chatgpt_response)
    print(f"\n[PASSED] Real Video 235 outline parsed into {len(parts)} parts (expected 4)")
    assert len(parts) == 4, f"Expected 4 parts, got {len(parts)}"
    for idx, p in enumerate(parts, 1):
        print(f"  Part {idx} length: {len(p)} chars -> {p[:70]}...")

if __name__ == "__main__":
    print("=== TESTING THINKING INDICATORS & OUTLINE EXTRACTION ===")
    test_thinking_indicators()
    test_video_235_real_outline_parsing()
    print("\n>>> ALL TESTS PASSED (100%) <<<")
