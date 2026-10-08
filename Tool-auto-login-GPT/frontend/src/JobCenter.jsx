import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { openVideoStudioInGpm } from './gpmOpener.js'

const API_BASE = 'http://127.0.0.1:8080'
const BULK_ACTIONS = ['retry', 'pause', 'resume', 'cancel', 'force_stop']
const BULK_ACTION_META = {
  retry: { label: 'Chạy lại', pastLabel: 'chạy lại' },
  pause: { label: 'Tạm dừng', pastLabel: 'tạm dừng' },
  resume: { label: 'Tiếp tục', pastLabel: 'tiếp tục' },
  cancel: { label: 'Dừng an toàn', pastLabel: 'dừng an toàn' },
  force_stop: { label: 'Dừng ngay', pastLabel: 'dừng ngay lập tức' }
}

const STATUS_META = {
  queued: { label: 'Đang chờ', color: '#f39c12' },
  running: { label: 'Đang chạy', color: '#2ecc71' },
  retry_wait: { label: 'Chờ tự phục hồi', color: '#4dd0e1' },
  paused: { label: 'Tạm dừng', color: '#f1c40f' },
  awaiting_review: { label: 'Chưa tự động kiểm tra', color: '#f5b041' },
  review_blocked: { label: 'Tự kiểm tra không đạt', color: '#ff6b6b' },
  done: { label: 'Hoàn thành', color: '#4dd0e1' },
  error: { label: 'Lỗi', color: '#ff5c5c' },
  canceled: { label: 'Đã hủy', color: '#999' }
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('vi-VN')
}

const PIPELINE_LABELS = {
  title: 'Tiêu đề',
  slug: 'Slug',
  description: 'Mô tả',
  tags: 'Tags',
  pinned_comment: 'Ghim',
  quiz: 'Quiz',
  metadata: 'Metadata',
  chapters: 'Chapter',
  thumbnail_with_text: 'Ảnh có chữ',
  thumbnail_without_text: 'Ảnh không chữ',
  audio: 'Audio',
  video_render: 'Dựng MP4',
  youtube_upload: 'Upload Private',
  youtube_schedule: 'Đặt lịch'
}

const PUBLISH_STAGE_LABELS = {
  preflight: 'Kiểm tra cấu hình',
  browser_preflight: 'Kiểm tra Profile trình duyệt',
  slot_reserved: 'Đã giữ lịch',
  session_created: 'Đã tạo phiên upload',
  uploading: 'Đang upload',
  uploaded: 'Đã upload Private',
  thumbnail_done: 'Đã gắn thumbnail',
  caption_done: 'Đã gắn phụ đề',
  processing: 'YouTube đang xử lý',
  uploaded_private: 'Đã upload Private',
  scheduled: 'Đã đặt lịch',
  public: 'Đã Public',
  completed: 'Hoàn tất',
  browser_launching: 'Đang mở GPM Browser',
  navigating_studio: 'Đang tải YouTube Studio',
  opening_upload_dialog: 'Đang mở hộp thoại Tải lên',
  filling_details: 'Đang điền thông tin video',
  uploading_thumbnail: 'Đang tải thumbnail',
  monetization_step: 'Đang cài đặt kiếm tiền',
  self_certification: 'Đang tự đánh giá quảng cáo',
  navigating_tabs: 'Đang duyệt các bước',
  visibility_and_publish: 'Đang đặt chế độ hiển thị & lịch',
  finishing_upload: 'Đang hoàn tất'
}

const CONFIGURATION_LABELS = {
  default_youtube_channel_id: 'kênh YouTube mặc định',
  youtube_oauth: 'OAuth YouTube',
  gpm_profile_id: 'GPM Profile',
  gpm_profile_exclusive: 'GPM Profile riêng không dùng chung',
  gpm_proxy_info: 'proxy riêng của GPM Profile',
  browser_cdp: 'kết nối tự động hóa CDP của Profile',
  publication_timezone: 'múi giờ đăng',
  publication_slots: 'khung giờ đăng',
  publication_daily_limit: 'giới hạn video mỗi ngày',
  publication_lead_minutes: 'khoảng an toàn trước giờ đăng',
  publication_paused: 'bỏ tạm dừng lịch đăng',
  public_upload_verified: 'xác minh quyền đặt lịch public',
  made_for_kids: 'lựa chọn dành cho trẻ em',
  metadata: 'metadata YouTube',
  final_mp4: 'file MP4',
  captions_srt: 'phụ đề SRT',
  thumbnail: 'thumbnail local',
  local_secret_store: 'kho bí mật DPAPI trên máy'
}

function formatPipeline(pipeline) {
  if (!pipeline) return ''
  const enabledSteps = Object.entries(PIPELINE_LABELS)
    .filter(([key]) => pipeline[key])
    .map(([, label]) => label)
  return enabledSteps.length ? enabledSteps.join(' → ') : 'Không có bước bổ sung'
}

