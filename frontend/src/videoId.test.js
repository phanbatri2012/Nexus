import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { isSameVideoId, normalizeVideoId } from './videoId.js'


test('normalizes numeric and URL video IDs to positive safe integers', () => {
  assert.equal(normalizeVideoId(261), 261)
  assert.equal(normalizeVideoId('261'), 261)
  assert.equal(normalizeVideoId(' 261 '), 261)
  assert.equal(normalizeVideoId('001'), 1)
})

test('rejects invalid video IDs before they reach an API request', () => {
  const invalidValues = [null, undefined, '', ' ', 'abc', '1.5', -1, 0, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1]
  for (const value of invalidValues) {
    assert.equal(normalizeVideoId(value), null)
  }
})

test('compares normalized IDs without accepting a different video', () => {
  assert.equal(isSameVideoId('261', 261), true)
  assert.equal(isSameVideoId(261, '261'), true)
  assert.equal(isSameVideoId('261', 262), false)
  assert.equal(isSameVideoId('invalid', 'invalid'), false)
})

test('normalizes deep-link and API IDs before storing or comparing them', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import \{ isSameVideoId, normalizeVideoId \} from ['"]\.\/videoId\.js['"]/)
  assert.match(appSource, /const videoId = normalizeVideoId\(router\.subPath\.split\('\/'\)\[0\]\)/)
  assert.match(appSource, /const normalizedId = normalizeVideoId\(id\)/)
  assert.match(appSource, /setCurrentVideoId\(normalizedId\)/)
  assert.match(appSource, /if \(!isSameVideoId\(data\.video_id, requestedVideoId\)\)/)
  assert.doesNotMatch(appSource, /data\.video_id !== requestedVideoId/)
})
