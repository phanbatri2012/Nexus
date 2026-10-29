import { useCallback, useEffect, useMemo, useState } from 'react'
import './ShortVideo.css'

const API_BASE = 'http://127.0.0.1:8080'
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
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

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
          visual_provider: visualProvider
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
      <button type="submit" className="short-primary" disabled={busy || !title.trim() || !poem.trim()}>
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

  const load = useCallback(async () => {
    try {
      setItem(await api(`/api/short-video/items/${itemId}`))
    } catch (loadError) {
      setError(loadError.message)
    }
  }, [itemId])

  useEffect(() => { load() }, [load])

  const action = async (name, path) => {
    setBusy(name)
    setError('')
    try {
      await api(path, { method: 'POST' })
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
  else content = <pre className="short-settings-json">{JSON.stringify(project, null, 2)}</pre>

  return (
    <div className="short-page">
      <ProjectNav project={project} tab={readOnly ? 'outputs' : tab} navigate={navigate} readOnly={readOnly} canCreate={canCreate} />
      {readOnly && <div className="short-readonly">Dự án đã archive. Thành phẩm vẫn xem được ở chế độ chỉ đọc.</div>}
      {content}
    </div>
  )
}
