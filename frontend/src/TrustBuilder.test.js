import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

test('Trust Builder exposes automated quotas and lifecycle controls', async () => {
  const source = await readFile(new URL('./TrustBuilder.jsx', import.meta.url), 'utf8')

  for (const field of [
    'daily_watch_target',
    'daily_search_target',
    'daily_like_target',
    'daily_comment_target',
    'daily_subscribe_target',
    'min_watch_minutes'
  ]) {
    assert.match(source, new RegExp(field))
  }

  assert.match(source, /handlePlanAction\('pause'\)/)
  assert.match(source, /handlePlanAction\('run-session'\)/)
  assert.match(source, /selectedPlan\.status === 'paused' \? 'resume' : 'start'/)
  assert.match(source, /Mở lại Engagement/)
  assert.match(source, /reason_code/)
  assert.doesNotMatch(source, /Guided Routine|guided-session|readiness-check/)
})

test('Trust Builder handleCreatePlan resolves channel publication timezone safely', async () => {
  const source = await readFile(new URL('./TrustBuilder.jsx', import.meta.url), 'utf8')

  // Must not have undeclared selectedChannel
  assert.doesNotMatch(source, /!selectedChannel\?\.publication_timezone/)
  // Must safely find channel from channels state
  assert.match(source, /const channel = channels\.find\(c => c\.id === selectedChannelId\)/)
  assert.match(source, /const isVN = !channel\?\.publication_timezone \|\| channel\?\.publication_timezone === 'Asia\/Ho_Chi_Minh'/)
})
