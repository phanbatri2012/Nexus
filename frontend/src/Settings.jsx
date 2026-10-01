import { useState, useEffect } from 'react';
import './Settings.css';

const DEFAULT_PIPELINE = {
  title: true,
  slug: true,
  description: true,
  hashtags: true,
  tags: true,
  pinned_comment: true,
  quiz: true,
  chapters: true,
  thumbnail_with_text: true,
  thumbnail_without_text: true,
  audio: true,
  video_render: false,
  youtube_upload: false,
  youtube_schedule: false
};

const GOOGLE_FLOW_IMAGE_MODELS = [
  {
    id: 'nano_banana_2',
    name: '🍌 Nano Banana 2',
    badge: '🟢 Chuẩn Google Flow',
    creditLabel: '🟢 Tiết kiệm (~1 credit/ảnh)',
    speed: '⚡ 3–5s',
    description: '⭐ Model tạo ảnh mặc định mới nhất trên Google Flow. Tốc độ sinh nhanh, màu sắc chân thực, chi tiết sắc nét, phù hợp tạo 30–50 cảnh visual cho video dài.'
  },
  {
    id: 'nano_banana_pro',
    name: 'Nano Banana Pro (Legacy)',
    badge: '🟢 Tiết kiệm Credit',
    creditLabel: '🟢 Thấp nhất (~1 credit/ảnh)',
    speed: '⚡ 3–5s',
    description: 'Model thế hệ tiền nhiệm, tương thích hoàn toàn với các prompt version cũ.',
    recommended: true
  },
  {
    id: 'imagen_3_standard',
    name: 'Google Imagen 3 (High-Fidelity)',
    badge: '🟡 Chất lượng cao',
    creditLabel: '🟡 Trung bình (~2–3 credit/ảnh)',
    speed: '⏳ 8–12s',
    description: 'Chất lượng siêu thực cao cấp. Tái tạo biểu cảm nhân vật, bàn tay, ánh sáng và chi tiết da xuất sắc; bám sát prompt phức tạp.'
  },
  {
    id: 'imagen_3_photoreal',
    name: 'Google Imagen 3 (Photorealistic / 35mm)',
    badge: '🟡 Tư liệu thực tế',
    creditLabel: '🟡 Tiêu chuẩn (~2 credit/ảnh)',
    speed: '⏳ 6–10s',
    description: 'Phong cách ảnh tư liệu / Điện ảnh thực tế. Tối ưu đặc biệt cho video kể chuyện, phim tài liệu, hạn chế cảm giác bóng bẩy 3D/CGI.'
  }
];

const GOOGLE_FLOW_VIDEO_MODELS = [
  {
    id: 'omni_1_1_flash',
    name: 'Omni 1.1 Flash',
    badge: '⚡ Mặc định / Siêu tốc',
    creditLabel: '🟡 Tiêu chuẩn (~10 credit/clip)',
    speed: '⚡ 15–25s',
    description: '⭐ Model tạo video AI mặc định mới nhất của Google Flow. Tốc độ sinh siêu nhanh, chuyển động mượt mà và phối cảnh nhất quán.'
  },
  {
    id: 'veo_3_1_lite',
    name: 'Veo 3.1 – Lite',
    badge: '🟢 Tiết kiệm Credit',
    creditLabel: '🟢 Thấp (~8 credit/clip)',
    speed: '⏳ 20–30s',
    description: 'Bản rút gọn của Veo 3.1, tối ưu chi phí credit cho các cảnh intro ngắn.',
    recommended: true
  },
  {
    id: 'veo_3_1_fast',
    name: 'Veo 3.1 – Fast',
    badge: '🟡 Tốc độ cao',
    creditLabel: '🟡 Trung bình (~12 credit/clip)',
    speed: '⏳ 25–40s',
    description: 'Veo 3.1 phiên bản tối ưu tốc độ, cân bằng chuyển động điện ảnh và thời gian chờ.'
  },
  {
    id: 'veo_3_1_quality',
    name: 'Veo 3.1 – Quality',
    badge: '🔴 Chất lượng Điện ảnh',
    creditLabel: '🔴 Cao (~15–20 credit/clip)',
    speed: '🐌 45–75s',
    description: 'Veo 3.1 chất lượng cao nhất với độ sâu trường ảnh và ánh sáng chân thực tối đa.'
  },
  {
    id: 'google_veo_intro',
    name: 'Google Veo (Intro Video Generator)',
    badge: '🔴 Video AI Legacy',
    creditLabel: '🔴 Cao (~10–20 credit/clip)',
    speed: '🐌 30–60s',
    description: 'Sinh video chuyển động AI 4–8 giây cho cảnh mở đầu để giữ chân người xem (Legacy ID).'
  }
];

const GOOGLE_FLOW_MODELS = [...GOOGLE_FLOW_IMAGE_MODELS, ...GOOGLE_FLOW_VIDEO_MODELS];

const VIDEO_MOTION_PRESETS = [
  { label: '🎬 Mặc định (Smooth Motion)', prompt: 'Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video.' },
  { label: '🔍 Slow Zoom In', prompt: 'Motion: slow subtle zoom in towards central subject, cinematic atmospheric lighting, 4k 24fps.' },
  { label: '↔️ Pan Trái sang Phải', prompt: 'Motion: slow cinematic camera pan from left to right, smooth parallax, 4k 24fps.' },
  { label: '🚀 Dramatic Push In', prompt: 'Motion: dramatic cinematic camera push in, dynamic atmospheric lighting, 4k 24fps.' },
  { label: '🚁 Drone Flyover', prompt: 'Motion: slow cinematic aerial flyover shot, breathtaking expansive perspective, 4k 24fps.' }
];

const SCENE_0_TAGS = [
  { tag: '{style}', label: '+ {style} (Phong cách)' },
  { tag: '{reference}', label: '+ {reference} (Nhân vật mẫu)' },
  { tag: '{thumbnail_concept}', label: '+ {thumbnail_concept} (Thumbnail)' },
  { tag: '{action}', label: '+ {action} (Kịch bản)' },
  { tag: '{scene_index}', label: '+ {scene_index} (STT)' }
];

const SCENE_BODY_TAGS = [
  { tag: '{style}', label: '+ {style} (Phong cách)' },
  { tag: '{reference}', label: '+ {reference} (Nhân vật mẫu)' },
  { tag: '{action}', label: '+ {action} (Nội dung cảnh)' },
  { tag: '{scene_index}', label: '+ {scene_index} (Số thứ tự)' }
];

const VIDEO_PROMPT_TAGS = [
  { tag: '{frame_directive}', label: '+ {frame_directive} (Ảnh gốc)' },
  { tag: '{action}', label: '+ {action} (Kịch bản)' },
  { tag: '{style}', label: '+ {style} (Phong cách video)' },
  { tag: '{motion}', label: '+ {motion} (Chuyển động camera)' }
];

const DEFAULT_IMAGE_GENERATION_SETTINGS = {
  provider: 'google_flow',
  model: 'nano_banana_pro',
  aspect_ratio: '16:9',
  output_count: 1,
  video_model: 'veo_3_1_lite',
  video_aspect_ratio: '16:9',
  video_output_count: 1,
  style_prompt: 'Cinematic documentary film still, 35mm photography, atmospheric natural lighting, realistic textures, cinematic composition, shallow depth of field, balanced color grading, high visual fidelity, 8k raw photo.',
  avoid_prompt: 'cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.',
  negative_prompt: 'cartoon, anime, 3D CGI render, illustration, drawing, plastic skin, oversaturated, blown-out highlights, deformed hands, extra fingers, missing limbs, duplicate faces, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.',
  thumbnail_variant: 'with_text',
  scene_0_source: 'from_thumbnail_without_text',
  enable_intro_video: true,
  intro_scene_target_seconds: 8.0,
  intro_crop_watermark: true,
  video_style_prompt: 'Cinematic documentary film, 35mm motion picture composition, natural atmospheric lighting, realistic textures, balanced color grading, 4k cinematic video footage.',
  video_negative_prompt: 'still image, static photo, cartoon, anime, 3D CGI render, illustration, deformed hands, distorted anatomy, text, watermark, signature, logo, blurry, low resolution.',
  video_prompt_template: '{frame_directive} Scene action: {action}. Visual style: {style}. {motion} Clean video without any text, letters, watermark, or subtitles.',
  video_motion_prompt: 'Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video.',
  scene_0_prompt_template: 'A cinematic movie still: {style}, opening scene hook. {reference} Story visual core: {thumbnail_concept}. 16:9 widescreen, photorealistic 8k, authentic documentary realism, clean framing without text.',
  scene_body_prompt_template: 'A still photograph: {style}, scene {scene_index}. {reference} Narrative scene: {action}. 16:9 widescreen still photograph, authentic documentary realism, natural lighting, clean visual without text.',
  scene_duration_min_seconds: 25,
  scene_duration_target_seconds: 30,
  scene_duration_max_seconds: 35
};

const DEFAULT_PUBLISHING_SETTINGS = {
  upload_method: 'browser',
  publish_mode: 'schedule',
  category_id: '',
  language: 'vi',
  made_for_kids: null,
  notify_subscribers: true,
  include_tags: true,
  default_tags: '',
  contains_synthetic_media: true,
  monetization_mode: 'auto_enable_if_available',
  midroll_ads: true,
  ad_suitability_mode: 'none_of_the_above',
  playlist_name: '',
  age_restriction: false,
  paid_promotion: false,
  automatic_chapters: true,
  automatic_places: true,
  automatic_concepts: true,
  title_description_language: 'vi',
  caption_certification: 'none',
  license: 'youtube',
  allow_embedding: true,
  remix_policy: 'video_and_audio',
  comments_enabled: true,
  comment_moderation: 'basic',
  comment_access: 'anyone',
  comment_sort: 'top',
  show_ratings: true,
  upload_captions: true,
  end_screen_source_video_id: '',
  premiere: false,
  checks_policy: 'schedule_immediately',
  description_template: ''
};

const YOUTUBE_CATEGORIES = [
  { id: '22', name: '22 - Mọi người & Blog (People & Blogs - Mặc định)' },
  { id: '24', name: '24 - Giải trí (Entertainment)' },
  { id: '27', name: '27 - Giáo dục (Education)' },
  { id: '28', name: '28 - Khoa học & Công nghệ (Science & Technology)' },
  { id: '10', name: '10 - Âm nhạc (Music)' },
  { id: '20', name: '20 - Trò chơi (Gaming)' },
  { id: '1', name: '1 - Phim & Hoạt hình (Film & Animation)' },
  { id: '2', name: '2 - Ô tô & Xe cộ (Autos & Vehicles)' },
  { id: '15', name: '15 - Thú cưng & Động vật (Pets & Animals)' },
  { id: '17', name: '17 - Thể thao (Sports)' },
  { id: '19', name: '19 - Du lịch & Sự kiện (Travel & Events)' },
  { id: '23', name: '23 - Hài kịch (Comedy)' },
  { id: '25', name: '25 - Tin tức & Chính trị (News & Politics)' },
  { id: '26', name: '26 - Hướng dẫn & Phong cách (Howto & Style)' },
  { id: '29', name: '29 - Hoạt động xã hội & Phi lợi nhuận (Nonprofits & Activism)' }
];

const PIPELINE_STEPS = [
  {
    key: 'title',
    label: 'Tiêu đề (Title)',
    description: 'Tự động tạo các phương án tiêu đề hấp dẫn.'
  },
  {
    key: 'slug',
    label: 'URL Slug',
    description: 'Tự động tạo slug để đặt tên file MP4 khi render.'
  },
  {
    key: 'description',
    label: 'Mô tả tóm tắt (Description)',
    description: 'Tự động tạo đoạn mô tả video chuẩn SEO.'
  },
  {
    key: 'hashtags',
    label: 'Hashtags (cho mô tả)',
    description: 'Tự động tạo 3–5 hashtag #... chèn vào mô tả video.'
  },
  {
    key: 'tags',
    label: 'Thẻ từ khóa (Tags YouTube)',
    description: 'Tự động tạo danh sách 10–15 tags tối ưu SEO tìm kiếm YouTube.'
  },
  {
    key: 'pinned_comment',
    label: 'Bình luận ghim',
    description: 'Tự động tạo nội dung bình luận ghim tương tác.'
  },
  {
    key: 'quiz',
    label: 'Quiz tương tác',
    description: 'Tự động tạo câu hỏi trắc nghiệm tương tác cho khán giả.'
  },
  {
    key: 'chapters',
    label: 'Chapters',
    description: 'Tự động tạo các mốc chapter sau khi kịch bản lõi hoàn tất.'
  },
  {
    key: 'thumbnail_with_text',
    label: 'Thumbnail có chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail có chữ.'
  },
  {
    key: 'thumbnail_without_text',
    label: 'Thumbnail không chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail không chữ.'
  },
  {
    key: 'audio',
    label: 'Tự động tạo audio',
    description: 'Tự kiểm duyệt kịch bản và gửi đúng nhà cung cấp của giọng đã chọn.'
  },
  {
    key: 'video_render',
    label: 'Dựng MP4',
    description: 'Lập cảnh theo SRT, tạo ảnh Google Flow và dựng MP4 1080p.'
  },
  {
    key: 'youtube_upload',
    label: 'Upload Private',
    description: 'Upload video, thumbnail và phụ đề lên đúng kênh, luôn giữ Private.'
  },
  {
    key: 'youtube_schedule',
    label: 'Đặt lịch đăng',
    description: 'Giữ khung giờ hợp lệ của kênh và đặt lịch sau khi YouTube xử lý xong.'
  }
];

const PIPELINE_STEP_LABELS = Object.fromEntries(
  PIPELINE_STEPS.map(step => [step.key, step.label])
);

const PUBLISH_CONFIGURATION_LABELS = {
  default_youtube_channel_id: 'kênh YouTube mặc định',
  youtube_oauth: 'OAuth YouTube',
  gpm_profile_id: 'GPM Profile riêng',
  gpm_profile_exclusive: 'GPM Profile không dùng chung với kênh khác',
  gpm_proxy_info: 'proxy riêng của GPM Profile',
  publication_timezone: 'múi giờ đăng',
  publication_slots: 'khung giờ đăng',
  publication_daily_limit: 'giới hạn video mỗi ngày',
  publication_lead_minutes: 'khoảng an toàn trước giờ đăng',
  publication_paused: 'bỏ tạm dừng lịch đăng',
  public_upload_verified: 'xác minh quyền đặt lịch public',
  made_for_kids: 'lựa chọn dành cho trẻ em'
};

function pipelineDependencies(thumbnailVariant) {
  return {
    video_render: ['audio'],
    youtube_upload: [
      'video_render',
      'title',
      'description',
      'tags',
      thumbnailVariant === 'with_text'
        ? 'thumbnail_with_text'
        : 'thumbnail_without_text'
    ],
    youtube_schedule: ['youtube_upload']
  };
}

function resolvePipelineDependencies(pipeline, thumbnailVariant) {
  const resolved = { ...DEFAULT_PIPELINE, ...pipeline };
  const enabled = [];
  const dependencies = pipelineDependencies(thumbnailVariant);
  let changed = true;
  while (changed) {
    changed = false;
    Object.entries(dependencies).forEach(([step, requiredSteps]) => {
      if (!resolved[step]) return;
      requiredSteps.forEach(requiredStep => {
        if (resolved[requiredStep]) return;
        resolved[requiredStep] = true;
        enabled.push(requiredStep);
        changed = true;
      });
    });
  }
  return { pipeline: resolved, enabled: [...new Set(enabled)] };
}

