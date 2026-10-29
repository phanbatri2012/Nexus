import { useCallback, useEffect, useMemo, useState } from 'react'
import './ShortVideo.css'

const API_BASE = 'http://127.0.0.1:8080'
const ACTIVE_PREVIEW_STATUSES = new Set(['queued', 'processing'])
const PROJECT_TABS = [
  ['create', 'Tạo video'],
  ['jobs', 'Công việc'],
  ['review', 'Chờ duyệt'],
  ['outputs', 'Thành phẩm'],
  ['settings', 'Cấu hình']
]

const STATUS_LABELS = {
  queued: 'Đang chờ',
  processing: 'Đang xử lý',
  rendering: 'Đang dựng',
  awaiting_review: 'Chờ duyệt',
  completed: 'Hoàn thành',
  needs_attention: 'Cần chú ý',
  error: 'Lỗi',
  canceled: 'Đã hủy'
}

const ERA_FALLBACK_LABELS = {
  nguyen_ai_quoc_1919_1923: 'Nguyễn Ái Quốc tại Pháp 1919–1923',
  international_1924_1940: 'Hoạt động quốc tế 1924–1940',
  pac_bo_1941_1942: 'Pác Bó 1941–1942',
  prison_1942_1943: 'Nhật ký trong tù 1942–1943',
  viet_bac_1944_1954: 'Việt Bắc 1944–1954',
  president_1954_1969: 'Chủ tịch nước 1954–1969'
}

async function api(path, options) {
  const response = await fetch(`${API_BASE}${path}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`)
  return data
}

function artifactUrl(path) {
  return path ? `${API_BASE}${path}` : ''
}

function StatusBadge({ status }) {
  return <span className={`short-status ${status}`}>{STATUS_LABELS[status] || status}</span>
}

function EmptyState({ children }) {
  return <div className="short-empty">{children}</div>
}

function previewStatusLabel(preview) {
  if (!preview) return ''
  if (preview.status === 'queued') return 'Đang chờ OmniVoice'
  if (preview.status === 'processing') return preview.provider_state === 'loading' ? 'Đang nạp model' : 'Đang tạo audio'
  if (preview.status === 'completed') return 'Nghe thử đã sẵn sàng'
  if (preview.status === 'canceled') return 'Đã hủy'
  return preview.error || 'Không tạo được bản nghe thử'
}

function ItemRow({ item, onOpen }) {
  return (
    <button type="button" className="short-item-row" onClick={() => onOpen?.(item)}>
      <span>
        <strong>{item.title}</strong>
        <small>{item.current_stage || 'queued'} · {new Date(item.updated_at).toLocaleString('vi-VN')}</small>
      </span>
      <StatusBadge status={item.status} />
    </button>
  )
}

function ProjectNav({ project, tab, navigate, readOnly, canCreate }) {
  return (
    <div className="short-project-header">
      <div>
        <button type="button" className="short-back" onClick={() => navigate('shortvideo')}>← Short Video</button>
        <h1>{project.display_name}</h1>
        <p>{project.description}</p>
      </div>
      {project.status !== 'active' && <span className="short-project-state">{project.status}</span>}
      <nav className="short-tabs" aria-label="Điều hướng dự án Short Video">
        {PROJECT_TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? 'active' : ''}
            disabled={(readOnly && id !== 'outputs') || (id === 'create' && !canCreate)}
            onClick={() => navigate('shortvideo', `${project.key}/${readOnly ? 'outputs' : id}`)}
          >{label}</button>
        ))}
      </nav>
    </div>
  )
}

