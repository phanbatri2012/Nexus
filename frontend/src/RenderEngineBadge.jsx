import React from 'react'

const ENGINE_CONFIGS = {
  google_flow: {
    label: 'Google Flow',
    icon: '⚡',
    color: '#38bdf8',
    bg: 'rgba(56, 189, 248, 0.15)',
    border: 'rgba(56, 189, 248, 0.45)',
    tooltip: 'Video tạo bằng Google Labs (Veo / Imagen / Nano Banana)'
  },
  muse_ai: {
    label: 'Muse AI',
    icon: '🎬',
    color: '#c084fc',
    bg: 'rgba(192, 132, 252, 0.15)',
    border: 'rgba(192, 132, 252, 0.45)',
    tooltip: 'Video tạo bằng Muse Studio API & Motion Smoother'
  },
  stock_video: {
    label: 'Video có sẵn',
    icon: '🎞️',
    color: '#fbbf24',
    bg: 'rgba(251, 191, 36, 0.15)',
    border: 'rgba(251, 191, 36, 0.45)',
    tooltip: 'Video ghép từ kho video có sẵn (Stock footage & Sóng âm)'
  },
  draft: {
    label: 'Kịch bản gốc',
    icon: '📝',
    color: '#9ca3af',
    bg: 'rgba(156, 163, 175, 0.12)',
    border: 'rgba(156, 163, 175, 0.35)',
    tooltip: 'Kịch bản mới, chưa dựng thành video'
  }
}

export default function RenderEngineBadge({
  engine,
  label,
  icon,
  color,
  flowProjectUrl,
  size = 'normal'
}) {
  const normalizedKey = (engine || '').toLowerCase()
  const fallback = ENGINE_CONFIGS[normalizedKey] || ENGINE_CONFIGS.draft
  const displayLabel = label || fallback.label
  const displayIcon = icon || fallback.icon
  const displayColor = color || fallback.color
  const displayBg = fallback.bg
  const displayBorder = fallback.border
  const tooltip = fallback.tooltip

  const isSmall = size === 'small'

  return (
    <span
      className="render-engine-badge"
      title={tooltip}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        fontSize: isSmall ? '0.7em' : '0.75em',
        fontWeight: 700,
        padding: isSmall ? '2px 6px' : '3px 8px',
        borderRadius: '12px',
        whiteSpace: 'nowrap',
        backgroundColor: displayBg,
        color: displayColor,
        border: `1px solid ${displayBorder}`,
        lineHeight: 1.2,
        letterSpacing: '0.2px',
        userSelect: 'none',
      }}
    >
      <span style={{ fontSize: isSmall ? '0.9em' : '1em' }}>{displayIcon}</span>
      <span>{displayLabel}</span>
      {flowProjectUrl && (
        <a
          href={flowProjectUrl}
          target="_blank"
          rel="noreferrer"
          title="Mở dự án trên Google Flow"
          onClick={(e) => e.stopPropagation()}
          style={{
            color: displayColor,
            textDecoration: 'none',
            marginLeft: '2px',
            opacity: 0.85,
            display: 'inline-flex',
            alignItems: 'center',
            fontSize: '0.9em'
          }}
        >
          ↗
        </a>
      )}
    </span>
  )
}
