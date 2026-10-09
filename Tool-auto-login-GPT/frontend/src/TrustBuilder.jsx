import { useState, useEffect, useCallback, useMemo } from 'react'
import { useSubRoute } from './router.js'
import { extractErrorMessage } from './apiError'
import './TrustBuilder.css'

const API_BASE = 'http://127.0.0.1:8080'

function formatSeconds(seconds) {
  const s = Math.floor(Number(seconds) || 0)
  const m = Math.floor(s / 60)
  const remS = s % 60
  if (m === 0) return `${remS}s`
  return `${m}m${remS > 0 ? ` ${remS}s` : ''}`
}

function getPhaseLabel(phase) {
  switch (phase) {
    case 'phase_1_consumer':
      return { label: 'Phase 1: Consumer Warm-up (Tìm kiếm & Xem video)', badge: 'phase-1' }
    case 'phase_2_engage':
      return { label: 'Phase 2: Active Engagement (Like, Comment & Sub)', badge: 'phase-2' }
    case 'phase_3_ready':
      return { label: 'Phase 3: Ready to Publish (Đã đạt chuẩn Trust)', badge: 'phase-3' }
    case 'completed':
      return { label: 'Hoàn tất Nuôi Kênh', badge: 'phase-completed' }
    default:
      return { label: 'Chưa kích hoạt', badge: 'phase-idle' }
  }
}

function getScoreColor(score) {
  if (score >= 70) return '#10b981' // Green
  if (score >= 40) return '#f59e0b' // Yellow
  return '#ef4444' // Red
}

function getStatusLabel(status) {
  const labels = {
    active: '🟢 Đang chạy',
    paused: '🟡 Tạm dừng',
    completed: '✅ Hoàn tất',
    error: '🔴 Lỗi cấu hình',
    draft: '⚪ Bản nháp'
  }
  return labels[status] || `⚪ ${status || 'Bản nháp'}`
}

function getChecklistState(value) {
  if (value === true) return { icon: '✅', className: 'checked' }
  if (value === false) return { icon: '❌', className: '' }
  return { icon: '❔', className: 'unknown' }
}

function getActivityIcon(type) {
  switch (type) {
    case 'search': return '🔍'
    case 'watch': return '👁️'
    case 'like': return '👍'
    case 'comment': return '💬'
    case 'subscribe': return '🔔'
    case 'branding_audit': return '🛡️'
    case 'error': return '⚠️'
    default: return '⚡'
  }
}

