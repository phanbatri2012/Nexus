import sys
sys.path.insert(0, 'src')
sys.stdout.reconfigure(encoding='utf-8')

from auto_yt.services.chatgpt_worker import split_outline_parts, strip_outline_preamble

def run_tests():
    test_cases = [
        (
            "1. Classic [PHAN] tags",
            """[PHAN]
            Phần 1: Bối cảnh Cánh Đồng Chum năm 1971.
            [PHAN]
            Phần 2: Diễn biến mở màn giờ G và các trung đoàn 174, 165.
            [PHAN]
            Phần 3: Tướng Lê Trọng Tấn phán đoán hướng rút phía Nam.
            [PHAN]
            Phần 4: Thất bại của quân Thái - Mỹ và ý nghĩa lịch sử.""",
            4
        ),
        (
            "2. Bracketed numbered tags [PHẦN 1], [PHẦN 2]",
            """Dưới đây là dàn ý chi tiết:
            [PHẦN 1]
            Mở đầu chiến dịch: Bối cảnh sau thất bại Lam Sơn 719, Mỹ đưa quân Thái Lan và Hoàng gia Lào vào Cánh Đồng Chum.
            [PHẦN 2]
            Diễn biến các đợt tiến công: Hỏa lực pháo lớn nổ súng, xe tăng tràn lên cùng bộ binh.
            [PHẦN 3]
            Quyết định lịch sử của tướng Lê Trọng Tấn: Phán đoán chính xác đường rút chạy phía Nam.
            [PHẦN 4]
            Kết quả: 244 trận đánh, 5600 tên bị loại khỏi vòng chiến, Nixon buộc phải ngừng bắn.""",
            4
        ),
        (
            "3. Bracketed tags with titles [PHẦN 1: Bối cảnh]",
            """[PHẦN 1: Bối cảnh lịch sử]
            Thái Lan và Việt Nam trong quá khứ từng đụng độ nhiều lần.
            [PHẦN 2: Tướng Lê Trọng Tấn ra quân]
            Thiếu tướng trực tiếp chỉ huy chiến dịch Cánh Đồng Chum.
            [PHẦN 3: Đòn hiểm phía Nam]
            Tập trung pháo binh tiêu diệt tàn quân đối phương.
            [PHẦN 4: Thắng lợi toàn diện]
            Tổng thống Mỹ Richard Nixon công khai đề nghị ngừng bắn.""",
            4
        ),
        (
            "4. Markdown headers ### PHẦN 1, ### PHẦN 2",
            """### PHẦN 1: Bối cảnh sau Lam Sơn 719
            Mỹ thử nghiệm Việt Nam hóa chiến tranh nhưng thất bại.
            ### PHẦN 2: Diễn biến mở màn
            Đúng giờ G, pháo lớn h-72, cối 82, 120 ly đồng loạt gầm vang.
            ### PHẦN 3: Trận đánh đồi Phú Tôn
            Trung đoàn 165 và 141 tiêu diệt chỉ huy đối phương.
            ### PHẦN 4: Quyết định táo bạo
            Tướng Tấn ra lệnh dồn hỏa lực vào con đường phía Nam.
            ### PHẦN 5: Kết cục chiến dịch
            Ba đời sau Thái Lan còn sợ.""",
            5
        ),
        (
            "5. Numbered list 1. ... 2. ... 3. ...",
            """Chào bạn, đây là dàn ý chia theo các phần:
            1. Bối cảnh chiến lược và tương quan lực lượng tại Cánh Đồng Chum
            2. Các mũi tiến công của Trung đoàn 174, 165, 141
            3. Cuộc đấu trí cân não và phán đoán xuất thần của tướng Lê Trọng Tấn
            4. Thất bại thảm hại của quân Thái Lan và kết quả chiến dịch""",
            4
        ),
        (
            "6. Preamble with chatter before parts",
            """Tôi xin gửi bạn dàn ý chi tiết đã chia thành các phần hợp lý để viết kịch bản:
            
            [PHẦN 1]
            Bối cảnh 1971
            [PHẦN 2]
            Diễn biến chiến sự
            [PHẦN 3]
            Kết quả""",
            3
        ),
    ]

    all_passed = True
    print("=== RUNNING COMPREHENSIVE OUTLINE PARSER TESTS ===\n")
    for name, text, expected_count in test_cases:
        parts = split_outline_parts(text)
        status = "PASSED" if len(parts) == expected_count else "FAILED"
        if status == "FAILED":
            all_passed = False
        print(f"[{status}] {name}: got {len(parts)} parts (expected {expected_count})")
        for i, p in enumerate(parts):
            print(f"    Part {i+1}: {p[:70]}...")
        print()

    # Test filtering logic simulation
    print("=== TESTING NON-DESTRUCTIVE FILTER LOGIC ===\n")
    sample_outline_with_historical_intro = """[PHẦN 1]
    Mở đầu chiến dịch: Bối cảnh sau thất bại Lam Sơn 719, Mỹ đưa quân Thái Lan và Hoàng gia Lào vào Cánh Đồng Chum.
    [PHẦN 2]
    Diễn biến các đợt tiến công: Hỏa lực pháo lớn nổ súng, xe tăng tràn lên cùng bộ binh.
    [PHẦN 3]
    Quyết định lịch sử của tướng Lê Trọng Tấn: Phán đoán chính xác đường rút chạy phía Nam.
    [PHẦN 4]
    Kết luận chiến dịch: 244 trận đánh, 5600 tên bị loại khỏi vòng chiến, Nixon buộc phải ngừng bắn."""

    parts = split_outline_parts(sample_outline_with_historical_intro)
    filtered = []
    removed = 0
    for part in parts:
        lower_header = part.lower()[:80].strip()
        words = part.split()
        is_pure_meta_intro = (
            len(words) < 15
            and any(lower_header.startswith(kw) for kw in ("intro:", "mở bài:", "lời chào:", "chào mừng"))
        )
        is_pure_meta_outro = (
            len(words) < 15
            and any(lower_header.startswith(kw) for kw in ("outro:", "kết bài:", "kêu gọi:", "like và subscribe"))
        )
        if len(parts) > 2 and (is_pure_meta_intro or is_pure_meta_outro):
            removed += 1
            continue
        filtered.append(part)

    print(f"Historical intro/outro test: got {len(filtered)} parts (expected 4, removed {removed} boilerplate labels)")
    if len(filtered) != 4:
        all_passed = False
        print("FAILED: Substantive historical sections were incorrectly removed!")
    else:
        print("PASSED: Substantive historical sections were correctly preserved!")

    if all_passed:
        print("\n>>> ALL TESTS PASSED SUCCESSFULLY (100%) <<<")
    else:
        print("\n>>> SOME TESTS FAILED <<<")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
