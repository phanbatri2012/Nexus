import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseTime24, formatTime24, formatTime12, formatDateTimeAMPM, formatTimeZoneLabel, MINUTES_15, snapTime24To15 } from './publicationTime.js'

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
  assert.match(componentSource, /MINUTES_15/)
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
  const d = new Date('2026-10-20T07:30:45Z')
  assert.equal(
    formatDateTimeAMPM(d, { includeSeconds: true, timeZone: 'Asia/Ho_Chi_Minh' }),
    '20/10/2026 02:30:45 PM · UTC+07:00 · Asia/Ho_Chi_Minh'
  )
  assert.equal(
    formatDateTimeAMPM(d, { includeSeconds: false, timeZone: 'UTC' }),
    '20/10/2026 07:30 AM · UTC+00:00 · UTC'
  )
})

test('formatDateTimeAMPM computes DST offset at the represented instant', () => {
  assert.equal(
    formatDateTimeAMPM('2026-01-15T12:00:00+00:00', { timeZone: 'America/New_York' }),
    '15/01/2026 07:00:00 AM · UTC-05:00 · America/New_York'
  )
  assert.equal(
    formatDateTimeAMPM('2026-07-15T12:00:00Z', { timeZone: 'America/New_York' }),
    '15/07/2026 08:00:00 AM · UTC-04:00 · America/New_York'
  )
})

test('formatDateTimeAMPM supports epoch and marks timezone-less legacy timestamps', () => {
  const epoch = Date.parse('2026-10-20T07:30:45Z') / 1000
  assert.equal(
    formatDateTimeAMPM(epoch, { timeZone: 'Asia/Ho_Chi_Minh' }),
    '20/10/2026 02:30:45 PM · UTC+07:00 · Asia/Ho_Chi_Minh'
  )
  assert.equal(
    formatDateTimeAMPM('2026-10-20 14:30:45', { timeZone: 'Asia/Ho_Chi_Minh' }),
    '20/10/2026 02:30:45 PM · TZ không xác định · legacy'
  )
})

test('timezone labels and invalid timezone errors are explicit', () => {
  assert.equal(
    formatTimeZoneLabel('Asia/Ho_Chi_Minh', '2026-10-20T07:30:45Z'),
    'Asia/Ho_Chi_Minh · UTC+07:00'
  )
  assert.equal(
    formatDateTimeAMPM('2026-10-20T07:30:45Z', { timeZone: 'Invalid/Timezone' }),
    '2026-10-20T07:30:45Z · Timezone không hợp lệ · Invalid/Timezone'
  )
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

test('MINUTES_15 contains only 15-minute intervals compatible with YouTube Studio', () => {
  assert.deepEqual(MINUTES_15, ['00', '15', '30', '45'])
})

test('snapTime24To15 correctly snaps arbitrary minutes to 15-minute grid', () => {
  assert.equal(snapTime24To15('11:59'), '12:00')
  assert.equal(snapTime24To15('07:08'), '07:15')
  assert.equal(snapTime24To15('07:07'), '07:00')
  assert.equal(snapTime24To15('07:02'), '07:00')
  assert.equal(snapTime24To15('18:30'), '18:30')
  assert.equal(snapTime24To15('23:55'), '00:00')
  assert.equal(snapTime24To15('00:00'), '00:00')
})
