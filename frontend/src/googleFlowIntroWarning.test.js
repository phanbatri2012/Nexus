import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const jobCenterSource = readFileSync(new URL('./JobCenter.jsx', import.meta.url), 'utf8')

test('Job Center shows a yellow intro fallback summary with affected scenes', () => {
  assert.match(jobCenterSource, /intro_video_summary\?\.fallback_count/)
  assert.match(jobCenterSource, /data-testid="intro-video-fallback-warning"/)
  assert.match(jobCenterSource, /#facc15/)
  assert.match(jobCenterSource, /Intro \{Number\(job\.intro_video_summary\.generated_count/)
  assert.match(jobCenterSource, /cảnh dùng ảnh tĩnh/)
  assert.match(jobCenterSource, /Cảnh fallback:/)
  assert.match(jobCenterSource, /Number\(scene\.scene_index\) \+ 1/)
})

test('Job Center distinguishes Flow reconciliation from generic recovery', () => {
  assert.match(jobCenterSource, /job\.type === 'flow_media_generation'/)
  assert.match(jobCenterSource, /job\.reconcile_pending_count > 0/)
  assert.match(jobCenterSource, /Đối soát Flow:/)
  assert.match(jobCenterSource, /job\.reconcile_poll_count \|\| 0/)
  assert.match(jobCenterSource, /job\.type !== 'flow_media_generation'/)
})