function supportsAction(job, action) {
  const capability = action === 'cancel' ? 'can_cancel' : `can_${action}`
  return Boolean(job?.[capability])
}

function isSelectable(job) {
  return BULK_ACTIONS.some(action => supportsAction(job, action))
}

function isWithinSnapshot(job, snapshotAt) {
  return !snapshotAt || !job?.created_at || job.created_at <= snapshotAt
}

function JobCenter({ onOpenVideo, refreshKey }) {
  const [jobs, setJobs] = useState([])
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [counts, setCounts] = useState({ total: 0, active: 0, error: 0 })
  const [filteredTotal, setFilteredTotal] = useState(0)
  const [selectableTotal, setSelectableTotal] = useState(0)
  const [serverActionCounts, setServerActionCounts] = useState({
    retry: 0, pause: 0, resume: 0, cancel: 0, force_stop: 0
  })
  const [listSnapshotAt, setListSnapshotAt] = useState('')
  const [filter, setFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [sortOrder, setSortOrder] = useState('start_desc')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [bulkMessage, setBulkMessage] = useState('')
  const [actionId, setActionId] = useState('')
  const [selectedJobs, setSelectedJobs] = useState({})
  const [selectAllMatching, setSelectAllMatching] = useState(false)
  const [excludedJobs, setExcludedJobs] = useState({})
  const [selectionSnapshotAt, setSelectionSnapshotAt] = useState('')
  const [selectionSummary, setSelectionSummary] = useState(null)
  const requestIdRef = useRef(0)
  const selectAllCheckboxRef = useRef(null)

  const clearSelection = useCallback(() => {
    setSelectedJobs({})
    setSelectAllMatching(false)
    setExcludedJobs({})
    setSelectionSnapshotAt('')
    setSelectionSummary(null)
  }, [])

  const handleOpenStudio = async (job) => {
    if (!job?.video_id) return
    try {
      const data = await openVideoStudioInGpm(job.video_id)
      setMessage(data.message || '🚀 Đã mở YouTube Studio trong GPM Profile!')
    } catch (err) {
      setMessage(`⚠️ Không thể mở Studio trong GPM: ${err.message}`)
    }
  }

  useEffect(() => {
    const timeoutId = setTimeout(
      () => setDebouncedSearchQuery(searchQuery.trim()),
      250
    )
    return () => clearTimeout(timeoutId)
  }, [searchQuery])

  const loadJobs = useCallback(async () => {
    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    try {
      const offset = (page - 1) * pageSize
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset)
      })
      if (debouncedSearchQuery) params.set('search', debouncedSearchQuery)
      if (filter !== 'all') params.set('status', filter)
      if (typeFilter !== 'all') params.set('job_type', typeFilter)
      if (sortOrder) params.set('sort_by', sortOrder)
      const response = await fetch(`${API_BASE}/api/jobs?${params.toString()}`)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
      if (requestId !== requestIdRef.current) return
      const loadedJobs = Array.isArray(data.items) ? data.items : []
      setJobs(loadedJobs)
      setCounts(data.counts || { total: 0, active: 0, error: 0 })
      setFilteredTotal(Number(data.filtered_total) || 0)
      setSelectableTotal(Number(data.selectable_total) || 0)
      setServerActionCounts({
        retry: Number(data.action_counts?.retry) || 0,
        pause: Number(data.action_counts?.pause) || 0,
        resume: Number(data.action_counts?.resume) || 0,
        cancel: Number(data.action_counts?.cancel) || 0,
        force_stop: Number(data.action_counts?.force_stop) || 0
      })
      setListSnapshotAt(data.snapshot_at || '')
      const loadedById = new Map(loadedJobs.map(job => [job.id, job]))
      const loadedComplete = (Number(data.filtered_total) || 0) <= loadedJobs.length
      setSelectedJobs(previous => Object.fromEntries(
        Object.entries(previous)
          .filter(([jobId]) => loadedById.has(jobId) || !loadedComplete)
          .map(([jobId, job]) => [jobId, loadedById.get(jobId) || job])
          .filter(([, job]) => isSelectable(job))
      ))
      setExcludedJobs(previous => Object.fromEntries(
        Object.entries(previous)
          .filter(([jobId]) => (
            (loadedById.has(jobId) || !loadedComplete) &&
            (!loadedById.has(jobId) || isSelectable(loadedById.get(jobId)))
          ))
          .map(([jobId, job]) => [jobId, loadedById.get(jobId) || job])
      ))
      setError('')
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return
      setError(`Không thể tải danh sách job: ${loadError.message}`)
    } finally {
      if (requestId === requestIdRef.current) setLoading(false)
    }
  }, [debouncedSearchQuery, filter, page, pageSize, sortOrder, typeFilter])

  useEffect(() => {
    setLoading(true)
    loadJobs()
  }, [loadJobs, refreshKey])

  const hasPollableJobs = useMemo(
    () => counts.active > 0,
    [counts.active]
  )

  useEffect(() => {
    if (!hasPollableJobs) return undefined
    const intervalId = setInterval(loadJobs, 2500)
    return () => clearInterval(intervalId)
  }, [hasPollableJobs, loadJobs])

  const filteredJobs = jobs
  const totalPages = Math.max(1, Math.ceil((filteredTotal || 0) / pageSize))
  const startItem = filteredTotal === 0 ? 0 : (page - 1) * pageSize + 1
  const endItem = Math.min(page * pageSize, filteredTotal)

  const renderPagination = () => (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', padding: '12px 16px', background: '#161616', borderRadius: '8px', border: '1px solid #333' }}>
      <div style={{ color: '#aaa', fontSize: '0.86em' }}>
        {filteredTotal > 0 ? (
          <>Hiển thị <strong style={{ color: '#fff' }}>{startItem} - {endItem}</strong> trong tổng số <strong style={{ color: '#a970ff' }}>{filteredTotal}</strong> job</>
        ) : (
          'Không có job nào'
        )}
      </div>
      {totalPages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <button
            type="button"
            className="btn-secondary"
            disabled={page <= 1 || Boolean(actionId)}
            onClick={() => setPage(1)}
            style={{ padding: '6px 12px', fontSize: '0.85em', width: 'auto' }}
            title="Trang đầu"
          >
            «
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={page <= 1 || Boolean(actionId)}
            onClick={() => setPage(p => Math.max(1, p - 1))}
            style={{ padding: '6px 14px', fontSize: '0.85em', width: 'auto' }}
          >
            ‹ Trước
          </button>
          <span style={{ color: '#eee', fontWeight: '600', fontSize: '0.85em', padding: '0 8px' }}>
            Trang {page} / {totalPages}
          </span>
          <button
            type="button"
            className="btn-secondary"
            disabled={page >= totalPages || Boolean(actionId)}
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            style={{ padding: '6px 14px', fontSize: '0.85em', width: 'auto' }}
          >
            Sau ›
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={page >= totalPages || Boolean(actionId)}
            onClick={() => setPage(totalPages)}
            style={{ padding: '6px 12px', fontSize: '0.85em', width: 'auto' }}
            title="Trang cuối"
          >
            »
          </button>
        </div>
      )}
    </div>
  )

  const selectedJobItems = useMemo(
    () => Object.values(selectedJobs),
    [selectedJobs]
  )
  const excludedJobItems = useMemo(
    () => Object.values(excludedJobs),
    [excludedJobs]
  )
  const selectedCount = selectAllMatching
    ? Math.max(0, (selectionSummary?.selectableTotal || 0) - excludedJobItems.length)
    : selectedJobItems.length
  const displayedSelectableTotal = selectAllMatching
    ? selectionSummary?.selectableTotal || 0
    : selectableTotal
  const bulkActionCounts = useMemo(() => Object.fromEntries(
    BULK_ACTIONS.map(action => {
      const count = selectAllMatching
        ? Math.max(
            0,
            (selectionSummary?.actionCounts?.[action] || 0) - excludedJobItems.filter(
              job => supportsAction(job, action)
            ).length
          )
        : selectedJobItems.filter(job => supportsAction(job, action)).length
      return [action, count]
    })
  ), [excludedJobItems, selectAllMatching, selectedJobItems, selectionSummary])

  useEffect(() => {
    if (!selectAllCheckboxRef.current) return
    selectAllCheckboxRef.current.indeterminate = (
      (!selectAllMatching && selectedCount > 0) ||
      (selectAllMatching && selectedCount > 0 && selectedCount < displayedSelectableTotal)
    )
  }, [displayedSelectableTotal, selectAllMatching, selectedCount])

  const isJobSelected = job => (
    selectAllMatching
      ? isSelectable(job) &&
        isWithinSnapshot(job, selectionSnapshotAt) &&
        !excludedJobs[job.id]
      : Boolean(selectedJobs[job.id])
  )

  const toggleJobSelection = job => {
    if (
      !isSelectable(job) ||
      actionId ||
      (selectAllMatching && !isWithinSnapshot(job, selectionSnapshotAt))
    ) return
    setBulkMessage('')
    if (selectAllMatching) {
      setExcludedJobs(previous => {
        const next = { ...previous }
        if (next[job.id]) delete next[job.id]
        else next[job.id] = job
        return next
      })
      return
    }
    setSelectedJobs(previous => {
      const next = { ...previous }
      if (next[job.id]) delete next[job.id]
      else next[job.id] = job
      return next
    })
  }

  const toggleSelectAll = event => {
    if (actionId || !displayedSelectableTotal) return
    setBulkMessage('')
    if (!event.target.checked) {
      clearSelection()
      return
    }
    setSelectedJobs({})
    setExcludedJobs({})
    setSelectAllMatching(true)
    setSelectionSnapshotAt(listSnapshotAt)
    setSelectionSummary({
      selectableTotal,
      actionCounts: serverActionCounts
    })
  }

  const runBulkAction = async action => {
    const actionCount = bulkActionCounts[action] || 0
    if (!actionCount || actionId) return
    const meta = BULK_ACTION_META[action]
    if (!window.confirm(
      `${meta.label} ${actionCount} job phù hợp? ` +
      'Các job không tương thích hoặc đã đổi trạng thái sẽ được bỏ qua.'
    )) return

    setActionId(`bulk:${action}`)
    setError('')
    setBulkMessage('')
    try {
      const payload = selectAllMatching
        ? {
            action,
            mode: 'all_matching',
            excluded_job_ids: Object.keys(excludedJobs),
            status: filter,
            job_type: typeFilter,
            search: debouncedSearchQuery,
            snapshot_at: selectionSnapshotAt
          }
        : {
            action,
            mode: 'explicit',
            job_ids: Object.keys(selectedJobs)
          }
      const response = await fetch(`${API_BASE}/api/jobs/bulk-action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`)
      }
      const summary = [
        `Đã ${meta.pastLabel} ${data.succeeded || 0} job`,
        data.skipped ? `bỏ qua ${data.skipped}` : '',
        data.failed ? `thất bại ${data.failed}` : ''
      ].filter(Boolean).join(' · ')
      const firstFailure = data.failures?.[0]?.error
      setBulkMessage(`${data.failed ? '⚠️' : '✅'} ${summary}${
        firstFailure ? ` · ${firstFailure}` : ''
      }`)
      clearSelection()
      await loadJobs()
    } catch (actionError) {
      setError(actionError.message)
    } finally {
      setActionId('')
    }
  }

  const runAction = async (job, action) => {
    if (action === 'force_stop') {
      if (!window.confirm(`⚠️ BẠN CÓ CHẮC MUỐN DỪNG NGAY LẬP TỨC?\n\nJob: "${job.title || job.id}"\n\nHành động này sẽ ngắt toàn bộ tiến trình con (FFmpeg/ChatGPT worker/download) và hủy job ngay lập tức!`)) {
        return
      }
    }
    setActionId(`${job.id}:${action}`)
    setBulkMessage('')
    try {
      let endpoint = ''
      if (action === 'force_stop') {
        endpoint = `/api/jobs/${job.raw_id}/force-stop`
      } else if (job.type === 'youtube_download') {
        endpoint = `/api/youtube-download/jobs/${job.raw_id}/${action}`
      } else {
        endpoint = `/api/jobs/${job.raw_id}/${action}`
      }
      const response = await fetch(`${API_BASE}${endpoint}`, { method: 'POST' })
      const data = await response.json()
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`)
      }
      if (selectAllMatching) {
        setExcludedJobs(previous => ({ ...previous, [job.id]: job }))
      } else {
        setSelectedJobs(previous => {
          const next = { ...previous }
          delete next[job.id]
          return next
        })
      }
      setBulkMessage(`✅ Đã gửi yêu cầu ${BULK_ACTION_META[action]?.pastLabel || action} cho job "${job.title || job.id}"`)
      setError('')
      await loadJobs()
    } catch (actionError) {
      setError(`Lỗi ${BULK_ACTION_META[action]?.pastLabel || action} job: ${actionError.message}`)
    } finally {
      setActionId('')
    }
  }

  return (
    <>
      <h1 className="hero-title">Trung tâm Job</h1>
      <p className="hero-subtitle">
        Theo dõi hàng đợi tạo video, audio, ChatGPT và tải YouTube tại một nơi.
      </p>

      <div className="result-panel" style={{ padding: '13px 16px', marginTop: '24px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span aria-hidden="true" style={{ color: '#999' }}>⌕</span>
          <input
            aria-label="Tìm job theo video"
            type="search"
            value={searchQuery}
            disabled={Boolean(actionId)}
            onChange={event => {
              clearSelection()
              setSearchQuery(event.target.value)
              setPage(1)
            }}
            placeholder="Tìm theo tiêu đề, link YouTube, mã video hoặc mã job..."
            style={{
              flex: 1, minWidth: 0, border: 0, outline: 0,
              background: 'transparent', color: '#fff', fontSize: '0.98em'
            }}
          />
          {searchQuery && (
            <button
              type="button"
              aria-label="Xóa nội dung tìm kiếm"
              className="btn-secondary"
              disabled={Boolean(actionId)}
              onClick={() => {
                clearSelection()
                setSearchQuery('')
                setPage(1)
              }}
              style={{ width: 'auto', padding: '5px 10px' }}
            >
              ×
            </button>
          )}
        </div>
        <div style={{ color: '#777', fontSize: '0.78em', marginTop: '6px' }}>
          Tìm cả tiêu đề trước/sau khi tạo và từng video trong job tải kênh.
          {searchQuery.trim() && searchQuery.trim() === debouncedSearchQuery
            ? ` · Tìm thấy ${filteredTotal || 0} job`
            : searchQuery.trim() ? ' · Đang tìm...' : ''}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', margin: '14px 0 24px' }}>
        {[
          ['all', `Tất cả ${counts.total || 0}`],
          ['active', `Đang xử lý ${counts.active || 0}`],
          ['error', `Lỗi ${counts.error || 0}`]
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? 'btn-run' : 'btn-secondary'}
            disabled={Boolean(actionId)}
            onClick={() => {
              clearSelection()
              setFilter(value)
              setPage(1)
            }}
            style={{ padding: '9px 16px', width: 'auto' }}
          >
            {label}
          </button>
        ))}
        <select
          aria-label="Lọc loại job"
          value={typeFilter}
          disabled={Boolean(actionId)}
          onChange={event => {
            clearSelection()
            setTypeFilter(event.target.value)
            setPage(1)
          }}
          style={{
            padding: '9px 14px', borderRadius: '7px', border: '1px solid #555',
            background: '#191919', color: '#eee', fontWeight: '600'
          }}
        >
          <option value="all">Mọi loại job</option>
          <option value="video_generation">Tạo video</option>
          <option value="audio_review">Kiểm duyệt audio</option>
          <option value="audio">Tạo audio</option>
          <option value="visual_scene_plan">Lập kế hoạch cảnh</option>
          <option value="video_render">Dựng video MP4</option>
          <option value="youtube_publish">Upload / đặt lịch YouTube</option>
          <option value="youtube_download">Tải YouTube</option>
          <option value="chatgpt">Tác vụ ChatGPT</option>
          <option value="comment_sync">Đồng bộ bình luận</option>
          <option value="comment_draft">Soạn trả lời bình luận</option>
          <option value="comment_publish">Đăng trả lời bình luận</option>
          <option value="comment_video_import">Khởi tạo Chat cho video cũ</option>
          <option value="fb_crosspost">Đăng chéo Facebook</option>
          <option value="fb_crosspost_sync">Đồng bộ video Facebook</option>
          <option value="thumbnail_generation">Sinh ảnh Thumbnail</option>
          <option value="tiktok_publish">Đăng video TikTok</option>
          <option value="trust_builder_session">Trust Builder</option>
        </select>
        <select
          aria-label="Sắp xếp danh sách job"
          value={sortOrder}
          disabled={Boolean(actionId)}
          onChange={event => {
            clearSelection()
            setSortOrder(event.target.value)
            setPage(1)
          }}
          style={{
            padding: '9px 14px', borderRadius: '7px', border: '1px solid #555',
            background: '#191919', color: '#eee', fontWeight: '600'
          }}
        >
          <option value="start_desc">Bắt đầu: Mới nhất trước</option>
          <option value="start_asc">Bắt đầu: Cũ nhất trước</option>
          <option value="created_desc">Thời gian tạo: Mới nhất</option>
          <option value="created_asc">Thời gian tạo: Cũ nhất</option>
          <option value="updated_desc">Cập nhật: Mới nhất</option>
          <option value="updated_asc">Cập nhật: Cũ nhất</option>
          <option value="active_first">Đang xử lý lên đầu (Active first)</option>
        </select>
        <button
          className="btn-secondary"
          disabled={Boolean(actionId)}
          onClick={loadJobs}
          style={{ padding: '9px 16px', width: 'auto', marginLeft: 'auto' }}
        >
          ↻ Làm mới
        </button>
      </div>

      <div className="result-panel" style={{ padding: '12px 16px', marginBottom: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#ddd', cursor: displayedSelectableTotal ? 'pointer' : 'default' }}>
            <input
              ref={selectAllCheckboxRef}
              type="checkbox"
              checked={selectAllMatching && excludedJobItems.length === 0}
              disabled={!displayedSelectableTotal || Boolean(actionId)}
              onChange={toggleSelectAll}
            />
            Chọn tất cả job có thể thao tác ({displayedSelectableTotal})
          </label>
          {selectedCount > 0 && (
            <>
              <strong style={{ color: '#a970ff' }}>
                Đã chọn {selectedCount}{selectAllMatching ? ' trên toàn bộ kết quả' : ''}
              </strong>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginLeft: 'auto' }}>
                {BULK_ACTIONS.map(action => {
                  const actionCount = bulkActionCounts[action] || 0
                  const meta = BULK_ACTION_META[action]
                  return (
                    <button
                      key={action}
                      className={action === 'retry' ? 'btn-run' : 'btn-secondary'}
                      disabled={Boolean(actionId) || !actionCount}
                      onClick={() => runBulkAction(action)}
                      style={{
                        width: 'auto', padding: '8px 12px',
                        color: action === 'cancel' || action === 'force_stop' ? '#ff6b6b' : undefined,
                        background: action === 'force_stop' ? '#331515' : undefined,
                        borderColor: action === 'force_stop' ? '#ff4d4f' : undefined,
                        fontWeight: action === 'force_stop' ? '600' : undefined
                      }}
                    >
                      {action === 'force_stop' ? '⏹️ ' : ''}{meta.label} ({actionCount})
                    </button>
                  )
                })}
                <button
                  className="btn-secondary"
                  disabled={Boolean(actionId)}
                  onClick={clearSelection}
                  style={{ width: 'auto', padding: '8px 12px' }}
                >
                  Bỏ chọn
                </button>
              </div>
            </>
          )}
        </div>
        {selectAllMatching && (
          <div style={{ color: '#888', fontSize: '0.8em', marginTop: '7px' }}>
            Lựa chọn bao gồm cả job phù hợp chưa tải lên màn hình và không bao gồm job tạo sau thời điểm chọn.
          </div>
        )}
      </div>

      {error && <div style={{ color: '#ff6b6b', marginBottom: '16px' }}>{error}</div>}
      {bulkMessage && <div style={{ color: bulkMessage.startsWith('✅') ? '#4dd0e1' : '#f5b041', marginBottom: '16px' }}>{bulkMessage}</div>}
      {loading ? (
        <div className="result-panel">Đang tải danh sách job...</div>
      ) : filteredJobs.length === 0 ? (
        <div className="result-panel" style={{ color: '#888' }}>Không có job phù hợp.</div>
      ) : (
        <>
          {renderPagination()}
          <div style={{ display: 'grid', gap: '12px', marginTop: '12px', marginBottom: '12px' }}>
          {filteredJobs.map(job => {
            const status = (
              job.status === 'paused' && job.attention_required === 'chatgpt_verification'
                ? job.automatic_login === 'pending'
                  ? { label: 'Đang Auto Login', color: '#4dd0e1' }
                  : { label: 'Cần xác minh ChatGPT', color: '#f1c40f' }
                : job.status === 'paused' && ['youtube_browser_cdp', 'fb_browser_cdp'].includes(job.attention_required)
                  ? { label: 'Chờ Profile CDP', color: '#f1c40f' }
                : job.status === 'paused' && job.attention_required === 'fb_browser_review'
                  ? { label: 'Cần kiểm tra Facebook', color: '#f1c40f' }
                : job.status === 'paused' && job.attention_required?.startsWith('youtube_')
                  ? job.missing_configuration?.length > 0
                    ? { label: 'Cần cấu hình', color: '#f1c40f' }
                    : { label: 'Chờ tiếp tục', color: '#4dd0e1' }
                : job.type === 'comment_publish' && job.status === 'retry_wait'
                ? { label: 'Đã hẹn đăng', color: '#4dd0e1' }
                : STATUS_META[job.status] || STATUS_META.error
            )
            return (
              <div key={job.id} className="result-panel" style={{ padding: '18px 20px' }}>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <input
                    type="checkbox"
                    aria-label={`Chọn job ${job.title || job.id}`}
                    checked={isJobSelected(job)}
                    disabled={
                      !isSelectable(job) ||
                      Boolean(actionId) ||
                      (selectAllMatching && !isWithinSnapshot(job, selectionSnapshotAt))
                    }
                    onChange={() => toggleJobSelection(job)}
                    title={isSelectable(job) ? 'Chọn job để thao tác hàng loạt' : 'Job này không có thao tác khả dụng'}
                    style={{ marginTop: '3px' }}
                  />
                  <div style={{ flex: 1, minWidth: '260px' }}>
                    <div style={{ display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' }}>
                      <strong style={{ color: '#fff' }}>{job.type_label}</strong>
                      <span style={{ color: status.color, border: `1px solid ${status.color}`, borderRadius: '12px', padding: '2px 9px', fontSize: '0.78em' }}>
                        {status.label}
                      </span>
                      {job.upload_method && (
                        <span style={{
                          color: job.upload_method === 'browser' ? '#38bdf8' : '#f59e0b',
                          border: `1px solid ${job.upload_method === 'browser' ? 'rgba(56, 189, 248, 0.4)' : 'rgba(245, 158, 11, 0.4)'}`,
                          borderRadius: '12px',
                          padding: '2px 8px',
                          fontSize: '0.76em',
                          background: job.upload_method === 'browser' ? 'rgba(56, 189, 248, 0.1)' : 'rgba(245, 158, 11, 0.1)'
                        }}>
                          {job.upload_method === 'browser' ? '🌐 Trình duyệt GPM' : '🔌 YouTube Data API'}
                        </span>
                      )}
                      {job.queue_position && (
                        <span style={{ color: '#f39c12', fontSize: '0.82em' }}>
                          Vị trí #{job.queue_position}
                        </span>
                      )}
                    </div>
                    <div title={job.title} style={{ color: '#ddd', marginTop: '9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {job.title || job.id}
                    </div>
                    <div style={{ color: '#999', marginTop: '7px', fontSize: '0.86em' }}>
                      {job.progress || 'Chưa có cập nhật'}
                    </div>
                    {job.publish_stage && (
                      <div style={{ color: '#76d7c4', marginTop: '6px', fontSize: '0.8em' }}>
                        Bước YouTube: {PUBLISH_STAGE_LABELS[job.publish_stage] || job.publish_stage}
                        {Number.isFinite(job.upload_percent) ? ` · ${job.upload_percent}%` : ''}
                      </div>
                    )}
                    {job.type === 'video_generation' && job.pipeline && (
                      <div style={{ color: '#b794f6', marginTop: '6px', fontSize: '0.8em' }}>
                        Pipeline: Kịch bản lõi → {formatPipeline(job.pipeline)}
                      </div>
                    )}
                    {(job.type === 'audio' || job.type === 'video_generation') && job.voice_id && (
                      <div style={{ color: '#76d7c4', marginTop: '6px', fontSize: '0.8em' }}>
                        TTS: {job.tts_provider_id === 'omnivoice' ? 'OmniVoice' : 'Genmax'} · {job.voice_name || job.voice_id}
                        {job.voice_revision ? ` · revision ${job.voice_revision}` : ''}
                      </div>
                    )}
                    <div style={{ color: '#666', marginTop: '6px', fontSize: '0.78em' }}>
                      Tạo: {formatDate(job.created_at)}
                      {job.started_at && job.started_at !== job.created_at ? ` · Bắt đầu: ${formatDate(job.started_at)}` : ''}
                      {job.attempt ? ` · Lần chạy ${job.attempt}` : ''}
                    </div>
                    {job.processing_poll_count > 0 && job.publish_stage === 'processing' && (
                      <div style={{ color: '#4dd0e1', marginTop: '6px', fontSize: '0.8em' }}>
                        Đã kiểm tra processing {job.processing_poll_count} lần · timeout an toàn 24 giờ
                      </div>
                    )}
                    {job.recovery_count > 0 && !(job.processing_poll_count > 0 && job.publish_stage === 'processing') && (
                      <div style={{ color: '#4dd0e1', marginTop: '6px', fontSize: '0.8em' }}>
                        Tự phục hồi: {job.recovery_count}/{job.recovery_limit || 3}
                        {job.resume_from_step ? ` · Tiếp tục từ ${job.resume_from_step}` : ''}
                      </div>
                    )}
                    {job.status === 'retry_wait' && job.next_retry_at && (
                      <div style={{ color: '#8aa', marginTop: '4px', fontSize: '0.78em' }}>
                        {job.type === 'comment_publish' ? 'Dự kiến đăng lúc' : 'Tự chạy lại lúc'} {formatDate(job.next_retry_at)}
                      </div>
                    )}
                    {job.scheduled_at && (
                      <div style={{ color: '#4dd0e1', marginTop: '4px', fontSize: '0.78em' }}>
                        Lịch YouTube: {formatDate(job.scheduled_at)}
                      </div>
                    )}
                    {job.error && (
                      <details style={{ marginTop: '9px', color: '#ff6b6b', fontSize: '0.84em' }}>
                        <summary>Xem lỗi</summary>
                        <div style={{ marginTop: '6px', whiteSpace: 'pre-wrap' }}>{job.error}</div>
                      </details>
                    )}
                    {job.attention_required === 'chatgpt_verification' && (
                      <div style={{ color: '#f1c40f', marginTop: '9px', fontSize: '0.84em' }}>
                        {job.automatic_login === 'pending'
                          ? 'Phiên đã hết hạn. Hệ thống đang tự đăng nhập và sẽ tự tiếp tục đúng job này khi thành công.'
                          : ['failed', 'cooldown', 'interrupted'].includes(job.automatic_login)
                            ? 'Auto Login tự động không hoàn tất. Hãy kiểm tra tài khoản hoặc mở Open Profile để xác minh, sau đó bấm “Tiếp tục”.'
                            : 'ChatGPT đang yêu cầu CAPTCHA/Cloudflare hoặc xác minh thủ công. Mở Auto Login hoặc Open Profile, xác minh xong rồi bấm “Tiếp tục”.'}
                      </div>
                    )}
                    {job.status === 'paused' && job.attention_required?.startsWith('youtube_') && (
                      <div style={{ color: job.missing_configuration?.length > 0 ? '#f1c40f' : '#4dd0e1', marginTop: '9px', fontSize: '0.84em' }}>
                        {job.attention_required === 'youtube_browser_cdp' ? (
                          <div>
                            Profile chưa sẵn sàng cho tự động hóa CDP. Tool sẽ không đóng hoặc khởi động lại trình duyệt đang chạy. Nếu Profile được mở không có CDP, hãy đóng thủ công khi không còn thao tác rồi mở lại bằng Tool; job sẽ tự tiếp tục khi CDP sẵn sàng.
                          </div>
                        ) : job.missing_configuration?.length > 0 ? (
                          <>
                            {job.missing_configuration?.some(item => ['made_for_kids', 'default_youtube_channel_id'].includes(item))
                              ? 'Hãy sửa trong Cài đặt (Settings) hoặc Channel Hub rồi bấm “Tiếp tục”.'
                              : 'Hãy sửa trong Channel Hub rồi bấm “Tiếp tục”.'}
                            <div style={{ marginTop: '4px' }}>
                              Còn thiếu: {job.missing_configuration
                                .map(item => CONFIGURATION_LABELS[item] || item)
                                .join(', ')}.
                            </div>
                          </>
                        ) : (
                          <div>Video đã được tải lên YouTube. Hãy bấm “Tiếp tục” để tự động hoàn tất lưu và đặt lịch phát sóng.</div>
                        )}
                      </div>
                    )}
                    {job.status === 'paused' && job.attention_required?.startsWith('fb_browser_') && (
                      <div style={{ color: '#f1c40f', marginTop: '9px', fontSize: '0.84em' }}>
                        {job.attention_required === 'fb_browser_cdp'
                          ? 'Profile chưa sẵn sàng cho CDP. Tool giữ nguyên trình duyệt đang mở; khi Profile được đóng thủ công lúc rảnh và mở lại bằng Tool, job sẽ tự tiếp tục từ checkpoint an toàn.'
                          : 'Tác vụ đã đi qua bước kết nối CDP nên Tool giữ nguyên checkpoint để tránh đăng trùng. Hãy kiểm tra Facebook trước khi bấm “Tiếp tục”.'}
                      </div>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    {job.artifact_download_url && (
                      <a
                        className="btn-secondary"
                        style={{ padding: '8px 12px', textDecoration: 'none' }}
                        href={`${API_BASE}${job.artifact_download_url}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Mở / tải MP4
                      </a>
                    )}
                    {job.youtube_studio_url && (
                      <button
                        className="btn-secondary"
                        style={{ padding: '8px 12px' }}
                        onClick={() => handleOpenStudio(job)}
                        title="Mở YouTube Studio trong GPM Profile của kênh"
                      >
                        📺 YouTube Studio
                      </button>
                    )}
                    {job.video_id && (
                      <button className="btn-secondary" style={{ padding: '8px 12px' }} onClick={() => onOpenVideo(job.video_id)}>
                        Xem video
                      </button>
                    )}
                    {job.can_pause && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px' }} onClick={() => runAction(job, 'pause')}>
                        Tạm dừng
                      </button>
                    )}
                    {job.can_resume && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px' }} onClick={() => runAction(job, 'resume')}>
                        {['youtube_browser_cdp', 'fb_browser_cdp'].includes(job.attention_required) ? 'Kiểm tra lại' : 'Tiếp tục'}
                      </button>
                    )}
                    {job.can_cancel && !job.cancel_requested && (
                      <button className="btn-secondary" disabled={Boolean(actionId)} style={{ padding: '8px 12px', color: '#ff6b6b' }} onClick={() => runAction(job, job.type === 'youtube_download' ? 'stop' : 'cancel')}>
                        Dừng
                      </button>
                    )}
                    {job.can_force_stop && (
                      <button
                        className="btn-secondary"
                        disabled={Boolean(actionId)}
                        style={{
                          padding: '8px 12px',
                          background: job.cancel_requested ? '#b32424' : '#331515',
                          borderColor: '#ff4d4f',
                          color: '#fff',
                          fontWeight: '700',
                          boxShadow: job.cancel_requested ? '0 0 10px rgba(255, 77, 79, 0.6)' : 'none'
                        }}
                        title="Ngắt tiến trình và dừng khẩn cấp ngay lập tức"
                        onClick={() => runAction(job, 'force_stop')}
                      >
                        ⏹️ {job.cancel_requested ? 'DỪNG NGAY LẬP TỨC' : 'Dừng ngay'}
                      </button>
                    )}
                    {job.can_resume_checkpoint && (
                      <button
                        className="btn-run"
                        disabled={Boolean(actionId)}
                        style={{ padding: '8px 12px', width: 'auto', color: '#7ee787' }}
                        title={`Giữ nguyên phần đã hoàn tất và tiếp tục từ ${job.checkpoint_resume_step || 'checkpoint'}`}
                        onClick={() => runAction(job, 'resume-checkpoint')}
                      >
                        Tiếp tục từ checkpoint
                      </button>
                    )}
                    {job.can_retry && (
                      <button
                        className="btn-run"
                        disabled={Boolean(actionId)}
                        style={{ padding: '8px 12px', width: 'auto' }}
                        title="Xóa checkpoint và chạy lại từ đầu"
                        onClick={() => runAction(job, 'retry')}
                      >
                        Chạy lại
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
          </div>
          {renderPagination()}
        </>
      )}
    </>
  )
}

export default JobCenter
