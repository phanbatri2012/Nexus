import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { PIPELINE_STAGES, formatSeconds, resolveJobStage } from './videoProgressResolver.js'
import './VideoGenerationProgressCard.css'

const API_BASE = 'http://127.0.0.1:8080'
const ACTIVE_STATUSES = ['queued', 'running', 'retry_wait', 'paused']
const POLLING_INTERVAL_MS = 2000

export default function VideoGenerationProgressCard({
  refreshKey,
  onOpenJobCenter,
  onOpenVideo,
  activeJobId: propActiveJobId
}) {
  const [jobs, setJobs] = useState([])
  const [selectedJobId, setSelectedJobId] = useState('')
  const [actionId, setActionId] = useState('')
  const [actionError, setActionError] = useState('')
  const [recentlyCompletedVideo, setRecentlyCompletedVideo] = useState(null)
  const [now, setNow] = useState(Date.now())
  const prevRunningIdsRef = useRef(new Set())

  // Ticking timer for real-time elapsed seconds display
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const loadJobs = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/jobs?job_type=video_generation&limit=50`)
      const data = await response.json()
      if (response.ok && Array.isArray(data.items)) {
        setJobs(data.items)

        // Check if any previously running job just completed
        const currentDone = data.items.filter(j => j.status === 'done')
        for (const doneJob of currentDone) {
          if (prevRunningIdsRef.current.has(doneJob.id) && doneJob.video_id) {
            setRecentlyCompletedVideo({
              id: doneJob.id,
              videoId: doneJob.video_id,
              title: doneJob.title
            })
          }
        }

        const currentlyRunning = new Set(
          data.items
            .filter(j => ACTIVE_STATUSES.includes(j.status))
            .map(j => j.id)
        )
        prevRunningIdsRef.current = currentlyRunning
      }
    } catch {
      // Keep silent on network blips
    }
  }, [])

  useEffect(() => {
    loadJobs()
  }, [loadJobs, refreshKey])

  const activeJobs = useMemo(() => {
    return jobs
      .filter(job => ACTIVE_STATUSES.includes(job.status))
      .sort((a, b) => {
        if (a.status === 'running' && b.status !== 'running') return -1
        if (b.status === 'running' && a.status !== 'running') return 1
        return (a.queue_position || 0) - (b.queue_position || 0)
      })
  }, [jobs])

  // Polling while jobs are active
  useEffect(() => {
    if (activeJobs.length === 0) return undefined
    const interval = setInterval(loadJobs, POLLING_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [activeJobs.length, loadJobs])

  // Resolve currently inspected job
  const currentJob = useMemo(() => {
    if (propActiveJobId) {
      const matched = jobs.find(j => j.id === propActiveJobId || j.raw_id === propActiveJobId)
      if (matched) return matched
    }
    if (selectedJobId) {
      const matched = jobs.find(j => j.id === selectedJobId || j.raw_id === selectedJobId)
      if (matched) return matched
    }
    return activeJobs[0] || null
  }, [activeJobs, jobs, propActiveJobId, selectedJobId])

  // Auto switch when video is finished
  const handleViewCompleted = useCallback((vid) => {
    setRecentlyCompletedVideo(null)
    if (onOpenVideo && vid) {
      onOpenVideo(vid)
    }
  }, [onOpenVideo])

  const runJobAction = async (jobId, action) => {
    setActionId(`${jobId}:${action}`)
    setActionError('')
    try {
      const response = await fetch(`${API_BASE}/api/jobs/${jobId}/${action}`, {
        method: 'POST'
      })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || `Không thể thực hiện hành động ${action}.`)
      }
      await loadJobs()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionId('')
    }
  }

  // If nothing is running and no recent celebration, hide completely
  if (!currentJob && !recentlyCompletedVideo) {
    return null
  }

  // If we have a recently completed video banner
  if (!currentJob && recentlyCompletedVideo) {
    return (
      <div className="vg-progress-hud" style={{ border: '1px solid rgba(16, 185, 129, 0.4)' }}>
        <div className="vg-success-banner">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '1.2rem' }}>✨</span>
            <div>
              <div><strong>Tạo video hoàn tất!</strong> "{recentlyCompletedVideo.title}"</div>
              <div style={{ fontSize: '0.78rem', color: '#6ee7b7', marginTop: '2px' }}>
                Kịch bản và toàn bộ audio đã sẵn sàng.
              </div>
            </div>
          </div>
          <button
            type="button"
            className="vg-btn-action btn-view"
            onClick={() => handleViewCompleted(recentlyCompletedVideo.videoId)}
          >
            Mở xem ngay ➔
          </button>
        </div>
      </div>
    )
  }

  const stageInfo = resolveJobStage(currentJob)

  // Compute elapsed seconds
  const startTimestamp = currentJob?.started_at || currentJob?.created_at
  let elapsedSeconds = 0
  if (startTimestamp) {
    const startDate = new Date(startTimestamp)
    if (!Number.isNaN(startDate.getTime())) {
      elapsedSeconds = Math.max(0, Math.floor((now - startDate.getTime()) / 1000))
    }
  }

  // Estimated remaining time calculation
  const totalEstimatedSec = 160 // standard baseline
  const estimatedRemainingSec = Math.max(
    5,
    Math.round(totalEstimatedSec * (1 - stageInfo.overallPercent / 100))
  )

  const lineFillPercent = Math.max(
    0,
    Math.min(100, ((stageInfo.stageIndex) / (PIPELINE_STAGES.length - 1)) * 100)
  )

  return (
    <div className="vg-progress-hud" aria-label="Bảng theo dõi tiến độ tạo video trực quan">
      {/* Header Bar */}
      <div className="vg-hud-header">
        <div className="vg-hud-title-group">
          <span className={`vg-live-badge status-${currentJob.status}`}>
            <span className="vg-live-dot" />
            {currentJob.status === 'running'
              ? 'LIVE PRODUCTION'
              : currentJob.status === 'retry_wait'
              ? 'TỰ PHỤC HỒI'
              : currentJob.status === 'paused'
              ? 'TẠM DỪNG'
              : currentJob.status === 'done'
              ? 'HOÀN TẤT'
              : 'HÀNG ĐỢI'}
          </span>
          <div
            className="vg-video-title"
            title={currentJob.title || currentJob.video_url || 'Đang tạo video...'}
          >
            {currentJob.title || currentJob.video_url || 'Video đang tạo'}
          </div>
        </div>

        <div className="vg-hud-meta">
          <div className="vg-timer-item" title="Thời gian đã chạy">
            ⏱ Đã chạy: <strong>{formatSeconds(elapsedSeconds)}</strong>
          </div>
          {currentJob.status === 'running' && (
            <div className="vg-timer-item" title="Ước tính thời gian còn lại">
              ⏳ Dự kiến: <strong>~{formatSeconds(estimatedRemainingSec)}</strong>
            </div>
          )}
          {currentJob.voice_name && (
            <div className="vg-timer-item" title="Giọng đọc AI">
              🎙️ <span>{currentJob.voice_name}</span>
            </div>
          )}
        </div>
      </div>

      {/* Switcher if multiple active jobs exist */}
      {activeJobs.length > 1 && (
        <div className="vg-job-switcher">
          {activeJobs.map((job, idx) => {
            const isSelected = job.id === currentJob.id
            return (
              <button
                key={job.id}
                type="button"
                className={`vg-switcher-tab ${isSelected ? 'active' : ''}`}
                onClick={() => setSelectedJobId(job.id)}
              >
                #{idx + 1} {job.title ? job.title.slice(0, 22) + '...' : `Job ${job.id.slice(0, 6)}`}
              </button>
            )
          })}
        </div>
      )}

      {/* 5-Stage Stepper */}
      <div className="vg-stepper-container">
        <div className="vg-stepper">
          <div className="vg-stepper-line-bg" />
          <div className="vg-stepper-line-fill" style={{ width: `calc(${lineFillPercent}% * 0.85)` }} />

          {PIPELINE_STAGES.map((stage, idx) => {
            const isCompleted = idx < stageInfo.stageIndex || currentJob.status === 'done'
            const isActive = idx === stageInfo.stageIndex && currentJob.status !== 'done'
            const isError = currentJob.status === 'error' && isActive

            let statusClass = ''
            if (isError) statusClass = 'error'
            else if (isCompleted) statusClass = 'completed'
            else if (isActive) statusClass = 'active'

            return (
              <div key={stage.id} className={`vg-step-node ${statusClass}`}>
                <div className="vg-node-circle">
                  {isCompleted ? '✓' : stage.icon}
                </div>
                <div className="vg-node-label">{stage.label}</div>
                <div className="vg-node-sub">
                  {isCompleted
                    ? 'Hoàn thành'
                    : isActive
                    ? (currentJob.status === 'paused' ? 'Tạm dừng' : 'Đang chạy')
                    : 'Chờ'}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Overall Progress Bar */}
      <div className="vg-bar-wrapper">
        <div className="vg-bar-header">
          <div className="vg-bar-stage-text">
            <span>⚡ Chặng hiện tại:</span>
            <strong style={{ color: '#f8fafc' }}>{stageInfo.stageName}</strong>
          </div>
          <div className="vg-bar-percent">{stageInfo.overallPercent}%</div>
        </div>
        <div className="vg-progress-track">
          <div
            className="vg-progress-fill"
            style={{ width: `${stageInfo.overallPercent}%` }}
          />
        </div>
      </div>

      {/* Live Log Ticker */}
      <div className="vg-log-ticker" title={stageInfo.subMessage}>
        <span className="vg-ticker-icon">▶</span>
        <span className="vg-ticker-msg">{stageInfo.subMessage}</span>
      </div>

      {actionError && (
        <div style={{ color: '#ff6b6b', fontSize: '0.8rem', marginBottom: '10px' }}>
          ⚠️ {actionError}
        </div>
      )}

      {/* Controls Footer */}
      <div className="vg-hud-footer">
        <div className="vg-hud-actions">
          {currentJob.can_resume_checkpoint && (
            <button
              type="button"
              className="vg-btn-action btn-resume"
              disabled={Boolean(actionId)}
              onClick={() => runJobAction(currentJob.raw_id || currentJob.id, 'resume-checkpoint')}
            >
              ↻ Tiếp tục từ checkpoint
            </button>
          )}

          {currentJob.can_cancel && (
            <button
              type="button"
              className="vg-btn-action btn-cancel"
              disabled={Boolean(actionId)}
              onClick={() => runJobAction(currentJob.raw_id || currentJob.id, 'cancel')}
            >
              ⏹ Dừng an toàn
            </button>
          )}

          {currentJob.can_pause && (
            <button
              type="button"
              className="vg-btn-action"
              disabled={Boolean(actionId)}
              onClick={() => runJobAction(currentJob.raw_id || currentJob.id, 'pause')}
            >
              ⏸ Tạm dừng
            </button>
          )}

          {currentJob.can_resume && (
            <button
              type="button"
              className="vg-btn-action btn-resume"
              disabled={Boolean(actionId)}
              onClick={() => runJobAction(currentJob.raw_id || currentJob.id, 'resume')}
            >
              ▶ Tiếp tục
            </button>
          )}

          {currentJob.video_id && (
            <button
              type="button"
              className="vg-btn-action btn-view"
              onClick={() => {
                if (onOpenVideo) onOpenVideo(currentJob.video_id)
              }}
            >
              👁️ Xem bản nháp video
            </button>
          )}
        </div>

        <div>
          {onOpenJobCenter && (
            <button
              type="button"
              className="vg-btn-action"
              onClick={onOpenJobCenter}
            >
              Mở Trung tâm Job ➔
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
