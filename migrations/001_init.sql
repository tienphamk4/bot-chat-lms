-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · 001_init.sql · Giai đoạn 1
--
--   MariaDB:  mariadb -u root -p < migrations/001_init.sql
--   MySQL:    mysql   -u root -p < migrations/001_init.sql   (cần >= 8.0.16)
--
-- Điều kiện tiên quyết: đã chạy scripts/00-preflight.sql và innodb_ft_min_token_size = 1.
--
-- File này CỐ TÌNH không dùng kiểu VECTOR: giai đoạn 1 chưa cần embedding, nên
-- migration chạy được từ MariaDB 10.6 trở lên. Cột embedding + VECTOR INDEX do
-- 002_vector.sql (giai đoạn 2) thêm vào, đúng thứ tự "nạp dữ liệu xong mới tạo
-- index" mà MariaDB yêu cầu.
--
-- Quy ước thời gian: MỌI cột DATETIME lưu giờ UTC. Ứng dụng ép
-- SET time_zone = '+00:00' trên từng kết nối, nên CURRENT_TIMESTAMP là UTC.
-- ---------------------------------------------------------------------------

SET NAMES utf8mb4;
SET SESSION time_zone = '+00:00';

CREATE DATABASE IF NOT EXISTS edu_docbot
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE edu_docbot;

