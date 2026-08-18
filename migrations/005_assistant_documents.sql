-- 005 — Tài liệu thuộc về trợ lý nào.
--
-- Trước bản này, mọi bot đọc chung một kho, chỉ khoanh vùng được theo LOẠI tài
-- liệu (doc_types). Không đủ: hai bot có thể cùng cần loại `quy_che` nhưng là
-- hai bộ văn bản khác hẳn nhau — quy chế đào tạo cho sinh viên, quy chế tuyển
-- sinh cho thí sinh. Khoanh theo loại thì bot tuyển sinh chìa ra quy chế thi
-- lại, còn bot học vụ chìa ra chỉ tiêu tuyển sinh.
--
-- Quan hệ nhiều-nhiều có chủ đích: một văn bản học phí vừa thuộc bot tuyển
-- sinh vừa thuộc bot hỗ trợ sinh viên là chuyện bình thường, không nên bắt
-- người quản trị nạp hai lần.
--
-- QUY TẮC ĐỌC (ở app/core/retrieval.py): bot CÓ tài liệu được gán thì chỉ đọc
-- đúng những tài liệu đó; bot CHƯA gán gì thì đọc toàn kho theo doc_types.
-- Nhờ vậy bật tính năng này không làm chết các bot đang chạy.

CREATE TABLE IF NOT EXISTS assistant_documents (
  assistant_id BIGINT UNSIGNED NOT NULL,
  document_id  BIGINT UNSIGNED NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (assistant_id, document_id),
  KEY ix_asdoc_document (document_id),
  CONSTRAINT fk_asdoc_assistant FOREIGN KEY (assistant_id)
    REFERENCES assistants (id) ON DELETE CASCADE,
  CONSTRAINT fk_asdoc_document FOREIGN KEY (document_id)
    REFERENCES documents (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- Mặc định khi nạp tài liệu qua trang của một bot: loại và quyền lấy sẵn từ
-- bot đó, người quản trị khỏi chọn lại từng file.
-- MySQL 8.4 không có "ADD COLUMN IF NOT EXISTS" (đó là cú pháp MariaDB) —
-- chạy lại migration lần hai sẽ báo lỗi trùng cột, bỏ qua là được.
ALTER TABLE assistants
  ADD COLUMN default_doc_type VARCHAR(32) NULL AFTER doc_types,
  ADD COLUMN default_access   VARCHAR(16) NOT NULL DEFAULT 'public' AFTER default_doc_type;
