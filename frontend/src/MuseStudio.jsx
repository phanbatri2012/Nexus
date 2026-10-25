import { useCallback, useEffect, useMemo, useState } from 'react'
import './MuseStudio.css'
import {
  getMuseAliasInputValue,
  normalizeMuseAliases,
  splitMuseAliasDraft
} from './museAliases'

const API_BASE = 'http://127.0.0.1:8080'

export default function MuseStudio() {
  const [activeTab, setActiveTab] = useState('characters')
  const [serviceStatus, setServiceStatus] = useState({
    configured: false,
    studio_path: 'E:\\yt\\Tool\\Muse_AI_Studio',
    running: false,
    owned: false,
    pid: 0,
    api_ready: false,
    accounts: 0,
    active_media_tasks: 0,
    task_recovery_released: false,
    api_url: 'http://127.0.0.1:18610'
  })
  const [studioPath, setStudioPath] = useState('E:\\yt\\Tool\\Muse_AI_Studio')
  const [isBusy, setIsBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [mediaItems, setMediaItems] = useState([])
  const [promptsData, setPromptsData] = useState(null)
  const [activePromptVersion, setActivePromptVersion] = useState('')
  const [previewMedia, setPreviewMedia] = useState(null)

  const showNotice = (text, type = 'info') => {
    setNotice({ text, type })
    setTimeout(() => setNotice(null), 6000)
  }

  // 1. Fetch Service Status and Settings
  const refreshStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/muse/settings`)
      if (res.ok) {
        const data = await res.json()
        if (data.service) setServiceStatus(data.service)
        if (data.settings?.muse_studio_path) {
          setStudioPath(data.settings.muse_studio_path)
        }
      }
    } catch (err) {
      console.warn('Không thể tải trạng thái Muse Studio:', err)
    }
  }, [])

  // 2. Fetch Accounts
  const refreshAccounts = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/muse/accounts`)
      if (res.ok) {
        const data = await res.json()
        setAccounts(data.items || [])
      }
    } catch (err) {
      console.warn('Không thể tải danh sách tài khoản Muse:', err)
    }
  }, [])

  // 3. Fetch Media
  const refreshMedia = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/muse/media`)
      if (res.ok) {
        const data = await res.json()
        setMediaItems(data.items || [])
      }
    } catch (err) {
      console.warn('Không thể tải media Muse:', err)
    }
  }, [])

  // 4. Fetch Prompts Data
  const loadPrompts = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/prompts`)
      if (res.ok) {
        const data = await res.json()
        setPromptsData(data)
        const defaultVer = data.default_version_id || Object.keys(data.versions || {})[0] || ''
        setActivePromptVersion(current => current || defaultVer)
      }
    } catch (err) {
      console.warn('Không thể tải cấu hình Prompts:', err)
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    loadPrompts()
    const interval = setInterval(refreshStatus, 4000)
    return () => clearInterval(interval)
  }, [refreshStatus, loadPrompts])

  useEffect(() => {
    if (activeTab === 'accounts') refreshAccounts()
    if (activeTab === 'media') refreshMedia()
  }, [activeTab, refreshAccounts, refreshMedia])

  // Lifecycle actions
  const handleStartService = async () => {
    setIsBusy(true)
    showNotice('Đang khởi động Muse2API service...', 'info')
    try {
      const res = await fetch(`${API_BASE}/api/muse/service/start`, { method: 'POST' })
      const data = await res.json()
      if (res.ok) {
        if (data.service) setServiceStatus(data.service)
        showNotice('Khởi động Muse2API service thành công!', 'success')
      } else {
        showNotice(data.detail || 'Không thể khởi động service Muse.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi kết nối khi khởi động: ${err.message}`, 'error')
    } finally {
      setIsBusy(false)
      refreshStatus()
    }
  }

  const handleStopService = async () => {
    if (!window.confirm('Bạn có chắc muốn dừng Muse2API service?')) return
    setIsBusy(true)
    showNotice('Đang dừng Muse2API service...', 'info')
    try {
      const res = await fetch(`${API_BASE}/api/muse/service/stop`, { method: 'POST' })
      const data = await res.json()
      if (res.ok) {
        if (data.service) setServiceStatus(data.service)
        showNotice('Đã dừng Muse2API service an toàn.', 'success')
      } else {
        showNotice(data.detail || 'Không thể dừng service Muse.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi kết nối khi dừng: ${err.message}`, 'error')
    } finally {
      setIsBusy(false)
      refreshStatus()
    }
  }

  const handleRestartService = async () => {
    setIsBusy(true)
    showNotice('Đang khởi động lại Muse2API service...', 'info')
    try {
      const res = await fetch(`${API_BASE}/api/muse/service/restart`, { method: 'POST' })
      const data = await res.json()
      if (res.ok) {
        if (data.service) setServiceStatus(data.service)
        showNotice('Khởi động lại Muse2API service thành công!', 'success')
      } else {
        showNotice(data.detail || 'Không thể khởi động lại service Muse.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi kết nối khi restart: ${err.message}`, 'error')
    } finally {
      setIsBusy(false)
      refreshStatus()
    }
  }

  const handleOpenDesktop = async () => {
    setIsBusy(true)
    showNotice('Đang mở ứng dụng Muse AI Studio (PyQt6 Desktop)...', 'info')
    try {
      const res = await fetch(`${API_BASE}/api/muse/studio/open`, { method: 'POST' })
      const data = await res.json()
      if (res.ok) {
        showNotice('Đã mở cửa sổ Muse Studio Desktop!', 'success')
      } else {
        showNotice(data.detail || 'Không thể mở Muse Studio Desktop.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi mở Desktop App: ${err.message}`, 'error')
    } finally {
      setIsBusy(false)
    }
  }

  const handleOpenWebAdmin = () => {
    window.open('http://127.0.0.1:18610/', '_blank')
  }

  const handleSaveStudioPath = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/muse/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ muse_studio_path: studioPath })
      })
      const data = await res.json()
      if (res.ok) {
        showNotice('Đã lưu đường dẫn Muse Studio thành công!', 'success')
        if (data.service) setServiceStatus(data.service)
      } else {
        showNotice(data.detail || 'Lưu đường dẫn thất bại.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi lưu: ${err.message}`, 'error')
    }
  }

  // Active prompt reference sets
  const currentVersionData = promptsData?.versions?.[activePromptVersion]
  const currentReferenceSets = useMemo(() => {
    return currentVersionData?.image_generation_settings?.muse_settings?.reference_sets || []
  }, [currentVersionData])

  const handleUpdateReferenceSets = async (newReferenceSets) => {
    if (!promptsData || !activePromptVersion) return
    const updatedPrompts = {
      ...promptsData,
      versions: {
        ...promptsData.versions,
        [activePromptVersion]: {
          ...promptsData.versions[activePromptVersion],
          image_generation_settings: {
            ...(promptsData.versions[activePromptVersion]?.image_generation_settings || {}),
            muse_settings: {
              ...(promptsData.versions[activePromptVersion]?.image_generation_settings?.muse_settings || {}),
              reference_sets: newReferenceSets
            }
          }
        }
      }
    }
    setPromptsData(updatedPrompts)

    try {
      const res = await fetch(`${API_BASE}/api/prompts`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updatedPrompts)
      })
      if (res.ok) {
        showNotice('Đã cập nhật danh sách nhân vật vào bộ prompt!', 'success')
      } else {
        showNotice('Không thể lưu nhân vật vào prompt.', 'error')
      }
    } catch (err) {
      showNotice(`Lỗi lưu prompt: ${err.message}`, 'error')
    }
  }

  const handleAddReferenceSet = () => {
    const newSet = {
      id: `reference_${Date.now()}`,
      name: 'Nhân vật mới',
      aliases: [],
      images: []
    }
    handleUpdateReferenceSets([...currentReferenceSets, newSet])
  }

  const handleRemoveReferenceSet = (index) => {
    if (!window.confirm('Bạn có chắc muốn xóa bộ nhân vật này?')) return
    const updated = currentReferenceSets.filter((_, i) => i !== index)
    handleUpdateReferenceSets(updated)
  }

  const handleModifyReferenceSet = (index, updates) => {
    const updated = [...currentReferenceSets]
    updated[index] = { ...updated[index], ...updates }
    handleUpdateReferenceSets(updated)
  }

  // Status visual label & class
  let statusBadgeClass = 'stopped'
  let statusText = 'Đã dừng'
  if (serviceStatus.running) {
    if (serviceStatus.active_media_tasks > 0) {
      statusBadgeClass = 'busy'
      statusText = `Đang bận (${serviceStatus.active_media_tasks} tác vụ)`
    } else {
      statusBadgeClass = 'online'
      statusText = `Sẵn sàng (Port 18610)`
    }
  } else if (isBusy) {
    statusBadgeClass = 'starting'
    statusText = 'Đang chuyển trạng thái...'
  }

  return (
    <div className="muse-page">
      {/* Header */}
      <div className="muse-heading">
        <div className="muse-title-area">
          <h1>🎨 Muse AI Studio</h1>
          <p>Hệ thống tự động sinh ảnh & video nhân vật nhất quán qua Meta Muse AI Engine</p>
        </div>
      </div>

      {notice && (
        <div className={`muse-notice ${notice.type}`}>
          <span>{notice.type === 'error' ? '⚠️' : notice.type === 'success' ? '✅' : 'ℹ️'}</span>
          <span>{notice.text}</span>
        </div>
      )}

      {/* Service Control Bar */}
      <div className="muse-control-bar">
        <div className="muse-control-main">
          <div className="muse-status-container">
            <span className={`muse-status-badge ${statusBadgeClass}`}>
              <span className="muse-status-dot" />
              {statusText}
            </span>
            {serviceStatus.running && serviceStatus.pid > 0 && (
              <span style={{ fontSize: '0.85rem', color: '#94a3b8' }}>
                PID: <strong>{serviceStatus.pid}</strong>
              </span>
            )}
          </div>

          <div className="muse-btn-group">
            <button
              type="button"
              className="muse-btn success"
              disabled={serviceStatus.running || isBusy}
              onClick={handleStartService}
              title="Khởi động Muse2API service nền"
            >
              ▶ Start Service
            </button>
            <button
              type="button"
              className="muse-btn danger"
              disabled={!serviceStatus.running || isBusy}
              onClick={handleStopService}
              title="Dừng an toàn Muse2API service"
            >
              ⏹ Stop Service
            </button>
            <button
              type="button"
              className="muse-btn warning"
              disabled={isBusy}
              onClick={handleRestartService}
              title="Khởi động lại Muse2API service"
            >
              🔄 Restart
            </button>
            <button
              type="button"
              className="muse-btn"
              disabled={isBusy}
              onClick={handleOpenDesktop}
              title="Mở ứng dụng PyQt6 Muse AI Studio Desktop"
            >
              🖥️ Mở Desktop App
            </button>
            <button
              type="button"
              className="muse-btn"
              onClick={handleOpenWebAdmin}
              title="Mở giao diện Web Admin gốc tại port 18610"
            >
              🌐 Web Admin
            </button>
          </div>
        </div>

        <div className="muse-metrics-row">
          <div className="muse-metric-item">
            <span>Tài khoản khả dụng:</span>
            <strong>{serviceStatus.accounts}</strong>
          </div>
          <div className="muse-metric-item">
            <span>Hàng đợi sinh media:</span>
            <strong>{serviceStatus.active_media_tasks} task</strong>
          </div>
          <div className="muse-metric-item">
            <span>Task Recovery:</span>
            <strong style={{ color: serviceStatus.task_recovery_released ? '#4ade80' : '#f87171' }}>
              {serviceStatus.task_recovery_released ? 'Đã kích hoạt' : 'Chưa bật'}
            </strong>
          </div>
          <div className="muse-metric-item" style={{ marginLeft: 'auto' }}>
            <span>Thư mục Studio:</span>
            <input
              type="text"
              value={studioPath}
              onChange={e => setStudioPath(e.target.value)}
              style={{
                background: '#1e293b',
                border: '1px solid #475569',
                borderRadius: 4,
                padding: '3px 8px',
                color: '#f1f5f9',
                fontSize: '0.8rem',
                width: '260px'
              }}
            />
            <button
              type="button"
              className="muse-btn"
              style={{ padding: '3px 8px', fontSize: '0.78rem' }}
              onClick={handleSaveStudioPath}
            >
              Lưu path
            </button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="muse-nav-tabs">
        <button
          type="button"
          className={`muse-tab-btn ${activeTab === 'characters' ? 'active' : ''}`}
          onClick={() => setActiveTab('characters')}
        >
          🎭 Nhân vật &amp; Tham chiếu ({currentReferenceSets.length})
        </button>
        <button
          type="button"
          className={`muse-tab-btn ${activeTab === 'accounts' ? 'active' : ''}`}
          onClick={() => setActiveTab('accounts')}
        >
          👥 Tài khoản &amp; Hạn mức ({accounts.length})
        </button>
        <button
          type="button"
          className={`muse-tab-btn ${activeTab === 'media' ? 'active' : ''}`}
          onClick={() => setActiveTab('media')}
        >
          🖼️ Kho Media ({mediaItems.length})
        </button>
      </div>

      {/* Tab 1: Characters & Reference Sets */}
      {activeTab === 'characters' && (
        <div className="muse-card">
          <div className="muse-card-title">
            <h2>🎭 Quản lý Bộ nhân vật tham chiếu (Reference Sets)</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {promptsData?.versions && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontSize: '0.88rem', color: '#94a3b8' }}>Bộ prompt:</span>
                  <select
                    value={activePromptVersion}
                    onChange={e => setActivePromptVersion(e.target.value)}
                    style={{
                      background: '#1e293b',
                      border: '1px solid #475569',
                      borderRadius: 6,
                      padding: '5px 10px',
                      color: '#f8fafc'
                    }}
                  >
                    {Object.entries(promptsData.versions).map(([vId, v]) => (
                      <option key={vId} value={vId}>
                        {v.name || vId}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <button
                type="button"
                className="muse-btn primary"
                onClick={handleAddReferenceSet}
              >
                + Thêm nhân vật mới
              </button>
            </div>
          </div>

          {currentReferenceSets.length === 0 ? (
            <p style={{ color: '#94a3b8', textAlign: 'center', padding: '30px 0' }}>
              Chưa có nhân vật nào được cấu hình trong bộ prompt này. Hãy nhấn &quot;+ Thêm nhân vật mới&quot; để thiết lập.
            </p>
          ) : (
            <div className="muse-refset-grid">
              {currentReferenceSets.map((refSet, index) => {
                const aliasValue = getMuseAliasInputValue(refSet)
                return (
                  <div key={refSet.id || index} className="muse-refset-card">
                    <div className="muse-refset-header">
                      <input
                        type="text"
                        className="muse-refset-name-input"
                        value={refSet.name || ''}
                        placeholder="Tên nhân vật (ví dụ: Nhân vật chính)"
                        onChange={e => handleModifyReferenceSet(index, { name: e.target.value })}
                      />
                      <button
                        type="button"
                        className="muse-btn danger"
                        style={{ padding: '4px 8px', fontSize: '0.75rem' }}
                        onClick={() => handleRemoveReferenceSet(index)}
                        title="Xóa bộ nhân vật này"
                      >
                        Xóa
                      </button>
                    </div>

                    <div className="muse-refset-field">
                      <label>Aliases (từ khóa nhận diện trong kịch bản):</label>
                      <input
                        type="text"
                        value={aliasValue}
                        placeholder="Ví dụ: anh ấy, chàng trai, John Doe, nam chính"
                        onChange={e => {
                          const draft = e.target.value
                          handleModifyReferenceSet(index, {
                            aliases_draft: draft,
                            aliases: normalizeMuseAliases(draft)
                          })
                        }}
                        onBlur={() => {
                          const normalized = normalizeMuseAliases(refSet.aliases_draft || refSet.aliases || [])
                          handleModifyReferenceSet(index, {
                            aliases: normalized,
                            aliases_draft: undefined
                          })
                        }}
                      />
                      <small style={{ color: '#64748b', fontSize: '0.75rem' }}>
                        Phân cách bằng dấu phẩy. Muse sẽ tự động dùng ảnh nhân vật khi gặp từ khóa này trong kịch bản.
                      </small>
                    </div>

                    <div className="muse-refset-field">
                      <label>Đường dẫn ảnh mẫu (Ảnh mặt/Face Reference):</label>
                      <textarea
                        rows={2}
                        value={(refSet.images || []).join('\n')}
                        placeholder="Nhập đường dẫn file ảnh mẫu hoặc URL (mỗi dòng một ảnh)"
                        onChange={e => {
                          const imgs = e.target.value.split('\n').map(s => s.trim()).filter(Boolean)
                          handleModifyReferenceSet(index, { images: imgs })
                        }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Tab 2: Accounts & Quotas */}
      {activeTab === 'accounts' && (
        <div className="muse-card">
          <div className="muse-card-title">
            <h2>👥 Danh sách Tài khoản Muse Pool</h2>
            <div style={{ display: 'flex', gap: 10 }}>
              <button type="button" className="muse-btn" onClick={refreshAccounts}>
                🔄 Làm mới
              </button>
              <button type="button" className="muse-btn primary" onClick={handleOpenWebAdmin}>
                + Thêm tài khoản qua Web Admin
              </button>
            </div>
          </div>

          {accounts.length === 0 ? (
            <p style={{ color: '#94a3b8', textAlign: 'center', padding: '30px 0' }}>
              Chưa tìm thấy tài khoản nào trong `accounts.json`. Hãy mở Web Admin hoặc Muse Desktop để đăng nhập và lấy cookie.
            </p>
          ) : (
            <table className="muse-table">
              <thead>
                <tr>
                  <th>Mã ID</th>
                  <th>Tên tài khoản / Email</th>
                  <th>Số Cookies</th>
                  <th>Trạng thái</th>
                  <th>Hạn dùng phiên</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map(acc => (
                  <tr key={acc.id}>
                    <td>
                      <code>{acc.id}</code>
                    </td>
                    <td>
                      <strong>{acc.label}</strong>
                    </td>
                    <td>{acc.cookie_count} cookies</td>
                    <td>
                      <span className={`muse-pill ${acc.is_expired ? 'expired' : 'active'}`}>
                        {acc.is_expired ? 'Hết hạn' : 'Đang hoạt động'}
                      </span>
                    </td>
                    <td style={{ color: '#94a3b8' }}>
                      {acc.expires_at ? new Date(acc.expires_at * 1000).toLocaleString('vi-VN') : 'Không xác định'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Tab 3: Media Gallery */}
      {activeTab === 'media' && (
        <div className="muse-card">
          <div className="muse-card-title">
            <h2>🖼️ Thư viện Media đã tạo bởi Muse AI</h2>
            <button type="button" className="muse-btn" onClick={refreshMedia}>
              🔄 Làm mới kho media
            </button>
          </div>

          {mediaItems.length === 0 ? (
            <p style={{ color: '#94a3b8', textAlign: 'center', padding: '30px 0' }}>
              Chưa có hình ảnh hoặc video nào được tạo trong thư mục `media`.
            </p>
          ) : (
            <div className="muse-gallery-grid">
              {mediaItems.map(item => (
                <div
                  key={item.filename}
                  className="muse-gallery-item"
                  onClick={() => setPreviewMedia(item)}
                >
                  {item.type === 'image' ? (
                    <img
                      src={`${API_BASE}${item.url}`}
                      alt={item.filename}
                      className="muse-gallery-thumb"
                      loading="lazy"
                    />
                  ) : (
                    <video
                      src={`${API_BASE}${item.url}`}
                      className="muse-gallery-thumb"
                      muted
                      preload="metadata"
                    />
                  )}
                  <span className="muse-gallery-badge">{item.type}</span>
                  <div className="muse-gallery-name">{item.filename}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Media Preview Modal */}
      {previewMedia && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0,0,0,0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 24
          }}
          onClick={() => setPreviewMedia(null)}
        >
          <div
            style={{
              maxWidth: '90vw',
              maxHeight: '90vh',
              background: '#0f172a',
              borderRadius: 12,
              padding: 16,
              border: '1px solid #334155',
              display: 'flex',
              flexDirection: 'column',
              gap: 12
            }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 600 }}>{previewMedia.filename}</span>
              <button
                type="button"
                className="muse-btn"
                style={{ padding: '4px 10px' }}
                onClick={() => setPreviewMedia(null)}
              >
                ✕ Đóng
              </button>
            </div>
            {previewMedia.type === 'image' ? (
              <img
                src={`${API_BASE}${previewMedia.url}`}
                alt={previewMedia.filename}
                style={{ maxWidth: '100%', maxHeight: '70vh', objectFit: 'contain' }}
              />
            ) : (
              <video
                src={`${API_BASE}${previewMedia.url}`}
                controls
                autoPlay
                style={{ maxWidth: '100%', maxHeight: '70vh' }}
              />
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
              <a
                href={`${API_BASE}${previewMedia.url}`}
                download={previewMedia.filename}
                className="muse-btn primary"
                style={{ textDecoration: 'none' }}
              >
                ⬇ Tải về máy
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
