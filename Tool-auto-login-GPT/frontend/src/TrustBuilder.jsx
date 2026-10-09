import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSubRoute } from './router.js'
import { extractErrorMessage } from './apiError'
import './TrustBuilder.css'

const API_BASE = 'http://127.0.0.1:8080'

const statusMeta = {
  pass: { icon: '✓', label: 'Đạt' },
  warn: { icon: '!', label: 'Cần xem lại' },
  fail: { icon: '×', label: 'Chặn' },
  unknown: { icon: '?', label: 'Chưa xác minh' },
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(extractErrorMessage(data, `HTTP ${response.status}`))
  return data
}

function ReadinessCard({ title, snapshot }) {
  const checks = snapshot?.checks || []
  return (
    <section className="readiness-card">
      <div className="readiness-card-head">
        <div><span className="readiness-eyebrow">Readiness</span><h3>{title}</h3></div>
        <div className="readiness-score">{snapshot?.percentage || 0}%</div>
      </div>
      {checks.length === 0 ? <p className="readiness-empty">Chưa kiểm tra. Hãy chạy kiểm tra thụ động trước.</p> : (
        <div className="readiness-checks">
          {checks.map((check) => {
            const meta = statusMeta[check.status] || statusMeta.unknown
            return (
              <div className={`readiness-check ${check.status}`} key={check.id}>
                <span className="readiness-check-icon">{meta.icon}</span>
                <div>
                  <div className="readiness-check-title">{check.label} {check.required && <small>Bắt buộc</small>}</div>
                  <p>{check.detail}</p>
                  {check.status !== 'pass' && check.action && <em>{check.action}</em>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

export default function TrustBuilder() {
  const [subRoute, setSubRoute] = useSubRoute('trustbuilder', '')
  const [channels, setChannels] = useState([])
  const [plans, setPlans] = useState([])
  const [selectedId, setSelectedId] = useState(() => {
    const routeId = Number.parseInt(subRoute, 10)
    return Number.isSafeInteger(routeId) && routeId > 0 ? routeId : null
  })
  const [detail, setDetail] = useState(null)
  const [sessions, setSessions] = useState([])
  const [busy, setBusy] = useState('')
  const [feedback, setFeedback] = useState(null)
  const [topics, setTopics] = useState('')
  const [sources, setSources] = useState('')
  const [reminderEnabled, setReminderEnabled] = useState(false)
  const [reminderTime, setReminderTime] = useState('09:00')

  const selectedPlan = detail?.plan || plans.find((plan) => plan.id === selectedId)
  const activeSession = useMemo(
    () => sessions.find((session) => ['ready', 'in_progress'].includes(session.status)),
    [sessions],
  )

  const loadOverview = useCallback(async () => {
    const [channelData, planData] = await Promise.all([
      api('/api/youtube-comments/channels'),
      api('/api/trust-builder/plans'),
    ])
    const nextPlans = planData.plans || []
    setChannels(channelData.items || [])
    setPlans(nextPlans)
    setSelectedId((current) => current || nextPlans[0]?.id || null)
  }, [])

  const loadPlan = useCallback(async (planId) => {
    if (!planId) { setDetail(null); setSessions([]); return }
    const [planData, sessionData] = await Promise.all([
      api(`/api/trust-builder/plans/${planId}`),
      api(`/api/trust-builder/plans/${planId}/guided-sessions`),
    ])
    setDetail(planData)
    setSessions(sessionData.sessions || [])
    const plan = planData.plan || {}
    setTopics((plan.niche_keywords || []).join('\n'))
    setSources((plan.approved_sources || []).join('\n'))
    setReminderEnabled(Boolean(plan.reminder_enabled))
    setReminderTime(plan.reminder_time_local || '09:00')
  }, [])

  useEffect(() => { loadOverview().catch((error) => setFeedback({ type: 'error', text: error.message })) }, [loadOverview])
  useEffect(() => { loadPlan(selectedId).catch((error) => setFeedback({ type: 'error', text: error.message })) }, [selectedId, loadPlan])
  useEffect(() => {
    const routeId = Number.parseInt(subRoute, 10)
    if (Number.isSafeInteger(routeId) && routeId > 0 && routeId !== selectedId) setSelectedId(routeId)
  }, [subRoute, selectedId])

  const selectPlan = (planId) => { setSelectedId(planId); setSubRoute(String(planId)) }

  const runAction = async (name, action, successText) => {
    setBusy(name); setFeedback(null)
    try {
      await action()
      await Promise.all([loadOverview(), loadPlan(selectedId)])
      setFeedback({ type: 'success', text: successText })
    } catch (error) {
      setFeedback({ type: 'error', text: error.message })
    } finally { setBusy('') }
  }

  const createPlan = async (channelId) => {
    setBusy(`create-${channelId}`)
    try {
      const result = await api('/api/trust-builder/plans', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ channel_db_id: channelId }),
      })
      await loadOverview(); selectPlan(result.plan.id)
      setFeedback({ type: 'success', text: 'Đã tạo kế hoạch Guided Readiness.' })
    } catch (error) { setFeedback({ type: 'error', text: error.message }) } finally { setBusy('') }
  }

  const saveSettings = () => runAction('save', () => api(`/api/trust-builder/plans/${selectedId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      niche_keywords: topics.split('\n').map((value) => value.trim()).filter(Boolean),
      approved_sources: sources.split('\n').map((value) => value.trim()).filter(Boolean),
      reminder_enabled: reminderEnabled, reminder_time_local: reminderTime,
    }),
  }), 'Đã lưu chủ đề, nguồn duyệt và lịch nhắc.')

  const updateSession = async (changes) => {
    if (!activeSession) return
    await runAction('session', () => api(`/api/trust-builder/plans/${selectedId}/guided-sessions/${activeSession.id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
    }), changes.status === 'completed' ? 'Đã hoàn tất routine do bạn tự thực hiện.' : 'Đã cập nhật checklist.')
  }

  const planChannelIds = new Set(plans.map((plan) => plan.channel_db_id))

  return (
    <div className="trust-readiness-page">
      <header className="readiness-hero">
        <div><span className="readiness-eyebrow">Channel operations · formerly Trust Builder</span><h1>Channel &amp; Profile Readiness</h1>
          <p>Kiểm tra cấu hình có bằng chứng và hướng dẫn routine do chính bạn thao tác. Đây không phải điểm trust của YouTube.</p></div>
        <div className={`readiness-state ${selectedPlan?.readiness_state || 'not-checked'}`}>
          {selectedPlan?.readiness_state === 'ready' ? 'Sẵn sàng' : 'Cần kiểm tra'}
        </div>
      </header>
      {feedback && <div className={`readiness-feedback ${feedback.type}`}>{feedback.text}</div>}

      <div className="readiness-layout">
        <aside className="readiness-sidebar">
          <h2>Kênh</h2>
          {plans.map((plan) => <button type="button" className={plan.id === selectedId ? 'active' : ''} key={plan.id} onClick={() => selectPlan(plan.id)}>
            <span>{plan.channel_title || `Plan #${plan.id}`}</span><small>{plan.profile_readiness_pct || 0}% / {plan.channel_readiness_pct || 0}%</small>
          </button>)}
          {channels.filter((channel) => !planChannelIds.has(channel.id)).map((channel) => <button type="button" key={channel.id} onClick={() => createPlan(channel.id)} disabled={Boolean(busy)}>
            <span>{channel.title || channel.channel_id}</span><small>{busy === `create-${channel.id}` ? 'Đang tạo…' : '+ Tạo readiness'}</small>
          </button>)}
        </aside>

        <main className="readiness-main">
          {!selectedPlan ? <div className="readiness-empty-state">Chọn một kênh để tạo kế hoạch readiness.</div> : <>
            {selectedPlan.requires_review && <div className="readiness-migration-note">Lịch sử automation cũ đã được giữ lại nhưng không còn dùng để tính readiness. Hãy chạy kiểm tra mới.</div>}
            <div className="readiness-actions">
              <button type="button" disabled={Boolean(busy)} onClick={() => runAction('passive', () => api(`/api/trust-builder/plans/${selectedId}/checks/passive`, { method: 'POST' }), 'Đã kiểm tra cấu hình mà không mở browser.')}>{busy === 'passive' ? 'Đang kiểm tra…' : 'Kiểm tra thụ động'}</button>
              <button type="button" className="primary" disabled={Boolean(busy)} onClick={() => runAction('interactive', () => api(`/api/trust-builder/plans/${selectedId}/checks/interactive`, { method: 'POST' }), 'Đã kiểm tra Studio và đối chiếu danh tính kênh.')}>{busy === 'interactive' ? 'Đang mở Studio…' : 'Kiểm tra tương tác'}</button>
            </div>

            <div className="readiness-grid"><ReadinessCard title="Profile Readiness" snapshot={selectedPlan.profile_readiness} /><ReadinessCard title="Channel Readiness" snapshot={selectedPlan.channel_readiness} /></div>

            <section className="guided-card">
              <div className="readiness-card-head"><div><span className="readiness-eyebrow">Human operated</span><h3>Guided Routine</h3></div>
                {!activeSession && <button type="button" className="primary" disabled={Boolean(busy)} onClick={() => runAction('create-session', () => api(`/api/trust-builder/plans/${selectedId}/guided-sessions`, { method: 'POST' }), 'Đã tạo checklist. Hệ thống sẽ không thao tác thay bạn.')}>Tạo routine</button>}
              </div>
              <p className="guided-notice">Bạn tự tìm kiếm, đọc và xem nội dung. Không có tự động cuộn, xem, like, comment hoặc subscribe.</p>
              {activeSession && <div className="guided-steps">
                {(activeSession.agenda?.steps || []).map((step) => <label key={step.id}><input type="checkbox" checked={Boolean(activeSession.checklist?.[step.id])} disabled={Boolean(busy)} onChange={(event) => updateSession({ status: 'in_progress', checklist: { ...activeSession.checklist, [step.id]: event.target.checked } })} /><span>{step.label}{step.required ? ' · bắt buộc' : ''}</span></label>)}
                <button type="button" disabled={Boolean(busy)} onClick={() => updateSession({ status: 'completed' })}>Xác nhận hoàn tất</button>
              </div>}
            </section>

            <section className="readiness-settings">
              <h3>Chủ đề, nguồn duyệt và nhắc việc</h3>
              <div className="readiness-form-grid">
                <label>Chủ đề quan tâm, mỗi dòng một mục<textarea value={topics} onChange={(event) => setTopics(event.target.value)} rows="5" /></label>
                <label>Nguồn HTTPS đã duyệt, mỗi dòng một URL<textarea value={sources} onChange={(event) => setSources(event.target.value)} rows="5" /></label>
              </div>
              <div className="readiness-reminder"><label><input type="checkbox" checked={reminderEnabled} onChange={(event) => setReminderEnabled(event.target.checked)} /> Tạo reminder hằng ngày</label><input type="time" value={reminderTime} onChange={(event) => setReminderTime(event.target.value)} /><button type="button" disabled={Boolean(busy)} onClick={saveSettings}>{busy === 'save' ? 'Đang lưu…' : 'Lưu cấu hình'}</button></div>
            </section>

            {detail?.stats && <details className="legacy-history"><summary>Lịch sử automation cũ (chỉ đọc)</summary><p>{detail.stats.search_count || 0} search · {detail.stats.watch_count || 0} watch · {detail.stats.error_count || 0} lỗi. Các số này không ảnh hưởng readiness.</p></details>}
          </>}
        </main>
      </div>
    </div>
  )
}
