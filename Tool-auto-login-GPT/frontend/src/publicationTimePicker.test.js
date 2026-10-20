import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseTime24, formatTime24, formatTime12, formatDateTimeAMPM } from './publicationTime.js'

const componentSource = readFileSync(
  new URL('./PublicationTimePicker.jsx', import.meta.url),
  'utf8'
)

test('PublicationTimePicker component exposes 12h AM/PM selectors and 24h helper', () => {
  assert.match(componentSource, /parseTime24/)
  assert.match(componentSource, /formatTime24/)
  assert.match(componentSource, /AM/)
  assert.match(componentSource, /PM/)
  assert.match(componentSource, /publication-time-picker/)
})

test('parseTime24 converts morning 24h to 12h AM format', () => {
  assert.deepEqual(parseTime24('09:00'), { hour: '09', minute: '00', period: 'AM' })
  assert.deepEqual(parseTime24('00:00'), { hour: '12', minute: '00', period: 'AM' })
  assert.deepEqual(parseTime24('00:30'), { hour: '12', minute: '30', period: 'AM' })
  assert.deepEqual(parseTime24('11:59'), { hour: '11', minute: '59', period: 'AM' })
})

test('parseTime24 converts afternoon/evening 24h to 12h PM format', () => {
  assert.deepEqual(parseTime24('12:00'), { hour: '12', minute: '00', period: 'PM' })
  assert.deepEqual(parseTime24('12:30'), { hour: '12', minute: '30', period: 'PM' })
  assert.deepEqual(parseTime24('18:00'), { hour: '06', minute: '00', period: 'PM' })
  assert.deepEqual(parseTime24('23:59'), { hour: '11', minute: '59', period: 'PM' })
})

test('parseTime24 parses explicit 12h AM/PM strings', () => {
  assert.deepEqual(parseTime24('09:00 AM'), { hour: '09', minute: '00', period: 'AM' })
  assert.deepEqual(parseTime24('12:00 AM'), { hour: '12', minute: '00', period: 'AM' })
  assert.deepEqual(parseTime24('02:30 PM'), { hour: '02', minute: '30', period: 'PM' })
  assert.deepEqual(parseTime24('11:45 pm'), { hour: '11', minute: '45', period: 'PM' })
})

test('formatTime24 converts 12h AM format back to 24h', () => {
  assert.equal(formatTime24('12', '00', 'AM'), '00:00')
  assert.equal(formatTime24('12', '30', 'AM'), '00:30')
  assert.equal(formatTime24('09', '00', 'AM'), '09:00')
  assert.equal(formatTime24('11', '59', 'AM'), '11:59')
})

test('formatTime24 converts 12h PM format back to 24h', () => {
  assert.equal(formatTime24('12', '00', 'PM'), '12:00')
  assert.equal(formatTime24('12', '30', 'PM'), '12:30')
  assert.equal(formatTime24('06', '00', 'PM'), '18:00')
  assert.equal(formatTime24('11', '59', 'PM'), '23:59')
})

test('formatTime12 converts 24h string to 12h AM/PM standard', () => {
  assert.equal(formatTime12('09:00'), '09:00 AM')
  assert.equal(formatTime12('14:30'), '02:30 PM')
  assert.equal(formatTime12('00:00'), '12:00 AM')
  assert.equal(formatTime12('12:00'), '12:00 PM')
  assert.equal(formatTime12('23:59'), '11:59 PM')
})

test('formatDateTimeAMPM formats dates with 12h AM/PM standard', () => {
  assert.equal(formatDateTimeAMPM(null), '-')
  assert.equal(formatDateTimeAMPM(''), '-')
  const d = new Date(2026, 9, 20, 14, 30, 45) // 20 Oct 2026 14:30:45
  assert.equal(formatDateTimeAMPM(d, { includeSeconds: true }), '20/10/2026 02:30:45 PM')
  assert.equal(formatDateTimeAMPM(d, { includeSeconds: false }), '20/10/2026 02:30 PM')
  const morningDate = new Date(2026, 9, 20, 9, 5, 0)
  assert.equal(formatDateTimeAMPM(morningDate, { includeSeconds: false }), '20/10/2026 09:05 AM')
})

test('12h and 24h conversions are lossless across all 1,440 minutes in a day', () => {
  for (let h = 0; h < 24; h++) {
    for (let m = 0; m < 60; m++) {
      const time24 = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
      const parsed = parseTime24(time24)
      const formatted = formatTime24(parsed.hour, parsed.minute, parsed.period)
      assert.equal(formatted, time24)
    }
  }
})
