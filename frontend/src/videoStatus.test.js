import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


test('dashboard exposes the manual error lifecycle and filter', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')
  const commentsSource = await readFile(new URL('./YouTubeComments.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /\['error', '❌ Lỗi'\]/)
  assert.match(appSource, /savedVideos\.count_error/)
  assert.match(appSource, /params\.set\('video_status', 'error'\)/)
  assert.match(appSource, /<option value="error">❌ Lỗi<\/option>/)
  assert.match(appSource, /setVideoLifecycleState/)
  assert.match(appSource, /Đã bỏ qua xử lý/)
  assert.match(commentsSource, /api\/videos\?limit=500&offset=0&video_status=active/)
})

test('marking a video as published requires and atomically saves its published URL', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /nextState === 'published' && !hasPublishedUrl/)
  assert.match(appSource, /Xác nhận video đã đăng \/ đã lên lịch/)
  assert.match(appSource, /api\/videos\/\$\{publicationDialog\.videoId\}\/publications/)
  assert.match(appSource, /JSON\.stringify\(\{ published_url: publishedUrl \}\)/)
  assert.match(appSource, /Lưu link & chuyển Đã đăng/)
  assert.match(appSource, /Video đặt lịch được kiểm tra ngay bằng quyền chủ kênh/)
  assert.match(appSource, /default_youtube_channel_title/)
  assert.match(appSource, /Chưa gắn kênh — link chưa được xác minh/)
  assert.match(appSource, /Bình luận chưa khả dụng/)
  assert.match(appSource, /disabled=\{publicationDialog\.isSaving \|\| !publicationDialog\.videoId \|\| !publicationDialog\.publishedUrl\.trim\(\)\}/)
  assert.doesNotMatch(appSource, /disabled=\{publicationDialog\.isSaving \|\| !publicationDialog\.defaultChannelTitle\}/)
})

test('video actions expose immediate public and schedule publish buttons', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /const \[isPublishingSchedule, setIsPublishingSchedule\] = useState\(false\)/)
  assert.match(appSource, /handlePublishSchedule\s*=\s*async/)
  assert.match(appSource, /api\/videos\/\$\{targetId\}\/publish-schedule/)
  assert.match(appSource, /⚡ Public ngay/)
  assert.match(appSource, /📅 Đặt lịch/)
  assert.match(appSource, /isPublishingSchedule \? '⏳ Đang Đặt lịch\.\.\.' : '📅 Đặt lịch'/)
})

test('mp4 rendering status prioritizes active job progress over stale artifacts and renders detailed completion stats', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /const isRenderActive = Boolean\(isRendering \|\| renderInfo\?\.is_active/)
  assert.match(appSource, /const hasMp4Ready = Boolean\(!isRenderActive && renderInfo\?\.has_mp4\)/)
  assert.match(appSource, /isRenderActive \? \(/)
  assert.match(appSource, /renderInfo\?\.mp4_details\?\.duration_formatted/)
  assert.match(appSource, /renderInfo\?\.mp4_details\?\.size_formatted/)
  assert.match(appSource, /renderInfo\?\.mp4_details\?\.completed_at_formatted/)
})


