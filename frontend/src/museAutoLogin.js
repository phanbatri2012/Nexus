export function shouldShowMuseRelogin(account) {
  return Boolean(account?.needs_relogin)
}

export function getMuseBulkReloginCandidates(accounts) {
  return (Array.isArray(accounts) ? accounts : []).filter(
    account => shouldShowMuseRelogin(account) && account?.auto_login_configured
  )
}

export function buildMuseAutoLoginPayload(form) {
  const method = form?.method === 'mailbox' ? 'mailbox' : 'password'
  const payload = {
    email: String(form?.email || '').trim(),
    method
  }
  if (method === 'mailbox') {
    payload.mailbox_url = String(form?.mailbox_url || '').trim()
    return payload
  }
  payload.password = String(form?.password || '')
  const twoFactorSecret = String(form?.two_factor_secret || '').trim()
  if (twoFactorSecret) payload.two_factor_secret = twoFactorSecret
  return payload
}