function pipelineDependents(pipeline, stepKey, thumbnailVariant) {
  const dependencies = pipelineDependencies(thumbnailVariant);
  const dependents = [];
  let frontier = [stepKey];
  while (frontier.length) {
    const source = frontier.shift();
    Object.entries(dependencies).forEach(([step, requiredSteps]) => {
      if (!pipeline[step] || dependents.includes(step) || !requiredSteps.includes(source)) return;
      dependents.push(step);
      frontier.push(step);
    });
  }
  return dependents;
}

function pipelineOutcome(pipeline, publishingSettings = {}) {
  if (pipeline.youtube_upload) {
    const mode = publishingSettings?.publish_mode || (pipeline.youtube_schedule ? 'schedule' : 'private');
    if (mode === 'public') return 'Dựng, upload và Public ngay';
    if (mode === 'schedule' || pipeline.youtube_schedule) return 'Dựng, upload và đặt lịch';
    return 'Upload và giữ Private';
  }
  if (pipeline.video_render) return 'Tạo MP4, không upload';
  if (pipeline.audio) return 'Kết thúc ở Audio';
  return 'Kết thúc sau khi tạo nội dung đã chọn';
}

function ProviderVoiceOptions({ voices }) {
  const providerNames = { genmax: 'Genmax', omnivoice: 'OmniVoice' };
  const groups = voices.reduce((result, voice) => {
    const providerId = voice.provider_id || 'genmax';
    if (!result[providerId]) result[providerId] = [];
    result[providerId].push(voice);
    return result;
  }, {});
  return Object.entries(groups).map(([providerId, items]) => (
    <optgroup key={providerId} label={providerNames[providerId] || providerId}>
      {items.map(voice => (
        <option key={voice.id} value={voice.id}>
          [{providerNames[providerId] || providerId}] {voice.name}
        </option>
      ))}
    </optgroup>
  ));
}

const PROMPT_FIELD_METADATA = {
  outline: {
    key: 'outline',
    label: '1. Dàn ý (Outline)',
    help: 'Chia nội dung video nguồn thành các phần lớn bằng tag [PHAN].',
    guide: 'Bắt buộc chứa biến {transcript} để nhận toàn bộ phụ đề/nội dung thô từ video nguồn.',
    variables: [
      { tag: '{transcript}', label: '+ {transcript}', required: true, description: 'Nội dung phụ đề/văn bản thô video nguồn', color: '#38bdf8' }
    ]
  },
  intro: {
    key: 'intro',
    label: '2. Mở đầu (Intro)',
    help: 'Viết đoạn mở đầu giật gân, cuốn hút giữ chân khán giả từ 5 giây đầu.',
    guide: 'Trong chế độ Đối thoại, hãy dùng thẻ [MC]: và [KHACH_1]: để phân chia lượt nói mở đầu.',
    variables: [
      { tag: '[MC]:', label: '+ [MC]:', required: true, forMode: 'dialogue', description: 'Lượt mở đầu của MC/Host', color: '#fbbf24' },
      { tag: '[KHACH_1]:', label: '+ [KHACH_1]:', required: true, forMode: 'dialogue', description: 'Lượt chào của Khách Mời Chính', color: '#34d399' },
      { tag: '[KHACH_2]:', label: '+ [KHACH_2]:', required: false, forMode: 'dialogue', description: 'Lượt lời của Khách Mời 2 (nếu có)', color: '#f472b6' }
    ]
  },
  body: {
    key: 'body',
    label: '3. Nội dung chính (Body)',
    help: 'Viết chi tiết từng phần nội dung câu chuyện trong vòng lặp kịch bản.',
    guide: 'Bắt buộc chứa {part} để nhận dữ liệu từng phần dàn ý. Trong chế độ Đối thoại, bắt buộc dùng thẻ [MC]: và [KHACH_1]: ở đầu mỗi lượt thoại.',
    variables: [
      { tag: '{part}', label: '+ {part}', required: true, description: 'Dữ liệu từng phần [PHAN] trong dàn ý', color: '#38bdf8' },
      { tag: '[MC]:', label: '+ [MC]:', required: true, forMode: 'dialogue', description: 'Lời dẫn/hỏi/phân tích của MC', color: '#fbbf24' },
      { tag: '[KHACH_1]:', label: '+ [KHACH_1]:', required: true, forMode: 'dialogue', description: 'Lời kể chuyện/chia sẻ của Khách Mời', color: '#34d399' },
      { tag: '[KHACH_2]:', label: '+ [KHACH_2]:', required: false, forMode: 'dialogue', description: 'Lời thoại của Khách Mời 2', color: '#f472b6' }
    ]
  },
  outro: {
    key: 'outro',
    label: '4. Kết thúc (Outro)',
    help: 'Đúc kết bài học, cảm ơn người xem và kêu gọi like, share, đăng ký kênh.',
    guide: 'Trong chế độ Đối thoại, hãy dùng thẻ [MC]: và [KHACH_1]: để phân chia lời kết thúc.',
    variables: [
      { tag: '[MC]:', label: '+ [MC]:', required: true, forMode: 'dialogue', description: 'Lời đúc kết & chào kết của MC', color: '#fbbf24' },
      { tag: '[KHACH_1]:', label: '+ [KHACH_1]:', required: true, forMode: 'dialogue', description: 'Lời cảm ơn của Khách Mời', color: '#34d399' },
      { tag: '[KHACH_2]:', label: '+ [KHACH_2]:', required: false, forMode: 'dialogue', description: 'Lời chào của Khách Mời 2', color: '#f472b6' }
    ]
  },
  title: {
    key: 'title',
    label: '5. Tiêu đề (Title)',
    help: 'Tiêu đề video YouTube giật gân, chuẩn SEO, từ khóa VIẾT HOA.',
    guide: 'AI sẽ tự động đọc toàn bộ nội dung kịch bản đã viết để sinh ra tiêu đề tối ưu nhất.',
    variables: []
  },
  slug: {
    key: 'slug',
    label: '6. URL Slug (Tên file MP4)',
    help: 'Chuỗi ký tự không dấu nối bằng dấu gạch ngang dùng làm tên file MP4 khi render.',
    guide: 'AI sẽ sinh ra chuỗi không dấu ngắn gọn (ví dụ: tam-su-hon-nhan-nguoi-thu-ba).',
    variables: []
  },
  description: {
    key: 'description',
    label: '7. Mô tả video (Description)',
    help: 'Tóm tắt nội dung câu chuyện và phân tích chuẩn SEO YouTube.',
    guide: 'Nội dung này sẽ được chèn vào biến {description} trong mẫu mô tả video xuất bản.',
    variables: []
  },
  hashtags: {
    key: 'hashtags',
    label: '8. Hashtags (cho mô tả)',
    help: '3–5 thẻ hashtag có dấu # ở đầu trên 1 dòng.',
    guide: 'Nội dung này sẽ được chèn vào biến {hashtags} trong mẫu mô tả video xuất bản.',
    variables: []
  },
  tags: {
    key: 'tags',
    label: '9. Thẻ từ khóa (Tags YouTube)',
    help: '10–15 từ khóa phân cách bằng dấu phẩy cho SEO YouTube.',
    guide: 'Các thẻ từ khóa YouTube phân cách bằng dấu phẩy, không chứa dấu #.',
    variables: []
  },
  pinned_comment: {
    key: 'pinned_comment',
    label: '10. Bình luận ghim',
    help: 'Bình luận ghim tương tác của chủ kênh để kích thích khán giả comment.',
    guide: 'Gợi mở câu hỏi hoặc thông điệp từ MC/Chủ kênh.',
    variables: []
  },
  quiz: {
    key: 'quiz',
    label: '11. Quiz tương tác',
    help: '1 câu hỏi trắc nghiệm kèm 4 đáp án và lời giải thích.',
    guide: 'Tạo câu hỏi đố vui hoặc suy ngẫm cho cộng đồng khán giả.',
    variables: []
  },
  chapters: {
    key: 'chapters',
    label: '12. Phân đoạn (Chapters)',
    help: 'Các mốc thời gian phân đoạn video (Timestamps) chuẩn định dạng YouTube.',
    guide: 'Nội dung này sẽ được chèn vào biến {chapters} trong mẫu mô tả video xuất bản.',
    variables: []
  },
  thumb_text: {
    key: 'thumb_text',
    label: '13. Thumbnail (Có chữ)',
    help: 'Ý tưởng thiết kế và prompt sinh ảnh nền thumbnail có kèm chữ clickbait nổi bật.',
    guide: 'Dùng cho ảnh đại diện video YouTube bản có chữ tiêu đề bắt mắt.',
    variables: []
  },
  thumb_notext: {
    key: 'thumb_notext',
    label: '14. Thumbnail (Không chữ)',
    help: 'Ý tưởng thiết kế và prompt sinh ảnh nền thumbnail nghệ thuật không chữ.',
    guide: 'Dùng cho ảnh đại diện video sạch hoặc dùng làm ảnh nền mở đầu Scene 0.',
    variables: []
  }
};

