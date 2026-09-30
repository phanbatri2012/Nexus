/**
 * videoProgressResolver.js
 * Stage mapping and ETA calculations for the Video Generation Progress HUD.
 */

export const PIPELINE_STAGES = [
  { id: 'ingest', label: 'Thu thập & Phụ đề', icon: '📥', minPercent: 0, maxPercent: 15 },
  { id: 'script', label: 'ChatGPT Kịch bản', icon: '✍️', minPercent: 15, maxPercent: 50 },
  { id: 'audio', label: 'Giọng đọc AI (TTS)', icon: '🎙️', minPercent: 50, maxPercent: 75 },
  { id: 'visual', label: 'Phân cảnh & Ảnh', icon: '🎨', minPercent: 75, maxPercent: 90 },
  { id: 'render', label: 'Dựng & Xuất MP4', icon: '🎬', minPercent: 90, maxPercent: 100 }
]

export function formatSeconds(totalSeconds) {
  if (totalSeconds == null || Number.isNaN(totalSeconds) || totalSeconds < 0) return '00:00'
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.floor(totalSeconds % 60)
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function resolveJobStage(job) {
  if (!job) {
    return {
      stageIndex: 0,
      stageId: 'ingest',
      overallPercent: 0,
      stageName: 'Đang khởi tạo',
      subMessage: 'Đang chuẩn bị hàng đợi...'
    }
  }

  const status = job.status || 'queued'
  const progressText = String(job.progress || '').toLowerCase()

  if (status === 'done' || status === 'completed') {
    return {
      stageIndex: 4,
      stageId: 'render',
      overallPercent: 100,
      stageName: 'Đã hoàn thành',
      subMessage: job.progress || 'Toàn bộ quy trình video đã hoàn tất thành công.'
    }
  }

  if (status === 'queued') {
    return {
      stageIndex: 0,
      stageId: 'ingest',
      overallPercent: 2,
      stageName: 'Đang chờ xử lý',
      subMessage: `Vị trí #${job.queue_position || 1} trong hàng đợi...`
    }
  }

  // Check stage based on progress text
  // 5. Render / Final stage
  if (
    progressText.includes('render') ||
    progressText.includes('ffmpeg') ||
    progressText.includes('dựng video') ||
    progressText.includes('muxing') ||
    progressText.includes('kịch bản và audio đã hoàn thành')
  ) {
    return {
      stageIndex: 4,
      stageId: 'render',
      overallPercent: 92,
      stageName: 'Dựng MP4 & Khớp Timeline',
      subMessage: job.progress || 'Đang kết hợp audio, visual scenes và subtitle SRT...'
    }
  }

  // 4. Visual Scene Planning & Images
  if (
    progressText.includes('visual') ||
    progressText.includes('scene') ||
    progressText.includes('phân cảnh') ||
    progressText.includes('google flow') ||
    progressText.includes('comfyui') ||
    progressText.includes('thumbnail') ||
    progressText.includes('sinh ảnh')
  ) {
    return {
      stageIndex: 3,
      stageId: 'visual',
      overallPercent: 82,
      stageName: 'Phân cảnh & Sinh Visual Assets',
      subMessage: job.progress || 'Đang tạo visual prompt và sinh hình ảnh phân cảnh...'
    }
  }

  // 3. Audio & TTS
  if (
    progressText.includes('audio') ||
    progressText.includes('omnivoice') ||
    progressText.includes('genmax') ||
    progressText.includes('giọng đọc') ||
    progressText.includes('kiểm tra kịch bản') ||
    progressText.includes('tạo audio') ||
    progressText.includes('pop/click') ||
    progressText.includes('chuẩn hóa âm thanh')
  ) {
    let percent = 60
    const chunkMatch = progressText.match(/(\d+)\s*[/]\s*(\d+)/)
    if (chunkMatch) {
      const current = parseInt(chunkMatch[1], 10)
      const total = parseInt(chunkMatch[2], 10)
      if (total > 0) {
        percent = Math.min(74, Math.round(50 + (current / total) * 25))
      }
    }
    return {
      stageIndex: 2,
      stageId: 'audio',
      overallPercent: percent,
      stageName: 'Tổng hợp Giọng đọc AI (TTS)',
      subMessage: job.progress || 'OmniVoice / Genmax đang render audio và khử pop/click...'
    }
  }

  // 2. Script & ChatGPT
  if (
    progressText.includes('chatgpt') ||
    progressText.includes('kịch bản') ||
    progressText.includes('dàn ý') ||
    progressText.includes('outline') ||
    progressText.includes('database') ||
    progressText.includes('lưu vào database') ||
    progressText.includes('xác minh phiên')
  ) {
    let percent = 30
    if (progressText.includes('dàn ý') || progressText.includes('outline')) percent = 22
    if (progressText.includes('database')) percent = 48
    return {
      stageIndex: 1,
      stageId: 'script',
      overallPercent: percent,
      stageName: 'ChatGPT Biên soạn Kịch bản',
      subMessage: job.progress || 'ChatGPT đang tạo dàn ý, chi tiết kịch bản và metadata...'
    }
  }

  // 1. Ingestion & Subtitle extraction
  return {
    stageIndex: 0,
    stageId: 'ingest',
    overallPercent: 10,
    stageName: 'Thu thập Dữ liệu & Phụ đề',
    subMessage: job.progress || 'Đang trích xuất transcript YouTube và tiêu đề...'
  }
}
