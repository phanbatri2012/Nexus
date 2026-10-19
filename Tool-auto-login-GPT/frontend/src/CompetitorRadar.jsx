import { useCallback, useEffect, useMemo, useState } from 'react'
import './CompetitorRadar.css'

const API = 'http://127.0.0.1:8080/api'

function errorMessage(payload, fallback) {
  const detail = payload?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) {
    const missing = Array.isArray(detail.missing_configuration)
      ? ` Thiếu: ${detail.missing_configuration.join(', ')}.`
      : ''
    return detail.message + missing
  }
  return payload?.error || fallback
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(errorMessage(payload, `HTTP ${response.status}`))
  return payload
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('vi-VN')
}

function statusLabel(status) {
  return ({
    enabled: 'Đang chạy', paused: 'Tạm dừng', reserved: 'Đã giữ chỗ', queued: 'Đang chờ',
    processing: 'Đang xử lý', generation_complete: 'Đã tạo nội dung', qc_passed: 'QC đạt',
    needs_attention: 'Cần xử lý', scheduled: 'Đã lên lịch', published: 'Đã đăng',
    duplicate: 'Đã có video', error: 'Lỗi',
  })[status] || status || '—'
}

function scanSourceLabel(source) {
  return ({
    rss: 'RSS + xác minh',
    proxy_html: 'Proxy HTML',
    gpm_browser: 'GPM Browser',
  })[source] || 'Chưa quét'
}

function usageModes(voice) {
  return voice.casting_profile?.usage_modes ||
    (voice.casting_profile?.auto_cast ? ['multi_guest'] : ['single_narrator'])
}

function promptLabel(promptVersions, promptVersion) {
  return promptVersions?.[promptVersion]?.name || promptVersion || '—'
}