export default function Settings({
  lockedPromptVersion = '',
  chatGptOperation = ''
}) {
  const [promptsData, setPromptsData] = useState(null);
  const [voicesData, setVoicesData] = useState(null);
  const [youtubeChannels, setYoutubeChannels] = useState([]);
  const [pipelineNotice, setPipelineNotice] = useState('');
  const [showCheatSheet, setShowCheatSheet] = useState(false);
  const [activeVersion, setActiveVersion] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');
  const [resultSection, setResultSection] = useState('');
  const [savingSection, setSavingSection] = useState('');
  const [promptAssets, setPromptAssets] = useState([]);
  const [promptAssetsFolder, setPromptAssetsFolder] = useState('');
  const [loadingAssets, setLoadingAssets] = useState(false);
  const [uploadingAsset, setUploadingAsset] = useState(false);
  const [assetMessage, setAssetMessage] = useState('');

  const fetchPromptAssets = async (version) => {
    if (!version) return;
    setLoadingAssets(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(version)}/assets`
      );
      if (response.ok) {
        const data = await response.json();
        setPromptAssets(data.assets || []);
        setPromptAssetsFolder(data.folder_path || '');
      }
    } catch (err) {
      console.error('Lỗi khi lấy danh sách assets của prompt:', err);
    } finally {
      setLoadingAssets(false);
    }
  };

  useEffect(() => {
    if (activeVersion) {
      fetchPromptAssets(activeVersion);
    }
  }, [activeVersion]);

  const handleOpenAssetsFolder = async () => {
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/open-folder`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (data.success) {
        setAssetMessage('Đã mở thư mục trên máy tính.');
        setTimeout(() => setAssetMessage(''), 4000);
      } else {
        setAssetMessage('Không thể mở thư mục.');
        setTimeout(() => setAssetMessage(''), 4000);
      }
    } catch (err) {
      console.error('Lỗi khi mở thư mục assets:', err);
      setAssetMessage('Lỗi khi mở thư mục.');
    }
  };

  const handleUploadAsset = async (event) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    setUploadingAsset(true);
    setAssetMessage('Đang tải ảnh lên...');
    try {
      for (let i = 0; i < files.length; i++) {
        const formData = new FormData();
        formData.append('file', files[i]);
        await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/upload`,
          {
            method: 'POST',
            body: formData
          }
        );
      }
      await fetchPromptAssets(activeVersion);
      setAssetMessage(`Đã tải lên thành công ${files.length} ảnh.`);
      setTimeout(() => setAssetMessage(''), 4000);
    } catch (err) {
      console.error('Lỗi khi tải ảnh lên:', err);
      setAssetMessage('Lỗi khi tải ảnh lên.');
    } finally {
      setUploadingAsset(false);
      event.target.value = '';
    }
  };

  const handleDeleteAsset = async (filename) => {
    if (!window.confirm(`Bạn có chắc muốn xóa ảnh "${filename}" khỏi bộ prompt này?`)) return;
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/${encodeURIComponent(filename)}`,
        { method: 'DELETE' }
      );
      if (response.ok) {
        await fetchPromptAssets(activeVersion);
        setAssetMessage(`Đã xóa ảnh "${filename}".`);
        setTimeout(() => setAssetMessage(''), 3000);
      }
    } catch (err) {
      console.error('Lỗi khi xóa ảnh:', err);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [promptsResponse, voicesResponse, channelsResponse] = await Promise.all([
        fetch('http://127.0.0.1:8080/api/prompts'),
        fetch('http://127.0.0.1:8080/api/voices'),
        fetch('http://127.0.0.1:8080/api/youtube-comments/channels')
      ]);
      const prompts = await promptsResponse.json();
      const voices = await voicesResponse.json();
      const channels = await channelsResponse.json();
      setPromptsData(prompts);
      setVoicesData(voices);
      setYoutubeChannels(Array.isArray(channels.items) ? channels.items : []);
      setActiveVersion(prompts.active_version);
    } catch (err) {
      console.error("Lỗi khi lấy prompts:", err);
    }
  };

  const handlePromptChange = (key, value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          prompts: {
            ...prev.versions[activeVersion].prompts,
            [key]: value
          }
        }
      }
    }));
  };

  const handleInsertVariable = (key, tag) => {
    const currentVal = currentVersion?.prompts?.[key] || '';
    const nextVal = currentVal ? `${currentVal.trimEnd()}\n${tag}` : tag;
    handlePromptChange(key, nextVal);
  };

  const handleVersionNameChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          name: value
        }
      }
    }));
  };

  const handleProjectUrlChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          project_url: value
        }
      }
    }));
  };

  const handlePromptDefaultVoiceChange = (voiceId) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          default_voice_id: voiceId
        }
      }
    }));
  };

  const handlePromptDefaultYoutubeChannelChange = (channelId) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          default_youtube_channel_id: channelId
        }
      }
    }));
  };

  const handlePipelineChange = (stepKey, enabled) => {
    const version = promptsData.versions[activeVersion];
    const imageSettings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...version.image_generation_settings
    };
    const current = { ...DEFAULT_PIPELINE, ...version.pipeline };
    let nextPipeline = { ...current, [stepKey]: enabled };
    if (enabled) {
      const resolved = resolvePipelineDependencies(
        nextPipeline,
        imageSettings.thumbnail_variant
      );
      nextPipeline = resolved.pipeline;
      setPipelineNotice(
        resolved.enabled.length
          ? `Đã tự bật: ${resolved.enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
          : ''
      );
    } else {
      const dependents = pipelineDependents(
        current,
        stepKey,
        imageSettings.thumbnail_variant
      );
      if (dependents.length) {
        const labels = dependents.map(key => PIPELINE_STEP_LABELS[key]).join(', ');
        if (!window.confirm(`Tắt bước này cũng sẽ tắt: ${labels}. Tiếp tục?`)) return;
        dependents.forEach(key => { nextPipeline[key] = false; });
        setPipelineNotice(`Đã tắt dây chuyền: ${labels}.`);
      } else {
        setPipelineNotice('');
      }
    }
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          pipeline: nextPipeline
        }
      }
    }));
  };

  const handlePromptSettingChange = (section, field, value) => {
    setPromptsData(prev => {
      const currentSection = {
        ...(section === 'image_generation_settings'
          ? DEFAULT_IMAGE_GENERATION_SETTINGS
          : DEFAULT_PUBLISHING_SETTINGS),
        ...prev.versions[activeVersion][section],
        [field]: value
      };
      if (section === 'image_generation_settings') {
        if (field === 'negative_prompt') {
          currentSection.avoid_prompt = value;
        } else if (field === 'avoid_prompt') {
          currentSection.negative_prompt = value;
        }
      }
      return {
        ...prev,
        versions: {
          ...prev.versions,
          [activeVersion]: {
            ...prev.versions[activeVersion],
            [section]: currentSection
          }
        }
      };
    });
  };

  const handleThumbnailVariantChange = (thumbnailVariant) => {
    const version = promptsData.versions[activeVersion];
    const current = { ...DEFAULT_PIPELINE, ...version.pipeline };
    const resolved = resolvePipelineDependencies(current, thumbnailVariant);
    handlePromptSettingChange('image_generation_settings', 'thumbnail_variant', thumbnailVariant);
    if (current.youtube_upload) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [activeVersion]: {
            ...prev.versions[activeVersion],
            pipeline: resolved.pipeline
          }
        }
      }));
      setPipelineNotice(
        resolved.enabled.length
          ? `Đã tự bật thumbnail được chọn: ${resolved.enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
          : ''
      );
    }
  };

  const handleVersionChange = (e) => {
    const newVersion = e.target.value;
    setActiveVersion(newVersion);
    setPromptsData(prev => ({ ...prev, active_version: newVersion }));
  };

  const handleDuplicateVersion = () => {
    const newName = prompt("Nhập tên cho phiên bản mới:");
    if (!newName) return;
    
    const versionId = "v_" + Date.now();
    
    setPromptsData(prev => {
      const newData = { ...prev };
      const currentPrompts = { ...newData.versions[activeVersion].prompts };
      
      newData.versions[versionId] = {
        name: newName,
        project_url: newData.versions[activeVersion].project_url,
        default_voice_id:
          newData.versions[activeVersion].default_voice_id || '',
        default_youtube_channel_id:
          newData.versions[activeVersion].default_youtube_channel_id || '',
        pipeline: {
          ...DEFAULT_PIPELINE,
          ...(newData.versions[activeVersion].pipeline || {})
        },
        image_generation_settings: {
          ...DEFAULT_IMAGE_GENERATION_SETTINGS,
          ...(newData.versions[activeVersion].image_generation_settings || {})
        },
        publishing_settings: {
          ...DEFAULT_PUBLISHING_SETTINGS,
          ...(newData.versions[activeVersion].publishing_settings || {})
        },
        prompts: currentPrompts
      };
      
      newData.active_version = versionId;
      setActiveVersion(versionId);
      return newData;
    });
  };

  const handleDeleteVersion = () => {
    if (activeVersion === lockedPromptVersion) {
      alert('Bộ prompt này đang được job sử dụng nên chưa thể xóa.');
      return;
    }
    if (Object.keys(promptsData.versions).length <= 1) {
      alert("Không thể xóa phiên bản duy nhất!");
      return;
    }
    
    if (!confirm("Bạn có chắc muốn xóa phiên bản này?")) return;
    
    setPromptsData(prev => {
      const newData = { ...prev };
      delete newData.versions[activeVersion];
      
      const remainingVersions = Object.keys(newData.versions);
      const nextVersion = remainingVersions[0];
      
      newData.active_version = nextVersion;
      setActiveVersion(nextVersion);
      return newData;
    });
  };

  const saveSection = async (sectionKey, loadingText, successText, request) => {
    try {
      setSavingSection(sectionKey);
      setLoadingMsg(loadingText);
      setResultMsg('');
      setResultSection(sectionKey);
      const response = await request();
      const contentType = response.headers.get('content-type') || '';
      const result = contentType.includes('application/json')
        ? await response.json()
        : { detail: await response.text() };
      if (!response.ok) {
        const compatibilityHint = response.status === 404
          ? ' Backend chưa nạp phiên bản API mới; hãy khởi động lại hệ thống.'
          : '';
        throw new Error(
          (result.detail || `Không thể lưu thiết lập (${response.status}).`) +
          compatibilityHint
        );
      }
      setResultMsg(`✅ ${successText}`);
      return result;
    } catch (err) {
      setResultMsg('❌ ' + err.message);
      return null;
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  const handleSaveVersionName = async () => {
    const versionId = activeVersion;
    const name = promptsData.versions[versionId].name;
    const result = await saveSection(
      'version-name',
      'Đang lưu tên bộ prompt...',
      'Đã lưu tên bộ prompt.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/name`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            name: result.version.name
          }
        }
      }));
    }
  };

  const handleSaveProject = () => {
    const versionId = activeVersion;
    const projectUrl = promptsData.versions[versionId].project_url;
    return saveSection(
      'project',
      'Đang lưu ChatGPT Project...',
      'Đã lưu ChatGPT Project cho bộ prompt này.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/project`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_url: projectUrl })
      })
    );
  };

  const handleSavePromptDefaultVoice = () => {
    const versionId = activeVersion;
    const voiceId = promptsData.versions[versionId].default_voice_id || '';
    return saveSection(
      'prompt-default-voice',
      'Đang lưu giọng mặc định của bộ prompt...',
      'Đã lưu giọng mặc định của bộ prompt.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/default-voice`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ voice_id: voiceId })
        }
      )
    );
  };

  const handleContentModeChange = (mode) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          content_mode: mode
        }
      }
    }));
    fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/content-mode`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content_mode: mode })
    }).catch(() => {});
  };

  const handleCastSettingChange = (role, field, value) => {
    setPromptsData(prev => {
      const version = prev.versions[activeVersion] || {};
      const cast = { ...(version.cast_settings || {}) };
      if (role === 'turn_pause_seconds') {
        cast.turn_pause_seconds = Number(value);
      } else {
        cast[role] = { ...(cast[role] || {}), [field]: value };
      }
      return {
        ...prev,
        versions: {
          ...prev.versions,
          [activeVersion]: {
            ...version,
            cast_settings: cast
          }
        }
      };
    });
  };

  const handleSaveCastSettings = () => {
    const versionId = activeVersion;
    const castSettings = promptsData.versions[versionId]?.cast_settings || {};
    return saveSection(
      'cast-settings',
      'Đang lưu cấu hình dàn vai và giọng đối thoại...',
      'Đã lưu cấu hình dàn vai và giọng đối thoại.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/cast-settings`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cast_settings: castSettings })
        }
      )
    );
  };

  const handleSavePromptDefaultYoutubeChannel = async () => {
    const versionId = activeVersion;
    const channelId = (
      promptsData.versions[versionId].default_youtube_channel_id || ''
    );
    const result = await saveSection(
      'prompt-default-youtube-channel',
      'Đang lưu kênh YouTube mặc định của bộ prompt...',
      'Đã lưu kênh YouTube mặc định của bộ prompt.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/default-youtube-channel`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ channel_id: channelId })
        }
      )
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: result.version
        }
      }));
    }
  };

  const handleSavePipeline = async () => {
    const versionId = activeVersion;
    const pipeline = {
      ...DEFAULT_PIPELINE,
      ...promptsData.versions[versionId].pipeline
    };
    const result = await saveSection(
      'pipeline',
      'Đang lưu pipeline của bộ prompt...',
      'Đã lưu pipeline cho bộ prompt này.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/pipeline`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pipeline)
        }
      )
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            pipeline: result.pipeline
          }
        }
      }));
      const notices = [];
      if (Array.isArray(result.auto_enabled) && result.auto_enabled.length) {
        notices.push(
          `Backend đã tự bật: ${result.auto_enabled.map(key => PIPELINE_STEP_LABELS[key]).join(', ')}.`
        );
      }
      if (result.ready === false) {
        const missing = (result.missing_configuration || [])
          .map(key => PUBLISH_CONFIGURATION_LABELS[key] || key)
          .join(', ');
        notices.push(`Chưa sẵn sàng chạy tự động: ${missing}.`);
      } else if (result.pipeline?.youtube_upload) {
        notices.push('Cấu hình upload/đặt lịch đã sẵn sàng; artifact sẽ được kiểm tra lại khi job chạy.');
      }
      setPipelineNotice(notices.join(' '));
    }
  };

  const handleSaveImageGeneration = async () => {
    const versionId = activeVersion;
    const currentImg = promptsData.versions[versionId]?.image_generation_settings || {};
    const negPrompt = currentImg.negative_prompt ?? currentImg.avoid_prompt ?? '';
    const settings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...currentImg,
      negative_prompt: negPrompt,
      avoid_prompt: negPrompt
    };
    const result = await saveSection(
      'image-generation',
      'Đang lưu cấu hình tạo ảnh và thumbnail...',
      'Đã lưu cấu hình tạo ảnh và thumbnail.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/image-generation`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(settings)
        }
      )
    );
    if (result?.version && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: { ...prev.versions, [versionId]: result.version }
      }));
    }
  };

  const handleSavePublishing = async () => {
    const versionId = activeVersion;
    const currentImg = promptsData.versions[versionId]?.image_generation_settings || {};
    const negPrompt = currentImg.negative_prompt ?? currentImg.avoid_prompt ?? '';
    const imgSettings = {
      ...DEFAULT_IMAGE_GENERATION_SETTINGS,
      ...currentImg,
      negative_prompt: negPrompt,
      avoid_prompt: negPrompt
    };
    const settings = {
      ...DEFAULT_PUBLISHING_SETTINGS,
      ...promptsData.versions[versionId].publishing_settings,
      contains_synthetic_media: true
    };
    const result = await saveSection(
      'publishing',
      'Đang lưu cấu hình đăng YouTube...',
      'Đã lưu cấu hình đăng YouTube.',
      async () => {
        await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/image-generation`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(imgSettings)
          }
        );
        return fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/publishing`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
          }
        );
      }
    );
    if (result?.version && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: { ...prev.versions, [versionId]: result.version }
      }));
    }
  };

  const handleSavePrompt = (promptKey, promptLabel) => {
    const versionId = activeVersion;
    const value = promptsData.versions[versionId].prompts[promptKey] || '';
    return saveSection(
      `prompt-${promptKey}`,
      `Đang lưu ${promptLabel}...`,
      `Đã lưu ${promptLabel}.`,
      async () => {
        const res = await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(promptKey)}`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value })
          }
        );
        if (!res.ok) throw new Error("Failed to save text");

        if (promptKey === 'thumb_text' || promptKey === 'thumb_notext') {
          const imageKey = `${promptKey}_image_base64`;
          const imageVal = promptsData.versions[versionId].prompts[imageKey] || '';
          const res2 = await fetch(
            `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(imageKey)}`,
            {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ value: imageVal })
            }
          );
          if (!res2.ok) throw new Error("Failed to save image");
        }
        return res;
      }
    );
  };

  const handleSave = async () => {
    try {
      setSavingSection('all');
      setLoadingMsg('Đang lưu thiết lập...');
      setResultMsg('');

      const promptsResponse = await fetch('http://127.0.0.1:8080/api/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptsData)
      });
      if (!promptsResponse.ok) {
        const promptsResult = await promptsResponse.json();
        throw new Error(promptsResult.detail || 'Không thể lưu cấu hình prompt.');
      }
      setResultMsg(
        lockedPromptVersion
          ? '✅ Đã lưu các thiết lập khác. Bộ prompt đang chạy được giữ nguyên.'
          : '✅ Đã lưu cấu hình Prompt và trình duyệt thành công!'
      );
    } catch (err) {
      setResultMsg('❌ ' + err.message);
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  if (!promptsData || !voicesData || !promptsData.versions[activeVersion]) {
    return <div style={{padding: '20px', color: 'white'}}>Loading Settings...</div>;
  }

  const currentVersion = promptsData.versions[activeVersion];
  const activeVersionLocked = Boolean(
    lockedPromptVersion && activeVersion === lockedPromptVersion
  );
  const lockedVersionName = lockedPromptVersion
    ? promptsData.versions[lockedPromptVersion]?.name || lockedPromptVersion
    : '';
  const globalDefaultVoice = voicesData.voices.find(
    voice => voice.id === voicesData.active_voice_id
  );
  const promptDefaultVoiceId = currentVersion.default_voice_id || '';
  const promptDefaultVoiceMissing = Boolean(
    promptDefaultVoiceId &&
    !voicesData.voices.some(voice => voice.id === promptDefaultVoiceId)
  );
  const promptDefaultYoutubeChannelId = (
    currentVersion.default_youtube_channel_id || ''
  );
  const promptDefaultYoutubeChannelMissing = Boolean(
    promptDefaultYoutubeChannelId &&
    !youtubeChannels.some(
      channel => channel.channel_id === promptDefaultYoutubeChannelId
    )
  );
  const currentPipeline = {
    ...DEFAULT_PIPELINE,
    ...currentVersion.pipeline
  };
  const currentImageGeneration = {
    ...DEFAULT_IMAGE_GENERATION_SETTINGS,
    ...currentVersion.image_generation_settings,
    negative_prompt: (
      currentVersion.image_generation_settings?.negative_prompt ??
      currentVersion.image_generation_settings?.avoid_prompt ??
      ''
    ),
    avoid_prompt: (
      currentVersion.image_generation_settings?.avoid_prompt ??
      currentVersion.image_generation_settings?.negative_prompt ??
      ''
    )
  };
  const currentPublishing = {
    ...DEFAULT_PUBLISHING_SETTINGS,
    ...currentVersion.publishing_settings
  };

  const promptFields = [
    { key: 'outline', label: '1. Dàn ý (Outline)', help: 'Biến có sẵn: {transcript}' },
    { key: 'intro', label: '2. Mở đầu (Intro)' },
    { key: 'body', label: '3. Nội dung chính (Body)', help: 'Biến có sẵn: {part}' },
    { key: 'outro', label: '4. Kết thúc (Outro)' },
    { key: 'title', label: '5. Tiêu đề (Title)' },
    { key: 'slug', label: '6. URL Slug (Dùng đặt tên file render MP4)' },
    { key: 'description', label: '7. Mô tả video (Description)' },
    { key: 'hashtags', label: '8. Hashtags (cho mô tả)' },
    { key: 'tags', label: '9. Thẻ từ khóa (Tags YouTube)' },
    { key: 'pinned_comment', label: '10. Bình luận ghim' },
    { key: 'quiz', label: '11. Quiz tương tác' },
    { key: 'chapters', label: '12. Phân đoạn (Chapters)' },
    { key: 'thumb_text', label: '13. Thumbnail (Có chữ)' },
    { key: 'thumb_notext', label: '14. Thumbnail (Không chữ)' },
  ];


  const handleImageUpload = (e, imageKey) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    let processedCount = 0;
    const newBase64s = [];

    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = (event) => {
        const base64 = event.target.result.split(',')[1];
        newBase64s.push(base64);
        processedCount++;

        if (processedCount === files.length) {
          const finalArray = [...currentArray, ...newBase64s];
          handlePromptChange(imageKey, JSON.stringify(finalArray));
        }
      };
      reader.readAsDataURL(file);
    });
  };

  const handleRemoveImage = (imageKey, indexToRemove) => {
    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    if (indexToRemove === -1) {
      handlePromptChange(imageKey, '');
    } else {
      currentArray.splice(indexToRemove, 1);
      if (currentArray.length === 0) {
        handlePromptChange(imageKey, '');
      } else {
        handlePromptChange(imageKey, JSON.stringify(currentArray));
      }
    }
  };

  const renderImagePreviews = (fieldKey) => {
    const imageKey = `${fieldKey}_image_base64`;
    const existing = currentVersion.prompts[imageKey];
    let images = [];
    if (existing) {
      if (existing.startsWith('[')) {
        try {
          images = JSON.parse(existing);
        } catch {
          images = [existing];
        }
      } else {
        images = [existing];
      }
    }

    if (images.length === 0) {
      return (
        <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', textAlign: 'center', color: '#888', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '5px', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
          <span style={{ fontSize: '2em' }}>🖼️</span>
          <span style={{ fontSize: '0.8em' }}>Tải ảnh mẫu lên</span>
          <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
        </label>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%' }}>
        <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', justifyContent: 'center' }}>
          {images.map((b64, idx) => (
            <div key={idx} style={{ position: 'relative' }}>
              <img
                src={`data:image/png;base64,${b64}`}
                alt="Reference"
                style={{ width: '45px', height: '45px', objectFit: 'cover', borderRadius: '4px', border: '1px solid #444' }}
              />
              <button
                onClick={() => handleRemoveImage(imageKey, idx)}
                disabled={activeVersionLocked}
                style={{ position: 'absolute', top: '-5px', right: '-5px', background: '#e74c3c', border: 'none', color: 'white', borderRadius: '50%', width: '16px', height: '16px', fontSize: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', padding: 0 }}
                title="Xóa ảnh này"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '5px', justifyContent: 'center', marginTop: '4px' }}>
          <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', background: 'var(--accent)', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            + Thêm
            <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
          </label>
          <button onClick={() => handleRemoveImage(imageKey, -1)} disabled={activeVersionLocked} style={{ background: '#e74c3c', border: 'none', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            Xóa hết
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="settings-container">
      <div className="settings-header">
        <div>
          <h1 className="hero-title">Prompt Management</h1>
          <p className="hero-subtitle">Quản lý và chỉnh sửa các lệnh AI hệ thống sử dụng.</p>
        </div>
      </div>

      {lockedPromptVersion && (
        <div className="prompt-lock-notice" role="status">
          🔒 Job {chatGptOperation || 'ChatGPT'} đang dùng bộ prompt
          <strong> {lockedVersionName}</strong>. Chỉ bộ này tạm khóa; bạn vẫn có
          thể quản lý các bộ prompt khác và danh sách giọng đọc.
        </div>
      )}

      <div className="version-control">
        <div className="version-editor">
          <label style={{color: '#fff', fontWeight: 'bold'}}>Phiên bản hiện tại:</label>
          <select value={activeVersion} onChange={handleVersionChange} className="version-select">
            {Object.entries(promptsData.versions).map(([key, version]) => (
              <option key={key} value={key}>{version.name}</option>
            ))}
          </select>
          <input
            value={currentVersion.name}
            onChange={(event) => handleVersionNameChange(event.target.value)}
            placeholder="Tên bộ prompt"
            className="version-select version-name-input"
            maxLength={100}
            disabled={activeVersionLocked}
          />
          <button
            className="btn-save section-save-button"
            onClick={handleSaveVersionName}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu tên
          </button>
        </div>
        
        <div className="version-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setShowCheatSheet(true)}
            title="Xem bảng tra cứu đầy đủ biến số và quy chuẩn viết prompt"
            style={{ background: 'rgba(56, 189, 248, 0.15)', borderColor: 'rgba(56, 189, 248, 0.35)', color: '#38bdf8' }}
          >
            📖 Tra Cứu Biến & Cú Pháp
          </button>
          <button
            className="btn-save btn-save-all"
            onClick={handleSave}
            disabled={Boolean(savingSection)}
            title="Lưu tất cả thay đổi trên toàn bộ trang"
          >
            💾 {savingSection === 'all' ? 'Đang lưu...' : 'Lưu Tất Cả'}
          </button>
          <button className="btn-secondary" onClick={handleDuplicateVersion}>➕ Tạo Bản Sao</button>
          <button
            className="btn-danger"
            onClick={handleDeleteVersion}
            disabled={activeVersionLocked}
            title={
              activeVersionLocked
                ? 'Bộ prompt này đang được job sử dụng'
                : 'Xóa bộ prompt hiện tại'
            }
          >
            🗑️ Xóa Bản Này
          </button>
        </div>
      </div>

      {showCheatSheet && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(5px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 9999,
          padding: '20px'
        }}>
          <div style={{
            background: '#0f172a',
            border: '1px solid rgba(255, 255, 255, 0.15)',
            borderRadius: '12px',
            maxWidth: '860px',
            width: '100%',
            maxHeight: '90vh',
            overflowY: 'auto',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.8)',
            padding: '24px',
            color: '#f8fafc'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, borderBottom: '1px solid rgba(255, 255, 255, 0.1)', paddingBottom: 12 }}>
              <h3 style={{ margin: 0, fontSize: '1.25rem', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: 8 }}>
                📖 Bảng Tra Cứu Biến Số & Cú Pháp Chuẩn
              </h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowCheatSheet(false)}
                style={{ padding: '4px 12px', fontSize: '0.85rem' }}
              >
                ✕ Đóng
              </button>
            </div>

            {/* Section 1: Cast Role Tags */}
            <div style={{ marginBottom: 20 }}>
              <h4 style={{ color: '#fbbf24', margin: '0 0 8px 0', fontSize: '1rem' }}>
                👥 1. Thẻ Định Danh Vai Diễn (Chế độ Đối Thoại - Dùng trong Intro, Body, Outro)
              </h4>
              <div style={{ background: 'rgba(255, 255, 255, 0.04)', borderRadius: 8, padding: 12, border: '1px solid rgba(255, 255, 255, 0.08)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.1)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Thẻ vai diễn</th>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Ý nghĩa & Vai trò</th>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Ghi chú bắt buộc</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                      <td style={{ padding: '8px', color: '#fbbf24', fontFamily: 'monospace', fontWeight: 'bold' }}>[MC]:</td>
                      <td style={{ padding: '8px' }}>Lượt nói của Host / MC (Tiến sĩ Đinh Đoàn)</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Đặt ở đầu dòng mỗi lượt thoại của MC</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                      <td style={{ padding: '8px', color: '#34d399', fontFamily: 'monospace', fontWeight: 'bold' }}>[KHACH_1]:</td>
                      <td style={{ padding: '8px' }}>Lượt nói của Khách Mời Chính (Người kể chuyện)</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Cho phép kể liên tục đoạn dài không giới hạn</td>
                    </tr>
                    <tr>
                      <td style={{ padding: '8px', color: '#f472b6', fontFamily: 'monospace', fontWeight: 'bold' }}>[KHACH_2]:</td>
                      <td style={{ padding: '8px' }}>Lượt nói của Khách Mời 2 (Người thứ ba / Chuyên gia phụ)</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Tùy chọn khi kịch bản có 3 nhân vật</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* Section 2: Script Dynamic Variables */}
            <div style={{ marginBottom: 20 }}>
              <h4 style={{ color: '#38bdf8', margin: '0 0 8px 0', fontSize: '1rem' }}>
                🔄 2. Biến Dữ Liệu Tự Động (Dùng trong ChatGPT Prompt)
              </h4>
              <div style={{ background: 'rgba(255, 255, 255, 0.04)', borderRadius: 8, padding: 12, border: '1px solid rgba(255, 255, 255, 0.08)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.1)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Biến số</th>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Áp dụng tại Prompt</th>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Dữ liệu tự động chèn vào</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                      <td style={{ padding: '8px', color: '#38bdf8', fontFamily: 'monospace', fontWeight: 'bold' }}>{'{transcript}'}</td>
                      <td style={{ padding: '8px' }}>1. Dàn ý (Outline)</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Toàn bộ phụ đề/văn bản thô của video nguồn để lập dàn ý</td>
                    </tr>
                    <tr>
                      <td style={{ padding: '8px', color: '#38bdf8', fontFamily: 'monospace', fontWeight: 'bold' }}>{'{part}'}</td>
                      <td style={{ padding: '8px' }}>3. Thân bài (Body)</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Từng phần [PHAN] trong dàn ý khi hệ thống viết từng đoạn kịch bản</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* Section 3: Publishing Template Variables */}
            <div style={{ marginBottom: 20 }}>
              <h4 style={{ color: '#a78bfa', margin: '0 0 8px 0', fontSize: '1rem' }}>
                📤 3. Biến Mẫu Mô Tả YouTube (Description Template)
              </h4>
              <div style={{ background: 'rgba(255, 255, 255, 0.04)', borderRadius: 8, padding: 12, border: '1px solid rgba(255, 255, 255, 0.08)' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.1)', textAlign: 'left' }}>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Biến số</th>
                      <th style={{ padding: '6px 8px', color: '#94a3b8' }}>Ý nghĩa</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                      <td style={{ padding: '8px', color: '#a78bfa', fontFamily: 'monospace', fontWeight: 'bold' }}>{'{description}'}</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Đoạn mô tả tóm tắt chuẩn SEO YouTube từ prompt 7</td>
                    </tr>
                    <tr style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.05)' }}>
                      <td style={{ padding: '8px', color: '#a78bfa', fontFamily: 'monospace', fontWeight: 'bold' }}>{'{chapters}'}</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Danh sách mốc thời gian (00:00 - Tiêu đề...) từ prompt 12</td>
                    </tr>
                    <tr>
                      <td style={{ padding: '8px', color: '#a78bfa', fontFamily: 'monospace', fontWeight: 'bold' }}>{'{hashtags}'}</td>
                      <td style={{ padding: '8px', color: '#cbd5e1' }}>Danh sách 3–5 thẻ # từ prompt 8</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            {/* Section 4: Scene Image/Video Variables */}
            <div style={{ marginBottom: 12 }}>
              <h4 style={{ color: '#ec4899', margin: '0 0 8px 0', fontSize: '1rem' }}>
                🖼️ 4. Biến Sinh Ảnh & Video Phân Cảnh (Image / Video Templates)
              </h4>
              <div style={{ background: 'rgba(255, 255, 255, 0.04)', borderRadius: 8, padding: 12, border: '1px solid rgba(255, 255, 255, 0.08)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 10, fontSize: '0.83rem' }}>
                  <div><code style={{ color: '#ec4899' }}>{'{style}'}</code>: Phong cách ảnh chung</div>
                  <div><code style={{ color: '#ec4899' }}>{'{reference}'}</code>: Ảnh tham chiếu nhân vật</div>
                  <div><code style={{ color: '#ec4899' }}>{'{thumbnail_concept}'}</code>: Ý tưởng cốt lõi câu chuyện</div>
                  <div><code style={{ color: '#ec4899' }}>{'{action}'}</code>: Hành động diễn biến phân cảnh</div>
                  <div><code style={{ color: '#ec4899' }}>{'{scene_index}'}</code>: Số thứ tự cảnh (1, 2, 3...)</div>
                  <div><code style={{ color: '#ec4899' }}>{'{motion}'}</code>: Lệnh chuyển động camera</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Content Mode Selector */}
      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🎭 Loại hình kịch bản video (Content Mode)</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Chọn giữa chế độ Đối thoại nhiều giọng (1 MC cố định + Khách mời linh hoạt) và Đơn thoại (1 người kể chuyện truyền thống).
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              cursor: activeVersionLocked ? 'not-allowed' : 'pointer',
              background: (currentVersion.content_mode || 'dialogue') === 'dialogue' ? 'rgba(56, 189, 248, 0.15)' : 'rgba(255, 255, 255, 0.03)',
              padding: '10px 16px',
              borderRadius: 8,
              border: (currentVersion.content_mode || 'dialogue') === 'dialogue' ? '1px solid #38bdf8' : '1px solid rgba(255, 255, 255, 0.1)'
            }}
          >
            <input
              type="radio"
              name="content_mode"
              value="dialogue"
              checked={(currentVersion.content_mode || 'dialogue') === 'dialogue'}
              onChange={() => handleContentModeChange('dialogue')}
              disabled={activeVersionLocked}
            />
            <span><strong>👥 Chế độ Đối thoại</strong> (1 MC cố định + Khách mời thay đổi)</span>
          </label>
          <label
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              cursor: activeVersionLocked ? 'not-allowed' : 'pointer',
              background: currentVersion.content_mode === 'monologue' ? 'rgba(56, 189, 248, 0.15)' : 'rgba(255, 255, 255, 0.03)',
              padding: '10px 16px',
              borderRadius: 8,
              border: currentVersion.content_mode === 'monologue' ? '1px solid #38bdf8' : '1px solid rgba(255, 255, 255, 0.1)'
            }}
          >
            <input
              type="radio"
              name="content_mode"
              value="monologue"
              checked={currentVersion.content_mode === 'monologue'}
              onChange={() => handleContentModeChange('monologue')}
              disabled={activeVersionLocked}
            />
            <span><strong>🎙️ Chế độ Đơn thoại</strong> (1 giọng đọc kể chuyện truyền thống)</span>
          </label>
        </div>
      </div>

      {/* Cast Management for Dialogue Mode */}
      {(currentVersion.content_mode || 'dialogue') === 'dialogue' ? (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>🎭 Dàn nhân vật & Giọng đọc đối thoại (Cast Profiles)</label>
              <div className="help-text" style={{ marginTop: '5px' }}>
                Cấu hình MC cố định của kênh cùng các vai khách mời. Video Fetcher sẽ tự động nạp giọng MC và cho phép bạn chọn nhanh giọng khách mời theo từng tập.
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSaveCastSettings}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu Dàn Vai
            </button>
          </div>

          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* MC Profile Card */}
            {(() => {
              const cast = currentVersion.cast_settings || {};
              const mc = cast.mc || {
                role_tag: '[MC]',
                display_name: 'Tiến sĩ Đinh Đoàn',
                default_voice_id: currentVersion.default_voice_id || '',
                subtitle_color: '#FFD700',
                persona: 'Chuyên gia tâm lý Đinh Đoàn, người dẫn dắt thông thái, phân tích tâm lý, chia sẻ và đúc kết bài học.'
              };
              const guest1 = cast.guest_1 || {
                role_tag: '[KHACH_1]',
                display_name: 'Khách Mời Chính',
                default_voice_id: '',
                subtitle_color: '#00E5FF',
                persona: 'Người trong cuộc kể lại câu chuyện tâm sự chi tiết, có thể chia sẻ một mạch câu chuyện dài đầy đủ cảm xúc.'
              };
              const guest2 = cast.guest_2 || {
                enabled: false,
                role_tag: '[KHACH_2]',
                display_name: 'Khách Mời 2',
                default_voice_id: '',
                subtitle_color: '#FF80AB',
                persona: 'Chuyên gia bổ sung hoặc nhân vật thứ ba trong câu chuyện.'
              };
              const turnPause = cast.turn_pause_seconds ?? 0.35;

              return (
                <>
                  {/* MC Profile */}
                  <div style={{ background: 'rgba(255, 215, 0, 0.05)', border: '1px solid rgba(255, 215, 0, 0.25)', borderRadius: 8, padding: '12px 14px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                      <strong style={{ color: '#ffd700', fontSize: '0.95rem' }}>👑 VAI 1: MC / HOST (Cố định của kênh)</strong>
                      <span style={{ fontSize: '0.78rem', color: '#cbd5e1', background: 'rgba(255, 215, 0, 0.15)', padding: '2px 8px', borderRadius: 4 }}>Thẻ kịch bản: <code>{mc.role_tag || '[MC]'}</code></span>
                    </div>
                    <div className="production-settings-grid" style={{ marginBottom: 8 }}>
                      <label>
                        Tên hiển thị nhân vật MC
                        <input
                          className="version-select"
                          value={mc.display_name || ''}
                          onChange={e => handleCastSettingChange('mc', 'display_name', e.target.value)}
                          placeholder="Tiến sĩ Đinh Đoàn"
                          disabled={activeVersionLocked}
                        />
                      </label>
                      <label>
                        Giọng đọc mặc định của MC
                        <select
                          className="version-select"
                          value={mc.default_voice_id || promptDefaultVoiceId}
                          onChange={e => {
                            handleCastSettingChange('mc', 'default_voice_id', e.target.value);
                            handlePromptDefaultVoiceChange(e.target.value);
                          }}
                          disabled={activeVersionLocked}
                        >
                          <option value="">Chọn giọng cho MC</option>
                          <ProviderVoiceOptions voices={voicesData.voices} />
                        </select>
                      </label>
                      <label>
                        Màu phụ đề (Subtitle Color)
                        <input
                          type="text"
                          className="version-select"
                          value={mc.subtitle_color || '#FFD700'}
                          onChange={e => handleCastSettingChange('mc', 'subtitle_color', e.target.value)}
                          placeholder="#FFD700"
                          disabled={activeVersionLocked}
                        />
                      </label>
                    </div>
                    <div>
                      <label style={{ fontSize: '0.84rem', color: '#94a3b8', display: 'block', marginBottom: 4 }}>Phong cách / Persona MC:</label>
                      <textarea
                        className="prompt-textarea"
                        rows={2}
                        value={mc.persona || ''}
                        onChange={e => handleCastSettingChange('mc', 'persona', e.target.value)}
                        placeholder="Mô tả tính cách và phong cách của MC..."
                        disabled={activeVersionLocked}
                      />
                    </div>
                  </div>

                  {/* Guest 1 Profile */}
                  <div style={{ background: 'rgba(0, 229, 255, 0.05)', border: '1px solid rgba(0, 229, 255, 0.25)', borderRadius: 8, padding: '12px 14px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                      <strong style={{ color: '#00e5ff', fontSize: '0.95rem' }}>🎙️ VAI 2: KHÁCH MỜI CHÍNH (Thay đổi theo từng tập)</strong>
                      <span style={{ fontSize: '0.78rem', color: '#cbd5e1', background: 'rgba(0, 229, 255, 0.15)', padding: '2px 8px', borderRadius: 4 }}>Thẻ kịch bản: <code>{guest1.role_tag || '[KHACH_1]'}</code></span>
                    </div>
                    <div className="production-settings-grid" style={{ marginBottom: 8 }}>
                      <label>
                        Tên gợi ý vai khách mời
                        <input
                          className="version-select"
                          value={guest1.display_name || ''}
                          onChange={e => handleCastSettingChange('guest_1', 'display_name', e.target.value)}
                          placeholder="Khách Mời Chính"
                          disabled={activeVersionLocked}
                        />
                      </label>
                      <label>
                        Giọng đọc mẫu / gợi ý ban đầu
                        <select
                          className="version-select"
                          value={guest1.default_voice_id || ''}
                          onChange={e => handleCastSettingChange('guest_1', 'default_voice_id', e.target.value)}
                          disabled={activeVersionLocked}
                        >
                          <option value="">Chọn giọng mẫu cho Khách 1</option>
                          <ProviderVoiceOptions voices={voicesData.voices} />
                        </select>
                      </label>
                      <label>
                        Màu phụ đề (Subtitle Color)
                        <input
                          type="text"
                          className="version-select"
                          value={guest1.subtitle_color || '#00E5FF'}
                          onChange={e => handleCastSettingChange('guest_1', 'subtitle_color', e.target.value)}
                          placeholder="#00E5FF"
                          disabled={activeVersionLocked}
                        />
                      </label>
                    </div>
                    <div>
                      <label style={{ fontSize: '0.84rem', color: '#94a3b8', display: 'block', marginBottom: 4 }}>Phong cách / Persona Khách Mời 1 (Có thể kể câu chuyện dài):</label>
                      <textarea
                        className="prompt-textarea"
                        rows={2}
                        value={guest1.persona || ''}
                        onChange={e => handleCastSettingChange('guest_1', 'persona', e.target.value)}
                        placeholder="Mô tả vai trò của khách mời..."
                        disabled={activeVersionLocked}
                      />
                    </div>
                  </div>

                  {/* Guest 2 Profile */}
                  <div style={{ background: 'rgba(255, 128, 171, 0.05)', border: '1px solid rgba(255, 128, 171, 0.25)', borderRadius: 8, padding: '12px 14px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', margin: 0 }}>
                        <input
                          type="checkbox"
                          checked={Boolean(guest2.enabled)}
                          onChange={e => handleCastSettingChange('guest_2', 'enabled', e.target.checked)}
                          disabled={activeVersionLocked}
                        />
                        <strong style={{ color: '#ff80ab', fontSize: '0.95rem' }}>🎙️ VAI 3: KHÁCH MỜI 2 / PHỤ (Kịch bản 3 người)</strong>
                      </label>
                      <span style={{ fontSize: '0.78rem', color: '#cbd5e1', background: 'rgba(255, 128, 171, 0.15)', padding: '2px 8px', borderRadius: 4 }}>Thẻ kịch bản: <code>{guest2.role_tag || '[KHACH_2]'}</code></span>
                    </div>
                    {guest2.enabled && (
                      <>
                        <div className="production-settings-grid" style={{ marginBottom: 8 }}>
                          <label>
                            Tên gợi ý vai khách mời 2
                            <input
                              className="version-select"
                              value={guest2.display_name || ''}
                              onChange={e => handleCastSettingChange('guest_2', 'display_name', e.target.value)}
                              placeholder="Chuyên gia / Khách Mời 2"
                              disabled={activeVersionLocked}
                            />
                          </label>
                          <label>
                            Giọng đọc mẫu
                            <select
                              className="version-select"
                              value={guest2.default_voice_id || ''}
                              onChange={e => handleCastSettingChange('guest_2', 'default_voice_id', e.target.value)}
                              disabled={activeVersionLocked}
                            >
                              <option value="">Chọn giọng cho Khách 2</option>
                              <ProviderVoiceOptions voices={voicesData.voices} />
                            </select>
                          </label>
                          <label>
                            Màu phụ đề
                            <input
                              type="text"
                              className="version-select"
                              value={guest2.subtitle_color || '#FF80AB'}
                              onChange={e => handleCastSettingChange('guest_2', 'subtitle_color', e.target.value)}
                              placeholder="#FF80AB"
                              disabled={activeVersionLocked}
                            />
                          </label>
                        </div>
                        <div>
                          <label style={{ fontSize: '0.84rem', color: '#94a3b8', display: 'block', marginBottom: 4 }}>Persona Khách 2:</label>
                          <textarea
                            className="prompt-textarea"
                            rows={2}
                            value={guest2.persona || ''}
                            onChange={e => handleCastSettingChange('guest_2', 'persona', e.target.value)}
                            placeholder="Mô tả vai trò của khách mời thứ 2..."
                            disabled={activeVersionLocked}
                          />
                        </div>
                      </>
                    )}
                  </div>

                  {/* Turn Pause Setting */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 12px', background: 'rgba(255, 255, 255, 0.03)', borderRadius: 6 }}>
                    <label style={{ margin: 0, fontWeight: 600, color: '#e2e8f0', fontSize: '0.88rem' }}>
                      ⏱️ Khoảng lặng khi đổi lượt nói giữa 2 nhân vật (Turn Pause):
                    </label>
                    <input
                      type="number"
                      min="0.1"
                      max="1.5"
                      step="0.05"
                      style={{ width: '80px', padding: '4px 8px', borderRadius: 4, background: '#1e293b', border: '1px solid #475569', color: '#fff' }}
                      value={turnPause}
                      onChange={e => handleCastSettingChange('turn_pause_seconds', null, e.target.value)}
                      disabled={activeVersionLocked}
                    />
                    <span style={{ fontSize: '0.8rem', color: '#94a3b8' }}>giây (mặc định 0.35s tạo nhịp đàm thoại tự nhiên)</span>
                  </div>
                </>
              );
            })()}
          </div>
        </div>
      ) : (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>🎙️ Giọng mặc định của bộ prompt (Đơn thoại)</label>
              <div className="help-text" style={{ marginTop: '5px' }}>
                Video Fetcher sẽ tự chọn giọng này khi bạn chọn bộ prompt. Bạn vẫn có thể đổi giọng thủ công trước khi tạo từng video.
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSavePromptDefaultVoice}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu
            </button>
          </div>
          <select
            value={promptDefaultVoiceId}
            onChange={(event) => handlePromptDefaultVoiceChange(event.target.value)}
            className="version-select"
            style={{ width: '100%', marginTop: '12px' }}
            disabled={activeVersionLocked}
          >
            <option value="">
              Dùng giọng mặc định chung
              {globalDefaultVoice ? ` — ${globalDefaultVoice.name}` : ''}
            </option>
            {promptDefaultVoiceMissing && (
              <option value={promptDefaultVoiceId}>
                ⚠️ Giọng đã bị xóa — sẽ dùng giọng mặc định chung
              </option>
            )}
            <ProviderVoiceOptions voices={voicesData.voices} />
          </select>
        </div>
      )}

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🌐 ChatGPT Project viết kịch bản</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Mỗi bộ prompt có thể lưu kịch bản vào một ChatGPT Project riêng.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSaveProject}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <input
          value={currentVersion.project_url || ''}
          onChange={(event) => handleProjectUrlChange(event.target.value)}
          placeholder="https://chatgpt.com/g/g-p-.../project"
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        />
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>📺 Kênh YouTube mặc định của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Mọi video cũ và mới thuộc bộ prompt này sẽ tự chọn kênh này khi
              gắn link đã đăng và đồng bộ bình luận.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePromptDefaultYoutubeChannel}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <select
          value={promptDefaultYoutubeChannelId}
          onChange={(event) => handlePromptDefaultYoutubeChannelChange(event.target.value)}
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        >
          <option value="">Chưa chọn kênh mặc định</option>
          {promptDefaultYoutubeChannelMissing && (
            <option value={promptDefaultYoutubeChannelId}>
              ⚠️ Kênh đã ngắt kết nối — hãy chọn lại
            </option>
          )}
          {youtubeChannels.map(channel => (
            <option key={channel.channel_id} value={channel.channel_id}>
              {channel.title}
            </option>
          ))}
        </select>
        {!youtubeChannels.length && (
          <div className="help-text" style={{ marginTop: 8, color: '#f5b041' }}>
            Chưa có kênh YouTube nào được kết nối. Hãy kết nối kênh trong menu Channel Hub.
          </div>
        )}
        {resultSection === 'prompt-default-youtube-channel' &&
          (loadingMsg || resultMsg) && (
            <div className="status-box inline-save-status" role="status">
              {loadingMsg && <p className="loading">{loadingMsg}</p>}
              {resultMsg && <p className="result">{resultMsg}</p>}
            </div>
          )}
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>⚙️ Pipeline tự động của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Bốn bước lõi Dàn ý → Intro → Body → Outro luôn bắt buộc. Các bước
              dưới đây được áp dụng độc lập cho video mới của riêng bộ prompt này.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePipeline}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu pipeline
          </button>
        </div>
        <div className="pipeline-core-flow" aria-label="Các bước lõi bắt buộc">
          <span>Dàn ý</span><b>→</b><span>Intro</span><b>→</b><span>Body</span><b>→</b><span>Outro</span>
        </div>
        <div className="pipeline-options">
          {PIPELINE_STEPS.map((step, index) => (
            <label key={step.key} className="pipeline-option">
              <input
                type="checkbox"
                checked={currentPipeline[step.key]}
                onChange={(event) => handlePipelineChange(step.key, event.target.checked)}
                disabled={activeVersionLocked}
              />
              <span className="pipeline-step-number">{index + 6}</span>
              <span>
                <strong>{step.label}</strong>
                <small>{step.description}</small>
              </span>
            </label>
          ))}
        </div>
        <div className="pipeline-outcome" role="status">
          Kết quả: <strong>{pipelineOutcome(currentPipeline, currentPublishing)}</strong>
        </div>
        {pipelineNotice && (
          <div className="help-text" style={{
            marginTop: 8,
            color: pipelineNotice.includes('Chưa sẵn sàng') ? '#f5b041' : '#4dd0e1'
          }}>
            {pipelineNotice}
          </div>
        )}
        <div className="help-text pipeline-snapshot-help">
          Mỗi job lưu một bản chụp pipeline khi được thêm vào hàng đợi. Sửa cấu
          hình tại đây không thay đổi job đã xếp hàng hoặc đang phục hồi.
        </div>
      </div>

      {currentPipeline.video_render && (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>🖼️ Cấu hình Media & Video Google Flow (Nano Banana 2 / Omni 1.1 / Veo 3.1)</label>
              <div className="help-text" style={{ marginTop: 5 }}>
                Hệ thống chia phân cảnh theo phụ đề SRT (25–35s), tạo ảnh/video chất lượng cao qua Google Flow và dựng thành video MP4 hoàn chỉnh.
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSaveImageGeneration}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu cấu hình media
            </button>
          </div>

          {/* Agent Settings Pro-tip Banner */}
          <div
            style={{
              background: 'linear-gradient(135deg, rgba(59, 130, 246, 0.12), rgba(16, 185, 129, 0.08))',
              border: '1px solid rgba(59, 130, 246, 0.25)',
              borderRadius: 8,
              padding: '12px 16px',
              marginBottom: 16,
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12
            }}
          >
            <span style={{ fontSize: '1.2rem', lineHeight: 1 }}>💡</span>
            <div style={{ fontSize: '0.85rem', color: '#e2e8f0', lineHeight: 1.5 }}>
              <strong style={{ color: '#93c5fd' }}>Mẹo tự động hóa mượt mà:</strong> Trên tài khoản Google Flow, bạn hãy vào <em>Cài đặt tác nhân (Agent settings)</em> &rarr; mục <em>Xác nhận trước khi tạo</em> &rarr; chọn <strong>"Không bao giờ"</strong> (Tác nhân sẽ tự động tạo nội dung nghe nhìn và trừ tín dụng). Thao tác này giúp bot sinh ảnh/video liên tục mà không bị dừng chờ duyệt popup.
            </div>
          </div>

          {/* Section 1: AI Image Models Configuration */}
          <div style={{ marginBottom: 18 }}>
            <h4 style={{ margin: '0 0 10px 0', fontSize: '0.95rem', color: '#6ee7b7', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>📸</span> 1. Cấu hình Tạo Hình Ảnh Phân Cảnh (AI Image Models)
            </h4>
            <div className="production-settings-grid" style={{ marginBottom: 10 }}>
              <label>
                Model tạo ảnh
                <select
                  className="version-select"
                  value={currentImageGeneration.model || 'nano_banana_2'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'model', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  {GOOGLE_FLOW_IMAGE_MODELS.map(m => (
                    <option key={m.id} value={m.id}>
                      {m.name} [{m.badge}]
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Tỉ lệ khung hình (Aspect)
                <select
                  className="version-select"
                  value={currentImageGeneration.aspect_ratio || '16:9'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'aspect_ratio', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="16:9">16:9 (YouTube Ngang)</option>
                  <option value="9:16">9:16 (Shorts / Dọc)</option>
                  <option value="1:1">1:1 (Vuông)</option>
                  <option value="4:3">4:3 (Truyền thống)</option>
                  <option value="3:4">3:4 (Dọc cổ điển)</option>
                </select>
              </label>

              <label>
                Số lượng ảnh / lần (Outputs)
                <select
                  className="version-select"
                  value={currentImageGeneration.output_count || 2}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'output_count', Number(event.target.value)
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="1">x1 (1 ảnh)</option>
                  <option value="2">x2 (2 ảnh - Mặc định Flow)</option>
                  <option value="3">x3 (3 ảnh)</option>
                  <option value="4">x4 (4 ảnh)</option>
                </select>
              </label>

              <label>
                Nguồn ảnh Scene 0 (Ảnh mở đầu)
                <select
                  className="version-select"
                  value={currentImageGeneration.scene_0_source || 'from_thumbnail_without_text'}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'scene_0_source', event.target.value
                  )}
                  disabled={activeVersionLocked}
                >
                  <option value="from_thumbnail_without_text">Từ Thumbnail không chữ (Đồng bộ ảnh bìa - Khuyên dùng)</option>
                  <option value="from_thumbnail_with_text">Từ Thumbnail có chữ</option>
                  <option value="from_intro_transcript">Theo kịch bản Intro (Không dùng Thumbnail)</option>
                </select>
              </label>

              {[
                ['scene_duration_min_seconds', 'Tối thiểu (giây)'],
                ['scene_duration_target_seconds', 'Mục tiêu (giây)'],
                ['scene_duration_max_seconds', 'Tối đa (giây)']
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    type="number"
                    min="10"
                    max="90"
                    value={currentImageGeneration[key]}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', key, Number(event.target.value)
                    )}
                    disabled={activeVersionLocked}
                  />
                </label>
              ))}
            </div>

            {/* Selected Image Model Info Card */}
            {(() => {
              const selectedImgModel = GOOGLE_FLOW_IMAGE_MODELS.find(
                m => m.id === (currentImageGeneration.model || 'nano_banana_2')
              ) || GOOGLE_FLOW_IMAGE_MODELS[0];
              return (
                <div
                  style={{
                    background: 'rgba(255, 255, 255, 0.03)',
                    border: '1px solid rgba(255, 255, 255, 0.08)',
                    borderRadius: 8,
                    padding: '8px 12px',
                    fontSize: '0.82rem',
                    marginBottom: 12
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                    <strong style={{ color: '#6ee7b7' }}>{selectedImgModel.name}</strong>
                    <span style={{ fontSize: '0.76rem', color: '#93c5fd' }}>({selectedImgModel.creditLabel} • {selectedImgModel.speed})</span>
                  </div>
                  <div style={{ color: '#cbd5e1', lineHeight: 1.4 }}>
                    {selectedImgModel.description}
                  </div>
                </div>
              );
            })()}

            {/* Image Style Prompt */}
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                  🎨 Style Prompt Hình Ảnh (Phong cách chung các Scene tĩnh)
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                  onClick={() => handlePromptSettingChange(
                    'image_generation_settings',
                    'style_prompt',
                    DEFAULT_IMAGE_GENERATION_SETTINGS.style_prompt
                  )}
                  disabled={activeVersionLocked}
                  title="Khôi phục Style prompt ảnh mặc định"
                >
                  🔄 Mặc định
                </button>
              </div>
              <textarea
                className="prompt-textarea"
                rows={3}
                value={currentImageGeneration.style_prompt}
                onChange={event => handlePromptSettingChange(
                  'image_generation_settings', 'style_prompt', event.target.value
                )}
                disabled={activeVersionLocked}
                placeholder="Phong cách hình ảnh dùng chung cho các scene tĩnh"
              />
              <div className="help-text" style={{ marginTop: 4 }}>
                Định hình phong cách mỹ thuật, ánh sáng, nhiếp ảnh dùng làm giá trị cho biến <code>{'{style}'}</code> trong các prompt tạo ảnh.
              </div>
            </div>

            {/* Image Negative Prompt */}
            <div style={{ marginTop: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                  🚫 Negative Prompt Hình Ảnh (Tránh tạo trong ảnh tĩnh)
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                  onClick={() => handlePromptSettingChange(
                    'image_generation_settings',
                    'negative_prompt',
                    DEFAULT_IMAGE_GENERATION_SETTINGS.negative_prompt
                  )}
                  disabled={activeVersionLocked}
                  title="Khôi phục Negative prompt ảnh mặc định"
                >
                  🔄 Mặc định
                </button>
              </div>
              <textarea
                className="prompt-textarea"
                rows={2}
                value={currentImageGeneration.negative_prompt}
                onChange={event => handlePromptSettingChange(
                  'image_generation_settings', 'negative_prompt', event.target.value
                )}
                disabled={activeVersionLocked}
                placeholder="Các đặc điểm cần loại trừ khi sinh ảnh tĩnh (deformed hands, text, blurry, cartoon...)"
              />
            </div>

            {/* Scene 0 Template */}
            <div style={{ marginTop: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                  🖼️ Template Cảnh mở đầu (Scene 0 / Hook Image)
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                  onClick={() => handlePromptSettingChange(
                    'image_generation_settings',
                    'scene_0_prompt_template',
                    DEFAULT_IMAGE_GENERATION_SETTINGS.scene_0_prompt_template
                  )}
                  disabled={activeVersionLocked}
                  title="Khôi phục công thức Scene 0 mặc định"
                >
                  🔄 Mặc định
                </button>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                {SCENE_0_TAGS.map(item => (
                  <button
                    key={item.tag}
                    type="button"
                    className="btn-secondary"
                    style={{ padding: '2px 7px', fontSize: '0.76rem', background: 'rgba(96, 165, 250, 0.12)', borderColor: 'rgba(96, 165, 250, 0.3)', color: '#93c5fd' }}
                    onClick={() => {
                      const cur = currentImageGeneration.scene_0_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.scene_0_prompt_template;
                      handlePromptSettingChange('image_generation_settings', 'scene_0_prompt_template', cur ? `${cur.trimEnd()} ${item.tag}` : item.tag);
                    }}
                    disabled={activeVersionLocked}
                    title={`Chèn biến ${item.tag}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <textarea
                className="prompt-textarea"
                rows={3}
                value={currentImageGeneration.scene_0_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.scene_0_prompt_template}
                onChange={event => handlePromptSettingChange(
                  'image_generation_settings', 'scene_0_prompt_template', event.target.value
                )}
                disabled={activeVersionLocked}
                placeholder="Ví dụ: A cinematic movie still: {style}, opening scene hook. {reference} Story visual core: {thumbnail_concept}. 16:9 widescreen, photorealistic 8k, authentic documentary realism, clean framing without text."
              />
              <div className="help-text" style={{ marginTop: 4 }}>
                Công thức tạo ảnh khung hình đầu tiên của video. Hỗ trợ <code>{'{style}'}</code>, <code>{'{reference}'}</code>, <code>{'{thumbnail_concept}'}</code>, <code>{'{action}'}</code>, <code>{'{scene_index}'}</code>.
              </div>
            </div>

            {/* Body Scenes Template */}
            <div style={{ marginTop: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                  🖼️ Template Cảnh thân bài & kết thúc (Body Scenes)
                </label>
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                  onClick={() => handlePromptSettingChange(
                    'image_generation_settings',
                    'scene_body_prompt_template',
                    DEFAULT_IMAGE_GENERATION_SETTINGS.scene_body_prompt_template
                  )}
                  disabled={activeVersionLocked}
                  title="Khôi phục công thức cảnh thân bài mặc định"
                >
                  🔄 Mặc định
                </button>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                {SCENE_BODY_TAGS.map(item => (
                  <button
                    key={item.tag}
                    type="button"
                    className="btn-secondary"
                    style={{ padding: '2px 7px', fontSize: '0.76rem', background: 'rgba(96, 165, 250, 0.12)', borderColor: 'rgba(96, 165, 250, 0.3)', color: '#93c5fd' }}
                    onClick={() => {
                      const cur = currentImageGeneration.scene_body_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.scene_body_prompt_template;
                      handlePromptSettingChange('image_generation_settings', 'scene_body_prompt_template', cur ? `${cur.trimEnd()} ${item.tag}` : item.tag);
                    }}
                    disabled={activeVersionLocked}
                    title={`Chèn biến ${item.tag}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <textarea
                className="prompt-textarea"
                rows={3}
                value={currentImageGeneration.scene_body_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.scene_body_prompt_template}
                onChange={event => handlePromptSettingChange(
                  'image_generation_settings', 'scene_body_prompt_template', event.target.value
                )}
                disabled={activeVersionLocked}
                placeholder="Ví dụ: A still photograph: {style}, scene {scene_index}. {reference} Narrative scene: {action}. 16:9 widescreen still photograph, authentic documentary realism, natural lighting, clean visual without text."
              />
              <div className="help-text" style={{ marginTop: 4 }}>
                Công thức tạo ảnh cho các phân đoạn nối tiếp suốt video. Hỗ trợ <code>{'{style}'}</code>, <code>{'{reference}'}</code>, <code>{'{action}'}</code>, <code>{'{scene_index}'}</code>.
              </div>
            </div>
          </div>

          {/* Section 2: AI Video Intro Configuration */}
          <div style={{ marginBottom: 18, borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <h4 style={{ margin: 0, fontSize: '0.95rem', color: '#a78bfa', display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🎬</span> 2. Cấu hình Tạo Video Intro (AI Video Models)
              </h4>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: '0.88rem', color: '#f1f5f9' }}>
                <input
                  type="checkbox"
                  checked={currentImageGeneration.enable_intro_video !== false}
                  onChange={event => handlePromptSettingChange(
                    'image_generation_settings', 'enable_intro_video', event.target.checked
                  )}
                  disabled={activeVersionLocked}
                />
                Bật sinh Video AI cho cảnh mở đầu
              </label>
            </div>

            {currentImageGeneration.enable_intro_video !== false && (
              <>
                <div className="production-settings-grid" style={{ marginBottom: 10 }}>
                  <label>
                    Model Video Google Flow
                    <select
                      className="version-select"
                      value={currentImageGeneration.video_model || 'omni_1_1_flash'}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'video_model', event.target.value
                      )}
                      disabled={activeVersionLocked}
                    >
                      {GOOGLE_FLOW_VIDEO_MODELS.map(m => (
                        <option key={m.id} value={m.id}>
                          {m.name} [{m.badge}]
                        </option>
                      ))}
                    </select>
                  </label>

                  <label>
                    Tỉ lệ Video Intro
                    <select
                      className="version-select"
                      value={currentImageGeneration.video_aspect_ratio || '16:9'}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'video_aspect_ratio', event.target.value
                      )}
                      disabled={activeVersionLocked}
                    >
                      <option value="16:9">16:9 (Widescreen Ngang)</option>
                      <option value="9:16">9:16 (Vertical Dọc)</option>
                    </select>
                  </label>

                  <label>
                    Thời lượng Intro mục tiêu (giây)
                    <input
                      type="number"
                      min="4"
                      max="15"
                      step="0.5"
                      value={currentImageGeneration.intro_scene_target_seconds || 8.0}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'intro_scene_target_seconds', Number(event.target.value)
                      )}
                      disabled={activeVersionLocked}
                    />
                  </label>

                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 22, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={currentImageGeneration.intro_crop_watermark !== false}
                      onChange={event => handlePromptSettingChange(
                        'image_generation_settings', 'intro_crop_watermark', event.target.checked
                      )}
                      disabled={activeVersionLocked}
                    />
                    <span>Cắt watermark SynthID</span>
                  </label>
                </div>

                {/* Selected Video Model Info Card */}
                {(() => {
                  const selectedVidModel = GOOGLE_FLOW_VIDEO_MODELS.find(
                    m => m.id === (currentImageGeneration.video_model || 'omni_1_1_flash')
                  ) || GOOGLE_FLOW_VIDEO_MODELS[0];
                  return (
                    <div
                      style={{
                        background: 'rgba(255, 255, 255, 0.03)',
                        border: '1px solid rgba(255, 255, 255, 0.08)',
                        borderRadius: 8,
                        padding: '8px 12px',
                        fontSize: '0.82rem',
                        marginBottom: 12
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                        <strong style={{ color: '#a78bfa' }}>{selectedVidModel.name}</strong>
                        <span style={{ fontSize: '0.76rem', color: '#93c5fd' }}>({selectedVidModel.creditLabel} • {selectedVidModel.speed})</span>
                      </div>
                      <div style={{ color: '#cbd5e1', lineHeight: 1.4 }}>
                        {selectedVidModel.description}
                      </div>
                    </div>
                  );
                })()}

                {/* Video Style Prompt */}
                <div style={{ marginTop: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                      🎨 Style Prompt Video (Phong cách Video Độc lập)
                    </label>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                      onClick={() => handlePromptSettingChange(
                        'image_generation_settings',
                        'video_style_prompt',
                        DEFAULT_IMAGE_GENERATION_SETTINGS.video_style_prompt
                      )}
                      disabled={activeVersionLocked}
                      title="Khôi phục Style prompt video mặc định"
                    >
                      🔄 Mặc định
                    </button>
                  </div>
                  <textarea
                    className="prompt-textarea"
                    rows={3}
                    value={currentImageGeneration.video_style_prompt ?? DEFAULT_IMAGE_GENERATION_SETTINGS.video_style_prompt}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', 'video_style_prompt', event.target.value
                    )}
                    disabled={activeVersionLocked}
                    placeholder="Phong cách điện ảnh, ánh sáng và màu sắc chuyên biệt cho video AI (Veo/Omni)"
                  />
                  <div className="help-text" style={{ marginTop: 4 }}>
                    Phong cách video độc lập hoàn toàn với ảnh tĩnh, định hình chất liệu chuyển động và không gian điện ảnh. Chèn vào biến <code>{'{style}'}</code> của template video.
                  </div>
                </div>

                {/* Video Negative Prompt */}
                <div style={{ marginTop: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                      🚫 Negative Prompt Video (Tránh tạo trong Video AI)
                    </label>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                      onClick={() => handlePromptSettingChange(
                        'image_generation_settings',
                        'video_negative_prompt',
                        DEFAULT_IMAGE_GENERATION_SETTINGS.video_negative_prompt
                      )}
                      disabled={activeVersionLocked}
                      title="Khôi phục Negative prompt video mặc định"
                    >
                      🔄 Mặc định
                    </button>
                  </div>
                  <textarea
                    className="prompt-textarea"
                    rows={2}
                    value={currentImageGeneration.video_negative_prompt ?? DEFAULT_IMAGE_GENERATION_SETTINGS.video_negative_prompt}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', 'video_negative_prompt', event.target.value
                    )}
                    disabled={activeVersionLocked}
                    placeholder="Các đặc điểm cần loại trừ khi sinh video AI (still image, static photo, deformed hands, watermark...)"
                  />
                </div>

                {/* Video Motion Directive */}
                <div style={{ marginTop: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <label style={{ fontWeight: 600, color: '#f1f5f9', fontSize: '0.88rem' }}>
                      🎥 Prompt Chỉ đạo Chuyển động & Camera Video Intro (Motion Directive)
                    </label>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                      onClick={() => handlePromptSettingChange(
                        'image_generation_settings',
                        'video_motion_prompt',
                        DEFAULT_IMAGE_GENERATION_SETTINGS.video_motion_prompt
                      )}
                      disabled={activeVersionLocked}
                      title="Khôi phục chỉ thị chuyển động mặc định"
                    >
                      🔄 Mặc định
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                    {VIDEO_MOTION_PRESETS.map((preset, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="btn-secondary"
                        style={{ padding: '2px 7px', fontSize: '0.76rem', background: 'rgba(167, 139, 250, 0.12)', borderColor: 'rgba(167, 139, 250, 0.3)', color: '#c4b5fd' }}
                        onClick={() => handlePromptSettingChange(
                          'image_generation_settings',
                          'video_motion_prompt',
                          preset.prompt
                        )}
                        disabled={activeVersionLocked}
                        title={preset.prompt}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                  <textarea
                    className="prompt-textarea"
                    rows={2}
                    value={currentImageGeneration.video_motion_prompt ?? DEFAULT_IMAGE_GENERATION_SETTINGS.video_motion_prompt}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', 'video_motion_prompt', event.target.value
                    )}
                    disabled={activeVersionLocked}
                    placeholder="Ví dụ: Motion: smooth cinematic camera movement, natural realistic motion, 4k 24fps high-fidelity video."
                  />
                  <div className="help-text" style={{ marginTop: 4 }}>
                    Chỉ đạo góc quay camera, zoom, pan, tilt, chuyển động chủ thể và tốc độ khung hình. Chèn vào biến <code>{'{motion}'}</code> của prompt video.
                  </div>
                </div>

                {/* Video Prompt Template */}
                <div style={{ marginTop: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                    <label className="production-field-label" style={{ margin: 0, fontWeight: 600, color: '#f1f5f9' }}>
                      🎬 Template Tạo Video Tổng Quát (Video Prompt Template)
                    </label>
                    <button
                      type="button"
                      className="btn-secondary"
                      style={{ padding: '2px 8px', fontSize: '0.78rem' }}
                      onClick={() => handlePromptSettingChange(
                        'image_generation_settings',
                        'video_prompt_template',
                        DEFAULT_IMAGE_GENERATION_SETTINGS.video_prompt_template
                      )}
                      disabled={activeVersionLocked}
                      title="Khôi phục công thức Video mặc định"
                    >
                      🔄 Mặc định
                    </button>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                    {VIDEO_PROMPT_TAGS.map(item => (
                      <button
                        key={item.tag}
                        type="button"
                        className="btn-secondary"
                        style={{ padding: '2px 7px', fontSize: '0.76rem', background: 'rgba(167, 139, 250, 0.12)', borderColor: 'rgba(167, 139, 250, 0.3)', color: '#c4b5fd' }}
                        onClick={() => {
                          const cur = currentImageGeneration.video_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.video_prompt_template;
                          handlePromptSettingChange('image_generation_settings', 'video_prompt_template', cur ? `${cur.trimEnd()} ${item.tag}` : item.tag);
                        }}
                        disabled={activeVersionLocked}
                        title={`Chèn biến ${item.tag}`}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                  <textarea
                    className="prompt-textarea"
                    rows={3}
                    value={currentImageGeneration.video_prompt_template ?? DEFAULT_IMAGE_GENERATION_SETTINGS.video_prompt_template}
                    onChange={event => handlePromptSettingChange(
                      'image_generation_settings', 'video_prompt_template', event.target.value
                    )}
                    disabled={activeVersionLocked}
                    placeholder="Ví dụ: {frame_directive} Scene action: {action}. Visual style: {style}. {motion} Clean video without any text, letters, watermark, or subtitles."
                  />
                  <div className="help-text" style={{ marginTop: 4 }}>
                    Công thức tổng hợp gửi cho Google Flow khi tạo video intro. Hỗ trợ các biến <code>{'{frame_directive}'}</code> (chỉ thị gắn ảnh gốc Scene 0), <code>{'{action}'}</code> (hành động cảnh), <code>{'{style}'}</code> (Video Style), <code>{'{motion}'}</code> (Motion directive).
                  </div>
                </div>
              </>
            )}
          </div>

          {/* Section 3: Live Prompt Preview */}
          <div style={{ marginTop: 18, borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: 14 }}>
            <h4 style={{ margin: '0 0 8px 0', fontSize: '0.95rem', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span>🔍</span> 3. Xem trước Prompt Mẫu Thực Tế khi gửi AI (Live Prompt Preview)
            </h4>
            <div className="help-text" style={{ marginBottom: 10 }}>
              Trực quan hóa câu lệnh prompt tiếng Anh hoàn chỉnh được tạo tự động cho AI Image & Video theo cấu hình và template hiện tại.
            </div>

            {(() => {
              const liveImgStyle = (currentImageGeneration.style_prompt || DEFAULT_IMAGE_GENERATION_SETTINGS.style_prompt).split('\n')[0].slice(0, 100);
              const liveVidStyle = (currentImageGeneration.video_style_prompt || DEFAULT_IMAGE_GENERATION_SETTINGS.video_style_prompt).split('\n')[0].slice(0, 100);
              const liveMotion = currentImageGeneration.video_motion_prompt || DEFAULT_IMAGE_GENERATION_SETTINGS.video_motion_prompt;
              const liveVidTemplate = currentImageGeneration.video_prompt_template || DEFAULT_IMAGE_GENERATION_SETTINGS.video_prompt_template;

              const previewScene0 = (
                currentImageGeneration.scene_0_prompt_template || DEFAULT_IMAGE_GENERATION_SETTINGS.scene_0_prompt_template
              )
                .replace('{style}', liveImgStyle)
                .replace('{reference}', 'Depicting Bác Ba. ')
                .replace('{thumbnail_concept}', 'Ngôi nhà cổ kính bên rặng tre làng quê thanh bình')
                .replace('{action}', 'Bác Ba ngồi trầm ngâm bên tách trà sớm')
                .replace('{scene_index}', '1');

              const previewBody = (
                currentImageGeneration.scene_body_prompt_template || DEFAULT_IMAGE_GENERATION_SETTINGS.scene_body_prompt_template
              )
                .replace('{style}', liveImgStyle)
                .replace('{reference}', 'Depicting Bác Ba. ')
                .replace('{action}', 'Hai người bạn lâu năm trò chuyện bên hiên nhà ấm cúng')
                .replace('{scene_index}', '2');

              const previewVideo = liveVidTemplate
                .replace('{frame_directive}', 'Generate exactly one 16:9 video, not a still image. Using the attached image as the starting frame, animate it into a cinematic video clip.')
                .replace('{action}', 'Bác Ba ngồi trầm ngâm bên tách trà sớm')
                .replace('{style}', liveVidStyle)
                .replace('{motion}', liveMotion);

              return (
                <div style={{ background: 'rgba(0, 0, 0, 0.25)', border: '1px solid rgba(255, 255, 255, 0.08)', borderRadius: 8, padding: '10px 14px' }}>
                  <div style={{ fontSize: '0.8rem', display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: 6, border: '1px solid rgba(96, 165, 250, 0.2)' }}>
                      <strong style={{ color: '#60a5fa' }}>📷 Prompt Tạo Ảnh Scene 0 (Hook Image):</strong>
                      <div style={{ color: '#94a3b8', marginTop: 4, fontFamily: 'monospace', lineHeight: 1.4, fontSize: '0.78rem' }}>
                        {previewScene0}
                      </div>
                    </div>
                    <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: 6, border: '1px solid rgba(110, 231, 183, 0.2)' }}>
                      <strong style={{ color: '#6ee7b7' }}>📷 Prompt Tạo Ảnh Scene 2 (Body Scenes):</strong>
                      <div style={{ color: '#94a3b8', marginTop: 4, fontFamily: 'monospace', lineHeight: 1.4, fontSize: '0.78rem' }}>
                        {previewBody}
                      </div>
                    </div>
                    {currentImageGeneration.enable_intro_video !== false && (
                      <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '8px 12px', borderRadius: 6, border: '1px solid rgba(167, 139, 250, 0.2)' }}>
                        <strong style={{ color: '#a78bfa' }}>🎬 Prompt Tạo Video Intro (Veo / Omni):</strong>
                        <div style={{ color: '#94a3b8', marginTop: 4, fontFamily: 'monospace', lineHeight: 1.4, fontSize: '0.78rem' }}>
                          {previewVideo}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}
          </div></div>
      )}

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🎭 Thư viện nhân vật & bối cảnh tham chiếu (Reference Assets)</label>
            <div className="help-text" style={{ marginTop: 5 }}>
              Mỗi bộ prompt có một thư mục riêng. Đặt ảnh nhân vật/địa danh vào đây với tên file tương ứng (ví dụ: <code>bac_ba.png</code>, <code>chua_mot_cot.jpg</code>).
              Hệ thống tự động so khớp tên nhân vật với phụ đề từng cảnh để gửi ảnh mẫu vào Google Flow, giúp nhân vật luôn đồng nhất trong suốt video.
            </div>
          </div>
          <div className="prompt-header-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={handleOpenAssetsFolder}
              title={promptAssetsFolder ? `Đường dẫn: ${promptAssetsFolder}` : 'Mở thư mục'}
            >
              📁 Mở thư mục trên máy tính
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => fetchPromptAssets(activeVersion)}
              title="Quét lại các file ảnh mới thêm vào thư mục"
            >
              🔄 Làm mới
            </button>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          {promptAssetsFolder && (
            <div style={{ fontSize: '0.8rem', color: '#888', marginBottom: 10, fontFamily: 'monospace', wordBreak: 'break-all' }}>
              📂 Đường dẫn thư mục: {promptAssetsFolder}
            </div>
          )}

          {assetMessage && (
            <div className="help-text" style={{ color: '#4ce0b3', marginBottom: 10, display: 'inline-block' }}>
              {assetMessage}
            </div>
          )}

          <div style={{ marginBottom: 15, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
            <label
              className="btn-secondary"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                cursor: activeVersionLocked ? 'not-allowed' : 'pointer',
                padding: '8px 16px',
                margin: 0,
                opacity: activeVersionLocked ? 0.6 : 1
              }}
            >
              {uploadingAsset ? '⏳ Đang tải lên...' : '➕ Tải thêm ảnh nhân vật / bối cảnh'}
              <input
                type="file"
                multiple
                accept="image/png, image/jpeg, image/webp"
                style={{ display: 'none' }}
                onChange={handleUploadAsset}
                disabled={activeVersionLocked || uploadingAsset}
              />
            </label>
            <span className="help-text">
              Chấp nhận .png, .jpg, .webp. Tên file nên viết không dấu hoặc gạch dưới (vd: <code>bac_ba.png</code>, <code>chi_lan.jpg</code>).
            </span>
          </div>

          {loadingAssets ? (
            <div style={{ color: '#888', padding: '16px 0' }}>Đang tải danh sách ảnh tham chiếu...</div>
          ) : promptAssets.length === 0 ? (
            <div style={{
              padding: '24px',
              textAlign: 'center',
              background: '#181818',
              borderRadius: '8px',
              border: '1px dashed #444',
              color: '#aaa'
            }}>
              <div style={{ fontSize: '2rem', marginBottom: 8 }}>🖼️</div>
              <div>Chưa có ảnh nhân vật hoặc bối cảnh nào trong bộ prompt <strong>{currentVersion?.name || activeVersion}</strong>.</div>
              <div style={{ fontSize: '0.85rem', color: '#777', marginTop: 6 }}>
                Nhấn <strong>"📁 Mở thư mục trên máy tính"</strong> để copy ảnh vào hoặc bấm <strong>"➕ Tải thêm ảnh..."</strong> ở trên.
              </div>
            </div>
          ) : (
            <div className="prompt-assets-grid">
              {promptAssets.map(asset => (
                <div key={asset.filename} className="prompt-asset-card">
                  <div className="prompt-asset-thumb-wrap">
                    <img
                      src={`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(activeVersion)}/assets/${encodeURIComponent(asset.filename)}`}
                      alt={asset.display_name}
                      className="prompt-asset-thumb"
                      loading="lazy"
                    />
                    <button
                      type="button"
                      className="prompt-asset-delete-btn"
                      onClick={() => handleDeleteAsset(asset.filename)}
                      disabled={activeVersionLocked}
                      title={`Xóa ảnh ${asset.filename}`}
                    >
                      ✕
                    </button>
                  </div>
                  <div className="prompt-asset-info">
                    <div className="prompt-asset-name" title={asset.filename}>
                      {asset.filename}
                    </div>
                    <div className="prompt-asset-keywords" title={`Từ khóa nhận diện: ${asset.keywords?.join(', ')}`}>
                      🔑 {asset.keywords?.slice(0, 3).join(', ')}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {currentPipeline.youtube_upload && (
        <div className="prompt-item" style={{ marginBottom: '20px' }}>
          <div className="prompt-header">
            <div>
              <label>📤 Thiết lập upload YouTube của bộ prompt</label>
              <div className="help-text" style={{ marginTop: 5 }}>
                Tùy chỉnh phương thức upload và chế độ phát hành (Public ngay, Lên lịch tự động, hoặc Giữ Riêng tư).
              </div>
            </div>
            <button
              className="btn-save section-save-button"
              onClick={handleSavePublishing}
              disabled={Boolean(savingSection) || activeVersionLocked}
            >
              💾 Lưu thiết lập upload
            </button>
          </div>
          <div className="production-settings-grid">
            <label>
              Chế độ phát hành (Publish Mode)
              <select
                className="version-select"
                value={currentPublishing.publish_mode || 'schedule'}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'publish_mode', event.target.value
                )}
                disabled={activeVersionLocked}
              >
                <option value="schedule">⏰ Lên lịch phát sóng tự động (Theo khung giờ đặt trước)</option>
                <option value="public">⚡ Public ngay lập tức (Công khai trực tiếp khi upload xong)</option>
                <option value="private">🔒 Riêng tư (Private - Lưu trong Studio không công khai)</option>
              </select>
            </label>
            <label>
              Phương thức Upload YouTube
              <select
                className="version-select"
                value={currentPublishing.upload_method || 'browser'}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'upload_method', event.target.value
                )}
                disabled={activeVersionLocked}
              >
                <option value="browser">🌐 UPLOAD Qua Trình duyệt GPM (Tự động kiếm tiền & Đặt lịch - Khuyên dùng)</option>
                <option value="api">🔌 UPLOAD Qua YouTube Data API (API ngầm - Tốn Quota)</option>
              </select>
            </label>
            <label>
              Thumbnail dùng để upload
              <select
                className="version-select"
                value={currentImageGeneration.thumbnail_variant}
                onChange={event => handleThumbnailVariantChange(event.target.value)}
                disabled={activeVersionLocked}
              >
                <option value="without_text">Không chữ</option>
                <option value="with_text">Có chữ</option>
              </select>
            </label>
            <label>
              Thể loại video (Category)
              <select
                className="version-select"
                value={currentPublishing.category_id || ''}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'category_id', event.target.value
                )}
                disabled={activeVersionLocked}
              >
                <option value="">Mặc định theo kênh (Không can thiệp)</option>
                {YOUTUBE_CATEGORIES.map(cat => (
                  <option key={cat.id} value={cat.id}>
                    {cat.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Ngôn ngữ
              <input
                className="version-select"
                value={currentPublishing.language}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'language', event.target.value
                )}
                placeholder="vi"
                disabled={activeVersionLocked}
              />
            </label>
            <label>
              Dành cho trẻ em
              <select
                className="version-select"
                value={currentPublishing.made_for_kids === null ? '' : String(currentPublishing.made_for_kids)}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings',
                  'made_for_kids',
                  event.target.value === '' ? null : event.target.value === 'true'
                )}
                disabled={activeVersionLocked}
              >
                <option value="">Bắt buộc chọn</option>
                <option value="false">Không dành cho trẻ em</option>
                <option value="true">Dành cho trẻ em</option>
              </select>
            </label>
          </div>
          {(currentPublishing.upload_method || 'browser') === 'browser' && (
            <details style={{ marginTop: 14 }}>
              <summary style={{ cursor: 'pointer', fontWeight: 700, color: '#c4b5fd' }}>
                ⚙️ Thiết lập nâng cao YouTube Studio
              </summary>
              <div className="production-settings-grid" style={{ marginTop: 12 }}>
                <label>
                  Chính sách kiếm tiền
                  <select
                    className="version-select"
                    value={currentPublishing.monetization_mode}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'monetization_mode', event.target.value)}
                    disabled={activeVersionLocked}
                  >
                    <option value="auto_enable_if_available">Tự bật nếu kênh hỗ trợ; nếu không thì bỏ qua</option>
                    <option value="keep_off">Luôn giữ tắt</option>
                    <option value="require_on">Bắt buộc bật; không hỗ trợ thì dừng</option>
                  </select>
                </label>
                <label>
                  Tự đánh giá quảng cáo
                  <select
                    className="version-select"
                    value={currentPublishing.ad_suitability_mode}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'ad_suitability_mode', event.target.value)}
                    disabled={activeVersionLocked}
                  >
                    <option value="none_of_the_above">Không chứa nội dung nào ở trên</option>
                  </select>
                </label>
                <label>
                  Playlist chính xác
                  <input
                    className="version-select"
                    value={currentPublishing.playlist_name}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'playlist_name', event.target.value)}
                    placeholder="Để trống để giữ mặc định theo kênh"
                    disabled={activeVersionLocked}
                  />
                </label>
                <label>
                  Ngôn ngữ tiêu đề và mô tả
                  <input
                    className="version-select"
                    value={currentPublishing.title_description_language}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'title_description_language', event.target.value)}
                    placeholder="vi"
                    disabled={activeVersionLocked}
                  />
                </label>
                <label>
                  Giấy phép
                  <select
                    className="version-select"
                    value={currentPublishing.license}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'license', event.target.value)}
                    disabled={activeVersionLocked}
                  >
                    <option value="youtube">Giấy phép chuẩn của YouTube</option>
                    <option value="creative_common">Creative Commons</option>
                  </select>
                </label>
                <label>
                  Chứng nhận phụ đề (FCC)
                  <select
                    className="version-select"
                    value={currentPublishing.caption_certification}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'caption_certification', event.target.value)}
                    disabled={activeVersionLocked}
                  >
                    <option value="none">Không áp dụng</option>
                    <option value="never_aired_us">Chưa từng phát sóng trên TV tại Hoa Kỳ</option>
                    <option value="aired_us_without_captions">Đã phát sóng tại Hoa Kỳ nhưng không có phụ đề</option>
                    <option value="not_aired_us_with_captions_since_2012">Không phát sóng có phụ đề tại Hoa Kỳ từ 30/09/2012</option>
                    <option value="fcc_not_required">Không thuộc diện FCC yêu cầu phụ đề</option>
                    <option value="fcc_exempt">Được FCC/Quốc hội Hoa Kỳ miễn trừ</option>
                  </select>
                </label>
                <label>
                  Chính sách remix
                  <select
                    className="version-select"
                    value={currentPublishing.remix_policy}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'remix_policy', event.target.value)}
                    disabled={activeVersionLocked}
                  >
                    <option value="video_and_audio">Cho phép remix video và âm thanh</option>
                    <option value="audio_only">Chỉ cho phép remix âm thanh</option>
                    <option value="disabled">Không cho phép remix</option>
                  </select>
                </label>
                <label>
                  Kiểm duyệt bình luận
                  <select
                    className="version-select"
                    value={currentPublishing.comment_moderation}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'comment_moderation', event.target.value)}
                    disabled={activeVersionLocked || !currentPublishing.comments_enabled}
                  >
                    <option value="none">Không kiểm duyệt</option>
                    <option value="basic">Cơ bản</option>
                    <option value="strict">Nghiêm ngặt</option>
                    <option value="hold_all">Giữ tất cả để xem xét</option>
                  </select>
                </label>
                <label>
                  Người được bình luận
                  <select
                    className="version-select"
                    value={currentPublishing.comment_access}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'comment_access', event.target.value)}
                    disabled={activeVersionLocked || !currentPublishing.comments_enabled}
                  >
                    <option value="anyone">Mọi người</option>
                    <option value="subscribers">Người đăng ký</option>
                    <option value="members">Hội viên</option>
                  </select>
                </label>
                <label>
                  Thứ tự bình luận
                  <select
                    className="version-select"
                    value={currentPublishing.comment_sort}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'comment_sort', event.target.value)}
                    disabled={activeVersionLocked || !currentPublishing.comments_enabled}
                  >
                    <option value="top">Hàng đầu</option>
                    <option value="newest">Mới nhất</option>
                  </select>
                </label>
                <label>
                  Video mẫu cho màn hình kết thúc
                  <input
                    className="version-select"
                    value={currentPublishing.end_screen_source_video_id}
                    onChange={event => handlePromptSettingChange('publishing_settings', 'end_screen_source_video_id', event.target.value)}
                    placeholder="YouTube Video ID (không bắt buộc)"
                    disabled={activeVersionLocked}
                  />
                </label>
              </div>
              <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
                {[
                  ['midroll_ads', 'Quảng cáo giữa video'],
                  ['age_restriction', 'Giới hạn độ tuổi'],
                  ['paid_promotion', 'Có nội dung trả phí'],
                  ['automatic_chapters', 'Chapter tự động'],
                  ['automatic_places', 'Địa điểm tự động'],
                  ['automatic_concepts', 'Khái niệm tự động'],
                  ['allow_embedding', 'Cho phép nhúng'],
                  ['comments_enabled', 'Bật bình luận'],
                  ['show_ratings', 'Hiển thị lượt thích'],
                  ['upload_captions', 'Upload SRT tự động'],
                  ['premiere', 'Đặt làm video Công chiếu']
                ].map(([key, label]) => (
                  <label key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={Boolean(currentPublishing[key])}
                      onChange={event => handlePromptSettingChange('publishing_settings', key, event.target.checked)}
                      disabled={activeVersionLocked}
                    /> {label}
                  </label>
                ))}
              </div>
              <div className="help-text" style={{ marginTop: 8 }}>
                Tool nhận diện ba trạng thái kiếm tiền: khả dụng, không khả dụng và không xác định. Kênh chưa kiếm tiền vẫn được đặt lịch; trạng thái không xác định sẽ dừng để tránh thao tác sai.
              </div>
            </details>
          )}
          {(currentPublishing.upload_method || 'browser') === 'browser' ? (
            <div
              style={{
                marginTop: 10,
                padding: '10px 14px',
                background: 'rgba(56, 189, 248, 0.08)',
                border: '1px solid rgba(56, 189, 248, 0.25)',
                borderRadius: 8,
                fontSize: '0.84rem',
                color: '#e2e8f0',
                lineHeight: 1.5
              }}
            >
              <strong style={{ color: '#38bdf8' }}>🌐 Chế độ Trình duyệt GPM (Khuyên dùng):</strong>
              <div style={{ marginTop: 3, color: '#cbd5e1' }}>
                • <strong>Yêu cầu:</strong> Kênh được gán GPM Profile ID và Proxy riêng trong tab Quản lý Kênh.
              </div>
              <div style={{ color: '#cbd5e1' }}>
                • <strong>Lợi thế:</strong> Không tốn Quota YouTube API (không giới hạn lượt đăng), tự động hoàn tất khảo sát kiếm tiền (Ad Suitability) và hỗ trợ Đặt lịch trên YouTube Studio mà không cần xác minh Google Audit.
              </div>
            </div>
          ) : (
            <div
              style={{
                marginTop: 10,
                padding: '10px 14px',
                background: 'rgba(245, 158, 11, 0.08)',
                border: '1px solid rgba(245, 158, 11, 0.25)',
                borderRadius: 8,
                fontSize: '0.84rem',
                color: '#e2e8f0',
                lineHeight: 1.5
              }}
            >
              <strong style={{ color: '#f59e0b' }}>🔌 Chế độ YouTube Data API v3:</strong>
              <div style={{ marginTop: 3, color: '#cbd5e1' }}>
                • <strong>Yêu cầu:</strong> Kênh đã kết nối OAuth YouTube thành công. Nếu đặt lịch cần Google Cloud Project đã được Google Audit phê duyệt.
              </div>
              <div style={{ color: '#cbd5e1' }}>
                • <strong>Lưu ý:</strong> Tốn ~1.600 units Quota mỗi video. Không tự động tick khảo sát bật kiếm tiền.
              </div>
            </div>
          )}
          <div className="help-text" style={{ marginTop: 4, fontSize: '0.84rem', color: '#94a3b8', lineHeight: 1.5 }}>
            💡 <strong>Thể loại video:</strong> Tự động chọn đúng thể loại trên YouTube Studio khi upload. Nếu chọn <em>Mặc định theo kênh</em>, hệ thống sẽ giữ nguyên thể loại mặc định của kênh bạn đã cài đặt trên YouTube Studio.
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12, alignItems: 'center' }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={currentPublishing.notify_subscribers}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'notify_subscribers', event.target.checked
                )}
                disabled={activeVersionLocked}
              /> Thông báo người đăng ký khi video được công khai
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={currentPublishing.include_tags ?? true}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'include_tags', event.target.checked
                )}
                disabled={activeVersionLocked}
              /> Đính kèm thẻ từ khóa (Tags) khi upload video
            </label>
            <span style={{ color: '#4dd0e1' }}>✓ Luôn khai báo nội dung tổng hợp bằng AI</span>
          </div>
          {currentPublishing.include_tags !== false && (
            <div style={{ marginTop: 12 }}>
              <label style={{ fontWeight: 600, color: '#fff', display: 'block', marginBottom: 6 }}>
                🏷️ Thẻ từ khóa mặc định / cố định (Default Tags)
              </label>
              <input
                type="text"
                className="version-select"
                style={{ width: '100%' }}
                value={currentPublishing.default_tags || ''}
                onChange={event => handlePromptSettingChange(
                  'publishing_settings', 'default_tags', event.target.value
                )}
                placeholder="Ví dụ: dinh doan phan tich, đinh đoàn, tam ly hoc, ke chuyen gia dinh"
                disabled={activeVersionLocked}
              />
              <div className="help-text" style={{ marginTop: 4 }}>
                Các thẻ này sẽ được tự động gộp chung với tags do AI sinh ra khi upload lên YouTube (phân cách bằng dấu phẩy, tối đa 500 ký tự).
              </div>
            </div>
          )}
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid rgba(255, 255, 255, 0.1)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
              <label style={{ fontWeight: 600, color: '#fff', margin: 0 }}>
                📝 Mẫu mô tả YouTube thực tế (Description Template)
              </label>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {[
                  { tag: '{title}', label: '+ Tiêu đề' },
                  { tag: '{slug}', label: '+ Slug' },
                  { tag: '{description}', label: '+ Mô tả' },
                  { tag: '{hashtags}', label: '+ Hashtags' },
                  { tag: '{tags}', label: '+ Tags' },
                  { tag: '{pinned_comment}', label: '+ Ghim' },
                  { tag: '{quiz}', label: '+ Quiz' },
                  { tag: '{chapters}', label: '+ Chapters' }
                ].map(item => (
                  <button
                    key={item.tag}
                    type="button"
                    className="btn-secondary"
                    style={{ padding: '2px 8px', fontSize: '0.75rem', borderRadius: '4px' }}
                    onClick={() => {
                      const currentTpl = currentPublishing.description_template || '';
                      const nextTpl = currentTpl ? `${currentTpl}\n\n${item.tag}` : item.tag;
                      handlePromptSettingChange('publishing_settings', 'description_template', nextTpl);
                    }}
                    disabled={activeVersionLocked}
                    title={`Chèn biến ${item.tag}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="help-text" style={{ marginBottom: 8 }}>
              Tùy biến nội dung mô tả sẽ được dùng khi upload lên YouTube. Nhấp các nút trên để chèn nhanh biến động. Nếu để trống, hệ thống sẽ tự động ghép theo thứ tự mặc định: Mô tả → Chapters → Hashtags.
            </div>
            <textarea
              className="prompt-textarea"
              rows={6}
              value={currentPublishing.description_template || ''}
              onChange={event => handlePromptSettingChange(
                'publishing_settings', 'description_template', event.target.value
              )}
              placeholder="Ví dụ:\n{description}\n\n--- DANH SÁCH PHÂN ĐOẠN ---\n{chapters}\n\n--- TƯƠNG TÁC CÙNG KÊNH ---\n{pinned_comment}\n\n{quiz}\n\n{hashtags}"
              disabled={activeVersionLocked}
            />
          </div>
          {currentPipeline.youtube_schedule && (
            <div className="help-text" style={{ marginTop: 10, color: '#f5b041' }}>
              Lịch đăng, timezone, giới hạn ngày và nút pause được cấu hình tại phần kênh YouTube phía trên.
            </div>
          )}
        </div>
      )}

      {(loadingMsg || resultMsg) &&
        resultSection !== 'prompt-default-youtube-channel' && (
        <div className="status-box" style={{marginBottom: '20px'}}>
          {loadingMsg && <p className="loading">{loadingMsg}</p>}
          {resultMsg && <p className="result">{resultMsg}</p>}
        </div>
      )}

      <div className="prompts-list">
        {promptFields.map(field => {
          const meta = PROMPT_FIELD_METADATA[field.key] || {};
          const currentMode = currentVersion.content_mode || 'dialogue';
          const availableVars = (meta.variables || []).filter(
            v => !v.forMode || v.forMode === currentMode
          );
          const promptValue = currentVersion.prompts[field.key] || '';
          const missingRequired = availableVars.filter(
            v => v.required && !promptValue.includes(v.tag)
          );

          return (
            <div key={field.key} className="prompt-item">
              <div className="prompt-header">
                <div>
                  <label>{field.label}</label>
                  {meta.guide && (
                    <div className="help-text" style={{ marginTop: '3px', color: '#94a3b8' }}>
                      💡 {meta.guide}
                    </div>
                  )}
                </div>
                <div className="prompt-header-actions">
                  {field.help && <span className="help-text">{field.help}</span>}
                  <button
                    className="btn-save section-save-button"
                    onClick={() => handleSavePrompt(field.key, field.label)}
                    disabled={Boolean(savingSection) || activeVersionLocked}
                  >
                    💾 Lưu
                  </button>
                </div>
              </div>

              {/* Variable Chips Bar */}
              {availableVars.length > 0 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', margin: '8px 0 6px 0' }}>
                  <span style={{ fontSize: '0.78rem', color: '#64748b', fontWeight: 600 }}>Biến có sẵn:</span>
                  {availableVars.map(v => (
                    <button
                      key={v.tag}
                      type="button"
                      className="btn-secondary"
                      style={{
                        padding: '2px 8px',
                        fontSize: '0.76rem',
                        borderRadius: 4,
                        background: `${v.color || '#38bdf8'}18`,
                        borderColor: `${v.color || '#38bdf8'}40`,
                        color: v.color || '#38bdf8'
                      }}
                      onClick={() => handleInsertVariable(field.key, v.tag)}
                      disabled={activeVersionLocked}
                      title={`Chèn biến ${v.tag}: ${v.description}`}
                    >
                      {v.label || v.tag}
                    </button>
                  ))}
                </div>
              )}

              {/* Missing Required Variable Warning */}
              {missingRequired.length > 0 && (
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  background: 'rgba(245, 158, 11, 0.12)',
                  border: '1px solid rgba(245, 158, 11, 0.35)',
                  borderRadius: 6,
                  padding: '6px 12px',
                  marginBottom: 8,
                  color: '#fbbf24',
                  fontSize: '0.8rem',
                  gap: 10
                }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span>⚠️</span>
                    <span>
                      <strong>Lưu ý:</strong> Prompt đang thiếu biến/tag bắt buộc: {missingRequired.map(v => v.tag).join(', ')}. {field.key === 'outline' ? 'AI sẽ không có dữ liệu nguồn để lập dàn ý.' : field.key === 'body' ? 'AI sẽ không nhận được từng phần dàn ý.' : 'OmniVoice sẽ không thể tự động nhận diện và đổi giọng đọc.'}
                    </span>
                  </span>
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    {missingRequired.map(v => (
                      <button
                        key={v.tag}
                        type="button"
                        className="btn-secondary"
                        style={{ padding: '2px 8px', fontSize: '0.75rem', background: '#f59e0b', color: '#000', fontWeight: 'bold', border: 'none' }}
                        onClick={() => handleInsertVariable(field.key, v.tag)}
                        title={`Khôi phục ${v.tag}`}
                      >
                        + Khôi phục {v.tag}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ display: 'flex', gap: '10px' }}>
                <textarea
                  className="prompt-textarea"
                  style={{ flex: 1 }}
                  value={currentVersion.prompts[field.key] || ''}
                  onChange={(e) => handlePromptChange(field.key, e.target.value)}
                  rows={6}
                  disabled={activeVersionLocked}
                />
                {(field.key === 'thumb_text' || field.key === 'thumb_notext') && (
                  <div style={{ width: '150px', border: '1px dashed #666', borderRadius: '4px', padding: '10px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.2)' }}>
                    {renderImagePreviews(field.key)}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
