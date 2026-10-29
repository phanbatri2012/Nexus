import { useEffect, useMemo, useState } from 'react'

const API_BASE = 'http://127.0.0.1:8080'

function CountBadges({ counts = {}, compact = false }) {
  const running = Number(counts.running || 0)
  const review = Number(counts.awaiting_review || 0)
  if (!running && !review) return null
  return (
    <span className={`short-nav-badges ${compact ? 'compact' : ''}`}>
      {running > 0 && <span title="Job đang chạy">{running}</span>}
      {review > 0 && <span className="review" title="Video chờ duyệt">{review}</span>}
    </span>
  )
}

export default function ShortVideoMenu({ activeView, subPath, navigate }) {
  const [projects, setProjects] = useState([])
  const [summary, setSummary] = useState({ projects: {}, totals: {} })
  const [open, setOpen] = useState(activeView === 'shortvideo')

  const refresh = async () => {
    try {
      const [projectsResponse, summaryResponse] = await Promise.all([
        fetch(`${API_BASE}/api/short-video/projects`),
        fetch(`${API_BASE}/api/short-video/summary`)
      ])
      if (projectsResponse.ok) {
        const data = await projectsResponse.json()
        setProjects(Array.isArray(data.items) ? data.items : [])
      }
      if (summaryResponse.ok) setSummary(await summaryResponse.json())
    } catch {
      // The parent menu remains usable while the backend is starting.
    }
  }

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 5000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (activeView === 'shortvideo') setOpen(true)
  }, [activeView])

  const currentProject = useMemo(() => String(subPath || '').split('/')[0], [subPath])

  const openDashboard = () => {
    setOpen(true)
    navigate('shortvideo')
  }

  return (
    <li className="short-nav-group">
      <div
        className={`nav-item short-nav-parent ${activeView === 'shortvideo' ? 'active' : ''}`}
        data-view="shortvideo"
        onClick={openDashboard}
      >
        <span className="short-nav-label">Short Video</span>
        <CountBadges counts={summary.totals} compact />
        <button
          type="button"
          className={`short-nav-chevron ${open ? 'open' : ''}`}
          aria-label={open ? 'Thu gọn Short Video' : 'Mở rộng Short Video'}
          aria-expanded={open}
          onClick={event => {
            event.stopPropagation()
            setOpen(value => !value)
          }}
        >⌄</button>
      </div>
      {open && (
        <ul className="short-nav-submenu">
          {projects.map(project => (
            <li key={project.key}>
              <button
                type="button"
                className={activeView === 'shortvideo' && currentProject === project.key ? 'active' : ''}
                onClick={() => navigate('shortvideo', `${project.key}/create`)}
              >
                <span>
                  {project.display_name}
                  {project.status === 'paused' && <small>Tạm dừng</small>}
                  {!project.available && <small>Chưa sẵn sàng</small>}
                </span>
                <CountBadges counts={summary.projects?.[project.key]} />
              </button>
            </li>
          ))}
          <li>
            <button
              type="button"
              className={activeView === 'shortvideo' && currentProject === 'manage' ? 'active' : ''}
              onClick={() => navigate('shortvideo', 'manage')}
            >
              <span>Quản lý dự án</span>
            </button>
          </li>
        </ul>
      )}
    </li>
  )
}
