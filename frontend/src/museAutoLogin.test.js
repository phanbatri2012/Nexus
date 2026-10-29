import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  buildMuseAutoLoginPayload,
  getMuseBulkReloginCandidates,
  shouldShowMuseRelogin
} from './museAutoLogin.js'

test('Muse relogin action is visible only for accounts needing a new session', () => {
  assert.equal(shouldShowMuseRelogin({ needs_relogin: true }), true)
  assert.equal(shouldShowMuseRelogin({ needs_relogin: false }), false)
  assert.equal(shouldShowMuseRelogin({}), false)
})

test('bulk Muse relogin selects only expired accounts with saved credentials', () => {
  assert.deepEqual(
    getMuseBulkReloginCandidates([
      { id: 'ready', needs_relogin: false, auto_login_configured: true },
      { id: 'missing', needs_relogin: true, auto_login_configured: false },
      { id: 'candidate-1', needs_relogin: true, auto_login_configured: true },
      { id: 'candidate-2', needs_relogin: true, auto_login_configured: true }
    ]).map(account => account.id),
    ['candidate-1', 'candidate-2']
  )
})

test('Muse Studio exposes the bulk relogin control and progress handler', () => {
  const source = readFileSync(new URL('./MuseStudio.jsx', import.meta.url), 'utf8')
  assert.match(source, /Đăng nhập lại tất cả/)
  assert.match(source, /handleReloginAll/)
  assert.match(source, /Chưa xác minh/)
  assert.match(source, /loginErrors\[acc\.id\]/)
})

test('password payload excludes mailbox data and keeps optional TOTP', () => {
  assert.deepEqual(buildMuseAutoLoginPayload({
    email: ' muse@example.com ',
    method: 'password',
    password: 'secret',
    two_factor_secret: ' ABC123 ',
    mailbox_url: 'https://mail.invalid'
  }), {
    email: 'muse@example.com',
    method: 'password',
    password: 'secret',
    two_factor_secret: 'ABC123'
  })
})

test('mailbox payload excludes password and TOTP data', () => {
  assert.deepEqual(buildMuseAutoLoginPayload({
    email: 'muse@example.com',
    method: 'mailbox',
    password: 'secret',
    two_factor_secret: 'ABC123',
    mailbox_url: ' https://mail.example/inbox '
  }), {
    email: 'muse@example.com',
    method: 'mailbox',
    mailbox_url: 'https://mail.example/inbox'
  })
})
