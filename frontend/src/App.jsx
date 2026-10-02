import { useState, useEffect, useRef } from 'react'
import './App.css'
import BackgroundCanvas, { BACKGROUND_MODES } from './BackgroundCanvas'
import { useAppRouter } from './router.js'
import AutoLogin from './AutoLogin'
import GoogleFlowLogin from './GoogleFlowLogin'
import AudioReviewPanel from './AudioReviewPanel'
import JobCenter from './JobCenter'
import Settings from './Settings'
import ChannelManager from './ChannelManager'
import VideoQueuePanel from './VideoQueuePanel'
import VideoGenerationProgressCard from './VideoGenerationProgressCard'
import YouTubeDownloader from './YouTubeDownloader'
import YouTubeComments from './YouTubeComments'
import TTSSettings from './TTSSettings'
import CrossPoster from './CrossPoster'
import { openVideoStudioInGpm, openVideoWatchInGpm } from './gpmOpener'

const SECONDS_PER_MINUTE = 60
const SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE

function formatAudioDuration(durationSeconds) {
  const totalSeconds = Math.floor(durationSeconds)
  const hours = Math.floor(totalSeconds / SECONDS_PER_HOUR)
  const minutes = Math.floor((totalSeconds % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE)
  const seconds = totalSeconds % SECONDS_PER_MINUTE
  const paddedSeconds = String(seconds).padStart(2, '0')

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`
  }
  return `${minutes}:${paddedSeconds}`
}

function voiceProviderName(providerId) {
  if (providerId === 'omnivoice') return 'OmniVoice'
  if (providerId === 'genmax') return 'Genmax'
  return providerId || 'TTS'
}

function videoProductionStatus(video) {
  if (['public', 'published'].includes(video.publish_status) || video.publication_privacy_status === 'public') return { label: 'Đã đăng', color: '#4caf50' }
  if (video.publish_status === 'scheduled') return { label: 'Đã lên lịch', color: '#4dd0e1' }
  if (video.publish_status === 'uploaded_private') return { label: 'Private', color: '#b794f6' }
  if (video.publish_status === 'paused') return { label: 'Tạm dừng đăng', color: '#f1c40f' }
  if (video.publish_status === 'canceled') return { label: 'Đã hủy đăng', color: '#999' }
  if (video.publish_status === 'processing' || video.current_stage === 'processing') return { label: 'YouTube đang xử lý', color: '#4dd0e1' }
  if (['session_created', 'uploading', 'uploaded', 'thumbnail_done', 'caption_done'].includes(video.current_stage)) return { label: 'Đang upload', color: '#4dd0e1' }
  if (video.current_stage === 'slot_reserved') return { label: 'Đang đặt lịch', color: '#4dd0e1' }
  if (video.render_status === 'waiting_for_image_service') return { label: 'Chờ tạo ảnh', color: '#f5b041' }
  if (video.current_stage === 'scene_generation' && video.render_status === 'running') return { label: 'Đang tạo ảnh', color: '#b794f6' }
  if (video.render_status === 'running') return { label: 'Đang render', color: '#b794f6' }
  if (['done', 'completed'].includes(video.render_status) && !video.publish_status) return { label: 'MP4 đã hoàn thành', color: '#4dd0e1' }
  if (video.render_status === 'error' || video.publish_status === 'error') return { label: 'Lỗi pipeline', color: '#ff6b6b' }
  return null
}

function VoiceOptions({ voices }) {
  const groups = voices.reduce((result, voice) => {
    const providerId = voice.provider_id || 'genmax'
    if (!result[providerId]) result[providerId] = []
    result[providerId].push(voice)
    return result
  }, {})
  return Object.entries(groups).map(([providerId, providerVoices]) => (
    <optgroup key={providerId} label={voiceProviderName(providerId)}>
      {providerVoices.map(voice => (
        <option key={voice.id} value={voice.id}>
          [{voiceProviderName(providerId)}] {voice.name}
        </option>
      ))}
    </optgroup>
  ))
}

async function saveAudioDuration(videoId, durationSeconds) {
  const response = await fetch(
    `http://127.0.0.1:8080/api/videos/${videoId}/audio-duration`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ duration_seconds: durationSeconds })
    }
  )
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
}

function AudioDurationBadge({ videoId, audioUrl, savedDurationSeconds, audioReviewStatus }) {
  const [durationSeconds, setDurationSeconds] = useState(savedDurationSeconds || null)
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    setDurationSeconds(savedDurationSeconds || null)
    setLoadFailed(false)
    if (!audioUrl || savedDurationSeconds) return undefined

    const audio = new Audio()
    const handleLoadedMetadata = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDurationSeconds(audio.duration)
        saveAudioDuration(videoId, audio.duration)
          .catch(error => console.error('Failed to save audio duration', error))
      } else {
        setLoadFailed(true)
      }
    }
    const handleError = () => setLoadFailed(true)

    audio.preload = 'metadata'
    audio.addEventListener('loadedmetadata', handleLoadedMetadata)
    audio.addEventListener('error', handleError)
    audio.src = audioUrl

    return () => {
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata)
      audio.removeEventListener('error', handleError)
      audio.removeAttribute('src')
      audio.load()
    }
  }, [videoId, audioUrl, savedDurationSeconds])

  let label = 'Chưa có audio'
  if (audioUrl) {
    label = durationSeconds !== null
      ? formatAudioDuration(durationSeconds)
      : loadFailed ? 'Audio không khả dụng' : 'Đang tải...'
  } else if (audioReviewStatus === 'pending') {
    label = 'Chưa tự động kiểm tra'
  } else if (audioReviewStatus === 'blocked') {
    label = 'Kịch bản cần sửa'
  }

  return (
    <span
      title={audioUrl || 'Video chưa có audio'}
      style={{
        color: durationSeconds !== null ? '#4dd0e1' : '#888',
        fontSize: '0.82em',
        fontWeight: '600',
        whiteSpace: 'nowrap'
      }}
    >
      🎵 {label}
    </span>
  )
}