export default function TrustBuilder() {
  const [subRoute, setSubRoute] = useSubRoute('trustbuilder', '')
  const [channels, setChannels] = useState([])
  const [plans, setPlans] = useState([])
  const [selectedChannelId, setSelectedChannelId] = useState(null)
  const [selectedPlan, setSelectedPlan] = useState(null)
  const [activities, setActivities] = useState([])
  const [stats, setStats] = useState(null)
  const [dailyStats, setDailyStats] = useState(null)
  const [activeJob, setActiveJob] = useState(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [feedback, setFeedback] = useState({ message: '', type: '' })

  // Keyword & Channel inputs
  const [keywordInput, setKeywordInput] = useState('')
  const [targetChannelInput, setTargetChannelInput] = useState('')
  const [searchFilter, setSearchFilter] = useState('')

  // Safety Shield State
  const [safetyConfig, setSafetyConfig] = useState(null)
  const [safetyCounts, setSafetyCounts] = useState(null)
  const [safetyModalOpen, setSafetyModalOpen] = useState(false)
  const [blacklistEntries, setBlacklistEntries] = useState([])
  const [blacklistLoading, setBlacklistLoading] = useState(false)
  const [blacklistSearch, setBlacklistSearch] = useState('')
  const [blacklistTypeFilter, setBlacklistTypeFilter] = useState('')
  const [newRuleValue, setNewRuleValue] = useState('')
  const [newRuleType, setNewRuleType] = useState('channel')
  const [newRuleReason, setNewRuleReason] = useState('')
  const [syncBusy, setSyncBusy] = useState(false)
  const [blockedChannelsMap, setBlockedChannelsMap] = useState({})

  const showFeedback = (msg, type = 'info') => {
    setFeedback({ message: msg, type })
    setTimeout(() => setFeedback({ message: '', type: '' }), 4500)
  }

  const loadBlacklistRules = useCallback(async (type = '', search = '') => {
    setBlacklistLoading(true)
    try {
      const params = new URLSearchParams()
      if (type) params.set('entry_type', type)
      if (search) params.set('search', search)
      params.set('limit', '200')
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/blacklist?${params.toString()}`)
      if (res.ok) {
        const data = await res.json()
        setBlacklistEntries(data.entries || [])
      }
    } catch (err) {
      console.warn('Lỗi tải danh sách Blacklist:', err)
    } finally {
      setBlacklistLoading(false)
    }
  }, [])

  const loadSafetyConfig = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/config`)
      if (res.ok) {
        const data = await res.json()
        setSafetyConfig(data.config)
        setSafetyCounts(data.counts)
      }
    } catch (err) {
      console.warn('Lỗi tải cấu hình Safety Shield:', err)
    }
  }, [])

  const handleTriggerSync = async () => {
    setSyncBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/sync`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        showFeedback(data.message || 'Đồng bộ Core Blacklist thành công!', 'success')
        await Promise.all([loadSafetyConfig(), loadBlacklistRules(blacklistTypeFilter, blacklistSearch)])
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể đồng bộ Blacklist.'), 'error')
      }
    } catch {
      showFeedback('Lỗi kết nối khi đồng bộ Blacklist.', 'error')
    } finally {
      setSyncBusy(false)
    }
  }

  const handleToggleShield = async () => {
    if (!safetyConfig) return
    const newState = !safetyConfig.shield_enabled
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shield_enabled: newState })
      })
      if (res.ok) {
        const data = await res.json()
        setSafetyConfig(data.config)
        showFeedback(`Đã ${newState ? 'BẬT' : 'TẮT'} Lá Chắn An Toàn Quốc Gia.`, 'success')
      }
    } catch {
      showFeedback('Lỗi khi cập nhật trạng thái khiên.', 'error')
    }
  }

  const handleQuickBlockChannel = async (channelVal, reason = 'Chặn nhanh từ Nhật ký hoạt động') => {
    if (!channelVal) return
    const clean = String(channelVal).trim()
    if (!clean) return
    setBlockedChannelsMap(prev => ({ ...prev, [clean]: 'loading' }))
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/blacklist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entry_type: 'channel',
          entry_value: clean,
          reason,
          is_custom: 1,
          is_enabled: true
        })
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        setBlockedChannelsMap(prev => ({ ...prev, [clean]: 'blocked' }))
        showFeedback(`Đã chặn kênh '${clean}' thành công vào Blacklist!`, 'success')
        await Promise.all([loadSafetyConfig(), loadBlacklistRules(blacklistTypeFilter, blacklistSearch)])
      } else {
        setBlockedChannelsMap(prev => ({ ...prev, [clean]: 'error' }))
        showFeedback(extractErrorMessage(data, 'Không thể chặn kênh.'), 'error')
      }
    } catch {
      setBlockedChannelsMap(prev => ({ ...prev, [clean]: 'error' }))
      showFeedback('Lỗi khi thêm quy tắc chặn kênh.', 'error')
    }
  }

  const handleAddCustomRule = async (e) => {
    if (e) e.preventDefault()
    const val = newRuleValue.trim()
    if (!val) return
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/blacklist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entry_type: newRuleType,
          entry_value: val,
          reason: newRuleReason.trim() || 'Người dùng thêm thủ công',
          is_custom: 1,
          is_enabled: true
        })
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        showFeedback(`Đã thêm quy tắc chặn '${val}' thành công!`, 'success')
        setNewRuleValue('')
        setNewRuleReason('')
        await loadSafetyConfig()
        await loadBlacklistRules(blacklistTypeFilter, blacklistSearch)
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể thêm quy tắc.'), 'error')
      }
    } catch {
      showFeedback('Lỗi khi thêm quy tắc.', 'error')
    }
  }

  const handleDeleteRule = async (ruleId) => {
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/safety/blacklist/${ruleId}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        showFeedback('Đã xóa quy tắc chặn.', 'success')
        await loadSafetyConfig()
        await loadBlacklistRules(blacklistTypeFilter, blacklistSearch)
      }
    } catch {
      showFeedback('Lỗi khi xóa quy tắc.', 'error')
    }
  }

  const handleSelectChannel = useCallback((channelId) => {
    setSelectedChannelId(channelId)
    setSubRoute(String(channelId), { replace: false })
  }, [setSubRoute])

  // Load plan details & activities for given channel
  const loadPlanDetails = useCallback(async (channelId, plansList = null) => {
    if (!channelId) {
      setSelectedPlan(null)
      setActivities([])
      setStats(null)
      setDailyStats(null)
      setActiveJob(null)
      return
    }

    const activePlans = plansList || plans
    const currentPlan = activePlans.find(p => p.channel_db_id === channelId)
    if (!currentPlan) {
      setSelectedPlan(null)
      setActivities([])
      setStats(null)
      setDailyStats(null)
      setActiveJob(null)
      return
    }

    try {
      const [detailRes, actRes] = await Promise.all([
        fetch(`${API_BASE}/api/trust-builder/plans/${currentPlan.id}`),
        fetch(`${API_BASE}/api/trust-builder/plans/${currentPlan.id}/activities?limit=30`)
      ])

      if (detailRes.ok) {
        const data = await detailRes.json()
        setSelectedPlan(data.plan)
        setStats(data.stats)
        setDailyStats(data.daily_stats || null)
        setActiveJob(data.active_job || null)
      }

      if (actRes.ok) {
        const actData = await actRes.json()
        setActivities(actData.logs || [])
      }
    } catch (err) {
      console.warn('Lỗi tải chi tiết plan:', err)
    }
  }, [plans])

  // Load channels and trust plans once or on demand
  const loadInitialData = useCallback(async () => {
    try {
      const [chRes, planRes] = await Promise.all([
        fetch(`${API_BASE}/api/youtube-comments/channels`),
        fetch(`${API_BASE}/api/trust-builder/plans`)
      ])
      let chList = []
      if (chRes.ok) {
        const chData = await chRes.json()
        chList = Array.isArray(chData) ? chData : (chData.items || chData.channels || [])
      }

      let planList = []
      if (planRes.ok) {
        const planData = await planRes.json()
        planList = Array.isArray(planData) ? planData : (planData.plans || planData.items || [])
      }

      setChannels(chList)
      setPlans(planList)
      return { chList, planList }
    } catch (err) {
      console.warn('Lỗi kết nối API Trust Builder:', err)
      return { chList: [], planList: [] }
    }
  }, [])

  // Mount effect: load initial channels, plans, and safety status
  useEffect(() => {
    let cancelled = false
    loadSafetyConfig()
    loadBlacklistRules()
    loadInitialData().then(({ chList, planList }) => {
      if (cancelled || chList.length === 0) return
      let matched = null
      if (subRoute) {
        matched = chList.find(c => String(c.id) === String(subRoute) || String(c.channel_id) === String(subRoute))
      }
      const target = matched || chList[0]
      setSelectedChannelId(target.id)
      loadPlanDetails(target.id, planList)
    })
    return () => { cancelled = true }
  }, [loadInitialData, loadSafetyConfig, loadBlacklistRules])

  // Sync state if subRoute changes externally (e.g. browser back/forward or direct link)
  useEffect(() => {
    if (!subRoute || channels.length === 0) return
    const matched = channels.find(c => String(c.id) === String(subRoute) || String(c.channel_id) === String(subRoute))
    if (matched && matched.id !== selectedChannelId) {
      setSelectedChannelId(matched.id)
    }
  }, [subRoute, channels, selectedChannelId])

  // Load plan details when selected channel changes
  useEffect(() => {
    if (selectedChannelId) {
      loadPlanDetails(selectedChannelId)
    }
  }, [selectedChannelId, loadPlanDetails])

  // Poll while a session is running or plan is active
  useEffect(() => {
    const isJobActive = activeJob?.status === 'running' || activeJob?.status === 'queued'
    const isPlanActive = selectedPlan?.status === 'active'
    if (!isJobActive && !isPlanActive) return

    const pollMilliseconds = isJobActive ? 5000 : 15000
    const interval = setInterval(() => {
      loadPlanDetails(selectedChannelId)
    }, pollMilliseconds)
    return () => clearInterval(interval)
  }, [activeJob?.status, selectedPlan?.status, selectedChannelId, loadPlanDetails])

  // Create Plan for selected channel
  const handleCreatePlan = async () => {
    if (!selectedChannelId) return
    setActionBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel_db_id: selectedChannelId,
          niche_keywords: ['công nghệ', 'xu hướng', 'kiến thức thú vị'],
          target_channels: [],
          daily_watch_target: 5,
          daily_search_target: 3,
          daily_like_target: 3,
          daily_comment_target: 0,
          daily_subscribe_target: 0,
          min_watch_minutes: 10,
          warmup_phase: 'phase_1_consumer'
        })
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        showFeedback('Đã khởi tạo Kế hoạch nuôi kênh thành công!', 'success')
        const { planList } = await loadInitialData()
        await loadPlanDetails(selectedChannelId, planList)
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể tạo Kế hoạch.'), 'error')
      }
    } catch {
      showFeedback('Lỗi kết nối khi tạo plan.', 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Update Plan config (Keywords, Targets, etc.)
  const handleUpdatePlan = async (changes) => {
    if (!selectedPlan) return
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes)
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        setSelectedPlan(data.plan)
        showFeedback('Đã lưu cấu hình nuôi kênh.', 'success')
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể lưu cấu hình.'), 'error')
      }
    } catch {
      showFeedback('Lỗi khi lưu cấu hình.', 'error')
    }
  }

  const handleTargetDraftChange = (field, value) => {
    setSelectedPlan((current) => current ? { ...current, [field]: value } : current)
  }

  const handleTargetCommit = (field, value) => {
    handleUpdatePlan({ [field]: Number(value) })
  }

  // Plan lifecycle actions (Start, Pause, Resume, Run Now)
  const handlePlanAction = async (actionType) => {
    if (!selectedPlan) return
    setActionBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/${actionType}`, {
        method: 'POST'
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        if (actionType === 'run-session') {
          setActiveJob(data.job || null)
          showFeedback(data.message || 'Đã xếp Trust Builder session vào hàng đợi.', 'success')
        } else {
          showFeedback(`Đã cập nhật trạng thái: ${actionType.toUpperCase()}`, 'success')
        }
        await loadPlanDetails(selectedChannelId)
      } else {
        showFeedback(extractErrorMessage(data, 'Thao tác không thành công.'), 'error')
      }
    } catch {
      showFeedback(`Lỗi khi thực hiện ${actionType}.`, 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Delete Plan
  const handleDeletePlan = async () => {
    if (!selectedPlan) return
    if (!window.confirm('Bạn có chắc chắn muốn xóa Kế hoạch nuôi kênh này? Lịch sử tương tác sẽ bị xóa.')) return
    setActionBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}`, {
        method: 'DELETE'
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        showFeedback('Đã xóa Kế hoạch nuôi kênh.', 'success')
        setSelectedPlan(null)
        await loadInitialData()
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể xóa plan.'), 'error')
      }
    } catch {
      showFeedback('Lỗi khi xóa plan.', 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Branding Audit via GPM
  const handleAuditBranding = async () => {
    if (!selectedPlan) return
    setActionBusy(true)
    showFeedback('Đang mở Profile GPM để Audit Branding kênh, vui lòng đợi...', 'info')
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/branding-audit`, {
        method: 'POST'
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        showFeedback(`Audit hoàn tất! Điểm Trust mới: ${data.trust_score}/100`, 'success')
        await loadPlanDetails(selectedChannelId)
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể audit kênh.'), 'error')
      }
    } catch {
      showFeedback('Lỗi khi kết nối audit.', 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Verify Feature Level
  const handleVerifyFeatures = async () => {
    if (!selectedPlan) return
    setActionBusy(true)
    try {
      const res = await fetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/verify-features`, {
        method: 'POST'
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) {
        const featureMessage = data.verified
          ? `Đã xác minh cấp tính năng: ${data.feature_level}. Readiness: ${data.trust_score}/100`
          : 'Studio chưa cung cấp đủ bằng chứng để xác minh; trạng thái được giữ là unknown.'
        showFeedback(featureMessage, data.verified ? 'success' : 'info')
        await loadPlanDetails(selectedChannelId)
      } else {
        showFeedback(extractErrorMessage(data, 'Không thể kiểm tra cấp tính năng.'), 'error')
      }
    } catch {
      showFeedback('Lỗi khi xác minh tính năng.', 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Keyword tag management
  const addKeyword = () => {
    const kw = keywordInput.trim()
    if (!kw || !selectedPlan) return
    const current = selectedPlan.niche_keywords || []
    if (current.includes(kw)) return
    const updated = [...current, kw]
    handleUpdatePlan({ niche_keywords: updated })
    setKeywordInput('')
  }

  const removeKeyword = (kw) => {
    if (!selectedPlan) return
    const updated = (selectedPlan.niche_keywords || []).filter(k => k !== kw)
    handleUpdatePlan({ niche_keywords: updated })
  }

  // Target Channel tag management
  const addTargetChannel = () => {
    const ch = targetChannelInput.trim()
    if (!ch || !selectedPlan) return
    const current = selectedPlan.target_channels || []
    if (current.includes(ch)) return
    const updated = [...current, ch]
    handleUpdatePlan({ target_channels: updated })
    setTargetChannelInput('')
  }

  const removeTargetChannel = (ch) => {
    if (!selectedPlan) return
    const updated = (selectedPlan.target_channels || []).filter(c => c !== ch)
    handleUpdatePlan({ target_channels: updated })
  }

  // Filter channels
  const filteredChannels = useMemo(() => {
    if (!searchFilter.trim()) return channels
    const q = searchFilter.toLowerCase()
    return channels.filter(c =>
      (c.title || '').toLowerCase().includes(q) ||
      (c.gpm_profile_name || '').toLowerCase().includes(q)
    )
  }, [channels, searchFilter])

  const currentChannel = channels.find(c => c.id === selectedChannelId)
  const trustScore = selectedPlan ? (selectedPlan.trust_score_estimated || 0) : 0
  const phaseInfo = getPhaseLabel(selectedPlan?.warmup_phase)
  const checklist = selectedPlan?.branding_checklist || {}
  const checklistStates = {
    avatar: getChecklistState(checklist.avatar),
    banner: getChecklistState(checklist.banner),
    about: getChecklistState(checklist.about),
    handle: getChecklistState(checklist.handle),
    contact_email: getChecklistState(checklist.contact_email),
    country: getChecklistState(checklist.country),
    two_factor_auth: getChecklistState(checklist.two_factor_auth)
  }
  const profileIsReady = Boolean(
    currentChannel?.gpm_profile_id &&
    !String(currentChannel.gpm_profile_id).startsWith('local_') &&
    selectedPlan?.gpm_proxy_configured
  )

  return (
    <div className="trust-builder-container">
      {/* Floating HUD Toast Notification (Always Visible) */}
      {feedback.message && (
        <div className={`trust-floating-toast ${feedback.type}`}>
          <span className="toast-icon">
            {feedback.type === 'success' ? '✅' : feedback.type === 'error' ? '❌' : 'ℹ️'}
          </span>
          <span className="toast-text">{feedback.message}</span>
          <button
            type="button"
            className="toast-close-btn"
            onClick={() => setFeedback({ message: '', type: '' })}
            title="Đóng thông báo"
          >
            ×
          </button>
        </div>
      )}

      {/* Header */}
      <div className="trust-header">
        <div className="trust-title-group">
          <h1 className="trust-main-title">🛡️ Channel Trust Builder</h1>
          <p className="trust-subtitle">
            Warm-up kênh qua GPM-Login &amp; Playwright CDP. Điểm hiển thị là chỉ số readiness nội bộ, không phải Trust Score chính thức của YouTube.
          </p>
        </div>

        <div className="trust-header-controls">
          <div className="safety-shield-widget">
            <span className={`safety-shield-badge ${safetyConfig?.shield_enabled ? 'enabled' : 'disabled'}`}>
              {safetyConfig?.shield_enabled ? '🛡️ VN Shield: BẬT' : '⚠️ VN Shield: TẮT'}
              {safetyCounts && (
                <span className="safety-rules-count">({safetyCounts.total_active} mục chặn)</span>
              )}
            </span>
            <button
              type="button"
              className="safety-sync-btn"
              onClick={handleTriggerSync}
              disabled={syncBusy}
              title="Cập nhật danh sách đen từ xa ngay lập tức"
            >
              {syncBusy ? '🔄 Đang tải...' : '🔄 Cập nhật Core'}
            </button>
            <button
              type="button"
              className="safety-manage-btn"
              onClick={() => {
                setSafetyModalOpen(true)
                loadBlacklistRules(blacklistTypeFilter, blacklistSearch)
              }}
              title="Xem và quản lý danh sách kênh/từ khóa bị chặn"
            >
              ⚙️ Quản lý Blacklist
            </button>
          </div>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="trust-layout-grid">
        {/* Left Panel: Channel List */}
        <div className="trust-channel-sidebar">
          <div className="trust-sidebar-header">
            <h3>Danh Sách Kênh ({filteredChannels.length})</h3>
            <input
              type="text"
              placeholder="🔍 Tìm kênh / profile..."
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              className="trust-search-input"
            />
          </div>

          <div className="trust-channel-list">
            {filteredChannels.length === 0 ? (
              <div className="trust-empty-state">
                {channels.length === 0 ? 'Chưa có kênh YouTube nào. Hãy thêm kênh trong Channel Hub.' : 'Không tìm thấy kênh phù hợp.'}
              </div>
            ) : (
              filteredChannels.map((ch) => {
                const plan = plans.find(p => p.channel_db_id === ch.id)
                const isSelected = ch.id === selectedChannelId
                const score = plan?.trust_score_estimated || 0
                return (
                  <div
                    key={ch.id}
                    className={`trust-channel-card ${isSelected ? 'active' : ''}`}
                    onClick={() => handleSelectChannel(ch.id)}
                  >
                    <img
                      src={ch.thumbnail_url || '/logoNexus.png'}
                      alt={ch.title}
                      className="trust-channel-avatar"
                      onError={(e) => { e.target.src = '/logoNexus.png' }}
                    />
                    <div className="trust-channel-info">
                      <div className="trust-channel-name">{ch.title || 'Kênh không tên'}</div>
                      <div className="trust-channel-meta">
                        {ch.gpm_profile_name ? (
                          <span className="trust-gpm-tag">🌐 {ch.gpm_profile_name}</span>
                        ) : (
                          <span className="trust-gpm-missing">⚠️ Chưa gán GPM</span>
                        )}
                      </div>
                    </div>
                    <div className="trust-score-badge" style={{ borderColor: getScoreColor(score), color: getScoreColor(score) }}>
                      {score}
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right Panel: Control Center & Activity Log */}
        <div className="trust-main-panel">
          {!currentChannel ? (
            <div className="trust-empty-panel">
              <span className="trust-empty-icon">📺</span>
              <h3>Vui lòng chọn một kênh ở danh sách bên trái</h3>
            </div>
          ) : !selectedPlan ? (
            <div className="trust-empty-panel">
              <span className="trust-empty-icon">🌱</span>
              <h3>Kênh "{currentChannel.title}" chưa có Kế Hoạch Nuôi Kênh</h3>
              <p>Khởi tạo kế hoạch để bắt đầu tự động tìm kiếm, xem video đối thủ và tích lũy Trust Score.</p>
              <button
                type="button"
                className="trust-btn primary-btn"
                onClick={handleCreatePlan}
                disabled={actionBusy}
              >
                🚀 Tạo Kế Hoạch Nuôi Kênh Ngay
              </button>
            </div>
          ) : (
            <div className="trust-control-sections">
              {/* Trust Score & Phase Banner */}
              <div className="trust-card score-hero-card">
                <div className="score-hero-header">
                  <div className="score-gauge-wrap">
                    <div className="score-number" style={{ color: getScoreColor(trustScore) }}>
                      {trustScore}
                      <span className="score-denom">/100</span>
                    </div>
                    <div className="score-label">Internal Readiness Score</div>
                  </div>

                  <div className="score-status-group">
                    <div className="phase-badge-line">
                      <span className={`trust-phase-pill ${phaseInfo.badge}`}>{phaseInfo.label}</span>
                      <span className={`trust-status-pill status-${selectedPlan.status}`}>
                        {getStatusLabel(selectedPlan.status)}
                      </span>
                    </div>
                    <div className="trust-progress-bar-bg">
                      <div
                        className="trust-progress-bar-fill"
                        style={{
                          width: `${Math.max(5, trustScore)}%`,
                          background: `linear-gradient(90deg, #6366f1 0%, ${getScoreColor(trustScore)} 100%)`
                        }}
                      />
                    </div>
                    <div className="score-hint">
                      {trustScore >= 70 ? 'Readiness nội bộ đã đạt ngưỡng hoàn tất warm-up.' : 'Tiếp tục hoạt động đúng quota để nâng chỉ số readiness nội bộ.'}
                    </div>
                  </div>
                </div>

                {/* Primary Action Buttons */}
                <div className="trust-actions-toolbar">
                  {selectedPlan.status !== 'active' ? (
                    <button
                      type="button"
                      className="trust-btn start-btn"
                      onClick={() => handlePlanAction(selectedPlan.status === 'paused' ? 'resume' : 'start')}
                      disabled={actionBusy || selectedPlan.status === 'completed' || !profileIsReady}
                    >
                      ▶ {selectedPlan.status === 'completed' ? 'Warm-up đã hoàn tất' : selectedPlan.status === 'paused' ? 'Tiếp tục Nuôi Kênh' : 'Bắt đầu Nuôi Kênh'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="trust-btn pause-btn"
                      onClick={() => handlePlanAction('pause')}
                      disabled={actionBusy}
                    >
                      ⏸ Tạm dừng
                    </button>
                  )}

                  <button
                    type="button"
                    className="trust-btn run-now-btn"
                    onClick={() => handlePlanAction('run-session')}
                    disabled={actionBusy || Boolean(activeJob) || !profileIsReady || selectedPlan.status === 'completed'}
                    title="Chạy ngay 1 lượt Search -> Watch -> Like trên GPM Profile"
                  >
                    🔄 Chạy ngay 1 Session
                  </button>

                  {activeJob && (
                    <span className="trust-status-pill status-active">
                      Job: {activeJob.status} · {activeJob.progress || 'đang chuẩn bị'}
                    </span>
                  )}

                  <button
                    type="button"
                    className="trust-btn audit-btn"
                    onClick={handleAuditBranding}
                    disabled={actionBusy}
                    title="Mở Studio quét Avatar, Banner, Mô tả..."
                  >
                    🔍 Audit Branding
                  </button>

                  <button
                    type="button"
                    className="trust-btn delete-btn"
                    onClick={handleDeletePlan}
                    disabled={actionBusy}
                  >
                    🗑️ Xóa Plan
                  </button>
                </div>
              </div>

              {/* Grid: Branding Checklist & Niche Config */}
              <div className="trust-dual-grid">
                {/* Branding Checklist */}
                <div className="trust-card">
                  <div className="card-header-flex">
                    <h4>📋 Checklist Hoàn Thiện Kênh</h4>
                    <button
                      type="button"
                      className="text-action-btn"
                      onClick={handleVerifyFeatures}
                      disabled={actionBusy}
                    >
                      ⚡ Xác minh cấp tính năng
                    </button>
                  </div>
                  <div className="checklist-grid">
                    <div className={`checklist-item ${checklistStates.avatar.className}`}>
                      <span>{checklistStates.avatar.icon}</span> Avatar Kênh
                    </div>
                    <div className={`checklist-item ${checklistStates.banner.className}`}>
                      <span>{checklistStates.banner.icon}</span> Banner Kênh (Art)
                    </div>
                    <div className={`checklist-item ${checklistStates.about.className}`}>
                      <span>{checklistStates.about.icon}</span> Mô tả kênh (About)
                    </div>
                    <div className={`checklist-item ${checklistStates.handle.className}`}>
                      <span>{checklistStates.handle.icon}</span> Handle (@kenh)
                    </div>
                    <div className={`checklist-item ${checklistStates.contact_email.className}`}>
                      <span>{checklistStates.contact_email.icon}</span> Email liên hệ
                    </div>
                    <div className={`checklist-item ${checklistStates.country.className}`}>
                      <span>{checklistStates.country.icon}</span> Quốc gia cư trú
                    </div>
                    <div className={`checklist-item ${checklistStates.two_factor_auth.className}`}>
                      <span>{checklistStates.two_factor_auth.icon}</span> 2-Step Verification (2FA)
                    </div>
                    <div className={`checklist-item ${['intermediate', 'advanced'].includes(checklist.feature_level) ? 'checked' : 'unknown'}`}>
                      <span>{['intermediate', 'advanced'].includes(checklist.feature_level) ? '✅' : '❔'}</span> Cấp tính năng: <b>{checklist.feature_level || 'unknown'}</b>
                    </div>
                  </div>
                </div>

                {/* Daily Targets Sliders */}
                <div className="trust-card">
                  <h4>🎯 Chỉ Tiêu Tương Tác Mỗi Ngày</h4>
                  <div className="targets-slider-list">
                    <div className="slider-row">
                      <div className="slider-label">
                        <span>👁️ Xem video ngách:</span>
                        <b>{selectedPlan.daily_watch_target} video</b>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="15"
                        value={selectedPlan.daily_watch_target}
                        onChange={(e) => handleTargetDraftChange('daily_watch_target', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('daily_watch_target', e.currentTarget.value)}
                      />
                    </div>

                    <div className="slider-row">
                      <div className="slider-label">
                        <span>⏱️ Thời lượng xem tối thiểu:</span>
                        <b>{selectedPlan.min_watch_minutes ?? 10} phút / video</b>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="30"
                        value={selectedPlan.min_watch_minutes ?? 10}
                        onChange={(e) => handleTargetDraftChange('min_watch_minutes', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('min_watch_minutes', e.currentTarget.value)}
                      />
                      <small style={{ color: '#9ca3af', fontSize: '0.78rem', marginTop: '2px', display: 'block' }}>
                        * Nếu video ngắn hơn {selectedPlan.min_watch_minutes ?? 10} phút sẽ tự động xem trọn vẹn 100%.
                      </small>
                    </div>

                    <div className="slider-row">
                      <div className="slider-label">
                        <span>🔍 Tìm kiếm từ khóa:</span>
                        <b>{selectedPlan.daily_search_target} lần</b>
                      </div>
                      <input
                        type="range"
                        min="1"
                        max="10"
                        value={selectedPlan.daily_search_target}
                        onChange={(e) => handleTargetDraftChange('daily_search_target', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('daily_search_target', e.currentTarget.value)}
                      />
                    </div>

                    <div className="slider-row">
                      <div className="slider-label">
                        <span>👍 Thích video (Like):</span>
                        <b>{selectedPlan.daily_like_target} lượt</b>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="10"
                        value={selectedPlan.daily_like_target}
                        onChange={(e) => handleTargetDraftChange('daily_like_target', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('daily_like_target', e.currentTarget.value)}
                      />
                    </div>

                    <div className="slider-row">
                      <div className="slider-label">
                        <span>💬 Bình luận (Comment):</span>
                        <b>{selectedPlan.daily_comment_target} lượt</b>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="5"
                        value={selectedPlan.daily_comment_target}
                        onChange={(e) => handleTargetDraftChange('daily_comment_target', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('daily_comment_target', e.currentTarget.value)}
                      />
                    </div>

                    <div className="slider-row">
                      <div className="slider-label">
                        <span>🔔 Đăng ký kênh (Sub):</span>
                        <b>{selectedPlan.daily_subscribe_target} kênh</b>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="5"
                        value={selectedPlan.daily_subscribe_target}
                        onChange={(e) => handleTargetDraftChange('daily_subscribe_target', Number(e.target.value))}
                        onPointerUp={(e) => e.currentTarget.blur()}
                        onBlur={(e) => handleTargetCommit('daily_subscribe_target', e.currentTarget.value)}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Niche Keywords & Target Competitor Channels */}
              <div className="trust-card">
                <h4>🏷️ Cấu Hình Từ Khóa &amp; Kênh Đối Thủ</h4>
                <div className="niche-config-grid">
                  {/* Keywords */}
                  <div className="niche-input-col">
                    <label>Từ khóa ngách (Dùng để tìm kiếm trên YouTube):</label>
                    <div className="tag-input-wrapper">
                      <input
                        type="text"
                        placeholder="Nhập từ khóa rồi Enter..."
                        value={keywordInput}
                        onChange={(e) => setKeywordInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addKeyword())}
                      />
                      <button type="button" onClick={addKeyword} className="tag-add-btn">+</button>
                    </div>
                    <div className="tags-cloud">
                      {(selectedPlan.niche_keywords || []).map((kw, i) => (
                        <span key={i} className="niche-tag">
                          {kw}
                          <button type="button" onClick={() => removeKeyword(kw)}>×</button>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Competitor Channels */}
                  <div className="niche-input-col">
                    <label>Kênh đối thủ trong ngách (Để xem &amp; subscribe):</label>
                    <div className="tag-input-wrapper">
                      <input
                        type="text"
                        placeholder="Nhập @handle kênh rồi Enter..."
                        value={targetChannelInput}
                        onChange={(e) => setTargetChannelInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTargetChannel())}
                      />
                      <button type="button" onClick={addTargetChannel} className="tag-add-btn">+</button>
                    </div>
                    <div className="tags-cloud">
                      {(selectedPlan.target_channels || []).map((ch, i) => (
                        <span key={i} className="channel-tag">
                          {ch}
                          <button type="button" onClick={() => removeTargetChannel(ch)}>×</button>
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Real-time Activity Log */}
              <div className="trust-card">
                <div className="card-header-flex">
                  <h4>📜 Nhật Ký Hoạt Động Thời Gian Thực ({activities.length})</h4>
                  <button
                    type="button"
                    className="text-action-btn"
                    onClick={() => loadPlanDetails(selectedChannelId)}
                  >
                    🔄 Làm mới
                  </button>
                </div>

                <div className="activity-logs-list">
                  {activities.length === 0 ? (
                    <div className="activity-empty">Chưa có nhật ký hoạt động nào. Hãy bấm "Chạy ngay 1 Session" để bắt đầu.</div>
                  ) : (
                    activities.map((log) => {
                      const matchedChannel = log.detail_json?.matched_channel || ''
                      return (
                        <div key={log.id} className={`activity-log-row ${log.success ? 'success' : 'failed'}`}>
                          <div className="activity-type-icon">{getActivityIcon(log.activity_type)}</div>
                          <div className="activity-main-info">
                            <div className="activity-title">
                              <b>{log.activity_type.toUpperCase()}</b>: {log.target_title || log.detail_json?.keyword || log.target_url || 'Tác vụ hệ thống'}
                            </div>
                            {log.detail_json?.comment_text && (
                              <div className="activity-detail-comment">"{log.detail_json.comment_text}"</div>
                            )}
                            {log.error_message && (
                              <div className="activity-detail-error">Lỗi: {log.error_message}</div>
                            )}
                          </div>
                          {log.duration_seconds > 0 && (
                            <div className="activity-duration">⏱️ {formatSeconds(log.duration_seconds)}</div>
                          )}
                          {matchedChannel && (() => {
                            const isBlocked = blockedChannelsMap[matchedChannel] === 'blocked' ||
                              blacklistEntries.some(b => b.entry_type === 'channel' && (
                                b.entry_value?.toLowerCase() === matchedChannel.toLowerCase() ||
                                b.normalized_value?.toLowerCase() === matchedChannel.toLowerCase().replace(/[\s\W_]+/g, '')
                              ))
                            const isLoading = blockedChannelsMap[matchedChannel] === 'loading'
                            if (isBlocked) {
                              return (
                                <span
                                  className="activity-quick-block-btn blocked"
                                  title={`Kênh '${matchedChannel}' đã có trong Blacklist an toàn`}
                                >
                                  ✓ Đã chặn
                                </span>
                              )
                            }
                            return (
                              <button
                                type="button"
                                className={`activity-quick-block-btn ${isLoading ? 'loading' : ''}`}
                                disabled={isLoading}
                                title={`Chặn ngay kênh '${matchedChannel}' vào Blacklist an toàn`}
                                onClick={() => handleQuickBlockChannel(matchedChannel, `Chặn nhanh từ log: ${log.target_title || ''}`)}
                              >
                                {isLoading ? '⏳ Đang chặn...' : '🚫 Chặn kênh'}
                              </button>
                            )
                          })()}
                          <div className="activity-time">{log.executed_at ? log.executed_at.slice(11, 19) : ''}</div>
                        </div>
                      )
                    })
                  )}
                </div>
              </div>

              {/* Weekly Aggregated Stats Banner */}
              {dailyStats && (
                <div className="trust-stats-footer">
                  <div className="stat-pill">Hôm nay · Search: <b>{dailyStats.search_count}/{selectedPlan.daily_search_target}</b></div>
                  <div className="stat-pill">Watch: <b>{dailyStats.watch_count}/{selectedPlan.daily_watch_target}</b></div>
                  <div className="stat-pill">Like: <b>{dailyStats.like_count}/{selectedPlan.daily_like_target}</b></div>
                  <div className="stat-pill">Comment: <b>{dailyStats.comment_count}/{selectedPlan.daily_comment_target}</b></div>
                  <div className="stat-pill">Sub: <b>{dailyStats.subscribe_count}/{selectedPlan.daily_subscribe_target}</b></div>
                  <div className="stat-pill">Lần chạy kế tiếp: <b>{selectedPlan.next_run_at ? new Date(selectedPlan.next_run_at).toLocaleString() : 'đang chờ lịch'}</b></div>
                </div>
              )}

              {stats && (
                <div className="trust-stats-footer">
                  <div className="stat-pill">👁️ Đã xem: <b>{stats.watch_count} video ({formatSeconds(stats.total_watch_seconds)})</b></div>
                  <div className="stat-pill">🔍 Tìm kiếm: <b>{stats.search_count} lần</b></div>
                  <div className="stat-pill">👍 Likes: <b>{stats.like_count} lượt</b></div>
                  <div className="stat-pill">💬 Bình luận: <b>{stats.comment_count} lượt</b></div>
                  <div className="stat-pill">🔔 Subs: <b>{stats.subscribe_count} kênh</b></div>
                  <div className="stat-pill">📅 Hoạt động: <b>{stats.active_days} ngày</b></div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Safety Shield Settings Modal */}
      {safetyModalOpen && (
        <div className="safety-modal-overlay" onClick={() => setSafetyModalOpen(false)}>
          <div className="safety-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="safety-modal-header">
              <div className="safety-modal-title">
                <h3>🛡️ Cấu Hình Lá Chắn An Toàn Quốc Gia (VN Safety Shield)</h3>
                <p>Tự động ngăn chặn tương tác với các kênh phản động, chống phá và nội dung vi phạm pháp luật.</p>
              </div>
              <button type="button" className="safety-modal-close" onClick={() => setSafetyModalOpen(false)}>✕</button>
            </div>

            <div className="safety-modal-body">
              {/* Shield Status Toggle */}
              <div className="safety-toggle-section">
                <div className="safety-toggle-info">
                  <div className="safety-toggle-label">Trạng thái Lá chắn:</div>
                  <div className="safety-toggle-desc">
                    {safetyConfig?.shield_enabled
                      ? 'Lá chắn ĐANG BẬT: Mọi video và kênh thuộc danh mục đen sẽ bị từ chối 100%.'
                      : 'Lá chắn ĐANG TẮT: Bot sẽ không kiểm tra danh mục an toàn quốc gia.'}
                  </div>
                </div>
                <button
                  type="button"
                  className={`safety-toggle-btn ${safetyConfig?.shield_enabled ? 'enabled' : 'disabled'}`}
                  onClick={handleToggleShield}
                >
                  {safetyConfig?.shield_enabled ? '🟢 ĐANG BẬT' : '🔴 ĐÃ TẮT'}
                </button>
              </div>

              {/* Sync Config Card */}
              <div className="safety-sync-card">
                <div className="safety-sync-info">
                  <div><b>URL Đồng bộ từ xa:</b> <span className="safety-url-text">{safetyConfig?.remote_sync_url || 'Mặc định (GitHub Raw)'}</span></div>
                  <div><b>Đồng bộ lần cuối:</b> <span>{safetyConfig?.last_synced_at ? new Date(safetyConfig.last_synced_at).toLocaleString() : 'Chưa đồng bộ'}</span></div>
                </div>
                <button
                  type="button"
                  className="safety-sync-action-btn"
                  onClick={handleTriggerSync}
                  disabled={syncBusy}
                >
                  {syncBusy ? '🔄 Đang tải...' : '🔄 Đồng bộ Ngay'}
                </button>
              </div>

              {/* Add Custom Rule Form */}
              <form className="safety-add-rule-form" onSubmit={handleAddCustomRule}>
                <div className="form-group-type">
                  <select
                    value={newRuleType}
                    onChange={(e) => setNewRuleType(e.target.value)}
                    className="safety-select"
                  >
                    <option value="channel">Kênh / Handle (@...)</option>
                    <option value="keyword">Từ khóa cấm</option>
                    <option value="regex_pattern">Mẫu Regex</option>
                  </select>
                </div>
                <div className="form-group-val">
                  <input
                    type="text"
                    placeholder={newRuleType === 'channel' ? 'Nhập handle (vd: @viettan, Tên kênh...)' : 'Nhập từ khóa cần chặn...'}
                    value={newRuleValue}
                    onChange={(e) => setNewRuleValue(e.target.value)}
                    className="safety-input"
                    required
                  />
                </div>
                <div className="form-group-reason">
                  <input
                    type="text"
                    placeholder="Lý do chặn (tùy chọn)..."
                    value={newRuleReason}
                    onChange={(e) => setNewRuleReason(e.target.value)}
                    className="safety-input"
                  />
                </div>
                <button type="submit" className="safety-add-btn">+ Thêm Chặn</button>
              </form>

              {/* Rules List Filter & Table */}
              <div className="safety-rules-header">
                <h4>Danh Mục Quy Tắc Chặn ({blacklistEntries.length})</h4>
                <div className="safety-filter-row">
                  <select
                    value={blacklistTypeFilter}
                    onChange={(e) => {
                      setBlacklistTypeFilter(e.target.value)
                      loadBlacklistRules(e.target.value, blacklistSearch)
                    }}
                    className="safety-select-filter"
                  >
                    <option value="">Tất cả loại</option>
                    <option value="channel">Kênh ({safetyCounts?.channels || 0})</option>
                    <option value="keyword">Từ khóa ({safetyCounts?.keywords || 0})</option>
                    <option value="regex_pattern">Mẫu Regex ({safetyCounts?.regex_patterns || 0})</option>
                  </select>
                  <input
                    type="text"
                    placeholder="🔍 Tìm kiếm quy tắc..."
                    value={blacklistSearch}
                    onChange={(e) => {
                      setBlacklistSearch(e.target.value)
                      loadBlacklistRules(blacklistTypeFilter, e.target.value)
                    }}
                    className="safety-search-input"
                  />
                </div>
              </div>

              <div className="safety-rules-table-wrapper">
                {blacklistLoading ? (
                  <div className="safety-loading">Đang tải danh sách quy tắc...</div>
                ) : blacklistEntries.length === 0 ? (
                  <div className="safety-empty">Không tìm thấy quy tắc nào.</div>
                ) : (
                  <table className="safety-rules-table">
                    <thead>
                      <tr>
                        <th>Loại</th>
                        <th>Giá trị chặn</th>
                        <th>Lý do</th>
                        <th>Nguồn</th>
                        <th>Thao tác</th>
                      </tr>
                    </thead>
                    <tbody>
                      {blacklistEntries.map((entry) => (
                        <tr key={entry.id}>
                          <td>
                            <span className={`rule-type-badge ${entry.entry_type}`}>
                              {entry.entry_type === 'channel' ? 'Kênh' : entry.entry_type === 'keyword' ? 'Từ khóa' : 'Regex'}
                            </span>
                          </td>
                          <td className="rule-val-cell">{entry.entry_value}</td>
                          <td className="rule-reason-cell">{entry.reason || '—'}</td>
                          <td>
                            <span className={`rule-source-badge ${entry.is_custom ? 'custom' : 'core'}`}>
                              {entry.is_custom ? 'Thủ công' : 'Core'}
                            </span>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="rule-del-btn"
                              onClick={() => handleDeleteRule(entry.id)}
                              title="Xóa quy tắc chặn này"
                            >
                              🗑️
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
