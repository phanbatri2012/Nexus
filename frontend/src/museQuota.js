import { formatDateTimeAMPM, getBrowserTimeZone } from './publicationTime.js'

export { getBrowserTimeZone }

export function formatMuseDateTime(unixSeconds, timeZone = getBrowserTimeZone()) {
  const value = Number(unixSeconds)
  if (!Number.isFinite(value) || value <= 0) return 'Không xác định'
  return formatDateTimeAMPM(value, { includeSeconds: true, timeZone })
}

export function formatMuseWeeklyReset(value) {
  const reset = String(value || '').trim()
  if (!reset) return 'Không xác định'
  const hasTime = /\b\d{1,2}:\d{2}\b/.test(reset)
  const hasTimeZone = /\b(?:UTC|GMT|[A-Z]{2,5})\b|[+-]\d{2}:?\d{2}|\b[A-Za-z]+\/[A-Za-z_]+\b/.test(reset)
  if (hasTime && hasTimeZone) return reset
  if (hasTime) return `${reset} · Muse không cung cấp múi giờ`
  return `${reset} · Muse không cung cấp giờ/múi giờ`
}

export function formatMuseTokens(quota) {
  const display = String(quota?.extra_left || '').trim()
  if (display) return display
  const value = Number(quota?.extra_left_value)
  if (!Number.isFinite(value)) return 'Chưa xác định'
  if (value === 0) return '0 token'
  return `${new Intl.NumberFormat('vi-VN', {
    notation: 'compact',
    maximumFractionDigits: 1
  }).format(value)} token`
}

export function summarizeMuseAccounts(accounts) {
  const summary = {
    total: Array.isArray(accounts) ? accounts.length : 0,
    eligible: 0,
    exhausted: 0,
    unknown: 0
  }
  for (const account of accounts || []) {
    const sessionUsable = account.enabled !== false && !account.is_expired && account.session_ok !== false
    const status = sessionUsable ? account.media_quota_status : 'unknown'
    if (status === 'eligible') summary.eligible += 1
    else if (status === 'exhausted') summary.exhausted += 1
    else summary.unknown += 1
  }
  return summary
}
