import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const settingsSource = readFileSync(new URL('./Settings.jsx', import.meta.url), 'utf8');

test('Settings exposes Content Mode and Cast Settings management for dialogue', () => {
  assert.match(settingsSource, /content_mode/);
  assert.match(settingsSource, /👥 Chế độ Đối thoại/);
  assert.match(settingsSource, /🎙️ Chế độ Đơn thoại/);
  assert.match(settingsSource, /cast_settings/);
  assert.match(settingsSource, /mc/);
  assert.match(settingsSource, /guest_1/);
  assert.match(settingsSource, /guest_2/);
  assert.match(settingsSource, /turn_pause_seconds/);
  assert.match(settingsSource, /\/cast-settings`/);
  assert.match(settingsSource, /\/content-mode`/);
});
