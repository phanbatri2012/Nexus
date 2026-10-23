import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'


function source(name) {
  return readFileSync(new URL(name, import.meta.url), 'utf8')
}


test('legacy localStorage values are copied before their old keys are removed', () => {
  const contracts = [
    ['ChannelManager.jsx', 'FB_STORAGE_KEY', 'LEGACY_FB_STORAGE_KEY'],
    ['ChannelManager.jsx', 'TT_STORAGE_KEY', 'LEGACY_TT_STORAGE_KEY'],
    ['CrossPoster.jsx', 'FB_STORAGE_KEY', 'LEGACY_FB_STORAGE_KEY'],
    ['TTSSettings.jsx', 'OMNIVOICE_PREVIEW_STORAGE_KEY', 'LEGACY_OMNIVOICE_PREVIEW_STORAGE_KEY'],
    ['JobCenter.jsx', 'DRAIN_MODE_STORAGE_KEY', 'LEGACY_DRAIN_MODE_STORAGE_KEY'],
  ]

  for (const [file, canonicalKey, legacyKey] of contracts) {
    const content = source(file)
    const writeThenDelete = new RegExp(
      `localStorage\\.setItem\\(\\s*${canonicalKey}[\\s\\S]*?localStorage\\.removeItem\\(\\s*${legacyKey}`
    )
    assert.match(content, writeThenDelete, `${file} must write ${canonicalKey} before deleting ${legacyKey}`)
    assert.doesNotMatch(
      content,
      new RegExp(`localStorage\\.setItem\\(\\s*${legacyKey}`),
      `${file} must never write legacy key ${legacyKey}`
    )
  }
})
