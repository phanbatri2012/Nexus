const MIN_VIDEO_ID = 1
const INTEGER_TEXT_PATTERN = /^\d+$/

export function normalizeVideoId(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null

  const candidate = typeof value === 'string' ? value.trim() : value
  if (candidate === '' || (typeof candidate === 'string' && !INTEGER_TEXT_PATTERN.test(candidate))) {
    return null
  }

  const normalizedId = Number(candidate)
  if (!Number.isSafeInteger(normalizedId) || normalizedId < MIN_VIDEO_ID) return null
  return normalizedId
}

export function isSameVideoId(left, right) {
  const normalizedLeft = normalizeVideoId(left)
  const normalizedRight = normalizeVideoId(right)
  return normalizedLeft !== null && normalizedLeft === normalizedRight
}
