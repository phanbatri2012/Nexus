import React, { useState } from 'react'
import './OAuthGuideBox.css'

export const REDIRECT_URI_DEFAULT = 'http://127.0.0.1:8080/api/youtube-comments/oauth/callback'

export default function OAuthGuideBox({ defaultOpen = false }) {
  const [isOpen, setIsOpen] = useState(defaultOpen)
  const [copied, setCopied] = useState(false)

  const handleCopyUri = async (e) => {
    e.stopPropagation()
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(REDIRECT_URI_DEFAULT)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = REDIRECT_URI_DEFAULT
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Không thể sao chép Redirect URI:', err)
    }
  }

  return (
    <div className="oauth-guide-card">
      <div
        className="oauth-guide-header"
        onClick={() => setIsOpen(!isOpen)}
        title="Bấm để mở rộng hoặc thu gọn hướng dẫn"
      >
        <div className="oauth-guide-title">
          <span>📖 Hướng dẫn lấy Client ID & Secret Google OAuth</span>
          <span className="oauth-guide-badge">Dành cho người mới</span>
        </div>
        <div className="oauth-guide-toggle-btn">
          <span>{isOpen ? '▲ Thu gọn hướng dẫn' : '▼ Xem hướng dẫn 5 bước'}</span>
        </div>
      </div>

      {isOpen && (
        <div className="oauth-guide-content">
          <div className="oauth-guide-topbar">
            <div>
              <b>Quy trình nhanh:</b> Tạo Project ➔ Bật YouTube Data API v3 ➔ Thêm Test User ➔ Tạo OAuth Client (Web app) ➔ Dán ID & Secret.
            </div>
            <a
              href="https://console.cloud.google.com/apis/credentials"
              target="_blank"
              rel="noopener noreferrer"
              className="oauth-guide-link-btn"
            >
              🚀 Mở Google Cloud Console ↗
            </a>
          </div>

          <div className="oauth-steps-grid">
            {/* Step 1 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">1</span>
                <span>Tạo Project trên Google Cloud</span>
              </div>
              <div className="oauth-step-body">
                Truy cập <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer" style={{ color: '#38bdf8' }}>Google Cloud Console</a>, chọn menu dự án trên cùng ➔ Bấm <b>New Project</b> (ví dụ đặt tên: <code>Auto-YT</code>) ➔ Bấm <b>Create</b> và chọn dự án vừa tạo.
              </div>
            </div>

            {/* Step 2 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">2</span>
                <span>Bật YouTube Data API v3</span>
              </div>
              <div className="oauth-step-body">
                Vào menu <b>APIs & Services</b> ➔ <b>Library</b> ➔ Tìm kiếm từ khóa <code>YouTube Data API v3</code> ➔ Nhấn vào kết quả và bấm <b>ENABLE (Bật)</b>.
              </div>
            </div>

            {/* Step 3 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">3</span>
                <span>OAuth Consent Screen & Test Users</span>
              </div>
              <div className="oauth-step-body">
                <div>• Chọn <b>External</b> (Bên ngoài) ➔ Nhập tên app & email hỗ trợ.</div>
                <div>• Thêm phạm vi (Scope): <code>.../auth/youtube.force-ssl</code>.</div>
                <div>• <b>Test users:</b> Bấm <i>+ Add Users</i> ➔ Nhập đúng <b>Gmail quản lý kênh YouTube</b> cần kết nối.</div>
              </div>
            </div>

            {/* Step 4 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">4</span>
                <span>Tạo OAuth Client ID</span>
              </div>
              <div className="oauth-step-body">
                <div>• Vào mục <b>Credentials</b> ➔ <b>Create Credentials</b> ➔ <b>OAuth client ID</b>.</div>
                <div>• Application type: Chọn <b>Web application</b>.</div>
                <div>• Mục <b>Authorized redirect URIs</b>: Thêm chính xác URL bên dưới:</div>
                <div className="oauth-copy-row">
                  <span className="oauth-copy-text">{REDIRECT_URI_DEFAULT}</span>
                  <button
                    type="button"
                    className={`oauth-btn-copy ${copied ? 'copied' : ''}`}
                    onClick={handleCopyUri}
                  >
                    {copied ? '✅ Đã sao chép' : '📋 Sao chép'}
                  </button>
                </div>
              </div>
            </div>

            {/* Step 5 */}
            <div className="oauth-step-box" style={{ gridColumn: '1 / -1' }}>
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">5</span>
                <span>Lấy Client ID & Secret dán vào Form bên dưới</span>
              </div>
              <div className="oauth-step-body">
                Sao chép <b>Client ID</b> và <b>Client Secret</b> từ popup vừa tạo, dán vào các ô tương ứng bên dưới ➔ Nhập tên gợi nhớ (ví dụ: <code>THVN</code>) ➔ Bấm <b>💾 Lưu OAuth Client</b> ➔ Bấm <b>➕ Kết nối qua Google OAuth</b> để cấp quyền cho kênh.
              </div>
            </div>
          </div>

          <div className="oauth-warning-callout">
            <span className="warn-icon">⚠️</span>
            <div>
              <b>Lưu ý quan trọng:</b> Nếu ứng dụng ở chế độ <i>Testing</i>, chỉ các Gmail đã được thêm vào danh sách <b>Test users</b> ở Bước 3 mới có thể đăng nhập. Token trong chế độ Testing có thời hạn 7 ngày (chỉ cần bấm lại <i>Kết nối qua Google OAuth</i> để gia hạn).
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
