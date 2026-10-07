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
          <span>📖 Hướng dẫn cấu hình Google OAuth (Chuẩn Google Auth Platform mới nhất)</span>
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
              <b>Quy trình nhanh:</b> Tạo Project ➔ Bật YouTube Data API v3 ➔ Cấu hình Google Auth Platform (Get started) ➔ Thêm Test Users ➔ Tạo Client (Web app) ➔ Dán ID & Secret.
            </div>
            <a
              href="https://console.cloud.google.com/auth/overview"
              target="_blank"
              rel="noopener noreferrer"
              className="oauth-guide-link-btn"
            >
              🚀 Mở Google Auth Platform ↗
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
                Truy cập <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer" style={{ color: '#38bdf8' }}>Google Cloud Console</a> ➔ Nhấp menu chọn dự án ở thanh trên cùng ➔ Bấm <b>New Project</b> (ví dụ: <code>Nexus Studio</code> hoặc <code>Auto Content</code>) ➔ Bấm <b>Create</b> và chọn dự án vừa tạo.
              </div>
            </div>

            {/* Step 2 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">2</span>
                <span>Bật YouTube Data API v3</span>
              </div>
              <div className="oauth-step-body">
                Trên thanh tìm kiếm trên cùng, gõ <code>YouTube Data API v3</code> ➔ Chọn kết quả tương ứng ➔ Nhấn nút <b>ENABLE (Bật)</b> để kích hoạt quyền gọi API YouTube.
              </div>
            </div>

            {/* Step 3 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">3</span>
                <span>Cấu hình Google Auth Platform</span>
              </div>
              <div className="oauth-step-body">
                <div>Menu trái chọn <b>Google Auth Platform</b> (hoặc <i>OAuth consent screen</i>) ➔ Bấm <b>Get started</b>:</div>
                <div>• <b>App Information:</b> Đặt tên ứng dụng (ví dụ: <code>Nexus Studio</code> — ⚠️ <i>Không dùng chữ "YouTube" / "Google"</i>) và chọn Email hỗ trợ.</div>
                <div>• <b>Audience:</b> Google tự để <i>External</i>. Kéo xuống phần <b>Test Users</b> ➔ Bấm <b>+ Add Users</b> ➔ Nhập chính xác <b>Gmail của kênh YouTube</b>.</div>
                <div>• <b>Data Access:</b> Thêm Scope <code>.../auth/youtube.force-ssl</code> ➔ Bấm <b>Finish</b>.</div>
              </div>
            </div>

            {/* Step 4 */}
            <div className="oauth-step-box">
              <div className="oauth-step-box-header">
                <span className="oauth-step-num">4</span>
                <span>Tạo OAuth Client ID (Web app)</span>
              </div>
              <div className="oauth-step-body">
                <div>• Menu bên trái chọn <b>Clients</b> (hoặc vào <i>Credentials</i> ➔ <i>Create Credentials</i> ➔ <i>OAuth client ID</i>).</div>
                <div>• Bấm <b>Create Client</b> ➔ Application type chọn: <b>Web application</b>.</div>
                <div>• Mục <b>Authorized redirect URIs</b>: Bấm <i>+ ADD URI</i> và dán URL:</div>
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
                Sao chép <b>Client ID</b> (dạng <code>...apps.googleusercontent.com</code>) và <b>Client Secret</b> từ popup Google, dán vào 2 ô tương ứng bên dưới ➔ Nhập Tên gợi nhớ (ví dụ: <code>THVN</code>) ➔ Bấm <b>💾 Lưu OAuth Client</b> ➔ Bấm <b>➕ Kết nối qua Google OAuth</b> để đăng nhập và cấp quyền cho kênh.
              </div>
            </div>
          </div>

          <div className="oauth-warning-callout">
            <span className="warn-icon">⚠️</span>
            <div>
              <b>Lưu ý quan trọng:</b>
              <div>1. <b>Quy định đặt tên (Brand Policy):</b> Không được chứa từ khóa thương hiệu độc quyền của Google như <code>YouTube</code>, <code>Google</code>, <code>Gmail</code> trong App Name, nếu không Google sẽ báo lỗi <i>"app name does not comply with Google's requirements"</i>.</div>
              <div>2. <b>Tránh lỗi 403 (Access Denied):</b> Trong chế độ <i>Testing</i>, bắt buộc phải thêm Gmail của kênh YouTube vào danh sách <b>Test users</b> ở mục <b>Audience</b>.</div>
              <div>3. <b>Thời hạn Token:</b> Ở chế độ Testing, token có hạn 7 ngày. Khi hết hạn chỉ cần bấm lại <i>Kết nối qua Google OAuth</i> để gia hạn tức thì.</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
