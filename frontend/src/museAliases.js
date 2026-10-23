export function splitMuseAliasDraft(value) {
  return String(value ?? '').split(',')
}

export function getMuseAliasInputValue(referenceSet) {
  if (typeof referenceSet?.aliases_draft === 'string') {
    return referenceSet.aliases_draft
  }
  return (Array.isArray(referenceSet?.aliases) ? referenceSet.aliases : []).join(', ')
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
