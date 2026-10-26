import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatMuseDateTime,
  formatMuseTokens,
  formatMuseWeeklyReset,
  summarizeMuseAccounts
} from './museQuota.js'

test('summarizeMuseAccounts separates usable, exhausted, and unknown accounts', () => {
  assert.deepEqual(
    summarizeMuseAccounts([
      { media_quota_status: 'eligible', enabled: true },
      { media_quota_status: 'exhausted', enabled: true },
      { media_quota_status: 'eligible', is_expired: true },
      { media_quota_status: 'unknown', enabled: true }
    ]),
    { total: 4, eligible: 1, exhausted: 1, unknown: 2 }
  )
})

test('formatMuseDateTime always identifies the selected timezone', () => {
  assert.match(formatMuseDateTime(1_700_000_000, 'UTC'), /UTC/)
})

test('formatMuseWeeklyReset does not invent a timezone absent from Muse', () => {
  assert.equal(
    formatMuseWeeklyReset('Sep 30'),
    'Sep 30 · Muse không cung cấp giờ/múi giờ'
  )
  assert.equal(
    formatMuseWeeklyReset('Sep 30 08:00'),
    'Sep 30 08:00 · Muse không cung cấp múi giờ'
  )
  assert.equal(formatMuseWeeklyReset('Sep 30 08:00 UTC'), 'Sep 30 08:00 UTC')
})

test('formatMuseTokens prefers the provider display and supports numeric fallback', () => {
  assert.equal(formatMuseTokens({ extra_left: '2.7B tokens left' }), '2.7B tokens left')
  assert.equal(formatMuseTokens({ extra_left_value: 0 }), '0 token')
})
