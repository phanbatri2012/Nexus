import assert from 'node:assert/strict'
import test from 'node:test'

import { PIPELINE_STAGES, formatSeconds, resolveJobStage } from './videoProgressResolver.js'

test('formats seconds to mm:ss safely', () => {
  assert.equal(formatSeconds(0), '00:00')
  assert.equal(formatSeconds(65), '01:05')
  assert.equal(formatSeconds(360), '06:00')
  assert.equal(formatSeconds(null), '00:00')
  assert.equal(formatSeconds(-10), '00:00')
  assert.equal(formatSeconds(NaN), '00:00')
})

test('resolves queued jobs with stage 0 and minimal percent', () => {
  const result = resolveJobStage({ status: 'queued', queue_position: 2 })
  assert.equal(result.stageIndex, 0)
  assert.equal(result.stageId, 'ingest')
  assert.equal(result.overallPercent, 2)
  assert.match(result.subMessage, /Vị trí #2/)
})

test('resolves transcript ingestion stage', () => {
  const result = resolveJobStage({
    status: 'running',
    progress: 'Đang tải phụ đề YouTube'
  })
  assert.equal(result.stageIndex, 0)
  assert.equal(result.stageId, 'ingest')
  assert.equal(result.overallPercent, 10)
  assert.equal(result.stageName, 'Thu thập Dữ liệu & Phụ đề')
})

test('resolves ChatGPT scriptwriting stage', () => {
  const outlineResult = resolveJobStage({
    status: 'running',
    progress: 'ChatGPT đang viết kịch bản: đang lập dàn ý...'
  })
  assert.equal(outlineResult.stageIndex, 1)
  assert.equal(outlineResult.stageId, 'script')
  assert.equal(outlineResult.overallPercent, 22)

  const dbResult = resolveJobStage({
    status: 'running',
    progress: 'Đang lưu vào database'
  })
  assert.equal(dbResult.stageIndex, 1)
  assert.equal(dbResult.overallPercent, 48)
})

test('resolves OmniVoice TTS audio stage with chunk progress', () => {
  const audioResult = resolveJobStage({
    status: 'running',
    progress: 'OmniVoice đang tạo audio phân đoạn 6/12'
  })
  assert.equal(audioResult.stageIndex, 2)
  assert.equal(audioResult.stageId, 'audio')
  // 50 + (6/12)*25 = 50 + 12.5 = 63%
  assert.equal(audioResult.overallPercent, 63)
  assert.equal(audioResult.stageName, 'Tổng hợp Giọng đọc AI (TTS)')
})

test('resolves visual scene planning stage', () => {
  const visualResult = resolveJobStage({
    status: 'running',
    progress: 'Google Flow đang sinh ảnh phân cảnh 3'
  })
  assert.equal(visualResult.stageIndex, 3)
  assert.equal(visualResult.stageId, 'visual')
  assert.equal(visualResult.overallPercent, 82)
})

test('resolves video render and done stage', () => {
  const renderResult = resolveJobStage({
    status: 'running',
    progress: 'FFmpeg đang dựng video MP4'
  })
  assert.equal(renderResult.stageIndex, 4)
  assert.equal(renderResult.stageId, 'render')
  assert.equal(renderResult.overallPercent, 92)

  const doneResult = resolveJobStage({
    status: 'done',
    progress: 'Kịch bản và audio đã hoàn thành'
  })
  assert.equal(doneResult.stageIndex, 4)
  assert.equal(doneResult.stageId, 'render')
  assert.equal(doneResult.overallPercent, 100)
  assert.equal(doneResult.stageName, 'Đã hoàn thành')
})

test('exposes all 5 core stages in PIPELINE_STAGES', () => {
  assert.equal(PIPELINE_STAGES.length, 5)
  assert.deepEqual(PIPELINE_STAGES.map(s => s.id), ['ingest', 'script', 'audio', 'visual', 'render'])
})
