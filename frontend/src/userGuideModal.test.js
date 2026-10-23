import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

test('UserGuideModal component exists and App renders guide trigger', () => {
  const modalPath = resolve(process.cwd(), 'src/UserGuideModal.jsx')
  const appPath = resolve(process.cwd(), 'src/App.jsx')

  const modalSource = readFileSync(modalPath, 'utf-8')
  const appSource = readFileSync(appPath, 'utf-8')

  assert.match(modalSource, /export default function UserGuideModal/)
  assert.match(modalSource, /quickstart/)
  assert.match(modalSource, /workflow/)
  assert.match(modalSource, /modules/)
  assert.match(modalSource, /troubleshooting/)

  assert.match(appSource, /import UserGuideModal from '\.\/UserGuideModal'/)
  assert.match(appSource, /setShowUserGuide/)
  assert.match(appSource, /header-guide-btn/)
  assert.match(appSource, /<UserGuideModal/)
})
