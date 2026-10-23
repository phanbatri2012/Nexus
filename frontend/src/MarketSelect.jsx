import React, { useState, useEffect, useRef, useMemo } from 'react'
import {
  WORLD_MARKETS,
  DEFAULT_MARKET_TIMEZONE,
  DEFAULT_MARKET,
  findMarketByTimezone,
  filterMarkets,
  getMarketLocalTime
} from './marketConstants'
import './MarketSelect.css'

export default function MarketSelect({
  value,
  onChange,
  disabled = false,
  label = 'Thị trường mục tiêu (Timezone)'
}) {
  const [isOpen, setIsOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [customMode, setCustomMode] = useState(false)
  const [customInput, setCustomInput] = useState('')
  const [currentTimeTick, setCurrentTimeTick] = useState(Date.now())
  const containerRef = useRef(null)
  const inputRef = useRef(null)

  // Current active timezone string
  const activeTimezone = value || DEFAULT_MARKET_TIMEZONE

  // Current resolved market object
  const currentMarket = useMemo(() => {
    return findMarketByTimezone(activeTimezone)
  }, [activeTimezone])

  // Current real-time local info for the selected timezone
  const localTimeInfo = useMemo(() => {
    return getMarketLocalTime(activeTimezone)
  }, [activeTimezone, currentTimeTick])

  // Periodic clock tick (every 30 seconds) to update live times
  useEffect(() => {
    const timer = setInterval(() => setCurrentTimeTick(Date.now()), 30000)
    return () => clearInterval(timer)
  }, [])

  // Filtered market list based on user search query
  const filteredList = useMemo(() => {
    return filterMarkets(searchQuery)
  }, [searchQuery])

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setIsOpen(false)
        setSearchQuery('')
        setCustomMode(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus()
    }
  }, [isOpen])

  const handleSelectMarket = (market) => {
    onChange?.(market.timezone)
    setIsOpen(false)
    setSearchQuery('')
    setCustomMode(false)
  }

  const handleApplyCustom = () => {
    const trimmed = customInput.trim()
    if (trimmed) {
      onChange?.(trimmed)
    }
    setIsOpen(false)
    setCustomMode(false)
    setSearchQuery('')
  }

  return (
    <div className="market-select-container" ref={containerRef}>
      {label && <span className="market-select-label">{label}</span>}

      {/* Selected Market Trigger Button */}
      <div
        className={`market-select-trigger ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
        onClick={() => !disabled && setIsOpen(!isOpen)}
        title="Nhấn để chọn hoặc tìm kiếm thị trường mục tiêu"
        role="button"
        tabIndex={disabled ? -1 : 0}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !disabled) {
            e.preventDefault()
            setIsOpen(!isOpen)
          }
        }}
      >
        <div className="market-trigger-info">
          <span className="market-flag">{currentMarket.flag}</span>
          <div className="market-text-group">
            <span className="market-name">{currentMarket.name}</span>
            <span className="market-cities">{currentMarket.cities}</span>
          </div>
        </div>

        <div className="market-time-badge" title="Giờ địa phương hiện tại">
          <span className="market-time-val">{localTimeInfo.timeStr}</span>
          <span className={`market-status-dot ${localTimeInfo.isDaytime ? 'day' : 'night'}`} />
          <span className="market-status-text">{localTimeInfo.isDaytime ? 'Thức' : 'Nghỉ'}</span>
          <span className="market-chevron">▼</span>
        </div>
      </div>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="market-dropdown">
          {/* Real-time Search Input */}
          <div className="market-search-box">
            <span className="market-search-icon">🔍</span>
            <input
              ref={inputRef}
              type="text"
              className="market-search-input"
              placeholder="Tìm theo nước, thành phố (VD: Mỹ, New York, Đức, Tokyo...)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="market-search-clear"
                onClick={() => setSearchQuery('')}
                title="Xóa tìm kiếm"
              >
                ✕
              </button>
            )}
          </div>

          {/* Quick Filter Info */}
          <div className="market-dropdown-header">
            <span>Kết quả: {filteredList.length} thị trường</span>
            {activeTimezone !== DEFAULT_MARKET_TIMEZONE && (
              <button
                type="button"
                className="market-quick-reset"
                onClick={() => handleSelectMarket(DEFAULT_MARKET)}
                title="Đặt lại về mặc định Việt Nam"
              >
                🇻🇳 Về mặc định (VN)
              </button>
            )}
          </div>

          {/* Markets List */}
          <div className="market-list-scroll">
            {filteredList.length === 0 ? (
              <div className="market-empty-state">
                <p>Không tìm thấy thị trường phù hợp với "{searchQuery}"</p>
                <button
                  type="button"
                  className="market-custom-btn"
                  onClick={() => {
                    setCustomMode(true)
                    setCustomInput(searchQuery)
                  }}
                >
                  🌐 Tự nhập IANA Timezone này
                </button>
              </div>
            ) : (
              filteredList.map((m) => {
                const isSelected = m.timezone.toLowerCase() === activeTimezone.toLowerCase()
                const mTime = getMarketLocalTime(m.timezone)
                return (
                  <div
                    key={m.id}
                    className={`market-item ${isSelected ? 'selected' : ''}`}
                    onClick={() => handleSelectMarket(m)}
                  >
                    <div className="market-item-left">
                      <span className="market-item-flag">{m.flag}</span>
                      <div className="market-item-details">
                        <div className="market-item-name-row">
                          <span className="market-item-name">{m.name}</span>
                          <span className="market-item-region">{m.region}</span>
                        </div>
                        <span className="market-item-cities">{m.cities}</span>
                        <span className="market-item-tz">{m.timezone}</span>
                      </div>
                    </div>

                    <div className="market-item-right">
                      <span className="market-item-time">{mTime.timeStr}</span>
                      <span className={`market-status-pill ${mTime.isDaytime ? 'active' : 'sleep'}`}>
                        {mTime.statusLabel}
                      </span>
                      {isSelected && <span className="market-check">✓</span>}
                    </div>
                  </div>
                )
              })
            )}
          </div>

          {/* Custom IANA Input Section */}
          <div className="market-custom-section">
            {!customMode ? (
              <button
                type="button"
                className="market-custom-toggle"
                onClick={() => setCustomMode(true)}
              >
                🌐 Nhập thủ công múi giờ IANA khác...
              </button>
            ) : (
              <div className="market-custom-input-group">
                <input
                  type="text"
                  className="market-custom-input"
                  placeholder="Ví dụ: Pacific/Guam hoặc Atlantic/Reykjavik"
                  value={customInput}
                  onChange={(e) => setCustomInput(e.target.value)}
                />
                <button
                  type="button"
                  className="market-custom-apply"
                  onClick={handleApplyCustom}
                >
                  Áp dụng
                </button>
                <button
                  type="button"
                  className="market-custom-cancel"
                  onClick={() => setCustomMode(false)}
                >
                  Hủy
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