-- ---------------------------------------------------------------------------
-- 1. faculties — khoa / bộ môn
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS faculties (
  id         SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(16)  NOT NULL,
  name       VARCHAR(255) NOT NULL,
  name_norm  VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_faculties_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 2. courses — GỘP courses + course_aliases + course_offerings
--
-- Một dòng = một môn học. Tên gọi tắt và các lớp học phần nằm trong hai cột
-- JSON. Nhánh metadata nhờ đó không phải JOIN gì cả.
--
-- Ba cột *_norm/semesters là DẪN XUẤT từ hai cột JSON, do tầng ứng dụng ghi
-- (MariaDB không cho FULLTEXT trên generated column ảo). Đường ghi duy nhất là
-- app/core/courses.save_course(); nếu sửa JSON bằng tay thì phải chạy
-- `python -m ingest.cli resync-derived` để dựng lại.
--
-- Quy ước cột dẫn xuất: mỗi phần tử được bao bằng '|' ở cả hai đầu
-- ('|gt1|giai tich 1|') để LIKE '%|gt1|%' khớp chính xác, không dính 'gt10'.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS courses (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  faculty_id     SMALLINT UNSIGNED NULL,

  code           VARCHAR(32)  NOT NULL,           -- 'MAT1093'
  code_norm      VARCHAR(32)  NOT NULL,           -- 'mat1093'
  name           VARCHAR(255) NOT NULL,           -- 'Giải tích 1'
  name_norm      VARCHAR(255) NOT NULL,           -- 'giai tich 1'
  credits        TINYINT UNSIGNED NULL,

  -- nguồn sự thật. DEFAULT dạng biểu thức (có ngoặc) để chạy được cả MySQL 8.0.13+
  -- lẫn MariaDB — MySQL không cho DEFAULT literal trên cột TEXT.
  aliases        LONGTEXT NOT NULL DEFAULT ('[]'),  -- ["GT1","Giải tích 1"]
  offerings      LONGTEXT NOT NULL DEFAULT ('[]'),  -- [{"semester":"20261","lecturer":"...","class_code":"CQ01"}]

  -- dẫn xuất (app ghi)
  aliases_norm   TEXT NOT NULL,                   -- '|gt1|giai tich 1|'
  lecturers_norm TEXT NOT NULL,                   -- '|nguyen van a|tran thi b|'
  semesters      VARCHAR(255) NOT NULL,           -- '|20261|20252|'
  search_norm    TEXT NOT NULL,                   -- 'mat1093 giai tich 1 gt1' → FULLTEXT

  active         TINYINT(1) NOT NULL DEFAULT 1,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uq_courses_code_norm (code_norm),
  KEY ix_courses_faculty (faculty_id),
  KEY ix_courses_name_norm (name_norm),
  FULLTEXT KEY ft_courses_search (search_norm),

  CONSTRAINT fk_courses_faculty FOREIGN KEY (faculty_id) REFERENCES faculties(id),
  CONSTRAINT ck_courses_aliases   CHECK (JSON_VALID(aliases)),
  CONSTRAINT ck_courses_offerings CHECK (JSON_VALID(offerings))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 3. users / user_courses — danh tính tối thiểu
--
-- Bảng độc lập, sẽ đồng bộ từ nguồn của trường về sau qua external_id.
-- Không có bảng này thì không kiểm chứng được NFR "rò rỉ tài liệu = 0".
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  external_id VARCHAR(64) NULL,              -- mã sinh viên / mã cán bộ
  username    VARCHAR(64) NOT NULL,
  full_name   VARCHAR(255) NULL,
  role        ENUM('student','lecturer','staff','admin') NOT NULL DEFAULT 'student',
  faculty_id  SMALLINT UNSIGNED NULL,
  active      TINYINT(1) NOT NULL DEFAULT 1,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_users_username (username),
  UNIQUE KEY uq_users_external (external_id),
  KEY ix_users_faculty (faculty_id),
  CONSTRAINT fk_users_faculty FOREIGN KEY (faculty_id) REFERENCES faculties(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Sinh viên thuộc lớp học phần nào — quyết định quyền xem tài liệu access='course'.
CREATE TABLE IF NOT EXISTS user_courses (
  user_id   BIGINT UNSIGNED NOT NULL,
  course_id INT UNSIGNED NOT NULL,
  semester  CHAR(5) NULL,
  PRIMARY KEY (user_id, course_id),
  KEY ix_uc_course (course_id),
  CONSTRAINT fk_uc_user   FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
  CONSTRAINT fk_uc_course FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 4. documents
--
-- status có hai giá trị ngoài thiết kế gốc:
--   pending_review — trích metadata từ tên file thất bại, chờ người xác nhận.
--                    Cả mẻ nạp không hỏng vì một file lạ.
--   needs_ocr      — phát hiện file scan (< 50 ký tự/trang), chưa OCR.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS documents (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  course_id     INT UNSIGNED NULL,
  faculty_id    SMALLINT UNSIGNED NULL,

  title         VARCHAR(500) NOT NULL,
  title_norm    VARCHAR(500) NOT NULL,
  type          ENUM('giao_trinh','slide','de_cuong','de_thi','bai_tap',
                     'tai_lieu_tham_khao','quy_che') NOT NULL,
  semester      CHAR(5) NULL,                   -- '20261' = HK1 năm học 2026-2027
  lecturer      VARCHAR(255) NULL,
  lecturer_norm VARCHAR(255) NULL,
  year          SMALLINT UNSIGNED NULL,

  access        ENUM('public','faculty','course','staff') NOT NULL DEFAULT 'public',
  status        ENUM('pending','pending_review','needs_ocr','indexed','failed')
                NOT NULL DEFAULT 'pending',

  file_path     VARCHAR(500) NOT NULL,          -- tương đối trong FILES_ROOT
  file_ext      VARCHAR(10)  NOT NULL,
  file_size     BIGINT UNSIGNED NOT NULL,
  page_count    INT UNSIGNED NULL,
  checksum      CHAR(64) NOT NULL,              -- SHA-256, chống nạp trùng
  error_message TEXT NULL,

  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  indexed_at    DATETIME NULL,

  UNIQUE KEY uq_documents_checksum (checksum),
  KEY ix_doc_lookup  (course_id, type, semester, status),
  KEY ix_doc_access  (access, faculty_id),
  KEY ix_doc_status  (status),
  KEY ix_doc_lecturer (lecturer_norm(64)),
  FULLTEXT KEY ft_doc_title (title_norm),

  CONSTRAINT fk_doc_course  FOREIGN KEY (course_id)  REFERENCES courses(id),
  CONSTRAINT fk_doc_faculty FOREIGN KEY (faculty_id) REFERENCES faculties(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 5. document_chunks
--
-- content      — bản gốc còn dấu, dùng để hiển thị và (giai đoạn 2) đưa vào prompt.
-- content_norm — bỏ dấu, chỉ để FULLTEXT.
-- Cột embedding VECTOR(768) do 002_vector.sql thêm ở giai đoạn 2.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS document_chunks (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  document_id  BIGINT UNSIGNED NOT NULL,
  chunk_index  INT UNSIGNED NOT NULL,
  content      MEDIUMTEXT NOT NULL,
  content_norm MEDIUMTEXT NOT NULL,
  heading_path VARCHAR(500) NULL,               -- 'Chương 3 > 3.2 Giới hạn'
  page_from    INT UNSIGNED NULL,
  page_to      INT UNSIGNED NULL,
  token_count  INT UNSIGNED NOT NULL DEFAULT 0,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  UNIQUE KEY uq_chunk (document_id, chunk_index),
  KEY ix_chunk_doc (document_id),
  FULLTEXT KEY ft_chunk_content (content_norm),

  CONSTRAINT fk_chunk_doc FOREIGN KEY (document_id)
    REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 6. query_cache — giai đoạn 1 chỉ dùng tầng hash
--
-- faculty_scope dùng 0 thay cho NULL là CÓ CHỦ ĐÍCH: trong MariaDB, NULL trong
-- UNIQUE KEY được coi là khác nhau, để NULL sẽ cho phép chèn trùng và làm thủng
-- khóa cache. 0 = không giới hạn theo khoa.
--
-- Cột query_vec VECTOR(768) cho cache tầng 2 sẽ do 002_vector.sql thêm.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS query_cache (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  question_hash CHAR(64) NOT NULL,              -- sha256(vn_norm(standalone_question))
  question_norm TEXT NOT NULL,
  answer        MEDIUMTEXT NOT NULL,
  citations     LONGTEXT NOT NULL DEFAULT ('[]'),
  route         VARCHAR(16) NOT NULL,
  access_scope  ENUM('public','faculty','course','staff') NOT NULL DEFAULT 'public',
  faculty_scope SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  hit_count     INT UNSIGNED NOT NULL DEFAULT 0,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_hit_at   DATETIME NULL,
  expires_at    DATETIME NOT NULL,

  UNIQUE KEY uq_cache_scope (question_hash, access_scope, faculty_scope),
  KEY ix_cache_expires (expires_at),
  CONSTRAINT ck_cache_citations CHECK (JSON_VALID(citations))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 7. query_logs — hạ tầng đo lường. Không có bảng này thì mọi "tối ưu chi phí"
-- chỉ là niềm tin.
--
-- standalone_question được lưu để soi chất lượng router (KT-07): router hỏng là
-- cả chuỗi hỏng trong khi từng khâu sau vẫn "xanh".
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS query_logs (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  session_id          CHAR(36) NOT NULL,
  user_id             BIGINT UNSIGNED NULL,
  question            TEXT NOT NULL,
  standalone_question TEXT NULL,
  intent              VARCHAR(24) NULL,
  route               VARCHAR(16) NOT NULL,     -- cache|metadata|fulltext|content|static
  router_source       VARCHAR(16) NOT NULL,     -- rule|llm|cache
  model               VARCHAR(64) NULL,
  tokens_in           INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_out          INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_cached       INT UNSIGNED NOT NULL DEFAULT 0,
  cost_usd            DECIMAL(12,8) NOT NULL DEFAULT 0,
  latency_ms          INT UNSIGNED NOT NULL DEFAULT 0,
  result_count        INT UNSIGNED NOT NULL DEFAULT 0,
  degraded            TINYINT(1) NOT NULL DEFAULT 0,
  created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  KEY ix_logs_created (created_at),
  KEY ix_logs_route   (route, created_at),
  KEY ix_logs_user    (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 8. token_quota — thay bộ đếm Redis. Cộng bằng INSERT ... ON DUPLICATE KEY
-- UPDATE trong một câu để không có race giữa các worker.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS token_quota (
  user_id       BIGINT UNSIGNED NOT NULL,
  usage_date    DATE NOT NULL,
  tokens_in     INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_out    INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_total  INT UNSIGNED NOT NULL DEFAULT 0,
  request_count INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, usage_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 9. feedback
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feedback (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  log_id     BIGINT UNSIGNED NOT NULL,
  helpful    TINYINT(1) NOT NULL,
  comment    TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_feedback_log (log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 10. Tài khoản ứng dụng — chỉ DML, không DDL. Đổi mật khẩu trước khi chạy.
-- Bỏ comment nếu tài khoản chưa tồn tại.
-- ---------------------------------------------------------------------------

-- CREATE USER IF NOT EXISTS 'docbot'@'localhost' IDENTIFIED BY 'ĐỔI_MẬT_KHẨU_NÀY';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON edu_docbot.* TO 'docbot'@'localhost';
-- FLUSH PRIVILEGES;

SELECT 'migration 001_init OK' AS ket_qua;

SELECT table_name, engine
FROM information_schema.tables
WHERE table_schema = 'edu_docbot'
ORDER BY table_name;
