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

test('Settings exposes Variable Chips, Contextual Guides, and Syntax Cheat Sheet', () => {
  assert.match(settingsSource, /PROMPT_FIELD_METADATA/);
  assert.match(settingsSource, /showCheatSheet/);
  assert.match(settingsSource, /handleInsertVariable/);
  assert.match(settingsSource, /Bảng Tra Cứu Biến Số & Cú Pháp Chuẩn/);
  assert.match(settingsSource, /Biến có sẵn:/);
  assert.match(settingsSource, /Prompt đang thiếu biến\/tag bắt buộc:/);
});
