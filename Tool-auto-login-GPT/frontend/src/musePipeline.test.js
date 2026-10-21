import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const settingsSource = fs.readFileSync(new URL('./Settings.jsx', import.meta.url), 'utf8')
const appSource = fs.readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
const jobCenterSource = fs.readFileSync(new URL('./JobCenter.jsx', import.meta.url), 'utf8')

test('Settings exposes the independent Muse pipeline and Studio API controls', () => {
  assert.match(settingsSource, /render_mode.*muse_ai/s)
  assert.match(settingsSource, /video_coverage_mode/)
  assert.match(settingsSource, /intro_only/)
  assert.match(settingsSource, /first_n_minutes/)
  assert.match(settingsSource, /reference_sets/)
  assert.match(settingsSource, /\/api\/muse\/settings/)
  assert.match(settingsSource, /\/api\/muse\/studio\/open/)
  assert.match(settingsSource, /\/api\/muse\/service\/verify/)
  assert.match(settingsSource, /runMuseStudioAction\('open'\)/)
  assert.match(settingsSource, /runMuseStudioAction\('verify'\)/)
  assert.match(settingsSource, /muse_studio_path/)
  assert.match(settingsSource, /Account khả dụng/)
  assert.doesNotMatch(settingsSource, /museGlobalSettings\.gpm_profile_id/)
  assert.doesNotMatch(settingsSource, /museProfiles/)
  assert.doesNotMatch(settingsSource, /\/api\/muse\/profile/)
})

test('App and Job Center recognize Muse media jobs', () => {
  assert.match(appSource, /muse_media_generation/)
  assert.match(appSource, /Tạo media Muse AI/)
  assert.match(appSource, /Muse AI & Subtitles/)
  assert.match(jobCenterSource, /muse_media_generation/)
  assert.match(jobCenterSource, /Muse AI/)
})
