import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('RenderEngineBadge source file defines standard engine labels, icons and colors', async () => {
  const badgeSource = await readFile(new URL('./RenderEngineBadge.jsx', import.meta.url), 'utf8')

  assert.match(badgeSource, /google_flow:/)
  assert.match(badgeSource, /label:\s*['"]Google Flow['"]/)
  assert.match(badgeSource, /icon:\s*['"]⚡['"]/)

  assert.match(badgeSource, /muse_ai:/)
  assert.match(badgeSource, /label:\s*['"]Muse AI['"]/)
  assert.match(badgeSource, /icon:\s*['"]🎬['"]/)

  assert.match(badgeSource, /stock_video:/)
  assert.match(badgeSource, /label:\s*['"]Video có sẵn['"]/)
  assert.match(badgeSource, /icon:\s*['"]🎞️['"]/)

  assert.match(badgeSource, /draft:/)
  assert.match(badgeSource, /label:\s*['"]Kịch bản gốc['"]/)
  assert.match(badgeSource, /icon:\s*['"]📝['"]/)
})

test('App integrates RenderEngineBadge and engine filter into dashboard toolbar and video cards', async () => {
  const appSource = await readFile(new URL('./App.jsx', import.meta.url), 'utf8')

  assert.match(appSource, /import RenderEngineBadge from '\.\/RenderEngineBadge\.jsx'/)
  assert.match(appSource, /const \[renderEngineFilter, setRenderEngineFilter\] = useState\('all'\)/)
  assert.match(appSource, /params\.set\('render_engine', engineFilter\)/)
  assert.match(appSource, /aria-label="Lọc theo nguồn video"/)
  assert.match(appSource, /<RenderEngineBadge[\s\S]*?engine=\{video\.render_engine\}/)
})

test('VideoGenerationProgressCard integrates RenderEngineBadge into HUD card', async () => {
  const cardSource = await readFile(new URL('./VideoGenerationProgressCard.jsx', import.meta.url), 'utf8')

  assert.match(cardSource, /import RenderEngineBadge from '\.\/RenderEngineBadge\.jsx'/)
  assert.match(cardSource, /'flow_media_generation'/)
  assert.match(cardSource, /'video_render'/)
  assert.match(cardSource, /<RenderEngineBadge[\s\S]*?engine=\{/)
})