function App() {
  const router = useAppRouter()
  const activeView = router.activeView
  const setActiveView = (view, subPath = '') => router.navigate(view, subPath)
  const [bgMode, setBgMode] = useState(() => {
    try {
      return localStorage.getItem('nexus_bg_mode') || 'particles'
    } catch {
      return 'particles'
    }
  })

  const handleBgModeChange = (newMode) => {
    setBgMode(newMode)
    try {
      localStorage.setItem('nexus_bg_mode', newMode)
    } catch {}
  }

  const [url, setUrl] = useState('')
  const [isFetching, setIsFetching] = useState(false)
  const [showResult, setShowResult] = useState(false)
  const [resultText, setResultText] = useState('')
  const [fullTranscript, setFullTranscript] = useState('')
  const [chatUrl, setChatUrl] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [activeTab, setActiveTab] = useState('summary')
  const [savedVideos, setSavedVideos] = useState({ items: [], total: 0 })
  const [currentPage, setCurrentPage] = useState(1)
  const [generatingThumbnailType, setGeneratingThumbnailType] = useState(null)
  const [isGeneratingChapters, setIsGeneratingChapters] = useState(false)
  const [isGeneratingMetadata, setIsGeneratingMetadata] = useState(false)
  const [isGeneratingTitle, setIsGeneratingTitle] = useState(false)
  const [isGeneratingSlug, setIsGeneratingSlug] = useState(false)
  const [isGeneratingDescription, setIsGeneratingDescription] = useState(false)
  const [isGeneratingTags, setIsGeneratingTags] = useState(false)
  const [isGeneratingPinnedComment, setIsGeneratingPinnedComment] = useState(false)
  const [isGeneratingQuiz, setIsGeneratingQuiz] = useState(false)
  const [previewDescription, setPreviewDescription] = useState('')
  const [isLoadingPreviewDescription, setIsLoadingPreviewDescription] = useState(false)
  const [showPreviewModal, setShowPreviewModal] = useState(false)
  const [copiedPreview, setCopiedPreview] = useState(false)
  const [isGenAudio, setIsGenAudio] = useState(false)
  const [isCancelingAudio, setIsCancelingAudio] = useState(false)
  const [audioStatus, setAudioStatus] = useState('not_started')
  const [audioMissingSegments, setAudioMissingSegments] = useState(0)
  const [audioRoleStreams, setAudioRoleStreams] = useState(null)
  const [renderInfo, setRenderInfo] = useState(null)
  const [isRendering, setIsRendering] = useState(false)
  const [isCancelingRender, setIsCancelingRender] = useState(false)
  const [progressMsg, setProgressMsg] = useState('')
  const [queueRefreshKey, setQueueRefreshKey] = useState(0)
  const [currentVideoId, setCurrentVideoId] = useState(null)
  const currentVideoIdRef = useRef(null)

  // Auto-load video on deep link / direct F5
  useEffect(() => {
    if (router.activeView === 'fetcher' && router.subPath) {
      const videoId = router.subPath.split('/')[0];
      if (videoId && (!currentVideoIdRef.current || String(currentVideoIdRef.current) !== String(videoId))) {
        viewSavedVideo(videoId);
      }
    } else if (router.activeView === 'dashboard' && router.subPath.startsWith('video/')) {
      const videoId = router.subPath.replace(/^video\//, '').split('/')[0];
      if (videoId && (!currentVideoIdRef.current || String(currentVideoIdRef.current) !== String(videoId))) {
        viewSavedVideo(videoId);
      }
    }
  }, [router.activeView, router.subPath]);
  const [videoTitle, setVideoTitle] = useState('')
  const [currentVideoPromptVersion, setCurrentVideoPromptVersion] = useState('')
  const [currentVideoStatus, setCurrentVideoStatus] = useState('active')
  const [isCurrentVideoPublished, setIsCurrentVideoPublished] = useState(false)
  const [currentVideoPublications, setCurrentVideoPublications] = useState([])
  const [currentVideoDefaultChannelTitle, setCurrentVideoDefaultChannelTitle] = useState('')
  const [publicationDialog, setPublicationDialog] = useState(null)
  const [sceneResetDialog, setSceneResetDialog] = useState(null)
  const [currentVideoHasCheckpoint, setCurrentVideoHasCheckpoint] = useState(false)
  const [isPublishingNow, setIsPublishingNow] = useState(false)
  const [publishImmediately, setPublishImmediately] = useState(false)
  const [chatGptStatus, setChatGptStatus] = useState({
    busy: false,
    operation: '',
    promptVersion: ''
  })
  
  const [promptVersions, setPromptVersions] = useState([])
  const [selectedPromptVersion, setSelectedPromptVersion] = useState('default')
  const [voiceOptions, setVoiceOptions] = useState([])
  const [globalDefaultVoiceId, setGlobalDefaultVoiceId] = useState('')
  const [selectedVoiceId, setSelectedVoiceId] = useState('')
  const [selectedMcVoiceId, setSelectedMcVoiceId] = useState('')
  const [selectedGuest1VoiceId, setSelectedGuest1VoiceId] = useState('')
  const [selectedGuest2VoiceId, setSelectedGuest2VoiceId] = useState('auto')
  const [currentVideoVoiceId, setCurrentVideoVoiceId] = useState('')
  const [currentVideoVoiceName, setCurrentVideoVoiceName] = useState('')
  const [currentVideoProviderId, setCurrentVideoProviderId] = useState('genmax')
  const [audioTaskVoiceName, setAudioTaskVoiceName] = useState('')
  const [audioTaskProviderId, setAudioTaskProviderId] = useState('genmax')
  const [audioReview, setAudioReview] = useState(null)
  const [isLoadingAudioReview, setIsLoadingAudioReview] = useState(false)
  const [regenerateVoiceId, setRegenerateVoiceId] = useState('')
  const [publishFilter, setPublishFilter] = useState('unpublished') // 'all' | 'published' | 'unpublished' | 'error'
  const [promptVersionFilter, setPromptVersionFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const dashboardFetchRequestRef = useRef(0)
  const lastAppliedPromptVersionRef = useRef('')
  
  const PAGE_SIZE = 10
  const chatGptProfileBusy =
    isFetching ||
    chatGptStatus.busy ||
    generatingThumbnailType !== null ||
    isGeneratingChapters ||
    isGeneratingMetadata ||
    isGeneratingTitle ||
    isGeneratingSlug ||
    isGeneratingDescription ||
    isGeneratingTags ||
    isGeneratingPinnedComment ||
    isGeneratingQuiz
  const currentVideoIsError = currentVideoStatus === 'error'
  const chatGptControlsDisabled = chatGptProfileBusy || currentVideoIsError
  const getPromptVersionName = (versionKey) =>
    promptVersions.find(version => version.key === versionKey)?.name ||
    versionKey ||
    'Không xác định'
  const getVoiceName = (voiceId, savedName = '') =>
    savedName ||
    voiceOptions.find(voice => voice.id === voiceId)?.name ||
    'Không xác định'

  const fetchSavedVideos = async (
    page = currentPage,
    filter = publishFilter,
    versionFilter = promptVersionFilter,
    search = debouncedSearchQuery
  ) => {
    const requestId = dashboardFetchRequestRef.current + 1
    dashboardFetchRequestRef.current = requestId
    try {
      const offset = (page - 1) * PAGE_SIZE;
      const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
      if (filter === 'published') params.set('is_published', '1');
      else if (filter === 'unpublished') params.set('is_published', '0');
      if (filter === 'published' || filter === 'unpublished') params.set('video_status', 'active');
      else if (filter === 'error') params.set('video_status', 'error');
      if (versionFilter !== 'all') params.set('prompt_version', versionFilter);
      if (search) params.set('search', search);
      const response = await fetch(`http://127.0.0.1:8080/api/videos?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (dashboardFetchRequestRef.current === requestId) {
        setSavedVideos(data);
      }
    } catch (err) {
      console.error("Failed to fetch videos", err);
    }
  };

  const handlePageChange = (newPage) => {
    setCurrentPage(newPage);
  }

  const setVideoLifecycleState = async ({
    videoId,
    videoTitle,
    videoStatus,
    isPublished,
    hasPublishedUrl,
    defaultChannelTitle,
    nextState
  }) => {
    if (!videoId) return;
    if (
      nextState === 'error' &&
      !confirm('Đánh dấu video là Lỗi? Các chức năng tự động và hàng đợi sẽ bỏ qua video này.')
    ) return;
    if (nextState === 'unpublished' && hasPublishedUrl) {
      alert('Hãy xóa link video đã đăng trước khi chuyển về Chưa đăng.');
      return;
    }
    if (nextState === 'published' && !hasPublishedUrl) {
      if (videoStatus === 'error') {
        alert('Hãy chuyển video về Chưa đăng trước khi gắn link đã đăng.');
        return;
      }
      setPublicationDialog({
        videoId,
        videoTitle: videoTitle || 'Video chưa có tiêu đề',
        defaultChannelTitle: defaultChannelTitle || '',
        publishedUrl: '',
        isSaving: false,
        error: ''
      });
      return;
    }

    try {
      let resolvedStatus = videoStatus;
      let resolvedPublished = Boolean(isPublished);
      if (nextState === 'error') {
        const response = await fetch(
          `http://127.0.0.1:8080/api/videos/${videoId}/status`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'error' })
          }
        );
        const data = await response.json();
        if (!response.ok || data.success === false) {
          throw new Error(data.detail || data.error || `HTTP ${response.status}`);
        }
        resolvedStatus = 'error';
      } else {
        if (videoStatus === 'error') {
          const response = await fetch(
            `http://127.0.0.1:8080/api/videos/${videoId}/status`,
            {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ status: 'active' })
            }
          );
          const data = await response.json();
          if (!response.ok || data.success === false) {
            throw new Error(data.detail || data.error || `HTTP ${response.status}`);
          }
          resolvedStatus = 'active';
        }
        const targetPublished = nextState === 'published';
        if (targetPublished !== resolvedPublished) {
          const response = await fetch(
            `http://127.0.0.1:8080/api/videos/${videoId}/publish?is_published=${targetPublished ? 1 : 0}`,
            { method: 'PUT' }
          );
          const data = await response.json();
          if (!response.ok || data.success === false) {
            throw new Error(data.detail || data.error || `HTTP ${response.status}`);
          }
          resolvedPublished = targetPublished;
        }
      }
      if (videoId === currentVideoId) {
        setCurrentVideoStatus(resolvedStatus);
        setIsCurrentVideoPublished(resolvedPublished);
      }
      setQueueRefreshKey(key => key + 1);
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert(error.message || 'Không thể cập nhật trạng thái video.');
      await fetchSavedVideos(currentPage, publishFilter);
    }
  }

  const submitVideoPublication = async (event) => {
    event.preventDefault();
    if (!publicationDialog || publicationDialog.isSaving) return;
    const publishedUrl = publicationDialog.publishedUrl.trim();
    if (!publishedUrl) {
      setPublicationDialog(dialog => ({ ...dialog, error: 'Hãy nhập link video đã đăng.' }));
      return;
    }

    setPublicationDialog(dialog => ({ ...dialog, isSaving: true, error: '' }));
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${publicationDialog.videoId}/publications`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ published_url: publishedUrl })
        }
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      }

      if (publicationDialog.videoId === currentVideoId) {
        setCurrentVideoStatus('active');
        setIsCurrentVideoPublished(true);
        setCurrentVideoPublications(publications => {
          const remaining = publications.filter(item => item.id !== data.id);
          return [{
            ...data,
            channel_title: data.channel_title || publicationDialog.defaultChannelTitle || ''
          }, ...remaining];
        });
      }
      setPublicationDialog(null);
      setQueueRefreshKey(key => key + 1);
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      setPublicationDialog(dialog => dialog ? ({
        ...dialog,
        isSaving: false,
        error: error.message || 'Không thể lưu link video đã đăng.'
      }) : dialog);
    }
  }

  useEffect(() => {
    fetchPromptVersions(); // Load mapping unconditionally for Dashboard
    fetchVoices();
  }, [])

  useEffect(() => {
    if (activeView === 'fetcher') {
      // Settings may have changed the prompt-specific default voice.
      fetchPromptVersions()
      fetchVoices()
    }
  }, [activeView])

  useEffect(() => {
    if (!promptVersions.length || !voiceOptions.length) return
    const selectedVersion = promptVersions.find(
      version => version.key === selectedPromptVersion
    )
    const promptVoiceId = selectedVersion?.defaultVoiceId || ''
    const promptVoiceExists = voiceOptions.some(
      voice => voice.id === promptVoiceId
    )
    const globalVoiceExists = voiceOptions.some(
      voice => voice.id === globalDefaultVoiceId
    )
    const resolvedDefaultVoice = promptVoiceExists
      ? promptVoiceId
      : globalVoiceExists
        ? globalDefaultVoiceId
        : voiceOptions[0].id

    const isPromptSwitched = lastAppliedPromptVersionRef.current !== selectedPromptVersion
    if (isPromptSwitched || !selectedMcVoiceId) {
      setSelectedVoiceId(resolvedDefaultVoice)
      const cast = selectedVersion?.castSettings || {}
      const mcVoiceId = cast.mc?.voice_id || cast.mc?.default_voice_id || resolvedDefaultVoice
      const guest1VoiceId = cast.guest_1?.voice_id || cast.guest_1?.default_voice_id || (voiceOptions[1]?.id || resolvedDefaultVoice)
      const guest2VoiceId = cast.guest_2?.voice_id || cast.guest_2?.default_voice_id || 'auto'
      setSelectedMcVoiceId(mcVoiceId)
      setSelectedGuest1VoiceId(guest1VoiceId)
      setSelectedGuest2VoiceId(guest2VoiceId)
      lastAppliedPromptVersionRef.current = selectedPromptVersion
    }
  }, [
    selectedPromptVersion,
    promptVersions,
    voiceOptions,
    globalDefaultVoiceId
  ])

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery.trim())
      setCurrentPage(1)
    }, 300)
    return () => clearTimeout(timeoutId)
  }, [searchQuery])

  useEffect(() => {
    if (activeView === 'dashboard') {
      fetchSavedVideos(
        currentPage,
        publishFilter,
        promptVersionFilter,
        debouncedSearchQuery
      )
    }
  }, [
    activeView,
    currentPage,
    publishFilter,
    promptVersionFilter,
    debouncedSearchQuery
  ])

  useEffect(() => {
    currentVideoIdRef.current = currentVideoId
  }, [currentVideoId])

  useEffect(() => {
    let stopped = false
    const syncChatGptStatus = async () => {
      try {
        const response = await fetch('http://127.0.0.1:8080/api/chatgpt-status')
        const data = await response.json()
        if (!stopped) {
          setChatGptStatus({
            busy: Boolean(data.busy),
            operation: data.operation || '',
            promptVersion: data.prompt_version || ''
          })
        }
      } catch {
        // Keep the last known state during transient backend errors.
      }
    }

    syncChatGptStatus()
    const intervalId = setInterval(syncChatGptStatus, 2000)
    return () => {
      stopped = true
      clearInterval(intervalId)
    }
  }, [])

  useEffect(() => {
    if (!currentVideoId) return;

    let stopped = false;
    let intervalId = null;
    const syncAudio = async () => {
      try {
        const response = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}/audio-status`
        );
        const data = await response.json();
        if (stopped || !data.success) return;

        const status = data.audio_task?.status || 'not_started';
        setAudioStatus(status);
        setAudioMissingSegments(data.audio_task?.missing_segments || 0);
        setAudioRoleStreams(data.audio_task?.role_streams || null);
        setAudioTaskVoiceName(data.audio_task?.voice_name || '');
        setAudioTaskProviderId(data.audio_task?.tts_provider_id || 'genmax');
        setIsGenAudio(status === 'pending' || status === 'processing');

        if (status === 'completed') {
          const videoResponse = await fetch(
            `http://127.0.0.1:8080/api/videos/${currentVideoId}`
          );
          const video = await videoResponse.json();
          if (!stopped) {
            setResultText(video.generated_script);
            setCurrentVideoVoiceId(video.voice_id || '');
            setCurrentVideoVoiceName(video.voice_name || '');
            setCurrentVideoProviderId(video.tts_provider_id || 'genmax');
            setRegenerateVoiceId(previousVoiceId =>
              video.voice_id || previousVoiceId
            );
          }
          if (intervalId) clearInterval(intervalId);
        } else if ((status === 'failed' || status === 'interrupted') && intervalId) {
          clearInterval(intervalId);
        }
      } catch (error) {
        console.error('Failed to sync audio status', error);
      }
    };

    syncAudio();
    intervalId = setInterval(syncAudio, 5000);
    return () => {
      stopped = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [currentVideoId])

  useEffect(() => {
    if (!currentVideoId) {
      setRenderInfo(null);
      setIsRendering(false);
      return undefined;
    }

    let stopped = false;
    let intervalId = null;
    const syncRender = async () => {
      try {
        const response = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}/render-status`
        );
        if (stopped || !response.ok) return;
        const data = await response.json();
        if (stopped) return;
        setRenderInfo(data);
        const jobStatus = data.job?.status;
        setIsRendering(jobStatus === 'queued' || jobStatus === 'running');
      } catch (error) {
        console.error('Failed to sync render status', error);
      }
    };

    syncRender();
    intervalId = setInterval(syncRender, 5000);
    return () => {
      stopped = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [currentVideoId]);

  useEffect(() => {
    if (!currentVideoId) {
      setAudioReview(null)
      return undefined
    }

    let stopped = false
    setIsLoadingAudioReview(true)
    fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/audio-review`)
      .then(async response => {
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.detail || data.error || `HTTP ${response.status}`)
        }
        if (!stopped) setAudioReview(data.audio_review || null)
      })
      .catch(error => {
        if (!stopped) console.error('Failed to load audio review', error)
      })
      .finally(() => {
        if (!stopped) setIsLoadingAudioReview(false)
      })

    return () => {
      stopped = true
    }
  }, [currentVideoId])
  
  const fetchPromptVersions = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8080/api/prompts');
      const data = await res.json();
      if (data.versions) {
        const versionsList = Object.entries(data.versions).map(([key, version]) => ({
          key: key,
          name: version.name,
          contentMode: version.content_mode || 'dialogue',
          castSettings: version.cast_settings || {},
          defaultVoiceId: version.default_voice_id || ''
        }));
        setPromptVersions(versionsList);
        setSelectedPromptVersion(data.active_version || 'default');
      }
    } catch (err) {
      console.error('Failed to fetch prompt versions', err);
    }
  }

  const fetchVoices = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8080/api/voices')
      const data = await response.json()
      if (!response.ok || !Array.isArray(data.voices)) {
        throw new Error(data.detail || 'Invalid voice configuration')
      }
      setVoiceOptions(data.voices)
      const primaryVoiceId = data.active_voice_id || data.voices[0]?.id || ''
      setGlobalDefaultVoiceId(primaryVoiceId)
      setSelectedVoiceId(previous => previous || primaryVoiceId)
      setSelectedMcVoiceId(previous => previous || primaryVoiceId)
      
      const distinctGuestVoice = data.voices.find(v => v.id !== primaryVoiceId)?.id || 'auto'
      setSelectedGuest1VoiceId(previous => previous || distinctGuestVoice)
      setSelectedGuest2VoiceId(previous => previous || 'auto')

      setRegenerateVoiceId(previousVoiceId =>
        previousVoiceId || primaryVoiceId
      )
    } catch (error) {
      console.error('Failed to fetch voices', error)
    }
  }

  const handleRun = async () => {
    if (!url.trim() || isFetching) return
    const submittedUrl = url.trim()
    setIsFetching(true)
    setErrorMsg('')
    setProgressMsg('Đang thêm video vào hàng đợi...')
    
    const selectedVersionObj = promptVersions.find(v => v.key === selectedPromptVersion);
    const isDialogue = (selectedVersionObj?.contentMode || 'dialogue') === 'dialogue';
    const primaryVoiceId = isDialogue ? (selectedMcVoiceId || selectedVoiceId || '') : (selectedVoiceId || '');
    const castOverrides = isDialogue ? {
      mc: selectedMcVoiceId || primaryVoiceId || '',
      guest_1: selectedGuest1VoiceId || 'auto',
      guest_2: selectedGuest2VoiceId || 'auto'
    } : null;

    try {
      const response = await fetch('http://127.0.0.1:8080/api/process-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: submittedUrl,
          prompt_version: selectedPromptVersion,
          voice_id: primaryVoiceId || null,
          cast_voice_overrides: castOverrides,
          publish_mode: publishImmediately ? 'public' : null,
          publish_immediately: publishImmediately
        })
      })
      const data = await response.json()
      if (!response.ok || !data.job_id) {
        throw new Error(data.detail || 'Backend không trả về mã job.')
      }
      setUrl('')
      setPublishImmediately(false)
      setProgressMsg(
        data.duplicate
          ? 'Video này đã có trong hàng đợi; hệ thống không tạo job trùng.'
          : data.status === 'running'
          ? 'Video đã bắt đầu xử lý.'
          : `Đã thêm vào hàng đợi${data.queue_position ? ` ở vị trí #${data.queue_position}` : ''}.`
      )
      setQueueRefreshKey(value => value + 1)
    } catch (error) {
      setProgressMsg('')
      setErrorMsg(error.message || 'Không thể kết nối tới backend Python.')
    } finally {
      setIsFetching(false)
    }
  }

  const getCleanText = (text) => {
    if (!text) return '';
    let clean = text.replace(/### \[IMAGE\][\s\S]*/, ''); // Remove image section entirely
    clean = clean.replace(/### \[AUDIO\][\s\S]*/, ''); // Remove audio section entirely
    clean = clean.replace(/### \[[^\]]+\]/g, ''); // Remove all ### [TITLE] markers
    clean = clean.replace(/\n\s*\n\s*\n/g, '\n\n').trim(); // Collapse excess newlines
    return clean;
  };

  const handleExportTxt = () => {
    const textToCopy = activeTab === 'summary' ? getCleanText(resultText) : fullTranscript;
    const blob = new Blob([textToCopy], { type: 'text/plain' });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = activeTab === 'summary' ? 'Kich_Ban_Nexus.txt' : 'Phu_De_Goc.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  };

  const viewSavedVideo = async (id) => {
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${id}`);
      const data = await response.json();
      setUrl(data.url);
      setResultText(data.generated_script);
      setFullTranscript(data.transcript);
      setChatUrl(data.chat_url || '');
      setVideoTitle(data.title || '');
      setCurrentVideoPromptVersion(data.prompt_version || '');
      setCurrentVideoStatus(data.video_status || 'active');
      setCurrentVideoVoiceId(data.voice_id || '');
      setCurrentVideoVoiceName(data.voice_name || '');
      setCurrentVideoProviderId(data.tts_provider_id || 'genmax');
      setAudioTaskVoiceName('');
      setAudioTaskProviderId(data.tts_provider_id || 'genmax');
      setRegenerateVoiceId(data.voice_id || selectedVoiceId);

      let castOverrides = null;
      try {
        if (data.production_snapshot_json) {
          const snapshot = JSON.parse(data.production_snapshot_json);
          castOverrides = snapshot.cast_voice_overrides || snapshot.cast_settings || null;
        }
      } catch (e) {}

      const promptVer = promptVersions.find(v => v.key === (data.prompt_version || ''));
      const promptCast = promptVer?.castSettings || {};

      const mcVoice = castOverrides?.mc || data.voice_id || promptCast.mc?.voice_id || promptCast.mc?.default_voice_id || selectedVoiceId || '';
      const guest1Voice = castOverrides?.guest_1 || promptCast.guest_1?.voice_id || promptCast.guest_1?.default_voice_id || 'auto';
      const guest2Voice = castOverrides?.guest_2 || promptCast.guest_2?.voice_id || promptCast.guest_2?.default_voice_id || 'auto';

      setSelectedMcVoiceId(mcVoice);
      setSelectedGuest1VoiceId(guest1Voice);
      setSelectedGuest2VoiceId(guest2Voice);

      setShowResult(true);
      setActiveView('fetcher', String(id));
      setCurrentVideoId(id);  // track which video is loaded
      setAudioReview(null);
      setIsCurrentVideoPublished(Boolean(data.is_published));
      setCurrentVideoPublications(data.publications || []);
      setCurrentVideoDefaultChannelTitle(data.default_youtube_channel_title || '');
      setCurrentVideoHasCheckpoint(Boolean(data.has_checkpoint));
      setAudioStatus('not_started');
      setAudioMissingSegments(0);
      setAudioRoleStreams(null);
      setRenderInfo(null);
      setIsRendering(false);
      setErrorMsg('');
      setProgressMsg('');
    } catch (err) {
      console.error("Failed to load video", err);
      alert("Failed to load video from database.");
    }
  };

  const clearCurrentVideo = () => {
    setCurrentVideoId(null);
    setUrl('');
    setResultText('');
    setFullTranscript('');
    setChatUrl('');
    setVideoTitle('');
    setCurrentVideoPromptVersion('');
    setCurrentVideoStatus('active');
    setCurrentVideoVoiceId('');
    setCurrentVideoVoiceName('');
    setCurrentVideoProviderId('genmax');
    setAudioTaskVoiceName('');
    setAudioTaskProviderId('genmax');
    setRegenerateVoiceId(selectedVoiceId);
    setShowResult(false);
    setAudioReview(null);
    setIsCurrentVideoPublished(false);
    setCurrentVideoPublications([]);
    setCurrentVideoDefaultChannelTitle('');
    setCurrentVideoHasCheckpoint(false);
    setAudioStatus('not_started');
    setAudioMissingSegments(0);
    setRenderInfo(null);
    setIsRendering(false);
    setErrorMsg('');
    setProgressMsg('');
  };

  const deleteSavedVideo = async (id) => {
    if (!confirm("Xóa video và toàn bộ job, audio, checkpoint liên quan?")) return;
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${id}`,
        { method: 'DELETE' }
      );
      const data = await response.json();
      if (!response.ok || data.success === false) {
        throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      }
      if (currentVideoId === id) clearCurrentVideo();
      setQueueRefreshKey(key => key + 1);
      const nextPage = (
        savedVideos.items.length === 1 && currentPage > 1
          ? currentPage - 1
          : currentPage
      );
      if (nextPage !== currentPage) setCurrentPage(nextPage);
      await fetchSavedVideos(nextPage);
      if (data.warnings?.length) {
        alert(`Video đã được xóa, nhưng còn cảnh báo:\n${data.warnings.join('\n')}`);
      }
    } catch (err) {
      console.error("Failed to delete", err);
      alert(`Không thể xóa video: ${err.message}`);
    }
  };

  const handleCopySection = (content, btnId) => {
    navigator.clipboard.writeText(content).then(() => {
      const btn = document.getElementById(btnId);
      if (btn) {
        const originalText = btn.innerText;
        btn.innerText = 'Copied! ✅';
        setTimeout(() => { btn.innerText = originalText; }, 2000);
      }
    });
  };

  const handleDownloadAudio = async () => {
    if (!audioUrl) return;
    try {
      const response = await fetch(audioUrl);
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = `voice_over_${Date.now()}.mp3`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch (error) {
      console.error('Download failed, opening in new tab', error);
      window.open(audioUrl, '_blank');
    }
  };

  const handleDownloadImage = async (url, defaultFilename, e) => {
    e.preventDefault();
    try {
      // Add a cache-busting parameter to prevent using opaque responses cached by <img> tag
      const fetchUrl = url.includes('?') ? `${url}&_cb=${Date.now()}` : `${url}?_cb=${Date.now()}`;
      const response = await fetch(fetchUrl, { mode: 'cors' });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const blob = await response.blob();
      
      if (window.showSaveFilePicker) {
        const handle = await window.showSaveFilePicker({
          suggestedName: defaultFilename,
          types: [{
            description: 'PNG Image',
            accept: { 'image/png': ['.png'] },
          }],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = defaultFilename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Download failed', err);
        alert('Lỗi tải ảnh: ' + err.message);
      }
    }
  };

  const handleOpenStudio = async (videoId, publishedYoutubeVideoId) => {
    try {
      const data = await openVideoStudioInGpm(videoId);
      alert(data.message || '🚀 Đã mở YouTube Studio trong GPM Profile!');
    } catch (err) {
      alert(`⚠️ Không thể mở Studio trong GPM: ${err.message}`);
    }
  };

  const handleOpenWatch = async (videoId) => {
    try {
      const data = await openVideoWatchInGpm(videoId);
      alert(data.message || '🚀 Đã mở video trên YouTube trong GPM Profile!');
    } catch (err) {
      alert(`⚠️ Không thể mở video trong GPM: ${err.message}`);
    }
  };

  const handleContinueGeneration = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    setIsFetching(true);
    setProgressMsg('⏳ Đang tiếp tục tạo...');
    setErrorMsg('');
    setShowResult(false);

    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/continue-generation`, {
        method: 'POST',
      });
      const data = await response.json();
      if (!response.ok || !data.job_id) {
        throw new Error(data.detail || data.error || 'Lỗi gọi API tiếp tục.');
      }

      const job_id = data.job_id;
      // Poll every 3 seconds until done or error
      await new Promise((resolve) => {
        const interval = setInterval(async () => {
          try {
            const res = await fetch(`http://127.0.0.1:8080/api/jobs/${job_id}`);
            const job = await res.json();
            setProgressMsg(job.progress || '...');
            if (job.status === 'done') {
              clearInterval(interval);
              const resultData = job.result;
              if (resultData) {
                setResultText(resultData.summary);
                setFullTranscript(resultData.full_transcript);
                setChatUrl(resultData.chat_url || '');
                setCurrentVideoId(resultData.video_id);
                setAudioReview(resultData.audio_review || null);
                setVideoTitle(resultData.title || '');
                setCurrentVideoHasCheckpoint(Boolean(resultData.failed_step));
                if (resultData.generation_warning) {
                  setErrorMsg(
                    'Đã lưu phần nội dung hoàn tất. Bước cần tạo lại: ' +
                    resultData.generation_warning
                  );
                }
              }
              resolve();
            } else if (job.status === 'error') {
              clearInterval(interval);
              setErrorMsg(job.error || 'Đã xảy ra lỗi không xác định.');
              resolve();
            }
          } catch {
            // ignore transient fetch errors
          }
        }, 3000);
      });
    } catch (err) {
      setErrorMsg('Lỗi tiếp tục: ' + (err?.message || String(err)));
    } finally {
      setIsFetching(false);
      setShowResult(true);
      await fetchSavedVideos(currentPage, publishFilter);
    }
  };

  const handleGenerateThumbnail = async (thumbnailType) => {
    if (!resultText || chatGptControlsDisabled) return;
    setGeneratingThumbnailType(thumbnailType);
    try {
      const res = await fetch('http://127.0.0.1:8080/api/generate-thumbnails', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          script: resultText,
          video_id: currentVideoId,
          thumbnail_type: thumbnailType
        })
      });
      const data = await res.json();
      if (data.success) {
        if (data.warning) {
          alert('Cảnh báo: ' + data.warning);
        }
        if (data.script) {
          setResultText(data.script);
        } else {
          const normalizeThumbnailUrls = (urls, fallbackUrl) => {
            const candidates = Array.isArray(urls) && urls.length > 0
              ? urls
              : (fallbackUrl ? [fallbackUrl] : []);
            return [...new Set(candidates.filter(Boolean))].slice(0, 2);
          };
          const patchThumbnailImages = (script, sectionTitle, imageUrls) => {
            if (imageUrls.length === 0) return script;
            const imageMarkers = imageUrls
              .map((imageUrl) => `[IMAGE_URL:${imageUrl}]`)
              .join('\n\n');
            return script.replace(
              new RegExp(`(### \\[${sectionTitle}\\][\\s\\S]*?)(?=\\n### |$)`),
              (section) => {
                const cleaned = section.replace(/\[IMAGE_URL:.*?\]/g, '').trimEnd();
                return `${cleaned}\n\n${imageMarkers}`;
              }
            );
          };

          let newScript = resultText;
          newScript = patchThumbnailImages(
            newScript,
            'THUMBNAIL CÓ CHỮ',
            normalizeThumbnailUrls(data.image1_urls, data.image1_url)
          );
          newScript = patchThumbnailImages(
            newScript,
            'THUMBNAIL KHÔNG CHỮ',
            normalizeThumbnailUrls(data.image2_urls, data.image2_url)
          );
          setResultText(newScript);
        }
        if (currentVideoId) {
          await fetchSavedVideos(currentPage, publishFilter);
        }
      } else {
        alert('Lỗi tạo thumbnail: ' + (data.error || 'Unknown error'));
      }
    } catch {
      alert('Không thể kết nối Backend.');
    } finally {
      setGeneratingThumbnailType(null);
    }
  };

  const handleGenerateChapters = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    const requestedVideoId = currentVideoId;
    setIsGeneratingChapters(true);
    try {
      const response = await fetch('http://127.0.0.1:8080/api/generate-chapters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: requestedVideoId })
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại chapter.');
      }
      if (data.video_id !== requestedVideoId) {
        throw new Error('Backend trả về sai video. Giao diện chưa được cập nhật.');
      }
      if (currentVideoIdRef.current === requestedVideoId) {
        setResultText(data.script);
        setErrorMsg(previousError =>
          previousError.toLowerCase().includes('chapters:')
            ? ''
            : previousError
        );
      } else {
        alert('Chapter đã được cập nhật. Hãy mở lại đúng video để xem kết quả.');
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert('Lỗi tạo chapter: ' + error.message);
    } finally {
      setIsGeneratingChapters(false);
    }
  };

  const handleGenerateItem = async (endpoint, label, setLocalLoading) => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    const requestedVideoId = currentVideoId;
    setLocalLoading(true);
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: requestedVideoId })
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || `Không thể tạo lại ${label}.`);
      }
      if (data.video_id !== requestedVideoId) {
        throw new Error('Backend trả về sai video. Giao diện chưa được cập nhật.');
      }

      const refreshedResponse = await fetch(
        `http://127.0.0.1:8080/api/videos/${requestedVideoId}?_=${Date.now()}`,
        { cache: 'no-store' }
      );
      if (!refreshedResponse.ok) {
        throw new Error('Không thể tải nội dung mới từ database.');
      }
      const refreshedVideo = await refreshedResponse.json();
      if (currentVideoIdRef.current === requestedVideoId) {
        setResultText(refreshedVideo.generated_script);
        if (refreshedVideo.title) {
          setVideoTitle(refreshedVideo.title);
        }
      } else {
        alert(`${label} đã được cập nhật. Hãy mở lại đúng video để xem kết quả.`);
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert(`Lỗi tạo ${label}: ` + error.message);
    } finally {
      setLocalLoading(false);
    }
  };

  const handleGenerateTitle = () => handleGenerateItem('generate-title', 'tiêu đề', setIsGeneratingTitle);
  const handleGenerateSlug = () => handleGenerateItem('generate-slug', 'URL slug', setIsGeneratingSlug);
  const handleGenerateDescription = () => handleGenerateItem('generate-description', 'mô tả', setIsGeneratingDescription);
  const handleGenerateTags = () => handleGenerateItem('generate-tags', 'tags & hashtags', setIsGeneratingTags);
  const handleGeneratePinnedComment = () => handleGenerateItem('generate-pinned-comment', 'bình luận ghim', setIsGeneratingPinnedComment);
  const handleGenerateQuiz = () => handleGenerateItem('generate-quiz', 'quiz', setIsGeneratingQuiz);

  const handleGenerateMetadata = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    const requestedVideoId = currentVideoId;
    setIsGeneratingMetadata(true);
    try {
      const response = await fetch('http://127.0.0.1:8080/api/generate-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: requestedVideoId })
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại tiêu đề.');
      }
      if (data.video_id !== requestedVideoId) {
        throw new Error('Backend trả về sai video. Giao diện chưa được cập nhật.');
      }

      const refreshedResponse = await fetch(
        `http://127.0.0.1:8080/api/videos/${requestedVideoId}?_=${Date.now()}`,
        { cache: 'no-store' }
      );
      if (!refreshedResponse.ok) {
        throw new Error('Không thể tải metadata mới từ database.');
      }
      const refreshedVideo = await refreshedResponse.json();
      if (currentVideoIdRef.current === requestedVideoId) {
        setResultText(refreshedVideo.generated_script);
      } else {
        alert('Metadata đã được cập nhật. Hãy mở lại đúng video để xem kết quả.');
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert('Lỗi tạo metadata: ' + error.message);
    } finally {
      setIsGeneratingMetadata(false);
    }
  };

  const handleFetchDescriptionPreview = async () => {
    if (!currentVideoId) return;
    setIsLoadingPreviewDescription(true);
    setShowPreviewModal(true);
    setCopiedPreview(false);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/youtube-description-preview?_=${Date.now()}`
      );
      const data = await response.json();
      if (response.ok && data.success) {
        setPreviewDescription(data.preview_description || '');
      } else {
        setPreviewDescription(data.detail || data.error || 'Không thể tải bản xem trước mô tả.');
      }
    } catch (error) {
      setPreviewDescription('Lỗi kết nối khi tải bản xem trước: ' + error.message);
    } finally {
      setIsLoadingPreviewDescription(false);
    }
  };

  const handleGenerateAudio = async () => {
    if (!currentVideoId || currentVideoIsError) return;
    if (audioReview && !audioReview.can_approve) {
      alert('Kịch bản không đạt kiểm tra tự động. Hãy sửa nội dung trước khi tạo audio.');
      return;
    }

    setIsGenAudio(true);
    let keepPolling = false;
    try {
      const res = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/generate-audio`,
        { method: 'POST' }
      );
      const data = await res.json();
      if (data.audio_review) setAudioReview(data.audio_review);
      if (!data.success) {
        setAudioStatus(data.audio_task?.status || 'not_started');
        setAudioMissingSegments(data.audio_task?.missing_segments || 0);
        alert('Lỗi: ' + (data.detail || data.error || 'Không thể tạo audio.'));
        return;
      }

      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || '');
      setAudioTaskProviderId(data.audio_task?.tts_provider_id || 'genmax');
      keepPolling = status === 'pending' || status === 'processing';
      if (status === 'completed') {
        const videoResponse = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}`
        );
        const video = await videoResponse.json();
        setResultText(video.generated_script);
        setCurrentVideoVoiceId(video.voice_id || '');
        setCurrentVideoVoiceName(video.voice_name || '');
        setCurrentVideoProviderId(video.tts_provider_id || 'genmax');
      }
    } catch {
      alert('Không thể kết nối Backend.');
    } finally {
      setIsGenAudio(keepPolling);
    }
  };

  const handleRetryAudio = async () => {
    if (!currentVideoId || currentVideoIsError) return;
    if (audioReview?.status === 'blocked') {
      alert('Kịch bản không đạt kiểm tra tự động nên chưa thể retry audio.');
      return;
    }
    const billable = audioTaskProviderId === 'genmax';
    if (billable && !window.confirm(
      'Retry bằng Genmax có thể trừ credit thêm một lần. Bạn có chắc muốn tiếp tục?'
    )) return;

    setIsGenAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/retry-audio`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm_credit_charge: billable })
        }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể retry audio.');
      }
      setAudioStatus(data.audio_task?.status || 'pending');
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || '');
      setAudioTaskProviderId(data.audio_task?.tts_provider_id || audioTaskProviderId);
    } catch (error) {
      setIsGenAudio(false);
      alert('Lỗi: ' + error.message);
    }
  };

  const handleRegenerateAudio = async () => {
    if (!currentVideoId || currentVideoIsError || !regenerateVoiceId || isGenAudio) return;
    if (audioReview?.status === 'blocked') {
      alert('Kịch bản không đạt kiểm tra tự động nên chưa thể tạo lại audio.');
      return;
    }
    const isDialogue = (resultText || '').includes('[MC]:') || (resultText || '').includes('[KHACH_1]:');
    const primaryVoiceId = isDialogue && selectedMcVoiceId ? selectedMcVoiceId : regenerateVoiceId;
    const voiceName = getVoiceName(primaryVoiceId);
    const selectedRegenerateVoice = voiceOptions.find(
      voice => voice.id === primaryVoiceId
    );
    const providerId = selectedRegenerateVoice?.provider_id || 'genmax';
    const billable = providerId === 'genmax';
    const confirmed = window.confirm(
      `Tạo lại toàn bộ audio bằng [${voiceProviderName(providerId)}] ${voiceName}. ` +
      (billable ? 'Dịch vụ cloud có thể tốn credit. ' : 'Engine local sẽ sử dụng GPU. ') +
      'Audio cũ được giữ cho đến khi audio mới hoàn thành. Bạn có tiếp tục không?'
    );
    if (!confirmed) return;

    const castOverrides = isDialogue ? {
      mc: selectedMcVoiceId || primaryVoiceId || '',
      guest_1: selectedGuest1VoiceId || 'auto',
      guest_2: selectedGuest2VoiceId || 'auto'
    } : null;

    setIsGenAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/regenerate-audio`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            voice_id: primaryVoiceId,
            cast_voice_overrides: castOverrides,
            confirm_credit_charge: billable
          })
        }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại audio.');
      }
      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || voiceName);
      setAudioTaskProviderId(data.audio_task?.tts_provider_id || providerId);
      setIsGenAudio(status === 'pending' || status === 'processing');
      if (status === 'completed') {
        const videoResponse = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}`
        );
        const video = await videoResponse.json();
        setResultText(video.generated_script);
        setCurrentVideoVoiceId(video.voice_id || '');
        setCurrentVideoVoiceName(video.voice_name || '');
        setCurrentVideoProviderId(video.tts_provider_id || 'genmax');
      }
    } catch (error) {
      setIsGenAudio(false);
      alert('Lỗi: ' + error.message);
    }
  };

  const handleCancelAudio = async () => {
    if (!currentVideoId || isCancelingAudio) return;
    setIsCancelingAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/cancel-audio`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể dừng tiến trình tạo audio.');
      }
      setIsGenAudio(false);
      setAudioStatus('failed');
    } catch (error) {
      alert('Lỗi dừng audio: ' + error.message);
    } finally {
      setIsCancelingAudio(false);
    }
  };

  const handleRegenerateRoleAudio = async (role, targetVoiceId) => {
    if (!currentVideoId || isGenAudio) return;
    const vId = targetVoiceId || selectedMcVoiceId || regenerateVoiceId;
    const vName = getVoiceName(vId);
    const roleLabel = role === 'MC' ? 'MC' : role === 'KHACH_1' ? 'Khách 1' : role === 'KHACH_2' ? 'Khách 2' : role;
    const confirmed = window.confirm(
      `Tạo lại chỉ riêng vai [${roleLabel}] với giọng ${vName}? ` +
      'Các vai khác đã hoàn thành sẽ được giữ nguyên 100% audio.'
    );
    if (!confirmed) return;

    setIsGenAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/regenerate-role-audio`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            role: role,
            voice_id: vId,
            voice_name: vName,
            confirm_credit_charge: false
          })
        }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại vai này.');
      }
      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      setAudioRoleStreams(data.audio_task?.role_streams || null);
      setIsGenAudio(status === 'pending' || status === 'processing');
    } catch (error) {
      setIsGenAudio(false);
      alert('Lỗi: ' + error.message);
    }
  };

  const handleMergeMasterAudio = async () => {
    if (!currentVideoId || isGenAudio) return;
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/merge-audio`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể ghép audio.');
      }
      alert('Đã ghép Audio Master thành công!');
      const status = data.audio_task?.status || 'completed';
      setAudioStatus(status);
      setAudioRoleStreams(data.audio_task?.role_streams || null);
      const videoResponse = await fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}`);
      const video = await videoResponse.json();
      setResultText(video.generated_script);
    } catch (error) {
      alert('Lỗi ghép audio: ' + error.message);
    }
  };

  const handleRenderVideo = async (mode = 'resume') => {
    if (!currentVideoId || isRendering || currentVideoIsError) return;
    setIsRendering(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/render-video?mode=${encodeURIComponent(mode)}`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể bắt đầu dựng video.');
      }
      setRenderInfo(prev => ({
        ...(prev || {}),
        video_id: currentVideoId,
        has_mp4: false,
        job: {
          status: 'queued',
          title: `Dựng video MP4 cho #${currentVideoId}${mode === 'recreate' ? ' (Tạo mới)' : ''}`
        }
      }));
    } catch (error) {
      setIsRendering(false);
      alert('Lỗi dựng video: ' + error.message);
    }
  };

  const handleCancelRenderVideo = async () => {
    if (!currentVideoId || isCancelingRender) return;
    setIsCancelingRender(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/cancel-render`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể dừng tác vụ dựng video.');
      }
      const statusRes = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/render-status`
      );
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        setRenderInfo(statusData);
        const jobStatus = statusData.job?.status;
        setIsRendering(jobStatus === 'queued' || jobStatus === 'running');
      }
    } catch (error) {
      alert('Lỗi dừng render: ' + error.message);
    } finally {
      setIsCancelingRender(false);
    }
  };

  const handleResetScenesFrom = async (fromIndex) => {
    if (!currentVideoId || isRendering || currentVideoIsError) return;
    setSceneResetDialog(prev => ({ ...(prev || {}), isSubmitting: true, error: '' }));
    setIsRendering(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/reset-scenes-from?from_index=${encodeURIComponent(fromIndex)}`,
        { method: 'POST' }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại từ cảnh.');
      }
      setSceneResetDialog(null);
      setRenderInfo(prev => ({
        ...(prev || {}),
        video_id: currentVideoId,
        has_mp4: false,
        job: {
          status: 'queued',
          title: `Dựng video MP4 cho #${currentVideoId} (Tạo lại từ cảnh ${Number(fromIndex) + 1})`
        }
      }));
    } catch (error) {
      setIsRendering(false);
      setSceneResetDialog(prev => ({ ...(prev || {}), isSubmitting: false, error: error.message }));
    }
  };

  const handlePublishNow = async (videoId) => {
    const targetId = videoId || currentVideoId;
    if (!targetId || isPublishingNow) return;
    if (!confirm(`Xác nhận Public ngay video #${targetId} lên YouTube? Video sẽ được công khai trực tiếp ngay lập tức.`)) {
      return;
    }
    setIsPublishingNow(true);
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${targetId}/publish-now`, {
        method: 'POST'
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể thực hiện Public ngay.');
      }
      alert(data.message || '🚀 Đã kích hoạt tác vụ Public ngay!');
      setQueueRefreshKey(k => k + 1);
      if (currentVideoId === targetId) {
        await viewSavedVideo(targetId);
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert(`Lỗi Public ngay: ${error.message}`);
    } finally {
      setIsPublishingNow(false);
    }
  };


  const parseSections = (text) => {
    if (!text) return [];
    
    const sections = [];
    const parts = text.split(/### \[(.*?)\]/g);
    
    let mainScriptContent = '';

    const splitMetadataContent = (text) => {
      const subs = [];
      const lines = text.split('\n');
      let currentTitle = 'METADATA';
      let currentBody = '';
      let hashtagsSeen = false;
      
      const flush = () => {
        if (currentBody.trim()) {
          let cleanContent = currentBody.trim();
          if (currentTitle === 'URL SLUG') {
            cleanContent = cleanContent.replace(/^(?:url\s+)?slug:\s*/i, '').trim();
          }
          subs.push({ title: currentTitle, content: cleanContent });
        }
        currentBody = '';
      };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        // Remove leading markdown asterisks, dashes, numbers, etc for matching
        const cleanLower = trimmed.toLowerCase().replace(/^[*\-\d.\s]+/, '').trim();
        
        if (cleanLower.startsWith('tiêu đề') && cleanLower.includes(':')) {
          flush(); currentTitle = 'TIÊU ĐỀ VIDEO'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if ((cleanLower.startsWith('url slug') || cleanLower.startsWith('slug:') || cleanLower.startsWith('slug :')) && cleanLower.includes(':')) {
          flush(); currentTitle = 'URL SLUG';
          let slugVal = line.split(':').slice(1).join(':').trim();
          slugVal = slugVal.replace(/^(?:url\s+)?slug:\s*/i, '').trim();
          currentBody += slugVal + '\n';
        } else if (cleanLower.startsWith('mô tả') && cleanLower.includes(':')) {
          flush(); currentTitle = 'MÔ TẢ VIDEO'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if ((cleanLower.startsWith('hashtag') || cleanLower.startsWith('tag')) && cleanLower.includes(':')) {
          flush(); currentTitle = 'HASHTAG'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
          hashtagsSeen = true;
        } else if (cleanLower.startsWith('#') && currentTitle !== 'QUIZ TƯƠNG TÁC') {
          flush(); currentTitle = 'HASHTAG'; currentBody += line + '\n';
          hashtagsSeen = true;
        } else if (cleanLower.startsWith('bình luận ghim') && cleanLower.includes(':')) {
          flush(); currentTitle = 'BÌNH LUẬN GHIM'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if (
          cleanLower.startsWith('quiz:') ||
          cleanLower.startsWith('quiz :') ||
          cleanLower.startsWith('câu hỏi:') ||
          cleanLower.startsWith('câu hỏi :') ||
          cleanLower.startsWith('trắc nghiệm:') ||
          cleanLower.startsWith('câu hỏi trắc nghiệm:') ||
          cleanLower.startsWith('theo các bạn') ||
          cleanLower.startsWith('theo quý vị') ||
          cleanLower.startsWith('theo ban') ||
          cleanLower.startsWith('theo quy vi') ||
          cleanLower.startsWith('theo cac ban') ||
          /^(?:A|B|C|D)[.)]\s+/i.test(trimmed)
        ) {
          if (currentTitle !== 'QUIZ TƯƠNG TÁC') {
            flush();
            currentTitle = 'QUIZ TƯƠNG TÁC';
          }
          if (cleanLower.startsWith('quiz:') || cleanLower.startsWith('câu hỏi:')) {
            const afterColon = line.split(':').slice(1).join(':').trim();
            currentBody += (afterColon || line) + '\n';
          } else {
            currentBody += line + '\n';
          }
        } else {
          if (currentTitle === 'METADATA' && trimmed) {
            currentTitle = hashtagsSeen ? 'BÌNH LUẬN GHIM' : 'MÔ TẢ VIDEO';
          }
          currentBody += line + '\n';
        }
      }
      flush();
      return subs;
    };

    for (let i = 1; i < parts.length; i += 2) {
      const tag = parts[i];
      const content = parts[i+1] ? parts[i+1].trim() : '';
      
      if (tag === 'INTRO' || tag === 'BODY' || tag === 'OUTRO') {
        if (mainScriptContent) mainScriptContent += '\n\n';
        mainScriptContent += content;
      } else if (tag === 'METADATA & QUIZ') {
        const parsedMeta = splitMetadataContent(content);
        sections.push(...parsedMeta);
      } else if (tag !== 'IMAGE' && tag !== 'AUDIO') {
        sections.push({
          title: tag,
          content: content
        });
      }
    }
    
    if (mainScriptContent) {
      sections.unshift({
        title: 'NỘI DUNG KỊCH BẢN',
        content: mainScriptContent
      });
    }

    // --- Combine MÔ TẢ VIDEO, CHAPTERS, and HASHTAG in correct order ---
    const finalSections = [];
    let chaptersContent = '';
    let hashtagContent = '';

    // First pass to extract chapters and hashtags
    sections.forEach((sec) => {
      if (sec.title === 'CHAPTERS') {
        chaptersContent = sec.content;
      }
      if (sec.title === 'HASHTAG') {
        hashtagContent = sec.content;
      }
    });

    // Second pass to build final sections in desired order
    sections.forEach(sec => {
      if (sec.title === 'CHAPTERS' || sec.title === 'HASHTAG') {
        return; // Handled inside MÔ TẢ VIDEO & CHAPTERS
      }
      if (sec.title === 'MÔ TẢ VIDEO') {
        const combinedParts = [];
        if (sec.content) combinedParts.push(sec.content);
        if (chaptersContent) combinedParts.push(chaptersContent);
        if (hashtagContent) combinedParts.push(hashtagContent);
        finalSections.push({
          title: 'MÔ TẢ VIDEO & CHAPTERS',
          content: combinedParts.join('\n\n')
        });
      } else {
        finalSections.push(sec);
      }
    });

    if (finalSections.length === 0 && text.trim()) {
      finalSections.push({
        title: 'KẾT QUẢ TRẢ VỀ',
        content: text.trim()
      });
    }

    return finalSections;
  };

  const displayTranscript = fullTranscript.length > 1000 
    ? fullTranscript.substring(0, 1000) + '...\n\n(Nội dung đã được thu gọn để dễ nhìn)'
    : fullTranscript;

  let parsedSections = [];
  let imageUrl = '';
  let audioUrl = '';
  
  if (resultText) {
    if (resultText.includes('### [IMAGE]')) {
      const parts = resultText.split('### [IMAGE]');
      imageUrl = parts[1].split('###')[0].trim();
    }
    if (resultText.includes('### [AUDIO]')) {
      const parts = resultText.split('### [AUDIO]');
      audioUrl = parts[1].split('###')[0].trim();
    }
    parsedSections = parseSections(resultText);
  }

  return (
    <div className="app-container">
      <BackgroundCanvas mode={bgMode} />

      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <img src="/logoNexus.png" alt="Nexus Logo" className="brand-logo-img" />
          <div className="brand-info">
            <div className="brand-name">Nexus</div>
            <div className="brand-subtitle">Studio Engine</div>
          </div>
        </div>
        <ul className="nav-menu">
          <li className={`nav-item ${activeView === 'dashboard' ? 'active' : ''}`} data-view="dashboard" onClick={() => setActiveView('dashboard')}>Dashboard</li>
          <li className={`nav-item ${activeView === 'jobs' ? 'active' : ''}`} data-view="jobs" onClick={() => setActiveView('jobs')}>Trung tâm Job</li>
          <li className={`nav-item ${activeView === 'comments' ? 'active' : ''}`} data-view="comments" onClick={() => setActiveView('comments')}>Bình luận YouTube</li>
          <li className={`nav-item ${activeView === 'channels' ? 'active' : ''}`} data-view="channels" onClick={() => setActiveView('channels')}>Channel Hub</li>
          <li className={`nav-item ${activeView === 'crossposter' ? 'active' : ''}`} data-view="crossposter" onClick={() => setActiveView('crossposter')}>Cross-Poster</li>
          <li className={`nav-item ${activeView === 'fetcher' ? 'active' : ''}`} data-view="fetcher" onClick={() => setActiveView('fetcher')}>Video Fetcher</li>
          <li
            className={`nav-item ${activeView === 'downloader' ? 'active' : ''}`}
            data-view="downloader"
            onClick={() => setActiveView('downloader')}
          >YouTube Downloader</li>
          <li
            className={`nav-item ${activeView === 'autologin' ? 'active' : ''}`}
            data-view="autologin"
            aria-disabled={chatGptControlsDisabled}
            onClick={() => !chatGptControlsDisabled && setActiveView('autologin')}
            style={chatGptControlsDisabled ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
          >Auto Login</li>
          <li
            className={`nav-item ${activeView === 'flowlogin' ? 'active' : ''}`}
            data-view="flowlogin"
            onClick={() => setActiveView('flowlogin')}
          >Google Flow</li>
          <li
            className={`nav-item ${activeView === 'tts' ? 'active' : ''}`}
            data-view="tts"
            onClick={() => setActiveView('tts')}
          >Giọng đọc &amp; TTS</li>
          <li
            className={`nav-item ${activeView === 'settings' ? 'active' : ''}`}
            data-view="settings"
            onClick={() => setActiveView('settings')}
          >Settings</li>
        </ul>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        <header className="header">
          <div className="bg-switcher-container" title="Chọn hiệu ứng hình nền động">
            {BACKGROUND_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                className={`bg-mode-btn ${bgMode === mode.id ? 'active' : ''}`}
                onClick={() => handleBgModeChange(mode.id)}
              >
                <span className="bg-mode-icon">{mode.icon}</span>
                <span className="bg-mode-label">{mode.label}</span>
              </button>
            ))}
          </div>

          <div className="status-badge">
            <span className="status-dot"></span>
            Ready
          </div>
        </header>

        <div className="view-container">
          {activeView === 'autologin' ? (
            <AutoLogin />
          ) : activeView === 'flowlogin' ? (
            <GoogleFlowLogin />
          ) : activeView === 'tts' ? (
            <TTSSettings />
          ) : activeView === 'channels' ? (
            <ChannelManager />
          ) : activeView === 'crossposter' ? (
            <CrossPoster />
          ) : activeView === 'settings' ? (
            <Settings
              lockedPromptVersion={chatGptStatus.promptVersion}
              chatGptOperation={chatGptStatus.operation}
            />
          ) : activeView === 'jobs' ? (
            <JobCenter
              onOpenVideo={viewSavedVideo}
              refreshKey={queueRefreshKey}
            />
          ) : activeView === 'comments' ? (
            <YouTubeComments
              onOpenVideo={viewSavedVideo}
              refreshKey={queueRefreshKey}
            />
          ) : activeView === 'downloader' ? (
            <YouTubeDownloader />
          ) : activeView === 'dashboard' ? (
            <>
              <h1 className="hero-title">Video Library</h1>
              <p className="hero-subtitle">All your automatically saved video scripts are here.</p>

              <div style={{ marginTop: '20px' }}>
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="🔎 Tìm theo link gốc, tiêu đề gốc, tiêu đề video hoặc mô tả..."
                  aria-label="Tìm kiếm video"
                  style={{
                    width: '100%',
                    padding: '12px 16px',
                    borderRadius: '10px',
                    border: '1px solid rgba(155, 89, 182, 0.55)',
                    background: '#17131d',
                    color: '#eee',
                    fontSize: '0.95em',
                    outline: 'none'
                  }}
                />
              </div>
              
              {/* Filter bar + stats */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '20px', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
                {/* Stats */}
                <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    📦 Tổng: <strong style={{color:'white'}}>{(savedVideos.count_published || 0) + (savedVideos.count_unpublished || 0) + (savedVideos.count_error || 0)}</strong> video
                  </span>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    ✅ Đã đăng: <strong style={{color:'#4caf50'}}>{savedVideos.count_published || 0}</strong>
                  </span>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    ⏳ Chưa đăng: <strong style={{color:'#f39c12'}}>{savedVideos.count_unpublished || 0}</strong>
                  </span>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    ❌ Lỗi: <strong style={{color:'#e74c3c'}}>{savedVideos.count_error || 0}</strong>
                  </span>
                </div>
                {/* Version + publication filters */}
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <label
                    htmlFor="dashboard-version-filter"
                    style={{ color: '#aaa', fontSize: '0.85em', fontWeight: '600' }}
                  >
                    Phiên bản:
                  </label>
                  <select
                    id="dashboard-version-filter"
                    value={promptVersionFilter}
                    onChange={(event) => {
                      const nextVersion = event.target.value;
                      setPromptVersionFilter(nextVersion);
                      setCurrentPage(1);
                    }}
                    style={{
                      minWidth: '190px', padding: '7px 12px', borderRadius: '8px',
                      border: '1px solid rgba(155, 89, 182, 0.55)',
                      background: '#17131d', color: '#eee', cursor: 'pointer', fontWeight: '600'
                    }}
                  >
                    <option value="all">🤖 Tất cả phiên bản</option>
                    {promptVersions.map(version => (
                      <option key={version.key} value={version.key}>{version.name}</option>
                    ))}
                  </select>
                  {[['all', '🗂️ Tất cả'], ['published', '✅ Đã đăng'], ['unpublished', '⏳ Chưa đăng'], ['error', '❌ Lỗi']].map(([val, label]) => (
                    <button
                      key={val}
                      onClick={() => { setPublishFilter(val); setCurrentPage(1); }}
                      style={{
                        padding: '6px 14px', borderRadius: '20px', fontSize: '0.85em', cursor: 'pointer', fontWeight: '600',
                        border: publishFilter === val
                          ? (val === 'published' ? '1px solid #4caf50' : val === 'unpublished' ? '1px solid #f39c12' : val === 'error' ? '1px solid #e74c3c' : '1px solid var(--accent)')
                          : '1px solid #444',
                        background: publishFilter === val
                          ? (val === 'published' ? 'rgba(76,175,80,0.2)' : val === 'unpublished' ? 'rgba(243,156,18,0.2)' : val === 'error' ? 'rgba(231,76,60,0.2)' : 'rgba(155,89,182,0.2)')
                          : 'transparent',
                        color: publishFilter === val
                          ? (val === 'published' ? '#4caf50' : val === 'unpublished' ? '#f39c12' : val === 'error' ? '#e74c3c' : 'var(--accent)')
                          : '#888',
                        transition: 'all 0.2s'
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="video-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
                {savedVideos.items.length === 0 ? (
                  <p style={{ color: '#888' }}>
                    {debouncedSearchQuery
                      ? 'Không tìm thấy video phù hợp.'
                      : 'No saved videos yet. Fetch a video first!'}
                  </p>
                ) : (
                  savedVideos.items.map(video => {
                    let cleanSnippet = "";
                    if (video.snippet) {
                       cleanSnippet = video.snippet.replace(/### \[[^\]]+\]/g, '').replace(/\n/g, ' ').trim();
                       if (cleanSnippet.length > 80) cleanSnippet = cleanSnippet.substring(0, 80) + '...';
                    }
                     const productionStatus = videoProductionStatus(video);
                     const isPublished = Boolean(video.is_published);
                     const isError = video.video_status === 'error';
                     const statusColor = isError ? '#e74c3c' : productionStatus?.color || (isPublished ? '#4caf50' : '#f39c12');
                     const lifecycleState = isError ? 'error' : isPublished ? 'published' : 'unpublished';
                     const lifecycleLabel = isError
                       ? '❌ Lỗi'
                       : productionStatus
                         ? productionStatus.label
                         : isPublished ? '✅ Đã đăng' : '⏳ Chưa đăng';
                    return (
                      <div key={video.id} className="result-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', borderTop: `3px solid ${statusColor}` }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                          <h4 style={{ margin: 0, color: 'white', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', paddingRight: '8px' }}>{video.title}</h4>
                          <span style={{ 
                            fontSize: '0.75em', fontWeight: 'bold', padding: '3px 8px', borderRadius: '12px', whiteSpace: 'nowrap',
                            backgroundColor: isError ? 'rgba(231,76,60,0.15)' : isPublished ? 'rgba(76,175,80,0.15)' : 'rgba(243,156,18,0.15)',
                            color: statusColor,
                            border: `1px solid ${statusColor}`
                          }}>
                             {lifecycleLabel}
                          </span>
                        </div>
                         <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                          <p style={{ color: '#888', fontSize: '0.9em', margin: 0 }}>{new Date(video.created_at).toLocaleString('vi-VN')}</p>
                          {isError ? (
                            <span style={{ color: '#e74c3c', fontSize: '0.8em' }}>Đã bỏ qua xử lý</span>
                          ) : (
                            <AudioDurationBadge
                              videoId={video.id}
                              audioUrl={video.audio_url}
                              savedDurationSeconds={video.audio_duration_seconds}
                              audioReviewStatus={video.audio_review_status}
                            />
                          )}
                         </div>
                         {productionStatus && (
                           <div style={{ marginBottom: 8 }}>
                             <span style={{ color: productionStatus.color, fontSize: '0.8em', fontWeight: 700 }}>
                               ● {productionStatus.label}
                             </span>
                             {video.production_progress && (
                               <span style={{ color: '#888', fontSize: '0.8em' }}> · {video.production_progress}</span>
                             )}
                             {video.blocking_reason && (
                               <div style={{ color: '#ff6b6b', fontSize: '0.78em', marginTop: 4 }}>
                                 {video.blocking_reason}
                               </div>
                             )}
                           </div>
                         )}
                        <div style={{ display: 'flex', gap: '15px', marginBottom: '10px', alignItems: 'center' }}>
                          <a href={video.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)', fontSize: '0.9em', textDecoration: 'none' }}>▶ Video gốc</a>
                          {video.published_url && (
                            <button
                              type="button"
                              onClick={() => handleOpenWatch(video.id)}
                              style={{
                                background: 'none',
                                border: 'none',
                                color: '#4dd0e1',
                                fontSize: '0.9em',
                                cursor: 'pointer',
                                padding: 0,
                                textDecoration: 'none'
                              }}
                              title="Mở video trong GPM Profile của kênh"
                            >
                              📺 Video đã đăng
                            </button>
                          )}
                          {video.chat_url && (
                            <a href={video.chat_url} target="_blank" rel="noreferrer" style={{ color: '#2ecc71', fontSize: '0.9em', textDecoration: 'none' }}>💬 Chat Gốc</a>
                          )}
                          {video.prompt_version && (
                            <span style={{
                              marginLeft: 'auto',
                              fontSize: '0.75em',
                              backgroundColor: 'rgba(155, 89, 182, 0.15)',
                              color: '#c39bd3',
                              padding: '2px 8px',
                              borderRadius: '4px',
                              border: '1px solid rgba(155, 89, 182, 0.4)'
                            }} title="Bộ Prompt sử dụng">
                              🤖 {getPromptVersionName(video.prompt_version)}
                            </span>
                          )}
                          <span style={{
                            marginLeft: video.prompt_version ? 0 : 'auto',
                            fontSize: '0.75em',
                            backgroundColor: 'rgba(26, 188, 156, 0.12)',
                            color: '#76d7c4', padding: '2px 8px',
                            borderRadius: '4px',
                            border: '1px solid rgba(26,188,156,0.4)',
                            whiteSpace: 'nowrap'
                          }} title={video.voice_id || 'Video cũ chưa lưu Voice ID'}>
                            🎙️ [{voiceProviderName(video.tts_provider_id || 'genmax')}] {getVoiceName(video.voice_id, video.voice_name)}
                          </span>
                        </div>
                        
                        <div style={{ backgroundColor: '#1a1a1a', padding: '10px', borderRadius: '6px', marginBottom: '15px', fontSize: '0.85em', color: '#ccc', fontStyle: 'italic' }}>
                          {cleanSnippet || 'Không có nội dung...'}
                        </div>
                        
                         <div style={{ marginTop: 'auto', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                           {Boolean(video.final_artifact_id) && (
                             <>
                               <a
                                 className="btn-secondary"
                                 style={{ padding: '8px 10px', fontSize: '0.85em', textDecoration: 'none' }}
                                 href={`http://127.0.0.1:8080/api/video-artifacts/${video.final_artifact_id}/download`}
                                 target="_blank"
                                 rel="noreferrer"
                               >
                                 🎬 Mở / tải MP4
                               </a>
                               <button
                                 className="btn-secondary"
                                 style={{
                                   padding: '8px 10px',
                                   fontSize: '0.85em',
                                   background: 'rgba(230, 126, 34, 0.15)',
                                   border: '1px solid #e67e22',
                                   color: '#f39c12',
                                   fontWeight: 'bold',
                                   cursor: isPublishingNow ? 'not-allowed' : 'pointer'
                                 }}
                                 onClick={() => handlePublishNow(video.id)}
                                 disabled={isPublishingNow || isError}
                                 title="Tải lên và Public ngay lập tức lên YouTube"
                               >
                                 ⚡ Public ngay
                               </button>
                             </>
                           )}
                           {video.published_youtube_video_id && (
                             <button
                               className="btn-secondary"
                               style={{ padding: '8px 10px', fontSize: '0.85em' }}
                               onClick={() => handleOpenStudio(video.id, video.published_youtube_video_id)}
                               title="Mở YouTube Studio trong GPM Profile của kênh"
                             >
                               📺 YouTube Studio
                             </button>
                           )}
                           <button
                            className="btn-run"
                            style={{ padding: '8px', flex: 1, fontSize: '0.9em' }}
                            onClick={() => viewSavedVideo(video.id)}
                          >📄 Xem Script</button>
                          <select
                            value={lifecycleState}
                            onChange={(event) => setVideoLifecycleState({
                              videoId: video.id,
                              videoTitle: video.title,
                              videoStatus: video.video_status || 'active',
                              isPublished,
                              hasPublishedUrl: Boolean(video.published_url),
                              defaultChannelTitle: video.default_youtube_channel_title,
                              nextState: event.target.value
                            })}
                            aria-label={`Trạng thái video ${video.title}`}
                            title="Chuyển thủ công giữa Chưa đăng, Đã đăng và Lỗi"
                            style={{
                              padding: '8px 10px', fontSize: '0.85em', borderRadius: '6px',
                              cursor: 'pointer', fontWeight: 'bold', border: `1px solid ${statusColor}`,
                              backgroundColor: isError ? 'rgba(231,76,60,0.2)' : isPublished ? 'rgba(76,175,80,0.2)' : 'rgba(243,156,18,0.2)',
                              color: statusColor
                            }}
                          >
                            <option value="unpublished" disabled={Boolean(video.published_url)}>⏳ Chưa đăng</option>
                            <option value="published">✅ Đã đăng</option>
                            <option value="error">❌ Lỗi</option>
                          </select>
                          <button
                            className="btn-secondary"
                            style={{ padding: '8px 10px', fontSize: '0.85em' }}
                            onClick={() => {
                              if (isPublished && !video.published_url) {
                                setPublicationDialog({
                                  videoId: video.id,
                                  videoTitle: video.title || 'Video chưa có tiêu đề',
                                  defaultChannelTitle: video.default_youtube_channel_title || '',
                                  publishedUrl: '',
                                  isSaving: false,
                                  error: ''
                                });
                              } else {
                                setActiveView('comments');
                              }
                            }}
                            disabled={isError}
                            title="Gắn link chuẩn của video đã đăng để đồng bộ bình luận"
                          >{isPublished && !video.published_url ? '🔗 Thêm link đã đăng' : '🔗 Link đã đăng'}</button>
                          <button className="btn-secondary" style={{ padding: '8px', fontSize: '0.9em' }} onClick={() => deleteSavedVideo(video.id)}>🗑️</button>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              {/* Pagination */}
              {savedVideos.total > PAGE_SIZE && (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', marginTop: '30px' }}>
                  <button 
                    className="btn-secondary"
                    style={{ padding: '8px 16px', opacity: currentPage === 1 ? 0.4 : 1 }}
                    disabled={currentPage === 1}
                    onClick={() => handlePageChange(currentPage - 1)}
                  >← Trước</button>
                  
                  {Array.from({ length: Math.ceil(savedVideos.total / PAGE_SIZE) }, (_, i) => i + 1).map(page => (
                    <button
                      key={page}
                      onClick={() => handlePageChange(page)}
                      style={{
                        padding: '8px 14px', borderRadius: '6px', border: '1px solid #444', cursor: 'pointer', fontWeight: 'bold',
                        backgroundColor: currentPage === page ? 'var(--accent)' : 'transparent',
                        color: currentPage === page ? '#000' : '#fff'
                      }}
                    >{page}</button>
                  ))}

                  <button 
                    className="btn-secondary"
                    style={{ padding: '8px 16px', opacity: currentPage === Math.ceil(savedVideos.total / PAGE_SIZE) ? 0.4 : 1 }}
                    disabled={currentPage === Math.ceil(savedVideos.total / PAGE_SIZE)}
                    onClick={() => handlePageChange(currentPage + 1)}
                  >Sau →</button>
                </div>
              )}
            </>
          ) : (
            <>
              <h1 className="hero-title">Video Content Fetcher</h1>
              <p className="hero-subtitle">Paste any YouTube URL to extract its core content and transcripts automatically.</p>

              <div style={{
                display: 'flex', justifyContent: 'center', marginBottom: '24px',
                gap: '12px', flexWrap: 'wrap'
              }}>
                <div style={{ 
                  display: 'flex', alignItems: 'center', gap: '12px', 
                  background: 'rgba(255, 255, 255, 0.03)', 
                  padding: '12px 24px', 
                  borderRadius: '12px', 
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
                  backdropFilter: 'blur(10px)'
                }}>
                  <label htmlFor="prompt-version-select" style={{ 
                    color: '#e0e0e0', 
                    fontSize: '15px',
                    fontWeight: '500',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px'
                  }}>
                    <span style={{ fontSize: '18px' }}>🤖</span> Bộ Prompt:
                  </label>
                  <div style={{ position: 'relative' }}>
                    <select 
                      id="prompt-version-select"
                      value={selectedPromptVersion}
                      onChange={(e) => setSelectedPromptVersion(e.target.value)}
                      style={{
                        appearance: 'none',
                        background: 'rgba(155, 89, 182, 0.15)', 
                        color: 'white', 
                        border: '1px solid rgba(155, 89, 182, 0.4)', 
                        padding: '8px 36px 8px 16px', 
                        borderRadius: '8px', 
                        outline: 'none',
                        fontSize: '15px',
                        fontWeight: '600',
                        cursor: 'pointer',
                        transition: 'all 0.2s ease',
                      }}
                      onMouseOver={(e) => {
                        e.target.style.background = 'rgba(155, 89, 182, 0.25)';
                        e.target.style.borderColor = 'rgba(155, 89, 182, 0.6)';
                      }}
                      onMouseOut={(e) => {
                        e.target.style.background = 'rgba(155, 89, 182, 0.15)';
                        e.target.style.borderColor = 'rgba(155, 89, 182, 0.4)';
                      }}
                    >
                      {promptVersions.map(v => (
                        <option key={v.key} value={v.key} style={{ background: '#1a1a1a', color: 'white' }}>
                          {v.name}
                        </option>
                      ))}
                    </select>
                    <div style={{
                      position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)',
                      pointerEvents: 'none', color: '#c39bd3', fontSize: '12px'
                    }}>▼</div>
                  </div>
                </div>
                {((promptVersions.find(v => v.key === selectedPromptVersion)?.contentMode || 'dialogue') === 'dialogue') ? (
                  <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px',
                    background: 'rgba(255, 255, 255, 0.03)',
                    padding: '10px 18px',
                    borderRadius: '12px',
                    border: '1px solid rgba(56, 189, 248, 0.3)',
                    boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)'
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                      <span style={{ color: '#38bdf8', fontSize: '13px', fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        👥 Phân Vai Giọng Đọc (Cast Voices):
                      </span>
                      <span style={{ color: '#94a3b8', fontSize: '11px' }}>
                        Tên nhân vật do AI tự động lấy từ video gốc
                      </span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center' }}>
                      {/* MC Voice */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <label htmlFor="mc-voice-select" style={{ color: '#fbbf24', fontSize: '12px', fontWeight: 'bold' }}>
                          🎙️ MC:
                        </label>
                        <select
                          id="mc-voice-select"
                          value={selectedMcVoiceId}
                          onChange={(e) => setSelectedMcVoiceId(e.target.value)}
                          disabled={voiceOptions.length === 0}
                          style={{
                            background: 'rgba(251, 191, 36, 0.15)', color: 'white',
                            border: '1px solid rgba(251, 191, 36, 0.5)',
                            padding: '6px 10px', borderRadius: '6px', outline: 'none',
                            fontSize: '13px', fontWeight: '600', cursor: 'pointer'
                          }}
                        >
                          <VoiceOptions voices={voiceOptions} />
                        </select>
                      </div>

                      {/* Guest 1 Voice */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <label htmlFor="guest1-voice-select" style={{ color: '#34d399', fontSize: '12px', fontWeight: 'bold' }}>
                          🎙️ Khách 1:
                        </label>
                        <select
                          id="guest1-voice-select"
                          value={selectedGuest1VoiceId}
                          onChange={(e) => setSelectedGuest1VoiceId(e.target.value)}
                          disabled={voiceOptions.length === 0}
                          style={{
                            background: 'rgba(52, 211, 153, 0.15)', color: 'white',
                            border: '1px solid rgba(52, 211, 153, 0.5)',
                            padding: '6px 10px', borderRadius: '6px', outline: 'none',
                            fontSize: '13px', fontWeight: '600', cursor: 'pointer'
                          }}
                        >
                          <option value="auto" style={{ background: '#1a1a1a', color: '#34d399' }}>
                            ⚡ Tự động chọn giọng khác biệt (Auto Distinct)
                          </option>
                          <VoiceOptions voices={voiceOptions} />
                        </select>
                      </div>

                      {/* Guest 2 Voice */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <label htmlFor="guest2-voice-select" style={{ color: '#f472b6', fontSize: '12px', fontWeight: 'bold' }}>
                          🎙️ Khách 2:
                        </label>
                        <select
                          id="guest2-voice-select"
                          value={selectedGuest2VoiceId}
                          onChange={(e) => setSelectedGuest2VoiceId(e.target.value)}
                          disabled={voiceOptions.length === 0}
                          style={{
                            background: 'rgba(244, 114, 182, 0.15)', color: 'white',
                            border: '1px solid rgba(244, 114, 182, 0.5)',
                            padding: '6px 10px', borderRadius: '6px', outline: 'none',
                            fontSize: '13px', fontWeight: '600', cursor: 'pointer'
                          }}
                        >
                          <option value="auto" style={{ background: '#1a1a1a', color: '#f472b6' }}>
                            ⚡ Tự động chọn giọng phụ khác biệt (Auto Distinct)
                          </option>
                          <VoiceOptions voices={voiceOptions} />
                        </select>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '12px',
                    background: 'rgba(255, 255, 255, 0.03)',
                    padding: '12px 24px', borderRadius: '12px',
                    border: '1px solid rgba(255, 255, 255, 0.1)'
                  }}>
                    <label htmlFor="voice-select" style={{
                      color: '#e0e0e0', fontSize: '15px', fontWeight: '500'
                    }}>
                      🎙️ Giọng đọc:
                    </label>
                    <select
                      id="voice-select"
                      value={selectedVoiceId}
                      onChange={(event) => setSelectedVoiceId(event.target.value)}
                      disabled={voiceOptions.length === 0}
                      style={{
                        background: 'rgba(26, 188, 156, 0.15)', color: 'white',
                        border: '1px solid rgba(26, 188, 156, 0.5)',
                        padding: '8px 14px', borderRadius: '8px', outline: 'none',
                        fontSize: '15px', fontWeight: '600', cursor: 'pointer'
                      }}
                    >
                      <VoiceOptions voices={voiceOptions} />
                    </select>
                  </div>
                )}

                <label style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '8px',
                  cursor: 'pointer',
                  userSelect: 'none',
                  color: publishImmediately ? '#f39c12' : '#bbb',
                  fontWeight: '600',
                  fontSize: '14px',
                  background: publishImmediately ? 'rgba(243, 156, 18, 0.15)' : 'rgba(255, 255, 255, 0.03)',
                  border: publishImmediately ? '1px solid #f39c12' : '1px solid rgba(255, 255, 255, 0.1)',
                  padding: '12px 20px',
                  borderRadius: '12px',
                  transition: 'all 0.2s ease'
                }}>
                  <input
                    type="checkbox"
                    checked={publishImmediately}
                    onChange={(e) => setPublishImmediately(e.target.checked)}
                    style={{ accentColor: '#f39c12', cursor: 'pointer', width: '16px', height: '16px' }}
                  />
                  <span>⚡ Public ngay khi làm xong</span>
                </label>
              </div>

              <div className="input-group">
                <input 
                  type="text" 
                  className="hero-input" 
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !isFetching) handleRun()
                  }}
                />
                <button className="btn-run" onClick={handleRun} disabled={isFetching || !url.trim()}>
                  {isFetching
                    ? 'Đang thêm vào hàng đợi...'
                    : chatGptStatus.busy
                      ? 'Thêm vào hàng đợi ＋'
                      : 'Lên Kịch Bản ⚡'}
                </button>
              </div>

              {progressMsg && (
                <div style={{ color: '#76d7c4', textAlign: 'center', marginTop: '12px' }}>
                  {progressMsg}
                </div>
              )}

              {errorMsg && !showResult && (
                <div style={{ color: '#ff4b4b', textAlign: 'center', marginTop: '12px', background: 'rgba(255,75,75,0.1)', padding: '8px 16px', borderRadius: '6px' }}>
                  ⚠️ {errorMsg}
                </div>
              )}

              <VideoQueuePanel
                refreshKey={queueRefreshKey}
                onOpenJobCenter={() => setActiveView('jobs')}
                onOpenVideo={viewSavedVideo}
              />

              <VideoGenerationProgressCard
                refreshKey={queueRefreshKey}
                onOpenJobCenter={() => setActiveView('jobs')}
                onOpenVideo={viewSavedVideo}
              />

              {showResult && (
                <div className="result-panel">
                  <div className="result-header">
                    <div className="thumbnail-placeholder">▶</div>
                    <div className="video-info">
                      <h3 title={videoTitle}>
                        {videoTitle || 'Nexus Extraction'}
                      </h3>
                      {currentVideoId && (
                        <div style={{
                          display: 'inline-flex', alignItems: 'center', gap: '6px',
                          marginTop: '6px', padding: '3px 10px', borderRadius: '5px',
                          border: '1px solid rgba(155, 89, 182, 0.5)',
                          background: 'rgba(155, 89, 182, 0.15)',
                          color: '#c39bd3', fontSize: '0.8em', fontWeight: '600'
                        }} title="Bộ prompt đã được lưu khi tạo video này">
                          🤖 Bộ prompt của video: {getPromptVersionName(currentVideoPromptVersion)}
                        </div>
                      )}
                      {currentVideoId && (
                        <div style={{
                          display: 'inline-flex', alignItems: 'center', gap: '6px',
                          marginTop: '6px', marginLeft: '8px', padding: '3px 10px',
                          borderRadius: '5px', border: '1px solid rgba(26,188,156,0.5)',
                          background: 'rgba(26,188,156,0.12)', color: '#76d7c4',
                          fontSize: '0.8em', fontWeight: '600'
                        }} title={currentVideoVoiceId || 'Video cũ chưa lưu Voice ID'}>
                          🎙️ Giọng đọc: {getVoiceName(
                            currentVideoVoiceId,
                            currentVideoVoiceName
                          )}
                        </div>
                      )}
                      <div style={{display: 'flex', gap: '10px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'center'}}>
                        <button 
                          onClick={() => setActiveTab('summary')}
                          style={{padding: '4px 12px', borderRadius: '4px', border: '1px solid var(--accent)', background: activeTab === 'summary' ? 'var(--accent)' : 'transparent', color: 'white', cursor: 'pointer'}}
                        >
                          Generated Script
                        </button>
                        <button 
                          onClick={() => setActiveTab('transcript')}
                          style={{padding: '4px 12px', borderRadius: '4px', border: '1px solid var(--accent)', background: activeTab === 'transcript' ? 'var(--accent)' : 'transparent', color: 'white', cursor: 'pointer'}}
                        >
                          Raw Transcript
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('with_text')}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #9b59b6', background: chatGptControlsDisabled ? '#333' : 'rgba(155,89,182,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#c39bd3', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'with_text' ? '⏳ Đang tạo có chữ...' : '🎨 Tạo lại thumbnail có chữ'}
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('without_text')}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #3498db', background: chatGptControlsDisabled ? '#333' : 'rgba(52,152,219,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#85c1e9', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'without_text' ? '⏳ Đang tạo không chữ...' : '🖼️ Tạo lại thumbnail không chữ'}
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('both')}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #e67e22', background: chatGptControlsDisabled ? '#333' : 'rgba(230,126,34,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#f5b041', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'both' ? '⏳ Đang tạo cả 2...' : '🎨 Tạo lại cả 2 thumbnail'}
                        </button>
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                          <button
                            onClick={handleGenerateTitle}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại Tiêu đề chuẩn SEO"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #f39c12',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(243,156,18,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#f39c12',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingTitle ? '⏳ Đang tạo...' : '🏷️ Tiêu đề'}
                          </button>
                          <button
                            onClick={handleGenerateSlug}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại URL Slug để đặt tên file render MP4"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #3498db',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(52,152,219,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#3498db',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingSlug ? '⏳ Đang tạo...' : '🔗 Slug'}
                          </button>
                          <button
                            onClick={handleGenerateDescription}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại đoạn Mô tả video"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #9b59b6',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(155,89,182,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#bb86fc',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingDescription ? '⏳ Đang tạo...' : '📝 Mô tả'}
                          </button>
                          <button
                            onClick={handleGenerateTags}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại Tags & Hashtags"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #1abc9c',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(26,188,156,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#1abc9c',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingTags ? '⏳ Đang tạo...' : '🔖 Tags'}
                          </button>
                          <button
                            onClick={handleGeneratePinnedComment}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại Bình luận ghim kêu gọi tương tác"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #e67e22',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(230,126,34,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#e67e22',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingPinnedComment ? '⏳ Đang tạo...' : '📌 Ghim'}
                          </button>
                          <button
                            onClick={handleGenerateQuiz}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại Quiz tương tác trắc nghiệm"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #e74c3c',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(231,76,60,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#e74c3c',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingQuiz ? '⏳ Đang tạo...' : '❓ Quiz'}
                          </button>
                          <button
                            onClick={handleGenerateChapters}
                            disabled={chatGptControlsDisabled}
                            title="Tạo lại các mốc Chapter phân đoạn"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #16a085',
                              background: chatGptControlsDisabled ? '#333' : 'rgba(22,160,133,0.15)',
                              color: chatGptControlsDisabled ? '#888' : '#48c9b0',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            {isGeneratingChapters ? '⏳ Đang tạo...' : '🕒 Chapters'}
                          </button>
                          <button
                            onClick={handleFetchDescriptionPreview}
                            title="Xem trước nội dung mô tả YouTube sau khi ghép mẫu template"
                            style={{
                              padding: '4px 10px', borderRadius: '4px',
                              cursor: 'pointer',
                              border: '1px solid #4dd0e1',
                              background: 'rgba(77,208,225,0.15)',
                              color: '#4dd0e1',
                              fontWeight: '600', fontSize: '0.82em'
                            }}
                          >
                            👁️ Mẫu mô tả YT
                          </button>
                        </div>
                        {currentVideoId && (
                          <select
                            value={currentVideoIsError ? 'error' : isCurrentVideoPublished ? 'published' : 'unpublished'}
                            onChange={(event) => setVideoLifecycleState({
                              videoId: currentVideoId,
                              videoTitle,
                              videoStatus: currentVideoStatus,
                              isPublished: isCurrentVideoPublished,
                              hasPublishedUrl: currentVideoPublications.length > 0,
                              defaultChannelTitle: currentVideoDefaultChannelTitle,
                              nextState: event.target.value
                            })}
                            aria-label="Trạng thái video đang xem"
                            title="Chuyển thủ công giữa Chưa đăng, Đã đăng và Lỗi"
                            style={{
                              padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold',
                              border: `1px solid ${currentVideoIsError ? '#e74c3c' : isCurrentVideoPublished ? '#4caf50' : '#f39c12'}`,
                              backgroundColor: currentVideoIsError ? 'rgba(231,76,60,0.2)' : isCurrentVideoPublished ? 'rgba(76,175,80,0.2)' : 'rgba(243,156,18,0.2)',
                              color: currentVideoIsError ? '#e74c3c' : isCurrentVideoPublished ? '#4caf50' : '#f39c12'
                            }}
                          >
                            <option value="unpublished" disabled={currentVideoPublications.length > 0}>⏳ Chưa đăng</option>
                            <option value="published">✅ Đã đăng</option>
                            <option value="error">❌ Lỗi</option>
                          </select>
                        )}
                        {currentVideoPublications.map(publication => (
                          <button
                            key={publication.id}
                            type="button"
                            onClick={() => handleOpenWatch(currentVideoId)}
                            style={{
                              padding: '4px 12px', borderRadius: '4px',
                              border: '1px solid #4dd0e1', color: '#4dd0e1',
                              background: 'transparent', cursor: 'pointer',
                              fontWeight: 'bold', fontSize: '0.82em'
                            }}
                            title={publication.youtube_channel_id
                              ? `${publication.channel_title}: ${publication.published_title || publication.published_url} (Bấm để mở trong GPM)`
                              : 'Chưa gắn kênh — link chưa được xác minh; bình luận chưa khả dụng'}
                          >📺 {publication.channel_title || 'Chưa gắn kênh — link chưa được xác minh'}</button>
                        ))}
                        {isCurrentVideoPublished && currentVideoPublications.length === 0 && (
                          <button
                            className="btn-secondary"
                            onClick={() => setPublicationDialog({
                              videoId: currentVideoId,
                              videoTitle: videoTitle || 'Video chưa có tiêu đề',
                              defaultChannelTitle: currentVideoDefaultChannelTitle,
                              publishedUrl: '',
                              isSaving: false,
                              error: ''
                            })}
                            style={{ padding: '4px 12px', fontSize: '0.82em' }}
                          >🔗 Thêm link đã đăng</button>
                        )}
                        {currentVideoId && (!audioUrl || audioStatus === 'failed') && (
                          <button
                            onClick={
                              audioStatus === 'failed'
                                ? handleRetryAudio
                                : handleGenerateAudio
                            }
                            disabled={
                              currentVideoIsError ||
                              isGenAudio ||
                              isLoadingAudioReview ||
                              (audioStatus === 'failed'
                                ? audioReview?.status !== 'approved'
                                : Boolean(audioReview && !audioReview.can_approve))
                            }
                            style={{
                              padding: '4px 14px', borderRadius: '4px',
                              cursor: (currentVideoIsError || isGenAudio || isLoadingAudioReview || Boolean(audioReview && !audioReview.can_approve)) ? 'not-allowed' : 'pointer',
                              border: '1px solid #1abc9c',
                              background: (currentVideoIsError || isGenAudio || isLoadingAudioReview) ? '#333' : 'rgba(26,188,156,0.2)',
                              color: (currentVideoIsError || isGenAudio || isLoadingAudioReview) ? '#888' : '#1abc9c',
                              fontWeight: 'bold'
                            }}
                          >
                            {isGenAudio
                              ? audioStatus === 'interrupted'
                                ? '⏳ Đang tiếp tục Audio...'
                                : '⏳ Đang chờ Genmax...'
                              : audioStatus === 'interrupted'
                                ? `🔄 Tiếp tục Audio${audioMissingSegments ? ` (còn ${audioMissingSegments} phần)` : ''}`
                              : audioStatus === 'failed'
                                ? '⚠️ Retry Audio (sẽ tốn credit)'
                                : audioReview?.status === 'blocked'
                                  ? '⛔ Kịch bản cần sửa'
                                  : '🔎 Kiểm tra tự động & tạo Audio'}
                          </button>
                        )}
                        {chatUrl && currentVideoHasCheckpoint && (
                          <button
                            onClick={handleContinueGeneration}
                            disabled={chatGptControlsDisabled}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #e74c3c', background: chatGptControlsDisabled ? '#333' : 'rgba(231, 76, 60, 0.2)',
                              color: chatGptControlsDisabled ? '#888' : '#e74c3c', fontWeight: 'bold'
                            }}
                            title="Tiếp tục tiến trình nếu bị lỗi giữa chừng"
                          >
                            ▶️ Tiếp tục tạo
                          </button>
                        )}
                        {chatUrl && !currentVideoHasCheckpoint && resultText && audioUrl && resultText.includes('THUMBNAIL KHÔNG CHỮ') && resultText.includes('CHAPTERS') && (
                          <button
                            disabled={true}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'not-allowed',
                              border: '1px solid #27ae60', background: 'rgba(39, 174, 96, 0.2)',
                              color: '#27ae60', fontWeight: 'bold'
                            }}
                            title="Video này đã được tạo xong toàn bộ."
                          >
                            ✅ Đã Hoàn thành
                          </button>
                        )}
                        {chatUrl && !currentVideoHasCheckpoint && resultText && (!resultText.includes('THUMBNAIL KHÔNG CHỮ') || !resultText.includes('CHAPTERS')) && (
                          <button
                            disabled={true}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'not-allowed',
                              border: '1px solid #c0392b', background: 'rgba(192, 57, 43, 0.2)',
                              color: '#c0392b', fontWeight: 'bold'
                            }}
                            title="Quy trình cũ chưa hoàn tất; trạng thái Lỗi chỉ được gắn thủ công."
                          >
                            ⚠️ Chưa hoàn tất
                          </button>
                        )}
                        {chatUrl && (
                          <button
                            onClick={() => window.open(chatUrl, '_blank')}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'pointer',
                              border: '1px solid #2ecc71', background: 'rgba(46, 204, 113, 0.2)',
                              color: '#2ecc71', fontWeight: 'bold'
                            }}
                            title="Mở phiên chat gốc trên ChatGPT"
                          >
                            💬 Mở Chat Gốc ↗
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                  
                  <div className="content-blocks" style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '20px' }}>
                    {activeTab === 'summary' && currentVideoId && (
                      <AudioReviewPanel
                        review={audioReview}
                        loading={isLoadingAudioReview}
                        submitting={isGenAudio}
                        voiceName={getVoiceName(currentVideoVoiceId, currentVideoVoiceName)}
                        hasAudio={Boolean(audioUrl)}
                      />
                    )}

                    {/* Persistent Live Pipeline Progress & Process Control Panel */}
                    {activeTab === 'summary' && currentVideoId && (
                      <div
                        className="result-panel"
                        style={{
                          padding: '16px 20px',
                          background: 'linear-gradient(135deg, rgba(20, 24, 35, 0.85) 0%, rgba(15, 18, 28, 0.95) 100%)',
                          border: '1px solid rgba(77, 208, 225, 0.3)',
                          borderRadius: '10px',
                          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3)',
                          display: 'flex',
                          flexDirection: 'column',
                          gap: '12px'
                        }}
                      >
                        {/* Header of Progress Panel */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            <span style={{ fontSize: '1.2em' }}>⚡</span>
                            <div>
                              <h4 style={{ margin: 0, color: '#4dd0e1', fontSize: '0.95em', fontWeight: '700', letterSpacing: '0.3px' }}>
                                TIẾN TRÌNH & ĐIỀU KHIỂN TÁC VỤ (PIPELINE CONTROL)
                              </h4>
                              <span style={{ fontSize: '0.78em', color: '#888' }}>
                                Theo dõi trạng thái thời gian thực và quản lý dừng các tiến trình tạo lại / dựng video
                              </span>
                            </div>
                          </div>

                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <button
                              onClick={() => setActiveView('jobs')}
                              className="btn-secondary"
                              style={{
                                padding: '4px 12px',
                                fontSize: '0.78em',
                                background: 'rgba(77, 208, 225, 0.1)',
                                borderColor: 'rgba(77, 208, 225, 0.4)',
                                color: '#4dd0e1',
                                borderRadius: '6px',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '5px',
                                cursor: 'pointer'
                              }}
                              title="Mở Trung tâm Job để xem logs chi tiết và quản lý toàn bộ tiến trình hệ thống"
                            >
                              ⚡ Mở Trung tâm Job
                            </button>
                          </div>
                        </div>

                        {/* Live Status Stages Grid */}
                        <div
                          style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                            gap: '10px',
                            marginTop: '4px'
                          }}
                        >
                          {/* 1. Kịch bản & Metadata */}
                          <div
                            style={{
                              background: 'rgba(255, 255, 255, 0.03)',
                              border: '1px solid rgba(255, 255, 255, 0.08)',
                              borderRadius: '8px',
                              padding: '10px 12px'
                            }}
                          >
                            <div style={{ fontSize: '0.76em', color: '#888', fontWeight: '600', marginBottom: '4px' }}>
                              📝 1. KỊCH BẢN & METADATA
                            </div>
                            <div style={{
                              fontSize: '0.85em',
                              fontWeight: '600',
                              color: (generatingThumbnailType !== null || isGeneratingChapters || isGeneratingMetadata || isGeneratingTitle || isGeneratingSlug || isGeneratingDescription || isGeneratingTags || isGeneratingPinnedComment || isGeneratingQuiz) ? '#f5b041' : resultText ? '#2ecc71' : '#888'
                            }}>
                              {(generatingThumbnailType !== null || isGeneratingChapters || isGeneratingMetadata || isGeneratingTitle || isGeneratingSlug || isGeneratingDescription || isGeneratingTags || isGeneratingPinnedComment || isGeneratingQuiz)
                                ? `⏳ Đang tạo ${generatingThumbnailType !== null ? 'Thumbnail' : isGeneratingChapters ? 'Chapters' : isGeneratingMetadata ? 'Metadata' : isGeneratingTitle ? 'Tiêu đề' : isGeneratingSlug ? 'Slug' : isGeneratingDescription ? 'Mô tả' : isGeneratingTags ? 'Tags' : isGeneratingPinnedComment ? 'Ghim' : 'Quiz'}...`
                                : resultText
                                  ? '✅ Kịch bản hoàn tất'
                                  : '⚪ Chưa có kịch bản'}
                            </div>
                          </div>

                          {/* 2. Audio Voice-over */}
                          <div
                            style={{
                              background: isGenAudio ? 'rgba(245, 176, 65, 0.08)' : 'rgba(255, 255, 255, 0.03)',
                              border: isGenAudio ? '1px solid rgba(245, 176, 65, 0.4)' : '1px solid rgba(255, 255, 255, 0.08)',
                              borderRadius: '8px',
                              padding: '10px 12px',
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                              gap: '6px'
                            }}
                          >
                            <div>
                              <div style={{ fontSize: '0.76em', color: '#888', fontWeight: '600', marginBottom: '4px' }}>
                                🎙️ 2. AUDIO VOICE-OVER
                              </div>
                              <div style={{
                                fontSize: '0.85em',
                                fontWeight: '600',
                                color: isGenAudio ? '#f5b041' : (audioUrl || audioStatus === 'completed') ? '#2ecc71' : (audioStatus === 'failed' || audioStatus === 'interrupted') ? '#ff6b6b' : '#888'
                              }}>
                                {isGenAudio
                                  ? `⏳ Đang tạo audio... ${audioMissingSegments > 0 ? `(còn ${audioMissingSegments} đoạn)` : ''}`
                                  : (audioUrl || audioStatus === 'completed')
                                    ? '✅ Audio đã hoàn thành'
                                    : (audioStatus === 'failed' || audioStatus === 'interrupted')
                                      ? '❌ Lỗi tạo audio'
                                      : '⚪ Chưa tạo audio'}
                              </div>
                            </div>
                            {isGenAudio && (
                              <div style={{ marginTop: '4px' }}>
                                <button
                                  onClick={handleCancelAudio}
                                  disabled={isCancelingAudio}
                                  style={{
                                    background: '#c0392b',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    padding: '4px 10px',
                                    fontSize: '0.78em',
                                    fontWeight: 'bold',
                                    cursor: isCancelingAudio ? 'not-allowed' : 'pointer',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '4px'
                                  }}
                                  title="Dừng tiến trình tạo audio này ngay lập tức"
                                >
                                  {isCancelingAudio ? '⏳ Đang dừng...' : '⏹️ Dừng tạo Audio'}
                                </button>
                              </div>
                            )}
                          </div>

                          {/* 3. Render MP4 */}
                          <div
                            style={{
                              background: isRendering ? 'rgba(183, 148, 246, 0.08)' : 'rgba(255, 255, 255, 0.03)',
                              border: isRendering ? '1px solid rgba(183, 148, 246, 0.4)' : '1px solid rgba(255, 255, 255, 0.08)',
                              borderRadius: '8px',
                              padding: '10px 12px',
                              display: 'flex',
                              flexDirection: 'column',
                              justifyContent: 'space-between',
                              gap: '6px'
                            }}
                          >
                            <div>
                              <div style={{ fontSize: '0.76em', color: '#888', fontWeight: '600', marginBottom: '4px' }}>
                                🎬 3. DỰNG VIDEO MP4
                              </div>
                              <div style={{
                                fontSize: '0.85em',
                                fontWeight: '600',
                                color: isRendering ? '#b794f6' : renderInfo?.has_mp4 ? '#2ecc71' : (renderInfo?.job?.status === 'error' || renderInfo?.job?.status === 'failed') ? '#ff6b6b' : (renderInfo?.job?.status === 'canceled') ? '#f39c12' : '#888'
                              }}>
                                {isRendering
                                  ? `⏳ ${renderInfo?.job?.progress || 'Đang dựng MP4 & Flow...'}`
                                  : renderInfo?.has_mp4
                                    ? '✅ MP4 đã dựng xong (1080p)'
                                    : renderInfo?.job?.status === 'canceled'
                                      ? '⏹️ Đã dừng dựng'
                                      : (renderInfo?.job?.status === 'error' || renderInfo?.job?.status === 'failed')
                                        ? '❌ Lỗi dựng MP4'
                                        : '⚪ Chưa dựng MP4'}
                              </div>
                            </div>
                            {isRendering && (
                              <div style={{ marginTop: '4px' }}>
                                <button
                                  onClick={handleCancelRenderVideo}
                                  disabled={isCancelingRender}
                                  style={{
                                    background: '#c0392b',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    padding: '4px 10px',
                                    fontSize: '0.78em',
                                    fontWeight: 'bold',
                                    cursor: isCancelingRender ? 'not-allowed' : 'pointer',
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '4px'
                                  }}
                                  title="Dừng tiến trình dựng video này ngay lập tức"
                                >
                                  {isCancelingRender ? '⏳ Đang dừng...' : '⏹️ Dừng Render'}
                                </button>
                              </div>
                            )}
                          </div>

                          {/* 4. Trạng thái Xuất bản */}
                          <div
                            style={{
                              background: 'rgba(255, 255, 255, 0.03)',
                              border: '1px solid rgba(255, 255, 255, 0.08)',
                              borderRadius: '8px',
                              padding: '10px 12px'
                            }}
                          >
                            <div style={{ fontSize: '0.76em', color: '#888', fontWeight: '600', marginBottom: '4px' }}>
                              🚀 4. TRẠNG THÁI XUẤT BẢN
                            </div>
                            <div style={{
                              fontSize: '0.85em',
                              fontWeight: '600',
                              color: isCurrentVideoPublished ? '#2ecc71' : '#4dd0e1'
                            }}>
                              {isCurrentVideoPublished ? '✅ Đã xuất bản' : '⏳ Chưa đăng / Sẵn sàng'}
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                    {errorMsg ? (
                      <span style={{color: '#ff4b4b'}}>{errorMsg}</span>
                    ) : (
                      <>
                        {activeTab === 'summary' && audioUrl && (
                          <div className="result-panel" style={{ padding: '20px' }}>
                            <div style={{
                              display: 'flex', justifyContent: 'space-between',
                              alignItems: 'center', marginBottom: '10px',
                              gap: '12px', flexWrap: 'wrap'
                            }}>
                              <div>
                                <h4 style={{color: 'var(--accent)', margin: 0}}>🔊 AI Voice-over:</h4>
                                <div style={{ color: '#76d7c4', fontSize: '0.8em', marginTop: '5px' }}>
                                  🎙️ Đang sử dụng: [{voiceProviderName(currentVideoProviderId)}] {getVoiceName(
                                    currentVideoVoiceId,
                                    currentVideoVoiceName
                                  )}
                                </div>
                                {audioTaskVoiceName &&
                                  ['pending', 'processing', 'interrupted', 'failed'].includes(audioStatus) &&
                                  audioTaskVoiceName !== currentVideoVoiceName && (
                                    <div style={{ color: '#f5b041', fontSize: '0.78em', marginTop: '3px' }}>
                                      ⏳ Audio mới: [{voiceProviderName(audioTaskProviderId)}] {audioTaskVoiceName} ({audioStatus})
                                    </div>
                                  )}
                              </div>
                              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                                {((resultText || '').includes('[MC]:') || (resultText || '').includes('[KHACH_1]:') || audioRoleStreams) ? (
                                  <div style={{
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: '10px',
                                    background: 'rgba(15, 23, 42, 0.65)',
                                    padding: '12px 14px',
                                    borderRadius: '10px',
                                    border: '1px solid rgba(56, 189, 248, 0.25)',
                                    width: '100%',
                                    marginBottom: '6px'
                                  }}>
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
                                      <span style={{ color: '#38bdf8', fontSize: '12px', fontWeight: 'bold', display: 'flex', alignItems: 'center', gap: '6px' }}>
                                        👥 Quản Lý Luồng Audio Theo Nhân Vật (Dual-Stream Pipeline):
                                      </span>
                                      <span style={{ fontSize: '11px', color: '#94a3b8' }}>
                                        Tách luồng độc lập • Tự động ghép Master
                                      </span>
                                    </div>

                                    {/* Role Cards Grid */}
                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '10px' }}>
                                      {/* MC Stream Card */}
                                      <div style={{
                                        background: 'rgba(251, 191, 36, 0.05)',
                                        border: '1px solid rgba(251, 191, 36, 0.3)',
                                        borderRadius: '8px',
                                        padding: '10px',
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: '6px'
                                      }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                          <span style={{ color: '#fbbf24', fontSize: '12px', fontWeight: 'bold' }}>
                                            🎙️ MC (Người dẫn)
                                          </span>
                                          {audioRoleStreams?.MC && (
                                            <span style={{
                                              fontSize: '11px',
                                              padding: '2px 6px',
                                              borderRadius: '4px',
                                              background: audioRoleStreams.MC.status === 'completed' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(56, 189, 248, 0.2)',
                                              color: audioRoleStreams.MC.status === 'completed' ? '#34d399' : '#38bdf8',
                                              fontWeight: '600'
                                            }}>
                                              {audioRoleStreams.MC.status === 'completed' ? '✅ Hoàn tất' : `⚡ ${audioRoleStreams.MC.percent}%`}
                                            </span>
                                          )}
                                        </div>
                                        <select
                                          value={selectedMcVoiceId || regenerateVoiceId}
                                          onChange={(e) => {
                                            setSelectedMcVoiceId(e.target.value);
                                            setRegenerateVoiceId(e.target.value);
                                          }}
                                          disabled={currentVideoIsError || isGenAudio || voiceOptions.length === 0}
                                          style={{
                                            background: '#17131d', color: '#eee',
                                            border: '1px solid rgba(251, 191, 36, 0.4)',
                                            padding: '4px 8px', borderRadius: '6px', fontSize: '12px', width: '100%'
                                          }}
                                        >
                                          <VoiceOptions voices={voiceOptions} />
                                        </select>
                                        {audioRoleStreams?.MC && (
                                          <div style={{ fontSize: '11px', color: '#cbd5e1', display: 'flex', justifyContent: 'space-between' }}>
                                            <span>Đoạn: {audioRoleStreams.MC.completed}/{audioRoleStreams.MC.total}</span>
                                            <span>{audioRoleStreams.MC.percent}%</span>
                                          </div>
                                        )}
                                        <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginTop: '2px' }}>
                                          <button
                                            className="btn-secondary"
                                            style={{ padding: '3px 8px', fontSize: '11px', flex: 1 }}
                                            onClick={() => handleRegenerateRoleAudio('MC', selectedMcVoiceId || regenerateVoiceId)}
                                            disabled={currentVideoIsError || isGenAudio}
                                            title="Tạo lại chỉ riêng vai MC, giữ nguyên audio của Khách"
                                          >
                                            🔄 Tạo lại vai MC
                                          </button>
                                          {audioRoleStreams?.MC?.preview_audio_url && (
                                            <audio
                                              controls
                                              src={audioRoleStreams.MC.preview_audio_url}
                                              style={{ height: '24px', maxWidth: '130px' }}
                                              title="Nghe thử riêng giọng MC"
                                            />
                                          )}
                                        </div>
                                      </div>

                                      {/* Guest 1 Stream Card */}
                                      <div style={{
                                        background: 'rgba(52, 211, 153, 0.05)',
                                        border: '1px solid rgba(52, 211, 153, 0.3)',
                                        borderRadius: '8px',
                                        padding: '10px',
                                        display: 'flex',
                                        flexDirection: 'column',
                                        gap: '6px'
                                      }}>
                                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                          <span style={{ color: '#34d399', fontSize: '12px', fontWeight: 'bold' }}>
                                            🎙️ Khách 1 (Khách mời)
                                          </span>
                                          {audioRoleStreams?.KHACH_1 && (
                                            <span style={{
                                              fontSize: '11px',
                                              padding: '2px 6px',
                                              borderRadius: '4px',
                                              background: audioRoleStreams.KHACH_1.status === 'completed' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(56, 189, 248, 0.2)',
                                              color: audioRoleStreams.KHACH_1.status === 'completed' ? '#34d399' : '#38bdf8',
                                              fontWeight: '600'
                                            }}>
                                              {audioRoleStreams.KHACH_1.status === 'completed' ? '✅ Hoàn tất' : `⚡ ${audioRoleStreams.KHACH_1.percent}%`}
                                            </span>
                                          )}
                                        </div>
                                        <select
                                          value={selectedGuest1VoiceId}
                                          onChange={(e) => setSelectedGuest1VoiceId(e.target.value)}
                                          disabled={currentVideoIsError || isGenAudio || voiceOptions.length === 0}
                                          style={{
                                            background: '#17131d', color: '#eee',
                                            border: '1px solid rgba(52, 211, 153, 0.4)',
                                            padding: '4px 8px', borderRadius: '6px', fontSize: '12px', width: '100%'
                                          }}
                                        >
                                          <option value="auto" style={{ background: '#1a1a1a', color: '#34d399' }}>
                                            ⚡ Tự động chọn giọng khác biệt
                                          </option>
                                          <VoiceOptions voices={voiceOptions} />
                                        </select>
                                        {audioRoleStreams?.KHACH_1 && (
                                          <div style={{ fontSize: '11px', color: '#cbd5e1', display: 'flex', justifyContent: 'space-between' }}>
                                            <span>Đoạn: {audioRoleStreams.KHACH_1.completed}/{audioRoleStreams.KHACH_1.total}</span>
                                            <span>{audioRoleStreams.KHACH_1.percent}%</span>
                                          </div>
                                        )}
                                        <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginTop: '2px' }}>
                                          <button
                                            className="btn-secondary"
                                            style={{ padding: '3px 8px', fontSize: '11px', flex: 1 }}
                                            onClick={() => handleRegenerateRoleAudio('KHACH_1', selectedGuest1VoiceId)}
                                            disabled={currentVideoIsError || isGenAudio}
                                            title="Tạo lại chỉ riêng vai Khách 1, giữ nguyên audio của MC"
                                          >
                                            🔄 Tạo lại vai Khách 1
                                          </button>
                                          {audioRoleStreams?.KHACH_1?.preview_audio_url && (
                                            <audio
                                              controls
                                              src={audioRoleStreams.KHACH_1.preview_audio_url}
                                              style={{ height: '24px', maxWidth: '130px' }}
                                              title="Nghe thử riêng giọng Khách 1"
                                            />
                                          )}
                                        </div>
                                      </div>
                                    </div>

                                    {/* Action Buttons Bar */}
                                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', paddingTop: '6px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                                      <button
                                        className="btn-secondary"
                                        style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                        onClick={handleRegenerateAudio}
                                        disabled={currentVideoIsError || isGenAudio || !regenerateVoiceId}
                                      >
                                        {isGenAudio ? '⏳ Audio đang chạy...' : '🎙️ Tạo lại tất cả các vai'}
                                      </button>
                                      <button
                                        className="btn-secondary"
                                        style={{ padding: '4px 12px', fontSize: '0.8em', background: 'rgba(56, 189, 248, 0.15)', border: '1px solid rgba(56, 189, 248, 0.4)', color: '#38bdf8' }}
                                        onClick={handleMergeMasterAudio}
                                        disabled={currentVideoIsError || isGenAudio}
                                        title="Ghép các clip của các vai thành audio master hoàn chỉnh"
                                      >
                                        🔄 Ghép lại Audio Tổng
                                      </button>
                                    </div>
                                  </div>
                                ) : (
                                  <>
                                    <select
                                      value={regenerateVoiceId}
                                      onChange={(event) => setRegenerateVoiceId(event.target.value)}
                                      disabled={currentVideoIsError || isGenAudio || voiceOptions.length === 0}
                                      aria-label="Giọng tạo lại audio"
                                      style={{
                                        background: '#17131d', color: '#eee',
                                        border: '1px solid rgba(26,188,156,0.5)',
                                        borderRadius: '6px', padding: '5px 9px'
                                      }}
                                    >
                                      <VoiceOptions voices={voiceOptions} />
                                    </select>
                                    <button
                                      className="btn-secondary"
                                      style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                      onClick={handleRegenerateAudio}
                                      disabled={currentVideoIsError || isGenAudio || !regenerateVoiceId}
                                    >
                                      {isGenAudio ? '⏳ Audio đang chạy...' : '🎙️ Tạo lại toàn bộ'}
                                    </button>
                                  </>
                                )}
                                {isGenAudio && (
                                  <button
                                    onClick={handleCancelAudio}
                                    disabled={isCancelingAudio}
                                    style={{
                                      padding: '4px 12px',
                                      fontSize: '0.8em',
                                      background: '#c0392b',
                                      border: '1px solid #e74c3c',
                                      color: 'white',
                                      borderRadius: '6px',
                                      cursor: isCancelingAudio ? 'not-allowed' : 'pointer',
                                      fontWeight: 'bold',
                                      display: 'flex',
                                      alignItems: 'center',
                                      gap: '4px'
                                    }}
                                    title="Dừng tiến trình tạo audio đang chạy"
                                  >
                                    {isCancelingAudio ? '⏳ Đang dừng...' : '⏹️ Dừng tạo Audio'}
                                  </button>
                                )}
                                <button
                                  className="btn-secondary"
                                  style={{ padding: '4px 12px', fontSize: '0.8em', display: 'flex', alignItems: 'center', gap: '5px' }}
                                  onClick={handleDownloadAudio}
                                >
                                  📥 Tải Audio MP3
                                </button>
                              </div>
                            </div>
                            <audio
                              controls
                              src={audioUrl}
                              style={{width: '100%'}}
                              onLoadedMetadata={(event) => {
                                const durationSeconds = event.currentTarget.duration
                                if (
                                  currentVideoId &&
                                  Number.isFinite(durationSeconds) &&
                                  durationSeconds > 0
                                ) {
                                  saveAudioDuration(currentVideoId, durationSeconds)
                                    .catch(error => console.error('Failed to save audio duration', error))
                                }
                              }}
                            />

                            {/* MP4 Render & Video Preview */}
                            <div style={{ marginTop: '16px', paddingTop: '14px', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
                              <div style={{
                                display: 'flex', justifyContent: 'space-between',
                                alignItems: 'center', marginBottom: '8px',
                                gap: '10px', flexWrap: 'wrap'
                              }}>
                                <div>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                    <h4 style={{ color: 'var(--accent)', margin: 0 }}>🎬 Video MP4 (Google Flow & Subtitles):</h4>
                                    {renderInfo?.has_mp4 && (
                                      <div className="media-badge-group">
                                        <span className="media-pill-badge">1080p FHD</span>
                                        <span className="media-pill-badge">30 FPS</span>
                                        <span className="media-pill-badge">16:9</span>
                                      </div>
                                    )}
                                  </div>
                                  <div style={{
                                    color: renderInfo?.has_mp4 ? '#2ecc71' : isRendering ? '#f5b041' : (renderInfo?.job?.status === 'error' || renderInfo?.job?.status === 'failed' || renderInfo?.job?.status === 'canceled') ? '#e74c3c' : '#aaa',
                                    fontSize: '0.82em',
                                    marginTop: '4px'
                                  }}>
                                    {renderInfo?.has_mp4
                                      ? '✅ Video MP4 đã render hoàn tất'
                                      : isRendering
                                        ? `⏳ ${renderInfo?.job?.progress || (renderInfo?.job?.status === 'running' ? 'Đang tạo ảnh Flow & render MP4...' : 'Đang trong hàng đợi render...')}`
                                        : renderInfo?.job?.status === 'canceled'
                                          ? `⏹️ Đã dừng: ${renderInfo?.job?.progress || renderInfo?.job?.error || 'Tác vụ dựng video đã dừng'}`
                                          : (renderInfo?.job?.status === 'error' || renderInfo?.job?.status === 'failed')
                                            ? `❌ Lỗi: ${renderInfo?.job?.error || 'Render thất bại'}`
                                            : 'Chưa dựng video MP4'}
                                  </div>
                                </div>
                                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                  {isRendering ? (
                                    <>
                                      <button
                                        className="btn-primary"
                                        disabled={true}
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.82em',
                                          background: '#333',
                                          cursor: 'not-allowed',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          border: 'none',
                                          color: '#aaa'
                                        }}
                                      >
                                        ⏳ Đang dựng...
                                      </button>
                                      <button
                                        className="btn-secondary"
                                        onClick={handleCancelRenderVideo}
                                        disabled={isCancelingRender}
                                        title="Dừng tác vụ dựng video MP4 hiện tại"
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.82em',
                                          background: 'rgba(231, 76, 60, 0.2)',
                                          border: '1px solid #e74c3c',
                                          color: '#ff6b6b',
                                          cursor: isCancelingRender ? 'not-allowed' : 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                      >
                                        {isCancelingRender ? '⏳ Đang dừng...' : '⏹️ Dừng'}
                                      </button>
                                    </>
                                  ) : (renderInfo?.job?.status === 'error' || renderInfo?.job?.status === 'failed' || renderInfo?.job?.status === 'canceled') ? (
                                    <>
                                      <button
                                        className="btn-primary"
                                        onClick={() => handleRenderVideo('resume')}
                                        disabled={currentVideoIsError}
                                        title="Dùng lại project cũ trên Google Flow và tiếp tục tạo các cảnh còn thiếu"
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.82em',
                                          background: 'linear-gradient(135deg, #1abc9c, #16a085)',
                                          cursor: 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          border: 'none',
                                          color: '#fff',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                      >
                                        ▶️ Tạo tiếp
                                      </button>
                                      <button
                                        className="btn-secondary"
                                        onClick={() => setSceneResetDialog({
                                          videoId: currentVideoId,
                                          videoTitle: videoTitle || `Video #${currentVideoId}`,
                                          fromSceneNumber: 17,
                                          isSubmitting: false,
                                          error: ''
                                        })}
                                        disabled={currentVideoIsError}
                                        title="Xóa và tạo lại từ một phân cảnh bất kỳ"
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.82em',
                                          background: 'rgba(241, 196, 15, 0.15)',
                                          border: '1px solid #f1c40f',
                                          color: '#f1c40f',
                                          cursor: 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                      >
                                        🎯 Tạo lại từ cảnh...
                                      </button>
                                      <button
                                        className="btn-secondary"
                                        onClick={() => handleRenderVideo('recreate')}
                                        disabled={currentVideoIsError}
                                        title="Tạo mới một project trên Google Flow và tạo lại toàn bộ từ cảnh 1"
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.82em',
                                          background: 'rgba(231, 76, 60, 0.15)',
                                          border: '1px solid #e74c3c',
                                          color: '#ff6b6b',
                                          cursor: 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                      >
                                        🔄 Tạo lại toàn bộ
                                      </button>
                                    </>
                                  ) : !renderInfo?.has_mp4 ? (
                                    <button
                                      className="btn-primary"
                                      onClick={() => handleRenderVideo('resume')}
                                      disabled={currentVideoIsError}
                                      style={{
                                        padding: '4px 14px',
                                        fontSize: '0.82em',
                                        background: 'linear-gradient(135deg, #1abc9c, #16a085)',
                                        cursor: 'pointer',
                                        fontWeight: 'bold',
                                        borderRadius: '4px',
                                        border: 'none',
                                        color: '#fff'
                                      }}
                                    >
                                      🎬 Dựng video MP4
                                    </button>
                                  ) : null}
                                  {renderInfo?.has_mp4 && (
                                    <>
                                      <button
                                        className="btn-secondary"
                                        onClick={() => setSceneResetDialog({
                                          videoId: currentVideoId,
                                          videoTitle: videoTitle || `Video #${currentVideoId}`,
                                          fromSceneNumber: 1,
                                          isSubmitting: false,
                                          error: ''
                                        })}
                                        disabled={isRendering || currentVideoIsError}
                                        style={{
                                          padding: '4px 12px',
                                          fontSize: '0.8em',
                                          background: 'rgba(241, 196, 15, 0.15)',
                                          border: '1px solid #f1c40f',
                                          color: '#f1c40f',
                                          cursor: 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                        title="Xóa và tạo lại từ một phân cảnh bất kỳ"
                                      >
                                        🎯 Tạo lại từ cảnh...
                                      </button>
                                      <button
                                        className="btn-secondary"
                                        onClick={() => handleRenderVideo('recreate')}
                                        disabled={isRendering || currentVideoIsError}
                                        style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                        title="Tạo mới 1 project trên Google Flow và dựng lại toàn bộ từ cảnh 1"
                                      >
                                        {isRendering ? '⏳ Đang dựng...' : '🔄 Tạo lại toàn bộ'}
                                      </button>
                                      <a
                                        href={`http://127.0.0.1:8080/api/videos/${currentVideoId}/download-mp4`}
                                        download={`video_${currentVideoId}.mp4`}
                                        className="btn-secondary"
                                        style={{
                                          padding: '4px 12px',
                                          fontSize: '0.8em',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '5px',
                                          background: 'rgba(39, 174, 96, 0.2)',
                                          border: '1px solid #27ae60',
                                          color: '#2ecc71',
                                          textDecoration: 'none',
                                          borderRadius: '4px',
                                          fontWeight: 'bold'
                                        }}
                                      >
                                        📥 Tải Video MP4
                                      </a>
                                      <button
                                        className="btn-primary"
                                        onClick={() => handlePublishNow(currentVideoId)}
                                        disabled={isPublishingNow || currentVideoIsError}
                                        style={{
                                          padding: '4px 14px',
                                          fontSize: '0.8em',
                                          background: 'linear-gradient(135deg, #e67e22, #d35400)',
                                          border: 'none',
                                          color: '#fff',
                                          cursor: isPublishingNow ? 'not-allowed' : 'pointer',
                                          fontWeight: 'bold',
                                          borderRadius: '4px',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: '4px'
                                        }}
                                        title="Tải lên và Public ngay lập tức lên YouTube"
                                      >
                                        {isPublishingNow ? '⏳ Đang Public...' : '⚡ Public ngay'}
                                      </button>
                                    </>
                                  )}
                                </div>
                              </div>
                              {renderInfo?.has_mp4 && (
                                <div className="video-player-wrapper">
                                  <video
                                    controls
                                    className="video-player-element"
                                    src={`http://127.0.0.1:8080/api/videos/${currentVideoId}/download-mp4`}
                                  />
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                        
                        {activeTab === 'summary' ? (
                          parsedSections.map((section, idx) => {
                            const isMainContent = section.title === 'NỘI DUNG KỊCH BẢN' || section.title === 'KỊCH BẢN CHÍNH';
                            const isThumbnail = section.title.toUpperCase().includes('THUMBNAIL');

                            // Parse [IMAGE_URL:...] from content
                            let displayContent = section.content;
                            let extractedImages = [];
                            const imgRegex = /\[IMAGE_URL:(.*?)\]/g;
                            let match;
                            while ((match = imgRegex.exec(section.content)) !== null) {
                              extractedImages.push(match[1]);
                            }
                            displayContent = displayContent.replace(/\[IMAGE_URL:.*?\]/g, '').trim();

                            if (isThumbnail) {
                              return (
                                <div key={idx} className="result-panel" style={{ padding: '20px', margin: 0 }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                                    <h4 style={{ color: 'var(--accent)', margin: 0 }}>🖼️ {section.title}</h4>
                                    <button
                                      id={`copy-btn-${idx}`}
                                      className="btn-secondary"
                                      style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                      onClick={() => handleCopySection(displayContent, `copy-btn-${idx}`)}
                                    >Copy Prompt</button>
                                  </div>

                                  {extractedImages.length > 0 ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '15px' }}>
                                      {extractedImages.map((img, i) => {
                                        const imgSrc = img.startsWith('/api/') ? `http://127.0.0.1:8080${img}` : img;
                                        const isWithoutText = section.title.toUpperCase().includes('KHÔNG CHỮ');
                                        const fileName = `${isWithoutText ? 'thumbnail-khong-chu' : 'thumbnail-co-chu'}-${i + 1}.png`;
                                        return (
                                        <div key={i} style={{ position: 'relative' }}>
                                          {extractedImages.length > 1 && (
                                            <div style={{ color: '#bbb', fontWeight: 600, marginBottom: '7px' }}>
                                              Phương án {i + 1}
                                            </div>
                                          )}
                                          <img
                                            src={imgSrc}
                                            alt={`${section.title} - Phương án ${i + 1}`}
                                            crossOrigin="use-credentials"
                                            style={{ width: '100%', borderRadius: '10px', border: '2px solid var(--accent)', display: 'block' }}
                                          />
                                          <a
                                            href={imgSrc}
                                            target="_blank"
                                            rel="noreferrer"
                                            style={{
                                              position: 'absolute', bottom: '10px', right: '10px',
                                              background: 'rgba(0,0,0,0.7)', color: 'white',
                                              padding: '5px 10px', borderRadius: '6px', fontSize: '0.8em',
                                              textDecoration: 'none'
                                            }}
                                          >↗ Mở ảnh gốc</a>
                                          <button
                                            onClick={(e) => {
                                              handleDownloadImage(imgSrc, fileName, e);
                                            }}
                                            style={{
                                              position: 'absolute', bottom: '10px', left: '10px',
                                              background: 'rgba(155,89,182,0.85)', color: 'white',
                                              padding: '5px 10px', borderRadius: '6px', fontSize: '0.8em',
                                              textDecoration: 'none',
                                              border: 'none', cursor: 'pointer'
                                            }}
                                          >⬇ Tải về</button>
                                        </div>
                                        );
                                      })}
                                    </div>
                                  ) : (
                                    <div style={{ background: '#1a1a1a', borderRadius: '10px', padding: '40px', textAlign: 'center', color: '#666', marginBottom: '15px' }}>
                                      🎨 Chưa có ảnh thumbnail
                                    </div>
                                  )}

                                  <details style={{ marginTop: '8px' }}>
                                    <summary style={{ cursor: 'pointer', color: '#888', fontSize: '0.85em', userSelect: 'none' }}>
                                      📝 Xem prompt chi tiết...
                                    </summary>
                                    <div style={{ whiteSpace: 'pre-wrap', color: '#aaa', lineHeight: '1.5', marginTop: '10px', fontSize: '0.85em', background: '#111', padding: '12px', borderRadius: '6px' }}>
                                      {displayContent}
                                    </div>
                                  </details>
                                </div>
                              );
                            }

                            return (
                              <div key={idx} className={isMainContent ? "transcript-area" : "result-panel"} style={{ padding: '20px', position: 'relative', margin: 0, backgroundColor: isMainContent ? 'rgba(0,0,0,0.3)' : '' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                                  <h4 style={{ color: 'var(--accent)', margin: 0 }}>{section.title}</h4>
                                  <button 
                                    id={`copy-btn-${idx}`}
                                    className="btn-secondary" 
                                    style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                    onClick={() => handleCopySection(displayContent, `copy-btn-${idx}`)}
                                  >
                                    Copy
                                  </button>
                                </div>
                                <div style={{ whiteSpace: 'pre-wrap', color: '#e0e0e0', lineHeight: '1.6' }}>
                                  {displayContent}
                                </div>
                                {extractedImages.length > 0 && (
                                  <div style={{ marginTop: '15px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                    {extractedImages.map((img, i) => (
                                      <img key={i} src={img} alt="Generated Thumbnail" style={{maxWidth: '100%', borderRadius: '8px', border: '1px solid #444'}} />
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })
                        ) : (
                          <div className="transcript-area" style={{ position: 'relative' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                              <h4 style={{ color: 'var(--accent)', margin: 0 }}>RAW TRANSCRIPT</h4>
                              <button 
                                id="copy-btn-transcript"
                                className="btn-secondary" 
                                style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                onClick={() => handleCopySection(fullTranscript, 'copy-btn-transcript')}
                              >
                                Copy All
                              </button>
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', color: '#e0e0e0', lineHeight: '1.6' }}>
                              {displayTranscript}
                            </div>
                          </div>
                        )}

                        {activeTab === 'summary' && imageUrl && (
                          <div className="result-panel" style={{ padding: '20px' }}>
                            <h4 style={{color: 'var(--accent)', marginBottom: '15px'}}>🖼️ AI Generated Thumbnail</h4>
                            <img src={imageUrl} alt="DALL-E Thumbnail" style={{maxWidth: '100%', borderRadius: '8px', border: '1px solid #444'}} />
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  
                  {activeTab === 'summary' && (
                    <div className="actions" style={{ marginTop: '20px', justifyContent: 'center' }}>
                      <button className="btn-run" onClick={handleExportTxt} style={{ width: '100%' }}>Download All (TXT)</button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </main>
      {showPreviewModal && (
        <div
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setShowPreviewModal(false);
            }
          }}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '20px', background: 'rgba(0, 0, 0, 0.75)'
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="preview-modal-title"
            style={{
              width: 'min(680px, 100%)', maxHeight: '85vh', display: 'flex', flexDirection: 'column',
              padding: '24px', borderRadius: '14px',
              border: '1px solid rgba(77, 208, 225, 0.5)', background: '#151218',
              boxShadow: '0 24px 80px rgba(0, 0, 0, 0.65)'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <h3 id="preview-modal-title" style={{ margin: 0, color: '#4dd0e1', display: 'flex', alignItems: 'center', gap: '8px' }}>
                👁️ Mô tả YouTube thực tế (Description Preview)
              </h3>
              <button
                type="button"
                onClick={() => setShowPreviewModal(false)}
                style={{ background: 'transparent', border: 'none', color: '#aaa', fontSize: '1.2rem', cursor: 'pointer' }}
              >✕</button>
            </div>
            <p style={{ margin: '0 0 12px', color: '#aaa', fontSize: '0.85rem' }}>
              Nội dung mô tả thực tế sẽ được đưa lên YouTube theo Description Template của bộ prompt.
            </p>
            <div style={{ flex: 1, overflowY: 'auto', marginBottom: '16px' }}>
              {isLoadingPreviewDescription ? (
                <div style={{ padding: '30px', textAlign: 'center', color: '#888' }}>
                  ⏳ Đang nạp bản xem trước mô tả...
                </div>
              ) : (
                <textarea
                  readOnly
                  rows={14}
                  value={previewDescription}
                  style={{
                    width: '100%', boxSizing: 'border-box', padding: '12px',
                    borderRadius: '8px', border: '1px solid #333',
                    background: '#1f1b24', color: '#eee', fontSize: '0.9rem',
                    lineHeight: '1.5', fontFamily: 'inherit', resize: 'vertical'
                  }}
                />
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  navigator.clipboard.writeText(previewDescription);
                  setCopiedPreview(true);
                  setTimeout(() => setCopiedPreview(false), 2500);
                }}
                disabled={!previewDescription || isLoadingPreviewDescription}
              >
                {copiedPreview ? '✓ Đã sao chép' : '📋 Sao chép mô tả'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowPreviewModal(false)}
              >
                Đóng
              </button>
            </div>
          </div>
        </div>
      )}

      {publicationDialog && (
        <div
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !publicationDialog.isSaving) {
              setPublicationDialog(null);
            }
          }}
          style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '20px', background: 'rgba(0, 0, 0, 0.72)'
          }}
        >
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="publication-dialog-title"
            onSubmit={submitVideoPublication}
            style={{
              width: 'min(560px, 100%)', padding: '24px', borderRadius: '14px',
              border: '1px solid rgba(155, 89, 182, 0.6)', background: '#151218',
              boxShadow: '0 24px 80px rgba(0, 0, 0, 0.55)'
            }}
          >
            <h3 id="publication-dialog-title" style={{ margin: '0 0 8px', color: '#fff' }}>
              Xác nhận video đã đăng / đã lên lịch
            </h3>
            <p style={{ margin: '0 0 16px', color: '#aaa', lineHeight: 1.45 }}>
              {publicationDialog.videoTitle}
            </p>
            <div style={{ marginBottom: '14px', color: publicationDialog.defaultChannelTitle ? '#76d7c4' : '#f5b041' }}>
              📺 Kênh: {publicationDialog.defaultChannelTitle || 'Chưa gắn kênh — link chưa được xác minh'}
            </div>
            <label htmlFor="published-video-url" style={{ display: 'block', marginBottom: '8px', color: '#ddd', fontWeight: 600 }}>
              Link chuẩn của video đã đăng hoặc đã lên lịch
            </label>
            <input
              id="published-video-url"
              type="url"
              autoFocus
              required
              value={publicationDialog.publishedUrl}
              onChange={(event) => setPublicationDialog(dialog => ({
                ...dialog,
                publishedUrl: event.target.value,
                error: ''
              }))}
              placeholder="https://www.youtube.com/watch?v=..."
              disabled={publicationDialog.isSaving}
              style={{
                width: '100%', boxSizing: 'border-box', padding: '12px 14px',
                borderRadius: '8px', border: '1px solid #5b4768',
                background: '#211c24', color: '#fff', fontSize: '0.95em'
              }}
            />
            {publicationDialog.error && (
              <p role="alert" style={{ margin: '12px 0 0', color: '#ff6b6b', lineHeight: 1.4 }}>
                {publicationDialog.error}
              </p>
            )}
            <p style={{ margin: '12px 0 0', color: '#888', fontSize: '0.85em', lineHeight: 1.4 }}>
              {publicationDialog.defaultChannelTitle
                ? 'Video đặt lịch được kiểm tra ngay bằng quyền chủ kênh, dù chưa công khai. Hệ thống chống trùng và chỉ đổi trạng thái sau khi xác nhận đúng kênh.'
                : 'Link sẽ được lưu chưa xác minh và video vẫn chuyển sang Đã đăng. Bình luận chưa khả dụng cho đến khi link được nhập lại qua một kênh đã xác minh.'}
            </p>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px' }}>
              <button
                type="button"
                className="btn-secondary"
                disabled={publicationDialog.isSaving}
                onClick={() => setPublicationDialog(null)}
              >Hủy</button>
              <button
                type="submit"
                className="btn-run"
                disabled={publicationDialog.isSaving || !publicationDialog.videoId || !publicationDialog.publishedUrl.trim()}
                style={{ width: 'auto', padding: '10px 18px' }}
              >{publicationDialog.isSaving ? 'Đang kiểm tra...' : 'Lưu link & chuyển Đã đăng'}</button>
            </div>
          </form>
        </div>
      )}
        {sceneResetDialog && (
          <div
            className="dialog-backdrop"
            onClick={(e) => {
              if (e.target === e.currentTarget && !sceneResetDialog.isSubmitting) {
                setSceneResetDialog(null)
              }
            }}
            style={{
              position: 'fixed', inset: 0, zIndex: 1000,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              padding: '20px', background: 'rgba(0, 0, 0, 0.72)'
            }}
          >
            <form
              role="dialog"
              aria-modal="true"
              aria-labelledby="scene-reset-dialog-title"
              onSubmit={(e) => {
                e.preventDefault();
                const sceneNum = parseInt(sceneResetDialog.fromSceneNumber, 10);
                if (Number.isNaN(sceneNum) || sceneNum < 1) {
                  setSceneResetDialog(prev => ({ ...prev, error: 'Số thứ tự cảnh phải từ 1 trở lên.' }));
                  return;
                }
                handleResetScenesFrom(sceneNum - 1);
              }}
              style={{
                width: 'min(520px, 100%)', padding: '24px', borderRadius: '14px',
                border: '1px solid rgba(241, 196, 15, 0.6)', background: '#181612',
                boxShadow: '0 24px 80px rgba(0, 0, 0, 0.65)'
              }}
            >
              <h3 id="scene-reset-dialog-title" style={{ margin: '0 0 8px', color: '#f1c40f', display: 'flex', alignItems: 'center', gap: '8px' }}>
                🎯 Tạo lại từ phân cảnh bất kỳ
              </h3>
              <p style={{ margin: '0 0 16px', color: '#ccc', fontSize: '0.9em', lineHeight: 1.45 }}>
                {sceneResetDialog.videoTitle}
              </p>
              
              <label htmlFor="scene-number-input" style={{ display: 'block', marginBottom: '8px', color: '#ddd', fontWeight: 600 }}>
                Số thứ tự cảnh muốn bắt đầu tạo lại (ví dụ: 17):
              </label>
              <input
                id="scene-number-input"
                type="number"
                min="1"
                max="200"
                autoFocus
                required
                value={sceneResetDialog.fromSceneNumber}
                onChange={(event) => setSceneResetDialog(dialog => ({
                  ...dialog,
                  fromSceneNumber: event.target.value,
                  error: ''
                }))}
                disabled={sceneResetDialog.isSubmitting}
                style={{
                  width: '100%', boxSizing: 'border-box', padding: '12px 14px',
                  borderRadius: '8px', border: '1px solid #7d6608',
                  background: '#242014', color: '#fff', fontSize: '1.05em', fontWeight: 'bold'
                }}
              />
              {sceneResetDialog.error && (
                <p role="alert" style={{ margin: '12px 0 0', color: '#ff6b6b', lineHeight: 1.4 }}>
                  {sceneResetDialog.error}
                </p>
              )}
              <div style={{
                margin: '14px 0 0', padding: '12px', borderRadius: '8px',
                background: 'rgba(241, 196, 15, 0.08)', border: '1px solid rgba(241, 196, 15, 0.2)',
                color: '#ddd', fontSize: '0.85em', lineHeight: 1.5
              }}>
                ℹ️ <strong>Nguyên lý hoạt động:</strong><br />
                • Các cảnh từ <strong>1 đến {Math.max(1, (parseInt(sceneResetDialog.fromSceneNumber, 10) || 1) - 1)}</strong> sẽ được <strong>giữ nguyên vẹn</strong>.<br />
                • Hệ thống sẽ xóa các ảnh lỗi từ cảnh <strong>{sceneResetDialog.fromSceneNumber || 1}</strong> trở đi và tiếp tục tạo mới với bộ lọc cấm chữ đã nâng cấp.
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={sceneResetDialog.isSubmitting}
                  onClick={() => setSceneResetDialog(null)}
                >
                  Hủy
                </button>
                <button
                  type="submit"
                  className="btn-run"
                  disabled={sceneResetDialog.isSubmitting || !sceneResetDialog.fromSceneNumber}
                  style={{ width: 'auto', padding: '10px 20px', background: 'linear-gradient(135deg, #f1c40f, #d4ac0d)', color: '#000', fontWeight: 'bold' }}
                >
                  {sceneResetDialog.isSubmitting ? '⏳ Đang khởi chạy...' : '🚀 Xác nhận & Tạo tiếp'}
                </button>
              </div>
            </form>
          </div>
        )}
    </div>
  )
}

export default App
