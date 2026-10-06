import { useState, useEffect, useCallback, useMemo } from 'react'
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
  const [channels, setChannels] = useState([])
  const [plans, setPlans] = useState([])
  const [selectedChannelId, setSelectedChannelId] = useState(null)
  const [selectedPlan, setSelectedPlan] = useState(null)
  const [activities, setActivities] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [feedback, setFeedback] = useState({ message: '', type: '' })
  
  // Keyword & Channel inputs
  const [keywordInput, setKeywordInput] = useState('')
  const [targetChannelInput, setTargetChannelInput] = useState('')
  const [searchFilter, setSearchFilter] = useState('')

  // CSRF / Security fetch helper
  const apiFetch = useCallback(async (url, options = {}) => {
    return fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        ...(options.headers || {})
      }
    })
  }, [])

  const showFeedback = (msg, type = 'info') => {
    setFeedback({ message: msg, type })
    setTimeout(() => setFeedback({ message: '', type: '' }), 5000)
  }

  // Load channels and trust plans
  const loadInitialData = useCallback(async () => {
    setLoading(true)
    try {
      // 1. Fetch channels
      const chRes = await apiFetch(`${API_BASE}/api/youtube-comments/channels`)
      let chList = []
      if (chRes.ok) {
        const chData = await chRes.json()
        chList = Array.isArray(chData) ? chData : (chData.items || chData.channels || [])
      }

      // 2. Fetch trust plans
      const planRes = await apiFetch(`${API_BASE}/api/trust-builder/plans`)
      let planList = []
      if (planRes.ok) {
        const planData = await planRes.json()
        planList = Array.isArray(planData) ? planData : (planData.plans || planData.items || [])
      }

      setChannels(chList)
      setPlans(planList)

      if (chList.length > 0 && !selectedChannelId) {
        setSelectedChannelId(chList[0].id)
      }
    } catch (err) {
      console.error('Error loading trust data:', err)
      showFeedback('Không thể kết nối API Trust Builder.', 'error')
    } finally {
      setLoading(false)
    }
  }, [apiFetch, selectedChannelId])

  useEffect(() => {
    loadInitialData()
  }, [loadInitialData])

  // Load plan details & activities when selected channel changes
  const loadPlanDetails = useCallback(async (channelId) => {
    if (!channelId) {
      setSelectedPlan(null)
      setActivities([])
      setStats(null)
      return
    }

    const currentPlan = plans.find(p => p.channel_db_id === channelId)
    if (!currentPlan) {
      setSelectedPlan(null)
      setActivities([])
      setStats(null)
      return
    }

    try {
      // Detail & Stats
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${currentPlan.id}`)
      if (res.ok) {
        const data = await res.json()
        setSelectedPlan(data.plan)
        setStats(data.stats)
      }

      // Activities
      const actRes = await apiFetch(`${API_BASE}/api/trust-builder/plans/${currentPlan.id}/activities?limit=30`)
      if (actRes.ok) {
        const actData = await actRes.json()
        setActivities(actData.logs || [])
      }
    } catch (err) {
      console.error('Error loading plan details:', err)
    }
  }, [plans, apiFetch])

  useEffect(() => {
    loadPlanDetails(selectedChannelId)
  }, [selectedChannelId, loadPlanDetails])

  // Periodic polling for active plan activities (every 8s)
  useEffect(() => {
    if (!selectedPlan || selectedPlan.status !== 'active') return
    const interval = setInterval(() => {
      loadPlanDetails(selectedChannelId)
    }, 8000)
    return () => clearInterval(interval)
  }, [selectedPlan, selectedChannelId, loadPlanDetails])

  // Create Plan for selected channel
  const handleCreatePlan = async () => {
    if (!selectedChannelId) return
    setActionBusy(true)
    try {
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans`, {
        method: 'POST',
        body: JSON.stringify({
          channel_db_id: selectedChannelId,
          niche_keywords: ['công nghệ', 'xu hướng', 'kiến thức thú vị'],
          target_channels: [],
          daily_watch_target: 5,
          daily_search_target: 3,
          daily_like_target: 3,
          daily_comment_target: 1,
          daily_subscribe_target: 1,
          warmup_phase: 'phase_1_consumer'
        })
      })
      const data = await res.json()
      if (res.ok) {
        showFeedback('Đã khởi tạo Kế hoạch nuôi kênh thành công!', 'success')
        await loadInitialData()
      } else {
        showFeedback(data.detail || 'Không thể tạo Kế hoạch.', 'error')
      }
    } catch (err) {
      showFeedback('Lỗi kết nối khi tạo plan.', 'error')
    } finally {
      setActionBusy(false)
    }
  }

  // Update Plan config (Keywords, Targets, etc.)
  const handleUpdatePlan = async (changes) => {
    if (!selectedPlan) return
    try {
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes)
      })
      if (res.ok) {
        const data = await res.json()
        setSelectedPlan(data.plan)
        showFeedback('Đã lưu cấu hình nuôi kênh.', 'success')
      }
    } catch (err) {
      showFeedback('Lỗi khi lưu cấu hình.', 'error')
    }
  }

  // Plan lifecycle actions (Start, Pause, Resume, Run Now)
  const handlePlanAction = async (actionType) => {
    if (!selectedPlan) return
    setActionBusy(true)
    try {
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/${actionType}`, {
        method: 'POST'
      })
      const data = await res.json()
      if (res.ok) {
        if (actionType === 'run-session') {
          showFeedback('Đã kích hoạt 1 Session nuôi kênh trong nền! Hãy theo dõi Activity Log.', 'success')
        } else {
          showFeedback(`Đã cập nhật trạng thái: ${actionType.toUpperCase()}`, 'success')
        }
        await loadPlanDetails(selectedChannelId)
      } else {
        showFeedback(data.detail || data.message || 'Thao tác không thành công.', 'error')
      }
    } catch (err) {
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
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        showFeedback('Đã xóa Kế hoạch nuôi kênh.', 'success')
        setSelectedPlan(null)
        await loadInitialData()
      }
    } catch (err) {
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
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/branding-audit`)
      const data = await res.json()
      if (res.ok) {
        showFeedback(`Audit hoàn tất! Điểm Trust mới: ${data.trust_score}/100`, 'success')
        await loadPlanDetails(selectedChannelId)
      } else {
        showFeedback(data.detail || 'Không thể audit kênh.', 'error')
      }
    } catch (err) {
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
      const res = await apiFetch(`${API_BASE}/api/trust-builder/plans/${selectedPlan.id}/verify-features`, {
        method: 'POST'
      })
      const data = await res.json()
      if (res.ok) {
        showFeedback(`Đã xác minh tính năng! Điểm Trust: ${data.trust_score}/100`, 'success')
        await loadPlanDetails(selectedChannelId)
      }
    } catch (err) {
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

  return (
    <div className="trust-builder-container">
      {/* Header */}
      <div className="trust-header">
        <div className="trust-title-group">
          <h1 className="trust-main-title">🛡️ Channel Trust Builder</h1>
          <p className="trust-subtitle">
            Tự động nuôi kênh YouTube mới qua GPM-Login &amp; Playwright CDP để tăng độ uy tín trước khi xuất bản video.
          </p>
        </div>
        {feedback.message && (
          <div className={`trust-feedback-toast ${feedback.type}`}>
            {feedback.message}
          </div>
        )}
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
                    onClick={() => setSelectedChannelId(ch.id)}
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
                    <div className="score-label">Điểm Tín Nhiệm (Trust Score)</div>
                  </div>

                  <div className="score-status-group">
                    <div className="phase-badge-line">
                      <span className={`trust-phase-pill ${phaseInfo.badge}`}>{phaseInfo.label}</span>
                      <span className={`trust-status-pill status-${selectedPlan.status}`}>
                        {selectedPlan.status === 'active' ? '🟢 Đang chạy' : selectedPlan.status === 'paused' ? '🟡 Tạm dừng' : '⚪ Bản nháp'}
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
                      {trustScore >= 70 ? '🎉 Kênh đã đạt mức độ uy tín cao, sẵn sàng xuất bản video đều đặn!' : '💡 Tiếp tục duy trì xem video và tương tác để nâng điểm Trust lên ≥ 70.'}
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
                      disabled={actionBusy}
                    >
                      ▶ {selectedPlan.status === 'paused' ? 'Tiếp tục Nuôi Kênh' : 'Bắt đầu Nuôi Kênh'}
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
                    disabled={actionBusy}
                    title="Chạy ngay 1 lượt Search -> Watch -> Like trên GPM Profile"
                  >
                    🔄 Chạy ngay 1 Session
                  </button>

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
                    <div className={`checklist-item ${checklist.avatar ? 'checked' : ''}`}>
                      <span>{checklist.avatar ? '✅' : '❌'}</span> Avatar Kênh
                    </div>
                    <div className={`checklist-item ${checklist.banner ? 'checked' : ''}`}>
                      <span>{checklist.banner ? '✅' : '❌'}</span> Banner Kênh (Art)
                    </div>
                    <div className={`checklist-item ${checklist.about ? 'checked' : ''}`}>
                      <span>{checklist.about ? '✅' : '❌'}</span> Mô tả kênh (About)
                    </div>
                    <div className={`checklist-item ${checklist.handle ? 'checked' : ''}`}>
                      <span>{checklist.handle ? '✅' : '❌'}</span> Handle (@kenh)
                    </div>
                    <div className={`checklist-item ${checklist.contact_email ? 'checked' : ''}`}>
                      <span>{checklist.contact_email ? '✅' : '❌'}</span> Email liên hệ
                    </div>
                    <div className={`checklist-item ${checklist.country ? 'checked' : ''}`}>
                      <span>{checklist.country ? '✅' : '❌'}</span> Quốc gia cư trú
                    </div>
                    <div className="checklist-item checked">
                      <span>✅</span> 2-Step Verification (2FA)
                    </div>
                    <div className="checklist-item checked">
                      <span>⭐</span> Cấp tính năng: <b>{checklist.feature_level || 'Intermediate'}</b>
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
                        onChange={(e) => handleUpdatePlan({ daily_watch_target: Number(e.target.value) })}
                      />
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
                        onChange={(e) => handleUpdatePlan({ daily_search_target: Number(e.target.value) })}
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
                        onChange={(e) => handleUpdatePlan({ daily_like_target: Number(e.target.value) })}
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
                        onChange={(e) => handleUpdatePlan({ daily_comment_target: Number(e.target.value) })}
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
                        onChange={(e) => handleUpdatePlan({ daily_subscribe_target: Number(e.target.value) })}
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
                    activities.map((log) => (
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
                        <div className="activity-time">{log.executed_at ? log.executed_at.slice(11, 19) : ''}</div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Weekly Aggregated Stats Banner */}
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
    </div>
  )
}
