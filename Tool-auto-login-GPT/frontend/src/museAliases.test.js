import test from 'node:test'
import assert from 'node:assert/strict'

import { normalizeMuseAliases, splitMuseAliasDraft } from './museAliases.js'

test('Muse alias draft preserves spaces inside and after an alias while typing', () => {
  assert.deepEqual(splitMuseAliasDraft('Hồ Chí '), ['Hồ Chí '])
  assert.deepEqual(splitMuseAliasDraft('Hồ Chí, Bác Hồ '), ['Hồ Chí', 'Bác Hồ '])
})

test('Muse alias draft preserves an empty item after a newly typed comma', () => {
  assert.deepEqual(splitMuseAliasDraft('Hồ Chí,'), ['Hồ Chí', ''])
})

test('Muse aliases are trimmed, deduplicated, and cleaned when editing finishes', () => {
  assert.deepEqual(
    normalizeMuseAliases([' Hồ Chí ', 'Bác Hồ ', '', 'hồ chí']),
    ['Hồ Chí', 'Bác Hồ'],
  )
})
