// publicationTime.js - Time utilities and constants for channel publication slots and AM/PM formatting

export const HOURS_12 = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0'))
export const MINUTES_60 = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'))

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
export function formatDateTimeAMPM(value, { includeSeconds = true } = {}) {
  if (!value) return '-'
  let date
  if (value instanceof Date) {
    date = value
  } else if (typeof value === 'number') {
    date = new Date(value > 1e11 ? value : value * 1000)
  } else {
    const raw = String(value).trim()
    date = new Date(raw.endsWith('Z') || raw.includes('+') ? raw : (raw.includes('T') ? raw : raw.replace(' ', 'T')))
  }
  if (Number.isNaN(date.getTime())) return String(value)

  const day = String(date.getDate()).padStart(2, '0')
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const year = date.getFullYear()

  let hours = date.getHours()
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  const period = hours >= 12 ? 'PM' : 'AM'

  hours = hours % 12
  if (hours === 0) hours = 12
  const hour12 = String(hours).padStart(2, '0')

  const timePart = includeSeconds
    ? `${hour12}:${minutes}:${seconds} ${period}`
    : `${hour12}:${minutes} ${period}`

  return `${day}/${month}/${year} ${timePart}`
}
