import assert from 'node:assert/strict'
import test from 'node:test'

import { installSecurityFetch } from './securityFetch.js'

test('frontend sends only the canonical Nexus CSRF header', async () => {
  globalThis.window = { location: { href: 'http://127.0.0.1:5173/' } }
  const calls = []
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init })
    if (String(input) === 'http://127.0.0.1:8080/api/security/session') {
      return {
        ok: true,
        status: 200,
        json: async () => ({ csrf_token: 'test-csrf-token' }),
      }
    }
    return { ok: true, status: 200 }
  }

  installSecurityFetch()
  await globalThis.fetch('http://127.0.0.1:8080/api/value', { method: 'POST' })

  assert.equal(calls.length, 2)
  const protectedRequest = calls[1].input
  assert.equal(protectedRequest.headers.get('X-Nexus-CSRF'), 'test-csrf-token')
  assert.equal(protectedRequest.headers.has('X-AutoYT-CSRF'), false)
})
