// publicationTime.js - Time utilities and constants for channel publication slots and AM/PM formatting

export const HOURS_12 = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'))
export const MINUTES_60 = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'))
export const MINUTES_15 = ['00', '15', '30', '45']
export const DEFAULT_TIME_ZONE = 'Asia/Ho_Chi_Minh'

export function getBrowserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || DEFAULT_TIME_ZONE
}

function hasExplicitTimeZone(value) {
  return /(?:Z|[+-]\d{2}:?\d{2})$/i.test(String(value || '').trim())
}

function normalizeOffsetLabel(value) {
  const raw = String(value || '').replace(/^GMT/i, 'UTC')
  if (raw === 'UTC') return 'UTC+00:00'
  const match = raw.match(/^UTC([+-])(\d{1,2})(?::?(\d{2}))?$/i)
  if (!match) return raw || 'UTC+00:00'
  return `UTC${match[1]}${match[2].padStart(2, '0')}:${match[3] || '00'}`
}

function formatLegacyDateTime(value, includeSeconds) {
  const match = String(value || '').trim().match(
    /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?$/
  )
  if (!match) return `${String(value)} · TZ không xác định · legacy`
  const [, year, month, day, hourText, minute, second = '00'] = match
  let hour = Number(hourText)
  const period = hour >= 12 ? 'PM' : 'AM'
  hour %= 12
  if (hour === 0) hour = 12
  const time = includeSeconds
    ? `${String(hour).padStart(2, '0')}:${minute}:${second} ${period}`
    : `${String(hour).padStart(2, '0')}:${minute} ${period}`
  return `${day}/${month}/${year} ${time} · TZ không xác định · legacy`
}

function getDateTimeParts(date, timeZone, includeSeconds) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...(includeSeconds ? { second: '2-digit' } : {}),
    hour12: true,
    timeZoneName: 'longOffset'
  })
  const parts = Object.fromEntries(
    formatter.formatToParts(date).map(part => [part.type, part.value])
  )
  return {
    day: parts.day,
    month: parts.month,
    year: parts.year,
    hour: parts.hour,
    minute: parts.minute,
    second: parts.second,
    period: parts.dayPeriod,
    offset: normalizeOffsetLabel(parts.timeZoneName)
  }
}

export function formatTimeZoneLabel(timeZone = getBrowserTimeZone(), value = Date.now()) {
  const targetTimeZone = timeZone || getBrowserTimeZone()
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return `Timezone không hợp lệ · ${targetTimeZone}`
  try {
    const { offset } = getDateTimeParts(date, targetTimeZone, false)
    return `${targetTimeZone} · ${offset}`
  } catch {
    return `Timezone không hợp lệ · ${targetTimeZone}`
  }
}

/**
 * Parse a 24-hour ("HH:MM") or 12-hour ("hh:mm AM/PM") time string into 12-hour components { hour, minute, period }.
 * Defaults to 09:00 AM on invalid input.
 */
export function parseTime24(timeStr) {
  const str = String(timeStr || '').trim()
  const match = str.match(/^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM|am|pm)?$/i)
  let h = 9
  let m = 0
  let explicitPeriod = null

  if (match) {
    h = parseInt(match[1], 10)
    m = parseInt(match[2], 10)
    if (match[3]) {
      explicitPeriod = match[3].toUpperCase()
    }
  }

  if (isNaN(h) || h < 0 || h > 23) h = 9
  if (isNaN(m) || m < 0 || m > 59) m = 0

  let period
  let hour12

  if (explicitPeriod) {
    period = explicitPeriod
    hour12 = h % 12
    if (hour12 === 0) hour12 = 12
  } else {
    period = h >= 12 ? 'PM' : 'AM'
    hour12 = h % 12
    if (hour12 === 0) hour12 = 12
  }

  return {
    hour: String(hour12).padStart(2, '0'),
    minute: String(m).padStart(2, '0'),
    period,
  }
}

/**
 * Format 12-hour components (hour: 01-12, minute: 00-59, period: 'AM'|'PM') into "HH:MM" 24h format.
 */
export function formatTime24(hour12, minute, period) {
  let h = parseInt(hour12, 10)
  if (isNaN(h) || h < 1 || h > 12) h = 12
  let m = parseInt(minute, 10)
  if (isNaN(m) || m < 0 || m > 59) m = 0

  if (period === 'PM') {
    if (h < 12) h += 12
  } else if (period === 'AM') {
    if (h === 12) h = 0
  }

  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/**
 * Format a 24-hour time string ("HH:MM") or any time string into 12-hour AM/PM format ("hh:mm AM/PM").
 * e.g. "14:30" -> "02:30 PM", "09:00" -> "09:00 AM"
 */
export function formatTime12(timeStr) {
  if (!timeStr) return '-'
  const { hour, minute, period } = parseTime24(timeStr)
  return `${hour}:${minute} ${period}`
}

/**
 * Format a Date, timestamp (seconds or ms), or ISO string into Vietnamese date + 12h AM/PM time.
 * Output: "DD/MM/YYYY hh:mm:ss AM/PM" or "DD/MM/YYYY hh:mm AM/PM"
 */
export function formatDateTimeAMPM(
  value,
  { includeSeconds = true, timeZone = getBrowserTimeZone() } = {}
) {
  if (!value) return '-'
  let date
  if (value instanceof Date) {
    date = value
  } else if (typeof value === 'number') {
    date = new Date(value > 1e11 ? value : value * 1000)
  } else {
    const raw = String(value).trim()
    if (!hasExplicitTimeZone(raw)) {
      return formatLegacyDateTime(raw, includeSeconds)
    }
    date = new Date(raw)
  }
  if (Number.isNaN(date.getTime())) return String(value)
  try {
    const parts = getDateTimeParts(date, timeZone, includeSeconds)
    const timePart = includeSeconds
      ? `${parts.hour}:${parts.minute}:${parts.second} ${parts.period}`
      : `${parts.hour}:${parts.minute} ${parts.period}`
    return `${parts.day}/${parts.month}/${parts.year} ${timePart} · ${parts.offset} · ${timeZone}`
  } catch {
    return `${String(value)} · Timezone không hợp lệ · ${timeZone}`
  }
}

/**
 * Snap a 24-hour ("HH:MM") time string to the nearest 15-minute interval (:00, :15, :30, :45).
 * e.g. "11:59" -> "12:00", "07:07" -> "07:15", "07:02" -> "07:00"
 */
export function snapTime24To15(timeStr) {
  const { hour, minute, period } = parseTime24(timeStr)
  let h = parseInt(hour, 10)
  if (isNaN(h) || h < 1 || h > 12) h = 12
  let m = parseInt(minute, 10)
  if (isNaN(m) || m < 0 || m > 59) m = 0

  if (period === 'PM') {
    if (h < 12) h += 12
  } else if (period === 'AM') {
    if (h === 12) h = 0
  }

  const totalMinutes = h * 60 + m
  const snappedTotal = Math.round(totalMinutes / 15) * 15
  const finalH = Math.floor(snappedTotal / 60) % 24
  const finalM = snappedTotal % 60

  return `${String(finalH).padStart(2, '0')}:${String(finalM).padStart(2, '0')}`

}
