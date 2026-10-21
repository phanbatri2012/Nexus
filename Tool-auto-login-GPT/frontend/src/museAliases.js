export function splitMuseAliasDraft(value) {
  return String(value ?? '')
    .split(',')
    .map(part => part.replace(/^\s+/, ''))
}

export function normalizeMuseAliases(value) {
  const parts = Array.isArray(value) ? value : String(value ?? '').split(',')
  const normalized = []
  const seen = new Set()
  for (const part of parts) {
    const alias = String(part ?? '').trim()
    const key = alias.toLocaleLowerCase('vi')
    if (!alias || seen.has(key)) continue
    seen.add(key)
    normalized.push(alias)
  }
  return normalized
}
