import { useCallback, useEffect, useMemo, useState } from 'react'
import './MuseStudio.css'
import {
  getMuseAliasInputValue,
  normalizeMuseAliases,
  splitMuseAliasDraft
} from './museAliases'
import {
  formatMuseDateTime,
  formatMuseTokens,
  formatMuseWeeklyReset,
  getBrowserTimeZone,
  summarizeMuseAccounts
} from './museQuota'
import {
  buildMuseAutoLoginPayload,
  getMuseBulkReloginCandidates,
  shouldShowMuseRelogin
} from './museAutoLogin'

const API_BASE = 'http://127.0.0.1:8080'
const EMPTY_LOGIN_FORM = {
  email: '',
  method: 'password',
  password: '',
  two_factor_secret: '',
  mailbox_url: ''
}

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
    account_relogin: 0,
    api_url: 'http://127.0.0.1:18610'
  })
  const [studioPath, setStudioPath] = useState('E:\\yt\\Tool\\Muse_AI_Studio')
  const [isBusy, setIsBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [refreshingQuotaIds, setRefreshingQuotaIds] = useState([])
  const [quotaRefreshProgress, setQuotaRefreshProgress] = useState(null)
  const [mediaItems, setMediaItems] = useState([])
  const [promptsData, setPromptsData] = useState(null)
  const [activePromptVersion, setActivePromptVersion] = useState('')
  const [previewMedia, setPreviewMedia] = useState(null)
  const [loginModalAccount, setLoginModalAccount] = useState(null)
  const [loginForm, setLoginForm] = useState(EMPTY_LOGIN_FORM)
  const [loginBusyId, setLoginBusyId] = useState('')
  const [loginStates, setLoginStates] = useState({})
  const [loginErrors, setLoginErrors] = useState({})
  const [bulkReloginProgress, setBulkReloginProgress] = useState(null)

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

  const refreshAccountQuota = async (accountId, { quiet = false } = {}) => {
    setRefreshingQuotaIds(current => current.includes(accountId) ? current : [...current, accountId])
    try {
      const res = await fetch(`${API_BASE}/api/muse/accounts/${encodeURIComponent(accountId)}/quota/refresh`, {
        method: 'POST'
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || 'Không thể cập nhật quota tài khoản Muse.')
      setAccounts(current => current.map(account => (
        account.id === accountId
          ? {
              ...account,
              quota: data.quota,
              media_quota_status: data.quota?.status || 'unknown'
            }
          : account
      )))
      if (!quiet) showNotice('Đã cập nhật quota tài khoản Muse.', 'success')
      return true
    } catch (err) {
      if (!quiet) showNotice(err.message, 'error')
      return false
    } finally {
      setRefreshingQuotaIds(current => current.filter(id => id !== accountId))
    }
  }

  const handleRefreshAllQuotas = async () => {
    if (serviceStatus.active_media_tasks > 0) {
      showNotice('Muse đang tạo media; hãy cập nhật quota sau khi tác vụ hoàn tất.', 'error')
      return
    }
    if (accounts.length === 0 || quotaRefreshProgress) return
    let succeeded = 0
    setQuotaRefreshProgress({ completed: 0, total: accounts.length })
    for (let index = 0; index < accounts.length; index += 1) {
      const ok = await refreshAccountQuota(accounts[index].id, { quiet: true })
      if (ok) succeeded += 1
      setQuotaRefreshProgress({ completed: index + 1, total: accounts.length })
    }
    await refreshAccounts()
    setQuotaRefreshProgress(null)
    if (succeeded === accounts.length) {
      showNotice(`Đã cập nhật quota của ${succeeded}/${accounts.length} tài khoản.`, 'success')
    } else {
      showNotice(`Đã cập nhật ${succeeded}/${accounts.length} tài khoản; một số tài khoản cần kiểm tra lại.`, 'error')
    }
  }

  const setAccountLoginState = (accountId, state, error = '') => {
    setLoginStates(current => ({ ...current, [accountId]: state }))
    setLoginErrors(current => ({ ...current, [accountId]: error }))
  }

  const closeLoginModal = () => {
    setLoginModalAccount(null)
    setLoginForm(EMPTY_LOGIN_FORM)
  }

  const openLoginModal = account => {
    setLoginModalAccount(account)
    setLoginForm({
      ...EMPTY_LOGIN_FORM,
      email: account.login_email || '',
      method: account.auto_login_method === 'mailbox' ? 'mailbox' : 'password'
    })
  }

  const refreshMuseAccountState = async () => {
    await Promise.all([refreshAccounts(), refreshStatus()])
  }

  const requestMuseRelogin = async account => {
    const res = await fetch(`${API_BASE}/api/muse/accounts/${encodeURIComponent(account.id)}/relogin`, {
      method: 'POST'
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || 'Không thể đăng nhập lại tài khoản Muse.')
    return data
  }

  const handleRelogin = async account => {
    setLoginBusyId(account.id)
    setAccountLoginState(account.id, 'running')
    try {
      await requestMuseRelogin(account)
      setAccountLoginState(account.id, 'recovered')
      showNotice('Đã khôi phục phiên Muse và tải lại trạng thái tài khoản.', 'success')
      await refreshMuseAccountState()
    } catch (err) {
      setAccountLoginState(account.id, 'failed', err.message)
      showNotice(err.message, 'error')
    } finally {
      setLoginBusyId('')
    }
  }

  const handleReloginAll = async () => {
    if (serviceStatus.active_media_tasks > 0) {
      showNotice('Muse đang tạo media; hãy đăng nhập lại sau khi tác vụ hoàn tất.', 'error')
      return
    }
    const needingRelogin = accounts.filter(shouldShowMuseRelogin)
    const candidates = getMuseBulkReloginCandidates(accounts)
    const missingConfiguration = needingRelogin.length - candidates.length
    if (candidates.length === 0) {
      showNotice(
        missingConfiguration > 0
          ? `${missingConfiguration} tài khoản cần đăng nhập lại nhưng chưa cấu hình auto-login.`
          : 'Không có tài khoản Muse nào cần đăng nhập lại.',
        missingConfiguration > 0 ? 'error' : 'info'
      )
      return
    }

    let succeeded = 0
    let failed = 0
    setBulkReloginProgress({ completed: 0, total: candidates.length })
    for (let index = 0; index < candidates.length; index += 1) {
      const account = candidates[index]
      setLoginBusyId(account.id)
      setAccountLoginState(account.id, 'running')
      try {
        await requestMuseRelogin(account)
        succeeded += 1
        setAccountLoginState(account.id, 'recovered')
      } catch (err) {
        failed += 1
        setAccountLoginState(account.id, 'failed', err.message)
      }
      setBulkReloginProgress({ completed: index + 1, total: candidates.length })
    }
    setLoginBusyId('')
    setBulkReloginProgress(null)
    await refreshMuseAccountState()

    const parts = [`đã khôi phục ${succeeded}/${candidates.length}`]
    if (failed > 0) parts.push(`${failed} thất bại`)
    if (missingConfiguration > 0) parts.push(`${missingConfiguration} thiếu cấu hình`)
    showNotice(
      `Đăng nhập lại Muse: ${parts.join(' · ')}.`,
      failed > 0 || missingConfiguration > 0 ? 'error' : 'success'
    )
  }

  const handleConfigureAutoLogin = async event => {
    event.preventDefault()
    const account = loginModalAccount
    if (!account) return
    const payload = buildMuseAutoLoginPayload(loginForm)
    setLoginBusyId(account.id)
    setAccountLoginState(account.id, 'running')
    try {
      const res = await fetch(`${API_BASE}/api/muse/accounts/${encodeURIComponent(account.id)}/auto-login`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || 'Không thể xác minh cấu hình đăng nhập Muse.')
      setAccountLoginState(account.id, 'recovered')
      showNotice('Credential đã được xác minh, mã hóa DPAPI và phiên Muse đã được khôi phục.', 'success')
      await refreshMuseAccountState()
    } catch (err) {
      setAccountLoginState(account.id, 'failed', err.message)
      showNotice(err.message, 'error')
    } finally {
      setLoginBusyId('')
      closeLoginModal()
    }
  }

  const handleDeleteAutoLogin = async account => {
    if (!window.confirm(`Xóa cấu hình auto-login đã lưu cho ${account.label || account.id}?`)) return
    setLoginBusyId(account.id)
    try {
      const res = await fetch(`${API_BASE}/api/muse/accounts/${encodeURIComponent(account.id)}/auto-login`, {
        method: 'DELETE'
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || 'Không thể xóa cấu hình auto-login Muse.')
      setAccountLoginState(account.id, account.needs_relogin ? 'missing' : '')
      showNotice('Đã xóa credential Muse đã mã hóa.', 'success')
      await refreshAccounts()
    } catch (err) {
      showNotice(err.message, 'error')
    } finally {
      setLoginBusyId('')
    }
  }

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
  const accountQuotaSummary = useMemo(() => summarizeMuseAccounts(accounts), [accounts])
  const displayTimeZone = useMemo(() => getBrowserTimeZone(), [])

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
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="muse-btn"
                disabled={Boolean(quotaRefreshProgress)}
                onClick={refreshAccounts}
                title="Đọc lại dữ liệu quota đã cache, không mở trình duyệt Muse"
              >
                🔄 Làm mới cache
              </button>
              <button
                type="button"
                className="muse-btn success"
                disabled={
                  accounts.length === 0
                  || Boolean(quotaRefreshProgress)
                  || Boolean(loginBusyId)
                  || Boolean(bulkReloginProgress)
                  || serviceStatus.active_media_tasks > 0
                }
                onClick={handleRefreshAllQuotas}
                title={serviceStatus.active_media_tasks > 0
                  ? 'Muse đang tạo media; cập nhật quota được khóa để tránh tranh chấp browser'
                  : 'Mở Settings Muse và cập nhật lần lượt từng tài khoản'}
              >
                {quotaRefreshProgress
                  ? `⏳ Đang kiểm tra ${quotaRefreshProgress.completed}/${quotaRefreshProgress.total}`
                  : '⚡ Cập nhật tất cả quota'}
              </button>
              {accounts.some(shouldShowMuseRelogin) && (
                <button
                  type="button"
                  className="muse-btn warning"
                  disabled={
                    Boolean(loginBusyId)
                    || Boolean(bulkReloginProgress)
                    || Boolean(quotaRefreshProgress)
                    || serviceStatus.active_media_tasks > 0
                  }
                  onClick={handleReloginAll}
                  title="Đăng nhập tuần tự các tài khoản cần khôi phục và đã lưu credential"
                >
                  {bulkReloginProgress
                    ? `⏳ Đang đăng nhập ${bulkReloginProgress.completed}/${bulkReloginProgress.total}`
                    : `🔐 Đăng nhập lại tất cả (${accounts.filter(shouldShowMuseRelogin).length})`}
                </button>
              )}
              <button type="button" className="muse-btn primary" onClick={handleOpenWebAdmin}>
                + Thêm tài khoản qua Web Admin
              </button>
            </div>
          </div>

          <div className="muse-quota-summary">
            <div className="muse-quota-summary-item">
              <span>Tổng tài khoản</span>
              <strong>{accountQuotaSummary.total}</strong>
            </div>
            <div className="muse-quota-summary-item eligible">
              <span>Có thể tạo media</span>
              <strong>{accountQuotaSummary.eligible}</strong>
            </div>
            <div className="muse-quota-summary-item exhausted">
              <span>Hết toàn bộ quota</span>
              <strong>{accountQuotaSummary.exhausted}</strong>
            </div>
            <div className="muse-quota-summary-item unknown">
              <span>Chưa rõ / dữ liệu cũ</span>
              <strong>{accountQuotaSummary.unknown}</strong>
            </div>
          </div>

          <div className="muse-quota-timezone-note">
            Các mốc có giờ hiển thị theo <strong>{displayTimeZone}</strong> và luôn kèm múi giờ.
            Ngày hồi hạn mức sẽ ghi rõ khi Muse không cung cấp giờ hoặc múi giờ.
          </div>

          {serviceStatus.active_media_tasks > 0 && (
            <div className="muse-notice info">
              ℹ️ Cập nhật quota đang tạm khóa vì Muse có {serviceStatus.active_media_tasks} tác vụ media đang chạy.
            </div>
          )}

          {accounts.length === 0 ? (
            <p style={{ color: '#94a3b8', textAlign: 'center', padding: '30px 0' }}>
              Chưa tìm thấy tài khoản nào trong `accounts.json`. Hãy mở Web Admin hoặc Muse Desktop để đăng nhập và lấy cookie.
            </p>
          ) : (
            <div className="muse-table-wrap">
              <table className="muse-table muse-quota-table">
                <thead>
                  <tr>
                    <th>Tài khoản</th>
                    <th>Phiên đăng nhập</th>
                    <th>Hạn mức tuần</th>
                    <th>Token bổ sung</th>
                    <th>Ngày hồi hạn mức</th>
                    <th>Trạng thái quota</th>
                    <th>Lần kiểm tra</th>
                    <th>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map(acc => {
                    const quota = acc.quota || {}
                    const weeklyRemaining = Number(quota.weekly_remaining_pct)
                    const hasWeeklyQuota = quota.found && Number.isFinite(weeklyRemaining)
                    const quotaStatus = acc.media_quota_status || 'unknown'
                    const quotaStatusText = quotaStatus === 'eligible'
                      ? 'Có thể dùng'
                      : quotaStatus === 'exhausted'
                        ? 'Hết quota'
                        : 'Chưa xác định'
                    const sessionText = acc.enabled === false
                      ? 'Đã tắt'
                      : acc.is_expired
                        ? 'Hết hạn'
                        : acc.verification_state === 'unverified'
                          ? 'Chưa xác minh'
                          : acc.session_ok === false
                          ? 'Lỗi phiên'
                          : acc.needs_relogin
                            ? 'Cần đăng nhập lại'
                            : 'Đang hoạt động'
                    const sessionClass = sessionText === 'Đang hoạt động' ? 'active' : 'expired'
                    const isRefreshing = refreshingQuotaIds.includes(acc.id)
                    return (
                      <tr key={acc.id}>
                        <td>
                          <strong>{acc.label || acc.id}</strong>
                          <div className="muse-table-subtext">
                            <code>{acc.id}</code> · {acc.cookie_count} cookies
                          </div>
                        </td>
                        <td>
                          <span className={`muse-pill ${sessionClass}`}>{sessionText}</span>
                          <div className="muse-table-subtext">
                            {acc.expires_at
                              ? `Hết hạn: ${formatMuseDateTime(acc.expires_at, displayTimeZone)}`
                              : 'Hạn phiên: Không xác định'}
                          </div>
                          <div className="muse-account-login-status">
                            {acc.auto_login_configured ? (
                              <span className="muse-pill auto-login-configured">
                                Auto-login: {acc.auto_login_method === 'mailbox' ? 'Hộp thư' : 'Mật khẩu'}
                              </span>
                            ) : acc.needs_relogin ? (
                              <span className="muse-pill auto-login-missing">Thiếu cấu hình</span>
                            ) : null}
                            {loginStates[acc.id] === 'running' && (
                              <div className="muse-table-subtext login-running">Đang tự đăng nhập…</div>
                            )}
                            {loginStates[acc.id] === 'recovered' && (
                              <div className="muse-table-subtext login-recovered">Đã khôi phục</div>
                            )}
                            {loginStates[acc.id] === 'failed' && (
                              <div className="muse-table-subtext warning">
                                {loginErrors[acc.id] || 'Đăng nhập thất bại'}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="muse-quota-cell">
                          {hasWeeklyQuota ? (
                            <>
                              <strong>Còn {Math.round(weeklyRemaining)}%</strong>
                              <div className="muse-quota-bar" aria-label={`Hạn mức tuần còn ${weeklyRemaining}%`}>
                                <span style={{ width: `${weeklyRemaining}%` }} />
                              </div>
                              <div className="muse-table-subtext">
                                Đã dùng {Math.round(Number(quota.weekly_used_pct) || 0)}%
                                {quota.plan ? ` · ${quota.plan}` : ''}
                              </div>
                            </>
                          ) : (
                            <span className="muse-table-subtext">Chưa kiểm tra</span>
                          )}
                        </td>
                        <td>
                          <strong>{quota.found ? formatMuseTokens(quota) : 'Chưa kiểm tra'}</strong>
                          {quota.extra_used_pct != null && (
                            <div className="muse-table-subtext">Đã dùng {Math.round(quota.extra_used_pct)}%</div>
                          )}
                        </td>
                        <td className="muse-reset-cell">
                          {formatMuseWeeklyReset(quota.weekly_reset)}
                        </td>
                        <td>
                          <span className={`muse-pill quota-${quotaStatus}`}>{quotaStatusText}</span>
                          {quota.stale && <div className="muse-table-subtext warning">Dữ liệu đã cũ</div>}
                        </td>
                        <td className="muse-checked-cell">
                          {quota.checked_at
                            ? formatMuseDateTime(quota.checked_at, displayTimeZone)
                            : 'Chưa kiểm tra'}
                        </td>
                        <td>
                          <div className="muse-account-actions">
                            <button
                              type="button"
                              className="muse-btn"
                              disabled={
                                isRefreshing
                                || Boolean(quotaRefreshProgress)
                                || serviceStatus.active_media_tasks > 0
                                || Boolean(loginBusyId)
                                || Boolean(bulkReloginProgress)
                              }
                              onClick={() => refreshAccountQuota(acc.id)}
                            >
                              {isRefreshing ? '⏳ Đang kiểm tra' : 'Kiểm tra quota'}
                            </button>
                            {shouldShowMuseRelogin(acc) && (
                              <button
                                type="button"
                                className="muse-btn warning"
                                disabled={
                                  serviceStatus.active_media_tasks > 0
                                  || Boolean(loginBusyId)
                                  || Boolean(bulkReloginProgress)
                                }
                                onClick={() => acc.auto_login_configured ? handleRelogin(acc) : openLoginModal(acc)}
                              >
                                {loginBusyId === acc.id
                                  ? '⏳ Đang tự đăng nhập'
                                  : acc.auto_login_configured
                                    ? 'Đăng nhập lại ngay'
                                    : 'Cấu hình đăng nhập lại'}
                              </button>
                            )}
                            {acc.auto_login_configured && (
                              <button
                                type="button"
                                className="muse-link-danger"
                                disabled={Boolean(loginBusyId) || Boolean(bulkReloginProgress)}
                                onClick={() => handleDeleteAutoLogin(acc)}
                              >
                                Xóa credential đã lưu
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
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

      {/* Muse auto-login configuration modal */}
      {loginModalAccount && (
        <div className="muse-login-modal-backdrop" onClick={closeLoginModal}>
          <form className="muse-login-modal" onSubmit={handleConfigureAutoLogin} onClick={event => event.stopPropagation()}>
            <div className="muse-login-modal-header">
              <div>
                <h2>Cấu hình đăng nhập lại Muse</h2>
                <p>{loginModalAccount.label || loginModalAccount.id}</p>
              </div>
              <button type="button" className="muse-modal-close" onClick={closeLoginModal}>✕</button>
            </div>

            <label className="muse-login-field">
              <span>Email Muse</span>
              <input
                type="email"
                value={loginForm.email}
                onChange={event => setLoginForm(current => ({ ...current, email: event.target.value }))}
                autoComplete="username"
                required
              />
            </label>

            <div className="muse-login-methods" role="group" aria-label="Phương thức đăng nhập Muse">
              <button
                type="button"
                className={loginForm.method === 'password' ? 'active' : ''}
                onClick={() => setLoginForm(current => ({ ...current, method: 'password' }))}
              >
                Mật khẩu + TOTP
              </button>
              <button
                type="button"
                className={loginForm.method === 'mailbox' ? 'active' : ''}
                onClick={() => setLoginForm(current => ({ ...current, method: 'mailbox' }))}
              >
                Link hộp thư
              </button>
            </div>

            {loginForm.method === 'password' ? (
              <>
                <label className="muse-login-field">
                  <span>Mật khẩu</span>
                  <input
                    type="password"
                    value={loginForm.password}
                    onChange={event => setLoginForm(current => ({ ...current, password: event.target.value }))}
                    autoComplete="current-password"
                    required
                  />
                </label>
                <label className="muse-login-field">
                  <span>Secret TOTP (nếu tài khoản có 2FA)</span>
                  <input
                    type="password"
                    value={loginForm.two_factor_secret}
                    onChange={event => setLoginForm(current => ({ ...current, two_factor_secret: event.target.value }))}
                    autoComplete="off"
                  />
                </label>
              </>
            ) : (
              <label className="muse-login-field">
                <span>Mailbox URL</span>
                <input
                  type="url"
                  value={loginForm.mailbox_url}
                  onChange={event => setLoginForm(current => ({ ...current, mailbox_url: event.target.value }))}
                  autoComplete="off"
                  placeholder="https://..."
                  required
                />
              </label>
            )}

            <div className="muse-login-security-note">
              Credential chỉ được gửi qua loopback để xác minh, sau đó lưu bằng DPAPI và không được trả lại giao diện.
            </div>
            <div className="muse-login-modal-actions">
              <button type="button" className="muse-btn" onClick={closeLoginModal}>Hủy</button>
              <button type="submit" className="muse-btn success" disabled={loginBusyId === loginModalAccount.id}>
                {loginBusyId === loginModalAccount.id ? 'Đang xác minh…' : 'Xác minh & lưu an toàn'}
              </button>
            </div>
          </form>
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
