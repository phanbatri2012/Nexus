import React, { useState } from 'react'
import './UserGuideModal.css'

export default function UserGuideModal({ isOpen, onClose }) {
  const [activeTab, setActiveTab] = useState('quickstart')

  if (!isOpen) return null

  return (
    <div className="guide-modal-overlay" onClick={onClose} role="dialog" aria-modal="true" aria-label="Hướng dẫn sử dụng Auto_YT">
      <div className="guide-modal-container" onClick={(e) => e.stopPropagation()}>
        <div className="guide-modal-header">
          <div className="guide-modal-title">
            <span className="guide-title-icon">📖</span>
            <div>
              <h3>Hướng Dẫn Sử Dụng Nexus Studio</h3>
              <p className="guide-subtitle">Quy trình vận hành & sản xuất video tự động từ A-Z</p>
            </div>
          </div>
          <button className="guide-close-btn" onClick={onClose} aria-label="Đóng">✕</button>
        </div>

        <div className="guide-nav-tabs">
          <button
            type="button"
            className={`guide-tab-btn ${activeTab === 'quickstart' ? 'active' : ''}`}
            onClick={() => setActiveTab('quickstart')}
          >
            ⚡ Khởi Động 1 Lần
          </button>
          <button
            type="button"
            className={`guide-tab-btn ${activeTab === 'workflow' ? 'active' : ''}`}
            onClick={() => setActiveTab('workflow')}
          >
            🎬 Quy Trình 5 Bước
          </button>
          <button
            type="button"
            className={`guide-tab-btn ${activeTab === 'modules' ? 'active' : ''}`}
            onClick={() => setActiveTab('modules')}
          >
            🧩 Các Trung Tâm
          </button>
          <button
            type="button"
            className={`guide-tab-btn ${activeTab === 'troubleshooting' ? 'active' : ''}`}
            onClick={() => setActiveTab('troubleshooting')}
          >
            ⚠️ Xử Lý Sự Cố
          </button>
        </div>

        <div className="guide-modal-body">
          {activeTab === 'quickstart' && (
            <div className="guide-content-section">
              <h4>Checklist Chuẩn Bị 1 Lần Đầu</h4>
              <p className="guide-desc">Thực hiện các bước sau trước khi bắt đầu tạo video đầu tiên:</p>
              <div className="guide-cards-grid">
                <div className="guide-step-card">
                  <div className="guide-step-badge">Bước 1</div>
                  <h5>Khởi động hệ thống</h5>
                  <p>Nhấp đúp file <code>run_autoyt.bat</code> tại thư mục gốc để khởi chạy đủ 5 dịch vụ ngầm (Backend, Frontend, OmniVoice, ChatGPT, Google Flow).</p>
                </div>
                <div className="guide-step-card">
                  <div className="guide-step-badge">Bước 2</div>
                  <h5>Đăng nhập 2 phiên AI</h5>
                  <p>Vào menu <b>Auto Login</b> để mở và đăng nhập ChatGPT. Sau đó vào menu <b>Google Flow</b> để đăng nhập tài khoản tạo ảnh.</p>
                </div>
                <div className="guide-step-card">
                  <div className="guide-step-badge">Bước 3</div>
                  <h5>Kết nối Kênh & Proxy</h5>
                  <p>Vào <b>Channel Hub</b>, thêm Kênh YouTube với <b>GPM Profile ID</b> và <b>Proxy riêng</b> nhằm bảo vệ tài khoản tuyệt đối.</p>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'workflow' && (
            <div className="guide-content-section">
              <h4>Quy Trình 5 Bước Tạo Video Hoàn Chỉnh</h4>
              <div className="guide-timeline">
                <div className="guide-timeline-item">
                  <div className="guide-timeline-num">1</div>
                  <div className="guide-timeline-content">
                    <h5>Nhập liệu & Sinh Kịch bản (Video Fetcher)</h5>
                    <p>Dán URL YouTube gốc hoặc chủ đề → Chọn Prompt Version & Cast giọng (MC + Khách mời) → Nhấn <b>Bắt đầu tạo</b>. ChatGPT sẽ viết lại kịch bản, phân cảnh và metadata SEO.</p>
                  </div>
                </div>
                <div className="guide-timeline-item">
                  <div className="guide-timeline-num">2</div>
                  <div className="guide-timeline-content">
                    <h5>Tạo Thumbnail AI</h5>
                    <p>Chọn tạo Thumbnail <b>Có Chữ</b> (thu hút người xem) hoặc <b>Không Chữ</b> (nghệ thuật). Hệ thống gửi prompt sang AI để tạo ảnh bìa chất lượng cao.</p>
                  </div>
                </div>
                <div className="guide-timeline-item">
                  <div className="guide-timeline-num">3</div>
                  <div className="guide-timeline-content">
                    <h5>Tạo Giọng Đọc & Kiểm Duyệt (Audio Review)</h5>
                    <p>Nhấn <b>Tạo Audio TTS</b> (OmniVoice). Sau khi xong, mở <b>Audio Review Panel</b> để nghe thử, sửa lại những câu chưa chuẩn và tạo lại ngay lập tức.</p>
                  </div>
                </div>
                <div className="guide-timeline-item">
                  <div className="guide-timeline-num">4</div>
                  <div className="guide-timeline-content">
                    <h5>Render Video MP4</h5>
                    <p>Nhấn <b>Render Video MP4</b>. Hệ thống tự động tạo ảnh phân cảnh qua Google Flow (hoặc Stock Video), ghép âm thanh TTS và chèn phụ đề tự động.</p>
                  </div>
                </div>
                <div className="guide-timeline-item">
                  <div className="guide-timeline-num">5</div>
                  <div className="guide-timeline-content">
                    <h5>Xuất bản & Lên Lịch YouTube</h5>
                    <p>Chọn <b>Lên lịch đăng</b> hoặc <b>Đăng ngay</b> → Chọn kênh đích. Hệ thống tự động đẩy video vào YouTube Studio, đặt Thumbnail, Tags và ghim Pinned Comment.</p>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'modules' && (
            <div className="guide-content-section">
              <h4>Các Trung Tâm Quản Lý Mở Rộng</h4>
              <div className="guide-features-list">
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Dashboard</span>
                  <div><b>Thư viện Video:</b> Tìm kiếm, lọc theo trạng thái (Đã đăng, Chưa đăng, Lỗi), nghe thử audio, xem preview MP4 và mở trực tiếp trong GPM Studio.</div>
                </div>
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Trung tâm Job</span>
                  <div><b>Giám sát tiến trình:</b> Theo dõi tất cả tác vụ nền thời gian thực (Script, Image, TTS, Render, Upload) và hỗ trợ Retry khi cần.</div>
                </div>
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Bình luận YouTube</span>
                  <div><b>Tương tác khán giả:</b> Đồng bộ bình luận từ các kênh về một nơi và trả lời tự động để tăng đề xuất cho video.</div>
                </div>
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Cross-Poster</span>
                  <div><b>Đăng chéo Fanpage:</b> Đẩy video tự động sang Facebook Reels / Fanpage nhằm gia tăng lượng tiếp cận đa nền tảng.</div>
                </div>
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Downloader</span>
                  <div><b>Tải tư liệu:</b> Tải video YouTube, bóc tách file âm thanh MP3 hoặc phụ đề .srt làm tài liệu nguồn.</div>
                </div>
                <div className="guide-feature-row">
                  <span className="guide-feature-tag">Settings</span>
                  <div><b>Tùy chỉnh Prompt:</b> Chỉnh sửa mẫu kịch bản, quy tắc phân vai Cast và pipeline render.</div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'troubleshooting' && (
            <div className="guide-content-section">
              <h4>Cẩm Nang Xử Lý Lỗi Nhanh</h4>
              <div className="guide-faq-table">
                <div className="guide-faq-item">
                  <div className="guide-faq-q">❓ Lỗi ChatGPT Session Expired hoặc không tạo được kịch bản</div>
                  <div className="guide-faq-a">👉 Vào tab <b>Auto Login</b>, nhấn <i>Mở trình duyệt ChatGPT</i> và đăng nhập/vượt CAPTCHA, sau đó đóng trình duyệt.</div>
                </div>
                <div className="guide-faq-item">
                  <div className="guide-faq-q">❓ Video dừng ở trạng thái "waiting_for_image_service"</div>
                  <div className="guide-faq-a">👉 Vào tab <b>Google Flow</b> kiểm tra phiên đăng nhập đã sẵn sàng hay chưa.</div>
                </div>
                <div className="guide-faq-item">
                  <div className="guide-faq-q">❓ Cần khởi động lại Tool khi đang có video render</div>
                  <div className="guide-faq-a">👉 Mở <b>Trung tâm Job</b> kiểm tra không còn job nào chạy (`Idle`). Sau đó mới chạy <code>run_autoyt_restart.bat</code>.</div>
                </div>
                <div className="guide-faq-item">
                  <div className="guide-faq-q">❓ Kênh YouTube bị lỗi tải video (Upload error)</div>
                  <div className="guide-faq-a">👉 Vào <b>Channel Hub</b> kiểm tra phần mềm GPM-Login (port 19995) và kiểm tra lại kết nối Proxy của kênh.</div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="guide-modal-footer">
          <span className="guide-footer-tip">💡 Xem tài liệu chi tiết đầy đủ tại file <code>HUONG_DAN_SU_DUNG.md</code> trong thư mục dự án.</span>
          <button className="guide-btn-primary" onClick={onClose}>Đã hiểu & Bắt đầu</button>
        </div>
      </div>
    </div>
  )
}
