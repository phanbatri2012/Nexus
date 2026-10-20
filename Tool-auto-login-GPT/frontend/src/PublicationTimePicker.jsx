import React from 'react'
import { HOURS_12, MINUTES_60, parseTime24, formatTime24 } from './publicationTime.js'

export default function PublicationTimePicker({ value = '09:00', onChange, disabled = false }) {
  const { hour, minute, period } = parseTime24(value)
  const time24 = formatTime24(hour, minute, period)

  const handleHourChange = e => {
    const newHour = e.target.value
    if (onChange) onChange(formatTime24(newHour, minute, period))
  }

  const handleMinuteChange = e => {
    const newMinute = e.target.value
    if (onChange) onChange(formatTime24(hour, newMinute, period))
  }

  const handlePeriodChange = e => {
    const newPeriod = e.target.value
    if (onChange) onChange(formatTime24(hour, minute, newPeriod))
  }

  return (
    <div
      className="publication-time-picker"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        background: '#232328',
        padding: '3px 8px',
        borderRadius: 6,
        border: '1px solid #3d3d46',
      }}
    >
      {/* Giờ */}
      <select
        className="version-select"
        style={{
          width: 58,
          padding: '4px 6px',
          textAlign: 'center',
          fontWeight: 600,
          background: '#1a1a1f',
          border: '1px solid #4a4a55',
          borderRadius: 4,
          color: '#f0f0f0',
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
        value={hour}
        onChange={handleHourChange}
        disabled={disabled}
        title="Chọn giờ (01 - 12)"
        aria-label="Giờ"
      >
        {HOURS_12.map(h => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>

      <span style={{ color: '#888', fontWeight: 'bold', fontSize: '1rem' }}>:</span>

      {/* Phút */}
      <select
        className="version-select"
        style={{
          width: 58,
          padding: '4px 6px',
          textAlign: 'center',
          fontWeight: 600,
          background: '#1a1a1f',
          border: '1px solid #4a4a55',
          borderRadius: 4,
          color: '#f0f0f0',
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
        value={minute}
        onChange={handleMinuteChange}
        disabled={disabled}
        title="Chọn phút (00 - 59)"
        aria-label="Phút"
      >
        {MINUTES_60.map(m => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>

      {/* AM / PM */}
      <select
        className="version-select"
        style={{
          width: 66,
          padding: '4px 6px',
          textAlign: 'center',
          fontWeight: 700,
          background: period === 'PM' ? 'rgba(243, 156, 18, 0.15)' : 'rgba(93, 173, 226, 0.15)',
          border: `1px solid ${period === 'PM' ? '#f39c12' : '#5dade2'}`,
          borderRadius: 4,
          color: period === 'PM' ? '#f5b041' : '#5dade2',
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
        value={period}
        onChange={handlePeriodChange}
        disabled={disabled}
        title="Buổi: AM (Sáng) hoặc PM (Chiều/Tối)"
        aria-label="AM hoặc PM"
      >
        <option value="AM" style={{ background: '#1e1e24', color: '#5dade2' }}>
          AM
        </option>
        <option value="PM" style={{ background: '#1e1e24', color: '#f5b041' }}>
          PM
        </option>
      </select>

      {/* 24-hour badge helper */}
      <span
        style={{
          fontSize: '0.78rem',
          color: '#95a5a6',
          marginLeft: 2,
          padding: '2px 4px',
          background: 'rgba(255, 255, 255, 0.05)',
          borderRadius: 4,
          fontFamily: 'monospace',
          whiteSpace: 'nowrap',
        }}
        title="Thời gian định dạng 24h"
      >
        {time24}
      </span>
    </div>
  )
}
