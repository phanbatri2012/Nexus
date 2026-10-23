import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const appSource = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8');

test('App exposes dynamic Cast Voice selection for dialogue prompt sets', () => {
  assert.match(appSource, /selectedMcVoiceId/);
  assert.match(appSource, /selectedGuest1VoiceId/);
  assert.match(appSource, /selectedGuest2VoiceId/);
  assert.match(appSource, /Phân Vai Giọng Đọc \(Cast Voices\)/);
  assert.match(appSource, /mc-voice-select/);
  assert.match(appSource, /guest1-voice-select/);
  assert.match(appSource, /guest2-voice-select/);
  assert.match(appSource, /Tự động chọn giọng phụ khác biệt/);
  assert.match(appSource, /cast_voice_overrides/);
});
