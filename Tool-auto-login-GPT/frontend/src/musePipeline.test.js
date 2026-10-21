import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const settingsSource = fs.readFileSync(new URL('./Settings.jsx', import.meta.url), 'utf8')
const appSource = fs.readFileSync(new URL('./App.jsx', import.meta.url), 'utf8')
const jobCenterSource = fs.readFileSync(new URL('./JobCenter.jsx', import.meta.url), 'utf8')

test('Settings exposes the independent Muse pipeline and profile controls', () => {
  assert.match(settingsSource, /render_mode.*muse_ai/s)
  assert.match(settingsSource, /video_coverage_mode/)
  assert.match(settingsSource, /intro_only/)
  assert.match(settingsSource, /first_n_minutes/)
  assert.match(settingsSource, /reference_sets/)
  assert.match(settingsSource, /\/api\/muse\/settings/)
  assert.match(settingsSource, /\/api\/muse\/profile\/\$\{action\}/)
  assert.match(settingsSource, /runMuseProfileAction\('open'\)/)
  assert.match(settingsSource, /runMuseProfileAction\('verify'\)/)
})

test('App and Job Center recognize Muse media jobs', () => {
  assert.match(appSource, /muse_media_generation/)
  assert.match(appSource, /Tạo media Muse AI/)
  assert.match(appSource, /Muse AI & Subtitles/)
  assert.match(jobCenterSource, /muse_media_generation/)
  assert.match(jobCenterSource, /Muse AI/)
})