export default function CompetitorRadar({ onOpenJobCenter }) {
  const [rules, setRules] = useState([])
  const [history, setHistory] = useState([])
  const [prompts, setPrompts] = useState({ active_version: '', versions: {} })
  const [channels, setChannels] = useState([])
  const [voices, setVoices] = useState([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [report, setReport] = useState(null)
  const [form, setForm] = useState({
    competitor: '', prompt_version: '', target_youtube_channel_id: '',
    publication_mode: 'inherit', window_mode: 'learned', start: '08:00', end: '22:00',
    guest_voice_pool: '',
    enabled: true,
  })

  const load = useCallback(async () => {
    const [ruleData, historyData, promptData, channelData, voiceData] = await Promise.all([
      request('/competitors/rules'),
      request('/competitors/history?limit=100'),
      request('/prompts'),
      request('/youtube-comments/channels'),
      request('/tts/voices?status=active'),
    ])
    setRules(ruleData.rules || [])
    setHistory(historyData.dispatches || [])
    setPrompts(promptData || { active_version: '', versions: {} })
    setChannels(channelData.channels || channelData.items || channelData || [])
    setVoices(voiceData.items || [])
    const availablePools = [...new Set((voiceData.items || []).flatMap(voice =>
      usageModes(voice).includes('multi_guest') ? (voice.casting_profile?.voice_pools || []) : []
    ))].sort()
    setForm(current => ({
      ...current,
      prompt_version: current.prompt_version || promptData.active_version || Object.keys(promptData.versions || {})[0] || '',
      guest_voice_pool: current.guest_voice_pool || availablePools[0] || '',
    }))
  }, [])

  useEffect(() => {
    load().catch(err => setError(err.message))
    const timer = setInterval(() => load().catch(() => {}), 15000)
    return () => clearInterval(timer)
  }, [load])

  const promptOptions = useMemo(() => Object.entries(prompts.versions || {}), [prompts])
  const guestPoolOptions = useMemo(() => {
    const pools = new Map()
    voices.forEach(voice => {
      if (!usageModes(voice).includes('multi_guest')) return
      const voicePools = voice.casting_profile?.voice_pools || []
      voicePools.forEach(pool => {
        const current = pools.get(pool) || { id: pool, voices: [] }
        current.voices.push(voice)
        pools.set(pool, current)
      })
    })
    return [...pools.values()].sort((left, right) => left.id.localeCompare(right.id))
  }, [voices])

  const run = async (key, action, message) => {
    setBusy(key)
    setError('')
    setNotice('')
    try {
      const value = await action()
      if (message) setNotice(message)
      await load()
      return value
    } catch (err) {
      setError(err.message)
      return null
    } finally {
      setBusy('')
    }
  }

  const createRule = event => {
    event.preventDefault()
    const payload = {
      competitor: form.competitor.trim(),
      prompt_version: form.prompt_version,
      target_youtube_channel_id: form.target_youtube_channel_id ? Number(form.target_youtube_channel_id) : null,
      publication_mode: form.publication_mode,
      window_mode: form.window_mode,
      guest_voice_pool: form.guest_voice_pool,
      manual_windows: form.window_mode === 'manual'
        ? [{ days: [0, 1, 2, 3, 4, 5, 6], start: form.start, end: form.end }]
        : [],
      enabled: form.enabled,
    }
    return run('create', () => request('/competitors/rules', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }), 'Đã tạo rule; Radar đang lấy tối đa 15 video dài làm baseline và không tạo video lịch sử.').then(result => {
      if (result) setForm(current => ({ ...current, competitor: '' }))
    })
  }

  const changeStatus = (rule, action) => run(`status-${rule.id}`, () =>
    request(`/competitors/rules/${rule.id}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }),
    }), action === 'pause' ? 'Đã tạm dừng rule.' : 'Đã bật lại rule và xếp lượt quét.')

  const scanNow = rule => run(`scan-${rule.id}`, () =>
    request(`/competitors/rules/${rule.id}/scan`, { method: 'POST' }),
  'Đã xếp lượt quét ngay vào Job Center.')

  const changeGuestPool = (rule, guestVoicePool) => run(`pool-${rule.id}`, () =>
    request(`/competitors/rules/${rule.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ guest_voice_pool: guestVoicePool }),
    }), 'Đã cập nhật nhóm giọng khách cho rule.')

  const deleteRule = rule => {
    if (!confirm(`Xóa rule theo dõi ${rule.competitor_title || rule.competitor_youtube_channel_id}?`)) return
    run(`delete-${rule.id}`, () => request(`/competitors/rules/${rule.id}`, { method: 'DELETE' }), 'Đã xóa rule Radar.')
  }

  const openReport = async item => {
    if (!item.video_id) return
    setBusy(`report-${item.id}`)
    setError('')
    try {
      const [casting, qc] = await Promise.all([
        request(`/competitors/videos/${item.video_id}/casting`),
        request(`/competitors/videos/${item.video_id}/qc`),
      ])
      setReport({ item, casting: casting.assignments || [], qc: qc.reviews || [] })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="radar-page">
      <header className="radar-heading">
        <div>
          <h1>Competitor Radar</h1>
          <p>Theo dõi video dài mới, tự động tạo nội dung và kiểm tra chất lượng trước khi phát hành.</p>
        </div>
        <div className="radar-heading-actions">
          <button onClick={() => load().catch(err => setError(err.message))}>↻ Làm mới</button>
          <button onClick={onOpenJobCenter}>Mở Job Center</button>
        </div>
      </header>

      {error && <div className="radar-alert error">{error}</div>}
      {notice && <div className="radar-alert success">{notice}</div>}

      <form className="radar-card radar-create" onSubmit={createRule}>
        <div className="radar-card-title">
          <div><h2>Thêm đối thủ</h2><small>Chỉ theo dõi video thường dài từ 3 phút; loại Shorts, live và premiere.</small></div>
          <label className="radar-toggle"><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} /> Bật tự động</label>
        </div>
        <div className="radar-form-grid">
          <label className="wide">URL / @handle / Channel ID
            <input required value={form.competitor} onChange={event => setForm({ ...form, competitor: event.target.value })} placeholder="https://youtube.com/@doi-thu" />
          </label>
          <label>Bộ prompt
            <select required value={form.prompt_version} onChange={event => setForm({ ...form, prompt_version: event.target.value })}>
              {promptOptions.map(([id, prompt]) => <option key={id} value={id}>{prompt.name || id}</option>)}
            </select>
          </label>
          <label>Kênh đích
            <select value={form.target_youtube_channel_id} onChange={event => setForm({ ...form, target_youtube_channel_id: event.target.value })}>
              <option value="">Theo bộ prompt</option>
              {channels.map(channel => <option key={channel.id} value={channel.id}>{channel.title}</option>)}
            </select>
          </label>
          <label>Phát hành
            <select value={form.publication_mode} onChange={event => setForm({ ...form, publication_mode: event.target.value })}>
              <option value="inherit">Theo bộ prompt</option><option value="schedule">Lên lịch</option><option value="public">Đăng ngay Public</option>
            </select>
          </label>
          <label>Nhóm giọng khách
            <select required value={form.guest_voice_pool} onChange={event => setForm({ ...form, guest_voice_pool: event.target.value })}>
              <option value="">Chọn nhóm giọng an toàn</option>
              {guestPoolOptions.map(pool => <option key={pool.id} value={pool.id}>{pool.id} · {pool.voices.length} giọng</option>)}
            </select>
          </label>
          <label>Khung quét
            <select value={form.window_mode} onChange={event => setForm({ ...form, window_mode: event.target.value })}>
              <option value="learned">Tự học giờ đăng</option><option value="manual">Khung giờ thủ công</option>
            </select>
          </label>
          {form.window_mode === 'manual' && <>
            <label>Bắt đầu <input type="time" required value={form.start} onChange={event => setForm({ ...form, start: event.target.value })} /></label>
            <label>Kết thúc <input type="time" required value={form.end} onChange={event => setForm({ ...form, end: event.target.value })} /></label>
          </>}
        </div>
        <div className="radar-create-footer">
          <small>{guestPoolOptions.length ? 'Radar chỉ auto-cast các giọng khách trong nhóm đã chọn.' : 'Chưa có nhóm giọng khách. Hãy cấp quyền và nhóm trong mục TTS.'}</small>
          <button className="primary" disabled={busy === 'create' || !form.guest_voice_pool}>{busy === 'create' ? 'Đang resolve qua GPM…' : 'Tạo rule tự động'}</button>
        </div>
      </form>

      <section className="radar-card">
        <div className="radar-card-title"><div><h2>Watch rules</h2><small>{rules.length} rule · chỉ video dài ≥ 3 phút · baseline không sinh video lịch sử</small></div></div>
        {!rules.length ? <p className="radar-empty">Chưa có đối thủ nào được theo dõi.</p> : <div className="radar-rule-grid">
          {rules.map(rule => <article className="radar-rule" key={rule.id}>
            <div className="radar-rule-head">
              <div><h3>{rule.competitor_title || rule.competitor_handle || rule.competitor_youtube_channel_id}</h3><small>{promptLabel(prompts.versions, rule.prompt_version)} → {rule.target_channel_title}</small></div>
              <span className={`radar-status ${rule.status}`}>{statusLabel(rule.status)}</span>
            </div>
            <dl>
              <div><dt>Baseline</dt><dd>{rule.baseline_complete ? `${rule.baseline_video_count} video dài` : 'Chưa lấy'}</dd></div>
              <div><dt>Phát hành</dt><dd>{rule.publication_mode}</dd></div>
              <div><dt>Lần quét tới</dt><dd>{formatDate(rule.next_scan_at)}</dd></div>
              <div><dt>Nhịp quét</dt><dd>{rule.window_mode === 'manual' ? 'Thủ công' : 'Tự học'}</dd></div>
              <div><dt>Nguồn quét</dt><dd>{scanSourceLabel(rule.last_scan_source)}</dd></div>
              <div><dt>RSS</dt><dd>{rule.rss_retry_after ? `Thử lại ${formatDate(rule.rss_retry_after)}` : (rule.rss_failure_count ? `${rule.rss_failure_count} lần lỗi` : 'Sẵn sàng')}</dd></div>
              <div><dt>Nhóm giọng khách</dt><dd>{rule.guest_voice_pool || 'Chưa cấu hình'}</dd></div>
            </dl>
            {rule.last_error && <p className="radar-inline-error">{rule.last_error}</p>}
            <div className="radar-actions">
              <select
                aria-label={`Nhóm giọng khách cho ${rule.competitor_title || rule.id}`}
                value={rule.guest_voice_pool || ''}
                disabled={busy === `pool-${rule.id}`}
                onChange={event => changeGuestPool(rule, event.target.value)}
              >
                <option value="">Chọn nhóm giọng</option>
                {guestPoolOptions.map(pool => <option key={pool.id} value={pool.id}>{pool.id} · {pool.voices.length} giọng</option>)}
              </select>
              <button disabled={busy === `scan-${rule.id}`} onClick={() => scanNow(rule)}>Quét ngay</button>
              <button disabled={busy === `status-${rule.id}`} onClick={() => changeStatus(rule, rule.status === 'enabled' ? 'pause' : 'resume')}>{rule.status === 'enabled' ? 'Tạm dừng' : 'Bật lại'}</button>
              <button className="danger" disabled={busy === `delete-${rule.id}`} onClick={() => deleteRule(rule)}>Xóa</button>
            </div>
          </article>)}
        </div>}
      </section>

      <section className="radar-card">
        <div className="radar-card-title"><div><h2>Lịch sử phát hiện</h2><small>Mỗi video nguồn × bộ prompt chỉ có một dispatch.</small></div></div>
        <div className="radar-table-wrap"><table className="radar-table"><thead><tr><th>Video nguồn</th><th>Đối thủ</th><th>Prompt</th><th>Phát hiện</th><th>Trạng thái</th><th>Báo cáo</th></tr></thead>
          <tbody>{history.map(item => <tr key={item.id}>
            <td><a href={item.source_url} target="_blank" rel="noreferrer">{item.source_title || item.youtube_video_id}</a></td>
            <td>{item.competitor_title}</td><td>{promptLabel(prompts.versions, item.prompt_version)}</td><td>{formatDate(item.created_at)}</td>
            <td><span className={`radar-status ${item.status}`}>{statusLabel(item.status)}</span>{item.error && <small className="radar-row-error">{item.error}</small>}</td>
            <td><button disabled={!item.video_id || busy === `report-${item.id}`} onClick={() => openReport(item)}>Casting &amp; QC</button></td>
          </tr>)}</tbody>
        </table>{!history.length && <p className="radar-empty">Chưa có video mới sau baseline.</p>}</div>
      </section>

      {report && <div className="radar-modal-backdrop" onClick={() => setReport(null)}><section className="radar-modal" onClick={event => event.stopPropagation()}>
        <div className="radar-card-title"><div><h2>Báo cáo video #{report.item.video_id}</h2><small>{report.item.source_title}</small></div><button onClick={() => setReport(null)}>Đóng</button></div>
        <h3>Casting</h3>
        {!report.casting.length ? <p className="radar-empty">Chưa có casting report.</p> : <table className="radar-table"><thead><tr><th>Nhân vật</th><th>Giọng</th><th>Confidence</th><th>Điểm</th><th>Nguồn</th></tr></thead><tbody>
          {report.casting.map(item => <tr key={item.id}><td>{item.speaker_name || item.speaker_key}</td><td>{item.voice_name || voices.find(voice => voice.id === item.voice_id)?.name || 'Giọng không còn trong danh mục'}</td><td>{Math.round((item.confidence || 0) * 100)}%</td><td>{item.match_score}</td><td>{item.source}</td></tr>)}
        </tbody></table>}
        <h3>Auto QC</h3>
        {!report.qc.length ? <p className="radar-empty">Chưa chạy Auto QC.</p> : report.qc.map(review => <article className="radar-review" key={review.id}>
          <div><b>Lần {review.attempt}: {statusLabel(review.status)}</b><span>{review.repair_stage ? `Repair: ${review.repair_stage}` : ''}</span></div>
          {Object.entries(review.checks || {}).map(([key, check]) => <p key={key} className={check.passed ? 'pass' : 'fail'}>{check.passed ? '✓' : '✕'} {key}: {check.detail}</p>)}
          {review.error && <small className="radar-inline-error">{review.error}</small>}
        </article>)}
      </section></div>}
    </div>
  )
}
