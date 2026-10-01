# src/auto_yt/default_prompts.py

DEFAULT_PROMPTS_DATA = {
    "active_version": "default",
    "versions": {
        "default": {
            "name": "Talkshow Tâm Lý Đinh Đoàn (MC & Khách Mời)",
            "content_mode": "dialogue",
            "project_url": "https://chatgpt.com/g/g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project",
            "default_voice_id": "",
            "default_youtube_channel_id": "",
            "cast_settings": {
                "mc": {
                    "role_tag": "[MC]",
                    "display_name": "Tiến sĩ Đinh Đoàn",
                    "default_voice_id": "",
                    "subtitle_color": "#FFD700",
                    "persona": "Chuyên gia tâm lý Đinh Đoàn, người dẫn dắt thông thái, lắng nghe, phân tích tâm lý, chia sẻ và đúc kết bài học."
                },
                "guest_1": {
                    "role_tag": "[KHACH_1]",
                    "display_name": "Khách Mời Chính",
                    "default_voice_id": "",
                    "subtitle_color": "#00E5FF",
                    "persona": "Người trong cuộc kể lại câu chuyện tâm sự chi tiết, có thể chia sẻ một mạch câu chuyện dài đầy đủ cảm xúc."
                },
                "guest_2": {
                    "enabled": False,
                    "role_tag": "[KHACH_2]",
                    "display_name": "Khách Mời 2",
                    "default_voice_id": "",
                    "subtitle_color": "#FF80AB",
                    "persona": "Chuyên gia bổ sung hoặc nhân vật thứ ba trong câu chuyện."
                },
                "turn_pause_seconds": 0.35
            },
            "pipeline": {
                "title": True,
                "slug": True,
                "description": True,
                "hashtags": True,
                "tags": True,
                "pinned_comment": True,
                "quiz": True,
                "chapters": True,
                "thumbnail_with_text": True,
                "thumbnail_without_text": True,
                "audio": True,
                "video_render": False,
                "youtube_upload": False,
                "youtube_schedule": False,
            },
            "image_generation_settings": {
                "provider": "google_flow",
                "model": "nano_banana_pro",
                "aspect_ratio": "16:9",
                "output_count": 1,
                "video_model": "veo_3_1_lite",
                "video_aspect_ratio": "16:9",
                "video_output_count": 1,
                "workflow_profile_id": "",
                "style_prompt": "Cinematic documentary film still, 35mm photography, atmospheric natural lighting, realistic textures, cinematic composition, shallow depth of field, balanced color grading, high visual fidelity, 8k raw photo.",
                "negative_prompt": "cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.",
                "seed_mode": "random",
                "thumbnail_variant": "with_text",
                "scene_0_source": "from_thumbnail_without_text",
                "enable_intro_video": True,
                "intro_scene_target_seconds": 8.0,
                "intro_crop_watermark": True,
                "video_style_prompt": "Cinematic documentary film, 35mm motion picture composition, natural atmospheric lighting, realistic textures, balanced color grading, 4k cinematic video footage.",
                "video_negative_prompt": "still image, static photo, cartoon, anime, 3D CGI render, illustration, deformed hands, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.",
                "video_prompt_template": "{frame_directive} Scene action: {action}. Visual style: {style}. {motion} Clean video without any text, letters, watermark, or subtitles.",
                "video_motion_prompt": "Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video.",
                "scene_0_prompt_template": "A cinematic movie still: {style}, opening scene hook. {reference} Story visual core: {thumbnail_concept}. 16:9 widescreen, photorealistic 8k, authentic documentary realism, clean framing without text.",
                "scene_body_prompt_template": "A still photograph: {style}, scene {scene_index}. {reference} Narrative scene: {action}. 16:9 widescreen still photograph, authentic documentary realism, natural lighting, clean visual without text.",
                "scene_duration_min_seconds": 25,
                "scene_duration_target_seconds": 30,
                "scene_duration_max_seconds": 35,
            },
            "publishing_settings": {
                "upload_method": "browser",
                "category_id": "",
                "language": "vi",
                "made_for_kids": None,
                "notify_subscribers": True,
                "include_tags": True,
                "default_tags": "",
                "contains_synthetic_media": True,
                "monetization_mode": "auto_enable_if_available",
                "midroll_ads": True,
                "ad_suitability_mode": "none_of_the_above",
                "playlist_name": "",
                "age_restriction": False,
                "paid_promotion": False,
                "automatic_chapters": True,
                "automatic_places": True,
                "automatic_concepts": True,
                "title_description_language": "vi",
                "caption_certification": "none",
                "license": "youtube",
                "allow_embedding": True,
                "remix_policy": "video_and_audio",
                "comments_enabled": True,
                "comment_moderation": "basic",
                "comment_access": "anyone",
                "comment_sort": "top",
                "show_ratings": True,
                "upload_captions": True,
                "end_screen_source_video_id": "",
                "premiere": False,
                "checks_policy": "schedule_immediately",
                "description_template": "{description}\n\n{chapters}\n\n{hashtags}",
            },
            "prompts": {
                "outline": "Bạn là biên tập viên nội dung cao cấp cho chương trình Podcast / Talkshow \"Thấu Hiểu Tâm Lý & Hôn Nhân\" do Tiến sĩ Đinh Đoàn chủ trì.\nDưới đây là một câu chuyện / kịch bản dài đã có sẵn.\n\nNhiệm vụ của bạn: Hãy phân tích toàn bộ nội dung và chia kịch bản này thành các phần lớn hợp lý theo dòng chảy của một buổi trò chuyện tâm sự và tư vấn tâm lý:\n- Phần 1: Mở đầu - Giới thiệu hoàn cảnh, nhân vật khách mời và nút thắt mâu thuẫn chính.\n- Các phần tiếp theo: Diễn biến câu chuyện - Từng chặng thời gian hoặc biến cố mà khách mời đã trải qua (cho phép đi sâu vào chi tiết, cảm xúc chân thực của người trong cuộc).\n- Phần cuối: Cao trào, bài học rút ra và những lời khuyên tâm lý sâu sắc từ chuyên gia.\n\nLưu ý CỰC KỲ QUAN TRỌNG:\n- Khi lập dàn ý, CHỈ LẤY phần nội dung câu chuyện bám sát theo đúng TIÊU ĐỀ KỊCH BẢN. Tuyệt đối bỏ qua tất cả các nội dung khác (quảng cáo, chào hỏi ngoài lề của video nguồn).\n- Không tách nhỏ vụn vặt, chỉ gộp thành các phần lớn. Mỗi phần trình bày dưới dạng gạch đầu dòng các ý chính, bao quát đủ tình tiết quan trọng, tên riêng, mốc thời gian, số liệu như bản gốc, không cắt bỏ ý quan trọng, không viết tắt.\n- Định dạng mỗi phần bằng chuỗi [PHAN] ở đầu dòng, ví dụ:\n[PHAN]\nNội dung tóm tắt phần 1...\n[PHAN]\nNội dung tóm tắt phần 2...\n\nDưới đây là kịch bản (Bao gồm Tiêu đề và Nội dung):\n{transcript}",

                "intro": "Bạn là biên tập viên kịch bản cho chương trình Talkshow \"Thấu Hiểu Tâm Lý\". Dựa trên toàn bộ nội dung câu chuyện, hãy viết một đoạn mở đầu thật cuốn hút, xúc động và kịch tính giữa Tiến sĩ Đinh Đoàn [MC] và người gửi tâm sự [KHACH_1].\n\nYêu cầu định dạng & cấu trúc:\n1. Bắt đầu bằng [MC]: \"Xin chào quý vị khán giả đang theo dõi kênh Đinh Đoàn Phân Tích, tôi là tiến sĩ, chuyên gia tâm lý Đinh Đoàn. Thưa quý vị, trong cuộc sống hôn nhân và gia đình, có những nỗi niềm khó nói thành lời... Và hôm nay, trường quay của chúng ta cùng lắng nghe câu chuyện đầy trăn trở từ một người trong cuộc...\"\n2. Tiếp theo là lượt nói ngắn gọn của [KHACH_1]: Lời chào gửi đến Tiến sĩ Đinh Đoàn và khán giả (xưng hô tự nhiên như \"em chào tiến sĩ\", \"cháu chào bác\", xưng tên của mình nếu có trong câu chuyện), bộc bạch ngắn về tâm trạng bế tắc/trăn trở trước khi bắt đầu kể.\n3. QUY TẮC PHÂN VAI & XƯNG HÔ BẮT BUỘC:\n- Thẻ [MC]: hoặc [KHACH_1]: CHỈ ĐƯỢC ĐẶT Ở ĐẦU DÒNG của mỗi lượt nói để phân biệt vai.\n- TUYỆT ĐỐI KHÔNG lặp lại hoặc chèn thẻ vai như [KHACH_1], [MC] vào bên trong nội dung câu nói.\n- Khi xưng hô hoặc nhắc đến nhau trong lời thoại, PHẢI dùng TÊN RIÊNG của nhân vật lấy từ câu chuyện (ví dụ: anh Nam, chị Lan, chị Hoa...) hoặc các danh xưng tự nhiên (\"tôi\", \"em\", \"cháu\", \"anh\", \"thưa tiến sĩ\"). Tuyệt đối không gọi là \"[KHACH_1]\" hay \"Khách 1\".\n4. Lời văn tự nhiên, mượt mà, đầy đủ dấu câu, không dùng dấu gạch ngang \"-\", không viết hoa chữ \"AI\", không viết tắt, dễ đọc thành lời. Đoạn mở đầu cần tạo ấn tượng mạnh ngay từ những giây đầu để giữ chân người nghe.",

                "body": "Bạn là biên tập viên kịch bản cho chương trình Talkshow \"Thấu hiểu Tâm Lý & Hôn Nhân\". Dưới đây là phần dàn ý cần viết lại ở lượt này:\n{part}\n\nNhiệm vụ của bạn: Hãy viết lại phần này thành một đoạn đối thoại sống động, sâu sắc giữa Tiến sĩ Đinh Đoàn [MC] và Khách mời [KHACH_1]:\n- [KHACH_1] (Người trong cuộc): Kể lại chi tiết những gì mình đã trải qua. [KHACH_1] hoàn toàn có thể kể một mạch câu chuyện dài, chân thực, bộc lộ trọn vẹn cảm xúc, sự phân vân hay đau khổ trong từng tình huống mà không bị ép ngắt câu quá ngắn.\n- [MC] (Tiến sĩ Đinh Đoàn): Lắng nghe, thỉnh thoảng có những lời chia sẻ, đồng cảm, đặt câu hỏi làm rõ chi tiết hoặc đưa ra những góc nhìn phân tích tâm lý sâu sắc giúp người nghe hiểu rõ bản chất vấn đề.\n\nQuy chuẩn định dạng BẮT BUỘC:\n1. Mọi lượt nói PHẢI bắt đầu bằng thẻ [MC]: hoặc [KHACH_1]: ở đầu dòng (Ví dụ: [MC]: Chào bạn... hoặc [KHACH_1]: Thưa tiến sĩ...).\n2. TUYỆT ĐỐI KHÔNG chèn bất kỳ thẻ kỹ thuật nào như [KHACH_1], [MC], [KHACH_2] vào giữa câu văn. Khi nhắc đến nhân vật, PHẢI dùng TÊN THẬT trong câu chuyện (anh Nam, chị Mai...) hoặc xưng hô tự nhiên.\n3. Trong mỗi lượt nói của một người, viết thành một đoạn văn liền mạch, không tự ý xuống dòng lưng chừng.\n4. Không sử dụng các dấu gạch ngang \"—\" hoặc \"–\". Sử dụng dấu câu chuẩn xác (dấu chấm, phẩy, hỏi chấm, cảm thán) để đảm bảo ngữ điệu đọc truyền cảm nhất.\n5. Tuyệt đối không viết tắt, không lặp lại nội dung của phần trước, không viết lấn sang nội dung phần sau.",

                "outro": "Hãy viết phần kết thúc (Outro) trọn vẹn và ý nghĩa cho buổi talkshow giữa [MC] và [KHACH_1]:\n\nYêu cầu nội dung:\n1. [MC]: Đưa ra lời đúc kết tâm lý sâu sắc, lời khuyên chân thành và giải pháp hướng thiện cho nhân vật (gọi tên riêng của nhân vật) cũng như bài học quý giá dành cho khán giả đang theo dõi.\n2. [KHACH_1]: Lời cảm ơn chân thành gửi đến Tiến sĩ Đinh Đoàn và chương trình, chia sẻ cảm giác nhẹ nhõm hoặc phương hướng mới sau khi được giãi bày.\n3. [MC]: Lời cảm ơn khán giả, kêu gọi like, chia sẻ video, đăng ký kênh, để lại bình luận góp ý và nhấn nút \"Tham gia\" hội viên.\n\nQuy chuẩn:\n- Mỗi lượt thoại bắt đầu bằng đúng thẻ [MC]: hoặc [KHACH_1]: ở đầu dòng.\n- Tuyệt đối không để thẻ vai lọt vào bên trong câu văn. Dùng tên riêng nhân vật hoặc xưng hô tự nhiên.\n- Lời văn ấm áp, lắng đọng, truyền cảm hứng tích cực. Không dùng dấu gạch ngang \"-\", không viết tắt.",

                "title": "Dựa vào toàn bộ nội dung buổi talkshow vừa viết, hãy viết tiêu đề video YouTube theo phong cách Talkshow / Podcast tâm lý giật gân, xúc động, chuẩn SEO.\n- Thể hiện rõ tính chất cuộc đối thoại / tâm sự giữa Tiến sĩ Đinh Đoàn và Khách mời.\n- Các từ khóa chính cần được VIẾT HOA để tạo điểm nhấn thị giác (Ví dụ: TÂM SỰ ĐẪM NƯỚC MẮT, SỰ THẬT ĐAU LÒNG, TIẾN SĨ ĐINH ĐOÀN...).\n- Độ dài tiêu đề không được vượt quá 100 ký tự, kích thích mạnh trí tò mò của người xem. Chỉ trả về duy nhất tiêu đề, không kèm lời giải thích thừa.",

                "slug": "Dựa vào nội dung kịch bản vừa viết, hãy tạo một URL slug ngắn gọn, dễ nhớ, thân thiện với SEO dùng làm tên file và đường dẫn. Slug chỉ sử dụng chữ thường không dấu và dấu gạch ngang (ví dụ: tam-su-hon-nhan-nguoi-thu-ba), không chứa ký tự đặc biệt, không quá 50 ký tự. Chỉ trả về duy nhất chuỗi slug.",

                "description": "Dựa vào toàn bộ nội dung buổi talkshow, hãy viết một đoạn mô tả video ngắn gọn, súc tích chuẩn SEO YouTube:\n- Giới thiệu ngắn về câu chuyện tâm sự đầy trăn trở của nhân vật.\n- Nhấn mạnh những lời phân tích và bài học tâm lý sâu sắc từ Tiến sĩ Đinh Đoàn.\n- Kèm lời kêu gọi khán giả để lại ý kiến dưới phần bình luận và đăng ký kênh.",

                "hashtags": "Dựa vào nội dung video vừa viết, hãy đề xuất chính xác từ 3 đến 5 hashtag liên quan trực tiếp nhất đến nội dung video (ưu tiên chủ đề gia đình, tâm lý, tên nhân vật, chuyên mục), đảm bảo là các từ khóa có thứ hạng cao và lượng tìm kiếm lớn. Các hashtag được viết trên 1 hàng, có # ở đầu, trong 1 hashtag các từ viết liền nhau (ví dụ: #DinhDoanPhanTich #TamLyHonNhan #TamSuGiaDinh), tuyệt đối không viết quá 5 hashtag.",

                "tags": "Dựa vào toàn bộ nội dung video vừa viết, hãy tạo danh sách từ 10 đến 15 thẻ từ khóa (tags) tối ưu SEO cho YouTube. Bao gồm các cụm từ tìm kiếm phổ biến, từ khóa dài (long-tail), từ khóa ngách, tên chuyên gia Đinh Đoàn, biến thể có dấu và không dấu. Các thẻ phân cách nhau bằng dấu phẩy, KHÔNG có dấu #. Tổng độ dài toàn bộ các tag không vượt quá 450 ký tự. Chỉ trả về danh sách thẻ từ khóa cách nhau bằng dấu phẩy, không thêm lời dẫn.",

                "pinned_comment": "Hãy gợi ý một bình luận ghim cho video với vai trò là Tiến sĩ Đinh Đoàn (chủ kênh). Nội dung bình luận cần viết thành một đoạn hoàn chỉnh, mang tính gợi mở hoặc đặt câu hỏi nhằm khuyến khích khán giả chia sẻ góc nhìn hoặc trải nghiệm tương tự. Dùng icon nhẹ nhàng, xuống dòng từng ý, ngắn gọn khoảng 1-2 dòng.",

                "quiz": "Dựa vào nội dung buổi trò chuyện trên, hãy tạo 1 câu hỏi trắc nghiệm và 4 câu trả lời dành cho khán giả suy ngẫm (câu hỏi không quá 100 ký tự, câu trả lời không quá 30 ký tự). Sau đó tạo nội dung giải thích cho câu hỏi (không quá 200 ký tự) và chỉ ra đáp án đúng. Bắt đầu bằng: Theo các bạn...",

                "metadata": "Dựa vào toàn bộ nội dung vừa viết, hãy thực hiện các yêu cầu sau:\n- Viết tiêu đề video YouTube theo phong cách Talkshow / Podcast tâm lý giật gân, chuẩn SEO, từ khóa chính VIẾT HOA, dưới 100 ký tự.\n- Tạo URL slug ngắn gọn không dấu, nối bằng dấu gạch ngang, dưới 50 ký tự.\n- Viết đoạn mô tả video ngắn gọn, hấp dẫn chuẩn SEO tóm tắt câu chuyện và lời phân tích của Tiến sĩ Đinh Đoàn.\n- Đề xuất chính xác từ 3 đến 5 hashtag liên quan trực tiếp nhất (có # ở đầu, viết trên 1 hàng).\n- Tạo danh sách từ 10 đến 15 thẻ từ khóa (tags) tối ưu SEO cho YouTube phân cách bằng dấu phẩy.\n- Gợi ý một bình luận ghim tương tác từ chủ kênh Tiến sĩ Đinh Đoàn.\n- Tạo 1 câu hỏi trắc nghiệm kèm 4 lựa chọn và giải thích đáp án đúng.",

                "chapters": "Dựa vào nội dung này, hãy tạo danh sách chapter ngắn gọn, chuẩn SEO cho video talkshow. Viết hoa chữ cái đầu câu, địa danh và tên riêng.\nVí dụ:\nNội dung chính trong video:\n00:00 - Lời mở đầu của Tiến sĩ Đinh Đoàn và Khách mời\n03:30 - Biến cố đầu tiên trong cuộc hôn nhân\n08:15 - Sự thật bàng hoàng được hé lộ\n14:00 - Lời khuyên tâm lý từ Tiến sĩ Đinh Đoàn",

                "thumb_text_image_base64": "",
                "thumb_text": "Dựa trên nội dung buổi trò chuyện, hãy gợi ý một ý tưởng thiết kế thumbnail YouTube phong cách Talkshow / Phim tài liệu tâm lý siêu thực:\n- Bố cục hình ảnh: Thể hiện Tiến sĩ Đinh Đoàn với biểu cảm lắng nghe trầm ngâm/phân tích và hình ảnh nhân vật khách mời (hoặc bối cảnh câu chuyện) đầy chiều sâu cảm xúc.\n- Ánh sáng: Điện ảnh 35mm, ánh sáng tự nhiên studio ấm áp hoặc tương phản kịch tính.\n- Trong thumbnail có chữ clickbait ngắn gọn (4-8 từ) nổi bật, font chữ dày, màu vàng hoặc đỏ viền đen rõ nét.\n- Viết prompt chi tiết bằng tiếng Anh (tỷ lệ 16:9, phong cách 8k raw photography, chân thực, không CGI hoạt hình) để tạo ảnh nền thu hút người xem bấm vào ngay. Sau khi viết xong prompt thì hãy tạo ảnh luôn.",

                "thumb_notext_image_base64": "",
                "thumb_notext": "Dựa trên nội dung buổi trò chuyện, hãy gợi ý một ý tưởng thiết kế thumbnail YouTube phong cách Talkshow / Phim tài liệu tâm lý siêu thực:\n- Bố cục hình ảnh: Thể hiện Tiến sĩ Đinh Đoàn với biểu cảm lắng nghe trầm ngâm/phân tích và hình ảnh nhân vật khách mời (hoặc bối cảnh câu chuyện) đầy chiều sâu cảm xúc.\n- Ánh sáng: Điện ảnh 35mm, ánh sáng tự nhiên studio ấm áp hoặc tương phản kịch tính.\n- Trong thumbnail KHÔNG có chữ to, giữ bối cảnh sạch sẽ và chiều sâu nghệ thuật.\n- Viết prompt chi tiết bằng tiếng Anh (tỷ lệ 16:9, phong cách 8k raw photography, chân thực, không CGI hoạt hình) để tạo ảnh nền thu hút người xem bấm vào ngay. Sau khi viết xong prompt thì hãy tạo ảnh luôn."
            }
        },
        "monologue_default": {
            "name": "Bản gốc Đinh Đoàn (Đơn thoại)",
            "content_mode": "monologue",
            "project_url": "https://chatgpt.com/g/g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project",
            "default_voice_id": "",
            "default_youtube_channel_id": "",
            "pipeline": {
                "title": True,
                "slug": True,
                "description": True,
                "hashtags": True,
                "tags": True,
                "pinned_comment": True,
                "quiz": True,
                "chapters": True,
                "thumbnail_with_text": True,
                "thumbnail_without_text": True,
                "audio": True,
                "video_render": False,
                "youtube_upload": False,
                "youtube_schedule": False,
            },
            "image_generation_settings": {
                "provider": "google_flow",
                "model": "nano_banana_pro",
                "aspect_ratio": "16:9",
                "output_count": 1,
                "video_model": "veo_3_1_lite",
                "video_aspect_ratio": "16:9",
                "video_output_count": 1,
                "workflow_profile_id": "",
                "style_prompt": "Cinematic documentary film still, 35mm photography, atmospheric natural lighting, realistic textures, cinematic composition, shallow depth of field, balanced color grading, high visual fidelity, 8k raw photo.",
                "negative_prompt": "cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.",
                "seed_mode": "random",
                "thumbnail_variant": "with_text",
                "scene_0_source": "from_thumbnail_without_text",
                "enable_intro_video": True,
                "intro_scene_target_seconds": 8.0,
                "intro_crop_watermark": True,
                "video_style_prompt": "Cinematic documentary film, 35mm motion picture composition, natural atmospheric lighting, realistic textures, balanced color grading, 4k cinematic video footage.",
                "video_negative_prompt": "still image, static photo, cartoon, anime, 3D CGI render, illustration, deformed hands, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.",
                "video_prompt_template": "{frame_directive} Scene action: {action}. Visual style: {style}. {motion} Clean video without any text, letters, watermark, or subtitles.",
                "video_motion_prompt": "Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video.",
                "scene_0_prompt_template": "A cinematic movie still: {style}, opening scene hook. {reference} Story visual core: {thumbnail_concept}. 16:9 widescreen, photorealistic 8k, authentic documentary realism, clean framing without text.",
                "scene_body_prompt_template": "A still photograph: {style}, scene {scene_index}. {reference} Narrative scene: {action}. 16:9 widescreen still photograph, authentic documentary realism, natural lighting, clean visual without text.",
                "scene_duration_min_seconds": 25,
                "scene_duration_target_seconds": 30,
                "scene_duration_max_seconds": 35,
            },
            "publishing_settings": {
                "upload_method": "browser",
                "category_id": "",
                "language": "vi",
                "made_for_kids": None,
                "notify_subscribers": True,
                "include_tags": True,
                "default_tags": "",
                "contains_synthetic_media": True,
                "monetization_mode": "auto_enable_if_available",
                "midroll_ads": True,
                "ad_suitability_mode": "none_of_the_above",
                "playlist_name": "",
                "age_restriction": False,
                "paid_promotion": False,
                "automatic_chapters": True,
                "automatic_places": True,
                "automatic_concepts": True,
                "title_description_language": "vi",
                "caption_certification": "none",
                "license": "youtube",
                "allow_embedding": True,
                "remix_policy": "video_and_audio",
                "comments_enabled": True,
                "comment_moderation": "basic",
                "comment_access": "anyone",
                "comment_sort": "top",
                "show_ratings": True,
                "upload_captions": True,
                "end_screen_source_video_id": "",
                "premiere": False,
                "checks_policy": "schedule_immediately",
                "description_template": "{description}\n\n{chapters}\n\n{hashtags}",
            },
            "prompts": {
                "outline": "Bạn là biên tập viên nội dung cho kênh YouTube Đinh Đoàn Phân Tích. Dưới đây là một kịch bản dài đã có sẵn. Dưới góc nhìn của tiến sĩ, Chuyên gia tâm lý Đinh Đoàn Đinh Đoàn phân tích Hãy chia nội dung này thành các phần lớn hợp lý theo dòng chảy tự nhiên của câu chuyện để dễ viết lại thành lời dẫn video. Không cần đặt tiêu đề cho từng phần. Hãy gộp thành các phần lớn, KHÔNG tách nhỏ quá nhiều. Mỗi phần lớn chỉ cần trình bày dưới dạng gạch đầu dòng các ý chính cơ bản, nhưng phải bao quát đủ thông tin quan trọng như bản gốc, không cắt gọn mất ý chính, không viết tắt. Kiểm tra các Tên riêng, mốc thời gian, địa điểm, số liệu để sửa lại cho đúng.\n\nLưu ý CỰC KỲ QUAN TRỌNG: Khi lập dàn ý, CHỈ LẤY phần nội dung câu chuyện bám sát theo đúng TIÊU ĐỀ KỊCH BẢN. Tuyệt đối bỏ qua tất cả các nội dung khác (ví dụ như đoạn quảng cáo, giới thiệu đầu video, hoặc các câu chuyện phụ không liên quan đến tiêu đề).\n\nQuan trọng: Định dạng mỗi phần bằng chuỗi [PHAN] ở đầu, ví dụ:\n[PHAN]\nNội dung phần 1...\n[PHAN]\nNội dung phần 2...\n\nDưới đây là kịch bản (Bao gồm Tiêu đề và Nội dung):\n{transcript}",
                
                "intro": "Bạn là biên tập viên nội dung cho kênh YouTube Đinh Đoàn Phân Tích. Dưới góc nhìn của tiến sĩ, Chuyên gia tâm lý Đinh Đoàn Đinh Đoàn phân tích Hãy Dựa trên toàn bộ nội dung của kịch bản, hãy viết lại một đoạn mở đầu, thật giật gân và kịch tính, phù hợp với giọng nam trung niên và phong cách kể chuyện về gia đình, cuốn hút đặc trưng của kênh YouTube Đinh Đoàn Phân Tích, mở đầu tiến sĩ, bắt đầu bằng: \"Xin chào quý vị khán giả đang theo dõi kênh Đinh Đoàn Phân Tích, tôi là tiến sĩ, chuyên gia tâm lý Đinh Đoàn. Thưa quý vị,...\". Đoạn văn cần được viết liền mạch trong một khối, không xuống dòng, không dùng dấu gạch ngang \"-\", nhưng phải đầy đủ dấu câu, dễ đọc thành lời. Mục tiêu là tạo ấn tượng mạnh ngay từ 5 giây đầu để giữ chân người xem đến cuối video. không viết hoa chữ \"AI\".",
                
                "body": "Bạn là biên tập viên nội dung cho kênh YouTube Thấu Hiểu Hôn Nhân. Dưới góc nhìn của tiến sĩ, Chuyên gia tâm lý Đinh Đoàn Đinh Đoàn kể lại Hãy viết tiếp phần nội dung trước,không lặp lại nội dung đã viết trước đó,không viết vào nội dung phần sau, viết ngắn gọn, súc tích, không xuống dòng, không sử dụng dấu gạch ngang \"—\", không dùng dấu gạch ngang \"–\", nhưng phải đảm bảo đầy đủ dấu câu, rõ ràng, dễ đọc và dễ đọc thành lời. Nội dung viết lại phải giữ đầy đủ ý so với bản gốc, tốt hơn nếu chi tiết và sâu sắc hơn. Giọng văn cần mạch lạc, có nhịp kể hợp lý, phù hợp với phong cách kể chuyện về gia đình, giúp người xem dễ theo dõi và bị cuốn hút,không viết tắt.\n\nDưới đây là phần dàn ý cần viết lại ở lượt này:\n{part}",
                
                "outro": "viết outro cho video (viết lại nội dung đầy đủ chi tiết, nội dung viết liền không cần xuống dòng) :\n- kết luận.\n- Lời cảm ơn đến người xem.\n- Kêu gọi like, chia sẻ và đăng ký kênh.\n- Nhắc nhở về việc bình luận và chia sẻ ý kiến về câu chuyện\n- Hứa hẹn cập nhật những câu chuyện hấp dẫn nhất trong các video tiếp theo.\n- Kêu gọi nhấn nút \"Tham gia\" để đăng ký thành viên hoặc gửi \"Cảm ơn\"",
                
                "title": "Dựa vào toàn bộ nội dung vừa viết, hãy viết tiêu đề video YouTube theo phong cách giật gân, chuẩn SEO, sử dụng từ ngữ gây tò mò hoặc sốc để thu hút người xem. Các từ khóa chính cần được VIẾT HOA để tăng khả năng nhận diện. Tiêu đề không được vượt quá 100 ký tự, khiến người xem tò mò về câu chuyện chưa từng được kể hoặc ít người biết đến và phải đủ sức khiến người xem muốn bấm vào ngay. Chỉ trả về tiêu đề, không kèm lời giải thích thừa.",
                
                "slug": "Dựa vào nội dung kịch bản vừa viết, hãy tạo một URL slug ngắn gọn, dễ nhớ, thân thiện với SEO dùng làm tên file và đường dẫn. Slug chỉ sử dụng chữ thường không dấu và dấu gạch ngang (ví dụ: bai-hoc-cuoc-song-y-nghia), không chứa ký tự đặc biệt, không quá 50 ký tự. Chỉ trả về duy nhất chuỗi slug.",
                
                "description": "Dựa vào toàn bộ nội dung vừa viết, hãy viết một đoạn mô tả video ngắn gọn, súc tích, hấp dẫn chuẩn SEO, tóm tắt đúng nội dung cốt lõi của câu chuyện và có lời kêu gọi tương tác ngắn.",
                
                "hashtags": "Dựa vào nội dung video vừa viết, hãy đề xuất chính xác từ 3 đến 5 hashtag liên quan trực tiếp nhất đến nội dung video (ưu tiên tên nhân vật, sự kiện lịch sử, chủ đề ngách), đảm bảo là các từ khóa có thứ hạng cao và lượng tìm kiếm lớn để tăng khả năng được đề xuất. Các hashtag được viết trên 1 hàng, có # ở đầu, trong 1 hashtag các từ viết liền nhau (ví dụ: #ChienTranhBienGioi #LichSuVietNam #KhmerDo), tuyệt đối không viết quá 5 hashtag và không chèn các hashtag chung chung ngoài lề.",

                "tags": "Dựa vào toàn bộ nội dung video vừa viết, hãy tạo danh sách từ 10 đến 15 thẻ từ khóa (tags) tối ưu SEO cho YouTube. Bao gồm các cụm từ tìm kiếm phổ biến, từ khóa dài (long-tail), từ khóa ngách, tên nhân vật, sự kiện, biến thể có dấu và không dấu. Các thẻ phân cách nhau bằng dấu phẩy, KHÔNG có dấu # (ví dụ: chiến tranh biên giới tây nam, chien tranh bien gioi tay nam, lịch sử việt nam, khmer đỏ, pol pot, đinh đoàn phân tích). Tổng độ dài toàn bộ các tag không vượt quá 450 ký tự. Chỉ trả về danh sách thẻ từ khóa cách nhau bằng dấu phẩy, không thêm lời dẫn.",
                
                "pinned_comment": "Hãy gợi ý một bình luận ghim cho video với vai trò là chủ kênh. Nội dung bình luận cần viết thành một đoạn hoàn chỉnh, mang tính gợi mở hoặc đặt câu hỏi nhằm khuyến khích người xem tương tác, chia sẻ quan điểm hoặc cảm xúc về video. dùng icon, xuống dòng từng ý, ngắn gọn khoảng 1-2 dòng.",
                
                "quiz": "Dựa vào nội dung vid trên hãy Tạo 1 câu hỏi và 4 câu trả lời dành cho khán giả trả lời (câu hỏi không đc quá 100 ký tự, câu trả lời không đc quá 30 ký tự). sau đó hãy tạo nội dung giải thích cho câu hỏi (không được quá 200 ký tự).hãy chỉ ra câu trả lời đúng. bắt đầu bằng: theo các bạn...",
                
                "metadata": "Dựa vào toàn bộ nội dung vừa viết, hãy thực hiện các yêu cầu sau:\n- Hãy viết tiêu đề video YouTube theo phong cách giật gân, chuẩn SEO, sử dụng từ ngữ gây tò mò hoặc sốc để thu hút người xem. Các từ khóa chính cần được VIẾT HOA để tăng khả năng nhận diện. Tiêu đề không được vượt quá 100 ký tự, khiến người xem tò mò về câu chuyện chưa từng được kể hoặc ít người biết đến và phải đủ sức khiến người xem muốn bấm vào ngay.\n- Hãy tạo lại một URL slug ngắn gọn, dễ nhớ, thân thiện với SEO. Slug chỉ sử dụng chữ thường và dấu gạch ngang, không có dấu tiếng Việt, không chứa ký tự đặc biệt, và phải phản ánh đúng nội dung chính của video. slug url không quá 50 ký tự.\n- Hãy viết một đoạn mô tả video ngắn gọn, súc tích, hấp dẫn chuẩn SEO, tóm tắt đúng nội dung cốt lõi của câu chuyện và lời kêu gọi tương tác ngắn.\n- Đề xuất chính xác từ 3 đến 5 hashtag liên quan trực tiếp nhất đến nội dung video (có dấu # ở đầu, viết trên 1 hàng, ví dụ: #ChienTranhBienGioi #LichSuVietNam #KhmerDo).\n- Tạo danh sách từ 10 đến 15 thẻ từ khóa (tags) tối ưu SEO cho YouTube (dạng cụm từ phân cách bằng dấu phẩy, không có dấu #, có cả từ khóa có dấu và không dấu, ví dụ: chiến tranh biên giới tây nam, chien tranh bien gioi tay nam, lịch sử việt nam, khmer đỏ).\n- Hãy gợi ý một bình luận ghim cho video với vai trò là chủ kênh. Nội dung bình luận cần viết thành một đoạn hoàn chỉnh, mang tính gợi mở hoặc đặt câu hỏi nhằm khuyến khích người xem tương tác, chia sẻ quan điểm hoặc cảm xúc về video. dùng icon, xuống dòng từng ý, ngắn gọn khoảng 1-2 dòng.\n- Dựa vào nội dung vid trên hãy Tạo 1 câu hỏi và 4 câu trả lời dành cho khán giả trả lời (câu hỏi không đc quá 100 ký tự, câu trả lời không đc quá 30 ký tự). sau đó hãy tạo nội dung giải thích cho câu hỏi (không được quá 200 ký tự).hãy chỉ ra câu trả lời đúng. bắt đầu bằng: theo các bạn...",
                
                "chapters": "Dựa vào nội dung này, mình đã tạo 1 video dài, hãy tạo 1 chapter ngắn gọn, chuẩn SEO cho video. viết hoa đầu câu, viết hoa chữ cái đầu của tên riêng hoặc địa danh và viết hoa viết hoa chỗ cần viết hoa.\nví dụ:\nNội dung chính trong video:\n00:00 - N...\n04:30 - G...\n09:00 - A....",
                
                "thumb_text_image_base64": "",
                "thumb_text": "Dựa trên nội dung video tôi cung cấp, hãy gợi ý một ý tưởng thiết kế thumbnail YouTube thật hấp dẫn và thu hút, phù hợp với nội dung video. Ý tưởng cần mô tả rõ: bố cục hình ảnh, nhân vật hoặc chi tiết chính, cảm xúc thể hiện, màu sắc chủ đạo, điểm nhấn thị giác, và cách truyền tải thông điệp một cách mạnh mẽ. Sau khi gợi ý xong ý tưởng thiết kế, hãy viết cho tôi một prompt hình ảnh thật chi tiết để có thể dùng cho ChatGPT hoặc công cụ AI vẽ hình nền dựa trên đúng ý tưởng đó, yêu cầu Prompt không vi phạm chính sách của ChatGPT,Trong thumbnail có chữ, font chữ và mà màu chữ phải phù hợp với phong cách click bait, chữ phải nổi bật và không che đi các bối cảnh quan trọng, Prompt tạo ra ảnh bằng với kích thước thumbnail của youtube, tỷ lệ khung hình 16:9. Prompt hình ảnh cần sử dụng ngôn ngữ mô tả cụ thể về khung cảnh, ánh sáng, phong cách, nhân vật và màu sắc. Ảnh phải giống thật, siêu thực. Sau khi viết xong prompt thì hãy tạo ảnh luôn.",
                
                "thumb_notext_image_base64": "",
                "thumb_notext": "Dựa trên nội dung video tôi cung cấp, hãy gợi ý một ý tưởng thiết kế thumbnail YouTube thật hấp dẫn và thu hút, phù hợp với nội dung video. Ý tưởng cần mô tả rõ: bố cục hình ảnh, nhân vật hoặc chi tiết chính, cảm xúc thể hiện, màu sắc chủ đạo, điểm nhấn thị giác, và cách truyền tải thông điệp một cách mạnh mẽ. Sau khi gợi ý xong ý tưởng thiết kế, hãy viết cho tôi một prompt hình ảnh thật chi tiết để có thể dùng cho ChatGPT hoặc công cụ AI vẽ hình nền dựa trên đúng ý tưởng đó, yêu cầu Prompt không vi phạm chính sách của ChatGPT,Trong thumbnail không có chữ to trong thumbnail, các chữ nhỏ của văn bản hoặc chi tiết thì có thể có . Prompt tạo ra ảnh bằng với kích thước thumbnail của youtube, tỷ lệ khung hình 16:9. Prompt hình ảnh cần sử dụng ngôn ngữ mô tả cụ thể về khung cảnh, ánh sáng, phong cách, nhân vật và màu sắc. Ảnh phải giống thật, siêu thực. Sau khi viết xong prompt thì hãy tạo ảnh luôn."
            }
        }
    }
}