function CreateView({ project, onCreated }) {
  const [title, setTitle] = useState('')
  const [poem, setPoem] = useState('')
  const visualProfile = project.visual_profile || {}
  const providers = Array.isArray(visualProfile.available_providers)
    ? visualProfile.available_providers
    : [visualProfile.provider || 'muse']
  const [visualProvider, setVisualProvider] = useState(visualProfile.provider || providers[0] || 'muse')
  const characterProfile = project.character_profile_summary
  const characterEras = Array.isArray(characterProfile?.eras) ? characterProfile.eras : []
  const [characterEra, setCharacterEra] = useState('auto')
  const voiceProfile = project.voice_profile || {}
  const voiceSelectable = Boolean(voiceProfile.selectable)
  const [voices, setVoices] = useState([])
  const [voiceId, setVoiceId] = useState(voiceProfile.voice_id || '')
  const [voiceError, setVoiceError] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!voiceSelectable) {
      setVoices([{ id: voiceProfile.voice_id, name: voiceProfile.voice_name }].filter(voice => voice.id))
      setVoiceId(voiceProfile.voice_id || '')
      return
    }
    let active = true
    api('/api/tts/voices?provider_id=omnivoice&status=active')
      .then(data => {
        if (!active) return
        const available = (Array.isArray(data.items) ? data.items : []).filter(voice => {
          const modes = voice.casting_profile?.usage_modes
          return !Array.isArray(modes) || modes.includes('single_narrator')
        })
        setVoices(available)
        setVoiceId(current => available.some(voice => voice.id === current)
          ? current
          : (available.find(voice => voice.id === voiceProfile.voice_id)?.id || available[0]?.id || ''))
        setVoiceError(available.length ? '' : 'Chưa có giọng OmniVoice đang hoạt động.')
      })
      .catch(error => active && setVoiceError(error.message))
    return () => { active = false }
  }, [voiceProfile.voice_id, voiceProfile.voice_name, voiceSelectable])

  const refreshPreview = useCallback(async previewId => {
    try {
      const result = await api(`/api/tts/previews/${previewId}`)
      setPreview(result)
      return result
    } catch (error) {
      setVoiceError(error.message)
      return null
    }
  }, [])

  useEffect(() => {
    if (!preview || !ACTIVE_PREVIEW_STATUSES.has(preview.status)) return undefined
    const timer = setInterval(() => refreshPreview(preview.id), 2000)
    return () => clearInterval(timer)
  }, [preview, refreshPreview])

  const createVoicePreview = async () => {
    if (!voiceId || previewBusy || (preview && ACTIVE_PREVIEW_STATUSES.has(preview.status))) return
    const sample = `${title.trim()}${title.trim() && poem.trim() ? '. ' : ''}${poem.trim()}`
      .replace(/\s+/g, ' ')
      .slice(0, 200) || 'Đây là bản nghe thử giọng đọc cho video thơ.'
    setPreviewBusy(true)
    setVoiceError('')
    try {
      setPreview(await api('/api/tts/previews', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice_id: voiceId, text: sample })
      }))
    } catch (error) {
      setVoiceError(error.message)
    } finally {
      setPreviewBusy(false)
    }
  }

  const submit = async event => {
    event.preventDefault()
    if (!title.trim() || !poem.trim() || busy) return
    setBusy(true)
    setMessage('')
    try {
      const data = await api(`/api/short-video/projects/${encodeURIComponent(project.key)}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title.trim(),
          poem: poem.trim(),
          client_request_id: crypto.randomUUID(),
          visual_provider: visualProvider,
          voice_id: voiceId || null,
          character_era: characterEra
        })
      })
      setTitle('')
      setPoem('')
      setMessage(data.created ? 'Đã xếp video vào hàng đợi.' : 'Yêu cầu này đã được tiếp nhận trước đó.')
      onCreated?.(data.item)
    } catch (error) {
      setMessage(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="short-create-card" onSubmit={submit}>
      <label>
        <span>Tên bài thơ</span>
        <input value={title} onChange={event => setTitle(event.target.value)} required maxLength={300} />
      </label>
      <label>
        <span>Nội dung bài thơ</span>
        <textarea value={poem} onChange={event => setPoem(event.target.value)} required rows={16} maxLength={12000} />
      </label>
      <section className="short-voice-choice">
        <div className="short-voice-row">
          <label>
            <span>Giọng đọc OmniVoice</span>
            <select
              value={voiceId}
              disabled={!voiceSelectable || (preview && ACTIVE_PREVIEW_STATUSES.has(preview.status))}
              onChange={event => {
                setVoiceId(event.target.value)
                setPreview(null)
                setVoiceError('')
              }}
            >
              {!voices.length && <option value="">Không có giọng khả dụng</option>}
              {voices.map(voice => <option key={voice.id} value={voice.id}>{voice.name}</option>)}
            </select>
          </label>
          <button
            type="button"
            disabled={!voiceId || previewBusy || (preview && ACTIVE_PREVIEW_STATUSES.has(preview.status))}
            onClick={createVoicePreview}
          >{previewBusy ? 'Đang gửi…' : preview ? 'Nghe thử lại' : 'Nghe thử'}</button>
        </div>
        {preview && <small className={`short-preview-status ${preview.status}`}>{previewStatusLabel(preview)}</small>}
        {preview?.status === 'completed' && preview.audio_url && (
          <audio key={preview.audio_url} controls preload="metadata" src={preview.audio_url} />
        )}
        {voiceError && <div className="short-error">{voiceError}</div>}
      </section>
      <div className="short-provider-choice" role="group" aria-label="Engine tạo hình">
        <span>Engine tạo hình</span>
        <div>
          {providers.map(provider => (
            <button
              key={provider}
              type="button"
              className={visualProvider === provider ? 'active' : ''}
              onClick={() => setVisualProvider(provider)}
            >{provider === 'google_flow' ? 'Google Flow' : 'Muse'}</button>
          ))}
        </div>
      </div>
      {characterProfile && (
        <details className="short-advanced">
          <summary>Tùy chọn nâng cao</summary>
          <label>
            <span>Thời kỳ hình ảnh</span>
            <select value={characterEra} onChange={event => setCharacterEra(event.target.value)}>
              <option value="auto">Tự động</option>
              {characterEras.map(era => (
                <option key={era.key} value={era.key} disabled={!era.available}>
                  {era.label || ERA_FALLBACK_LABELS[era.key] || era.key}{era.available ? '' : ' · thiếu ảnh'}
                </option>
              ))}
            </select>
          </label>
        </details>
      )}
      <button type="submit" className="short-primary" disabled={busy || !title.trim() || !poem.trim() || !voiceId}>
        {busy ? 'Đang tạo…' : 'Tạo video'}
      </button>
      {message && <div className="short-form-message">{message}</div>}
    </form>
  )
}

function ReviewDetail({ itemId, onBack, onChanged }) {
  const [item, setItem] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [eraChoice, setEraChoice] = useState('auto')

  const load = useCallback(async () => {
    try {
      const loaded = await api(`/api/short-video/items/${itemId}`)
      setItem(loaded)
      setEraChoice(loaded.character?.requested_era || loaded.character?.resolved_era || 'auto')
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [itemId])

  useEffect(() => { load() }, [load])

  const action = async (name, path, body = null) => {
    setBusy(name)
    setError('')
    try {
      await api(path, {
        method: 'POST',
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
      })
      await load()
      onChanged?.()
    } catch (actionError) {
      setError(actionError.message)
    } finally {
      setBusy('')
    }
  }

  if (!item) return <EmptyState>{error || 'Đang tải bản nháp…'}</EmptyState>
  return (
    <div className="short-review-detail">
      <button type="button" className="short-back" onClick={onBack}>← Danh sách chờ duyệt</button>
      <div className="short-review-heading">
        <div><h2>{item.title}</h2><StatusBadge status={item.status} /></div>
        <div className="short-review-actions">
          <button
            type="button"
            disabled={Boolean(busy)}
            onClick={() => action('audio', `/api/short-video/items/${item.id}/regenerate-audio`)}
          >{busy === 'audio' ? 'Đang xếp…' : 'Tạo lại giọng đọc'}</button>
          <button
            type="button"
            className="short-primary"
            disabled={Boolean(busy) || item.status !== 'awaiting_review'}
            onClick={() => action('approve', `/api/short-video/items/${item.id}/approve`)}
          >{busy === 'approve' ? 'Đang duyệt…' : 'Duyệt & render final'}</button>
        </div>
      </div>
      {error && <div className="short-error">{error}</div>}
      {item.character && (
        <section className="short-character-review">
          <div>
            <strong>Profile {item.character.profile_key} v{item.character.profile_version}</strong>
            <small>
              {item.character.resolved_era ? (ERA_FALLBACK_LABELS[item.character.resolved_era] || item.character.resolved_era) : 'Chưa xác định thời kỳ'}
              {item.character.source === 'auto' && item.character.confidence != null
                ? ` · độ tin cậy ${(Number(item.character.confidence) * 100).toFixed(0)}%`
                : ''}
            </small>
            {item.character.reason && <p>{item.character.reason}</p>}
          </div>
          <div className="short-era-override">
            <select value={eraChoice} onChange={event => setEraChoice(event.target.value)}>
              <option value="auto">Tự động chọn lại</option>
              {(item.character.eras || []).map(era => (
                <option key={era.key} value={era.key}>{era.label || ERA_FALLBACK_LABELS[era.key] || era.key}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => action('era', `/api/short-video/items/${item.id}/character-era`, { era_key: eraChoice })}
            >{busy === 'era' ? 'Đang xếp…' : 'Đổi thời kỳ & tạo lại hình'}</button>
          </div>
          <ul>
            <li>Đúng khuôn mặt, độ tuổi và thời kỳ</li>
            <li>Trang phục, đạo cụ và cơ thể tự nhiên</li>
            <li>Đen trắng, không chữ, logo hoặc màu hóa</li>
          </ul>
        </section>
      )}
      {item.status === 'awaiting_review' && (
        <video className="short-preview" controls src={artifactUrl(item.artifact_urls.draft)} />
      )}
      <div className="short-scene-grid">
        {(item.scenes || []).map(scene => (
          <article key={scene.index} className="short-scene-card">
            {scene.video_url ? (
              <video controls muted src={artifactUrl(scene.video_url)} />
            ) : scene.image_url ? (
              <img src={artifactUrl(scene.image_url)} alt={`Cảnh ${scene.index + 1}`} />
            ) : <div className="short-scene-placeholder">Chưa có media</div>}
            <div>
              <strong>Cảnh {scene.index + 1}</strong>
              <small>{Number(scene.duration || 0).toFixed(1)} giây · {scene.fallback_to_image ? 'Pan/zoom fallback' : scene.media_type}</small>
              {scene.character_presence && (
                <small>Có Bác Hồ · {scene.shot_type} · ref: {(scene.reference_asset_ids || []).join(', ') || scene.reference_role}</small>
              )}
              <p>{scene.transcript}</p>
              <button
                type="button"
                disabled={Boolean(busy)}
                onClick={() => action(`scene-${scene.index}`, `/api/short-video/items/${item.id}/scenes/${scene.index}/regenerate`)}
              >{busy === `scene-${scene.index}` ? 'Đang xếp…' : 'Tạo lại cảnh'}</button>
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}

function ItemsView({ projectKey = '', status = '', mode = 'jobs' }) {
  const [page, setPage] = useState({ items: [], total: 0 })
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: '100' })
    if (projectKey) params.set('project', projectKey)
    if (status) params.set('status', status)
    try {
      setPage(await api(`/api/short-video/items?${params}`))
      setError('')
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [projectKey, status])

  const hasActiveItems = page.items.some(item => ['queued', 'processing', 'rendering'].includes(item.status))

  useEffect(() => {
    load()
    const poll = hasActiveItems ? window.setInterval(load, 4000) : null
    return () => { if (poll) window.clearInterval(poll) }
  }, [load, hasActiveItems])

  if (selected) return <ReviewDetail itemId={selected} onBack={() => setSelected('')} onChanged={load} />
  if (error) return <div className="short-error">{error}</div>
  if (!page.items.length) return <EmptyState>Chưa có video phù hợp.</EmptyState>
  if (mode === 'outputs') {
    return (
      <div className="short-output-grid">
        {page.items.map(item => (
          <article key={item.id} className="short-output-card">
            <video controls preload="metadata" src={artifactUrl(item.artifact_urls.final)} />
            <h3>{item.title}</h3>
            <small>{new Date(item.completed_at || item.updated_at).toLocaleString('vi-VN')}</small>
          </article>
        ))}
      </div>
    )
  }
  return <div className="short-item-list">{page.items.map(item => <ItemRow key={item.id} item={item} onOpen={setSelected} />)}</div>
}

function Dashboard({ projects, summary, tab, navigate }) {
  return (
    <div className="short-dashboard">
      <div className="short-title-row">
        <div><h1>Short Video</h1><p>Pipeline riêng cho các dự án video dọc ngắn hạn.</p></div>
        <div className="short-total-badges"><span>{summary.totals?.running || 0} đang chạy</span><span>{summary.totals?.awaiting_review || 0} chờ duyệt</span></div>
      </div>
      <nav className="short-tabs">
        {[['overview', 'Tổng quan'], ['jobs', 'Công việc'], ['outputs', 'Thành phẩm']].map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? 'active' : ''} onClick={() => navigate('shortvideo', '', { query: id === 'overview' ? null : { tab: id } })}>{label}</button>
        ))}
      </nav>
      {tab === 'jobs' ? <ItemsView /> : tab === 'outputs' ? <ItemsView status="completed" mode="outputs" /> : (
        <>
          <div className="short-project-grid">
            {projects.filter(project => ['active', 'paused'].includes(project.status)).map(project => {
              const counts = summary.projects?.[project.key] || {}
              return (
                <button key={project.key} type="button" className="short-project-card" onClick={() => navigate('shortvideo', `${project.key}/create`)}>
                  <span><strong>{project.display_name}</strong><small>{project.description}</small></span>
                  <span className="short-project-metrics"><b>{counts.running || 0}</b> chạy · <b>{counts.awaiting_review || 0}</b> duyệt</span>
                </button>
              )
            })}
          </div>
          <section className="short-section"><h2>Lỗi gần đây</h2>{summary.recent_errors?.length ? summary.recent_errors.map(item => <ItemRow key={item.id} item={item} />) : <EmptyState>Không có lỗi gần đây.</EmptyState>}</section>
        </>
      )}
    </div>
  )
}

function ProjectManager({ projects }) {
  return (
    <div className="short-dashboard">
      <h1>Quản lý dự án Short Video</h1>
      <p>v1 chỉ đọc manifest trong <code>config/short_video_projects/</code>; không tạo hoặc sửa project từ UI.</p>
      <div className="short-manager-list">
        {projects.map(project => (
          <article key={`${project.key}-${project.source}`}>
            <div><strong>{project.display_name}</strong><small>{project.key} · {project.profile_version || 'manifest lỗi'}</small></div>
            <StatusBadge status={project.available ? project.status : 'error'} />
            {!project.available && <p>{(project.errors || []).join(' ')}</p>}
          </article>
        ))}
      </div>
    </div>
  )
}

function CharacterAssetForm({ projectKey, era, onSaved }) {
  const [role, setRole] = useState('primary_portrait')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const submit = async event => {
    event.preventDefault()
    if (busy || era.reference_count >= 3) return
    setBusy(true)
    setMessage('')
    const form = event.currentTarget
    const payload = new FormData(form)
    payload.set('era_key', era.key)
    try {
      const updated = await api(
        `/api/short-video/projects/${encodeURIComponent(projectKey)}/character-profile/assets`,
        { method: 'POST', body: payload }
      )
      form.reset()
      setRole('primary_portrait')
      setMessage('Đã lưu ảnh vào Character Profile.')
      onSaved(updated)
    } catch (uploadError) {
      setMessage(uploadError.message)
    } finally {
      setBusy(false)
    }
  }

  if (era.reference_count >= 3) return <p className="short-profile-note">Giai đoạn này đã đủ tối đa 3 ảnh.</p>
  return (
    <details className="short-profile-upload">
      <summary>+ Thêm ảnh tham chiếu</summary>
      <form onSubmit={submit}>
        <label>
          <span>Ảnh PNG, JPEG hoặc WebP</span>
          <input name="image" type="file" accept="image/png,image/jpeg,image/webp" required />
        </label>
        <label>
          <span>Vai trò ảnh</span>
          <select name="role" value={role} onChange={event => setRole(event.target.value)}>
            <option value="primary_portrait">Chân dung chính</option>
            <option value="half_body">Bán thân</option>
            <option value="full_body">Toàn thân</option>
            <option value="profile_context">Góc nghiêng / bối cảnh</option>
          </select>
        </label>
        <label>
          <span>Tư thế hoặc góc chụp</span>
          <input name="pose" maxLength="300" placeholder="Ví dụ: chính diện, góc 3/4" required />
        </label>
        <label>
          <span>Nguồn ảnh</span>
          <input name="source_label" maxLength="500" placeholder="Tên sách, kho lưu trữ hoặc website" required />
        </label>
        <label>
          <span>Năm chụp hoặc khoảng năm</span>
          <input name="source_year" maxLength="100" defaultValue={`${era.year_from}–${era.year_to}`} required />
        </label>
        <label className="wide">
          <span>Ghi chú quyền sử dụng</span>
          <input name="rights_note" maxLength="1000" placeholder="Nguồn công cộng, đã được cấp phép…" required />
        </label>
        <button type="submit" disabled={busy}>{busy ? 'Đang tải…' : 'Tải ảnh lên'}</button>
      </form>
      {message && <p className={message.startsWith('Đã lưu') ? 'short-form-message' : 'short-error'}>{message}</p>}
    </details>
  )
}

function SettingsView({ project }) {
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState('')
  const [removing, setRemoving] = useState('')

  const loadProfile = useCallback(async () => {
    if (!project.character_profile) return
    try {
      setProfile(await api(`/api/short-video/projects/${encodeURIComponent(project.key)}/character-profile`))
      setError('')
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [project])

  useEffect(() => { loadProfile() }, [loadProfile])

  const removeAsset = async asset => {
    if (!window.confirm(`Gỡ ảnh ${asset.id} khỏi profile? File sẽ được chuyển vào .trash.`)) return
    setRemoving(asset.id)
    setError('')
    try {
      setProfile(await api(
        `/api/short-video/projects/${encodeURIComponent(project.key)}/character-profile/assets/${encodeURIComponent(asset.id)}`,
        { method: 'DELETE' }
      ))
    } catch (removeError) {
      setError(removeError.message)
    } finally {
      setRemoving('')
    }
  }

  return (
    <div className="short-settings">
      <section>
        <h2>Cấu hình project</h2>
        <pre className="short-settings-json">{JSON.stringify(project, null, 2)}</pre>
      </section>
      {project.character_profile && (
        <section>
          <h2>Character Profile</h2>
          {error && <div className="short-error">{error}</div>}
          {!profile && !error && <EmptyState>Đang tải Character Profile…</EmptyState>}
          {profile && (
            <>
              <p><strong>{profile.display_name}</strong> · v{profile.profile_version} · {profile.status}</p>
              <p className="short-profile-note">
                Không cần tự tạo thư mục. Ảnh tải lên được lưu theo từng giai đoạn dưới{' '}
                <code>data/short_video_character_profiles/{profile.key}/v{profile.profile_version}/</code>.
              </p>
              {(profile.warnings || []).map(warning => <div key={warning} className="short-profile-warning">{warning}</div>)}
              <div className="short-era-grid">
                {(profile.eras || []).map(era => (
                  <article key={era.key} className="short-era-card">
                    <div><strong>{era.label}</strong><small>{era.reference_count}/2–3 ảnh · {era.available ? 'Sẵn sàng' : 'Chưa sẵn sàng'}</small></div>
                    {(era.errors || []).map(message => <p key={message}>{message}</p>)}
                    <div className="short-reference-grid">
                      {(era.assets || []).map(asset => (
                        <figure key={asset.id}>
                          {asset.available
                            ? <img src={artifactUrl(asset.asset_url)} alt={`${era.label} – ${asset.role}`} />
                            : <div className="short-reference-missing">Thiếu ảnh</div>}
                          <figcaption>
                            {asset.source_label}<small>{asset.role} · {asset.source_year}</small>
                            {profile.status === 'draft' && (
                              <button type="button" disabled={removing === asset.id} onClick={() => removeAsset(asset)}>
                                {removing === asset.id ? 'Đang gỡ…' : 'Gỡ ảnh'}
                              </button>
                            )}
                          </figcaption>
                        </figure>
                      ))}
                    </div>
                    {profile.status === 'draft' && (
                      <CharacterAssetForm projectKey={project.key} era={era} onSaved={setProfile} />
                    )}
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  )
}

export default function ShortVideo({ subPath, query, navigate }) {
  const [projects, setProjects] = useState([])
  const [summary, setSummary] = useState({ projects: {}, totals: {}, recent_errors: [] })
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const [projectData, summaryData] = await Promise.all([
        api('/api/short-video/projects?include_archived=true'),
        api('/api/short-video/summary')
      ])
      setProjects(projectData.items || [])
      setSummary(summaryData)
      setError('')
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const segments = useMemo(() => String(subPath || '').split('/').filter(Boolean), [subPath])
  if (error && !projects.length) return <div className="short-page"><div className="short-error">{error}</div></div>
  if (segments[0] === 'manage') return <div className="short-page"><ProjectManager projects={projects} /></div>
  if (!segments.length) {
    const dashboardTab = ['jobs', 'outputs'].includes(query?.tab) ? query.tab : 'overview'
    return <div className="short-page"><Dashboard projects={projects} summary={summary} tab={dashboardTab} navigate={navigate} /></div>
  }

  const project = projects.find(entry => entry.key === segments[0])
  if (!project) {
    return <div className="short-page"><EmptyState><h2>Dự án không khả dụng</h2><p>Project không tồn tại hoặc manifest không thể nhận diện.</p></EmptyState></div>
  }
  const readOnly = project.status === 'archived'
  const canCreate = project.status === 'active' && project.available
  const requestedTab = segments[1] || (readOnly ? 'outputs' : 'create')
  const tab = PROJECT_TABS.some(([id]) => id === requestedTab) ? requestedTab : 'create'
  let content
  if (readOnly || tab === 'outputs') content = <ItemsView projectKey={project.key} status="completed" mode="outputs" />
  else if (tab === 'create') content = canCreate
    ? <CreateView key={project.key} project={project} onCreated={refresh} />
    : <EmptyState>Dự án hiện không nhận yêu cầu tạo video mới.</EmptyState>
  else if (tab === 'jobs') content = <ItemsView projectKey={project.key} />
  else if (tab === 'review') content = <ItemsView projectKey={project.key} status="awaiting_review" mode="review" />
  else content = <SettingsView project={project} />

  return (
    <div className="short-page">
      <ProjectNav project={project} tab={readOnly ? 'outputs' : tab} navigate={navigate} readOnly={readOnly} canCreate={canCreate} />
      {readOnly && <div className="short-readonly">Dự án đã archive. Thành phẩm vẫn xem được ở chế độ chỉ đọc.</div>}
      {content}
    </div>
  )
}
