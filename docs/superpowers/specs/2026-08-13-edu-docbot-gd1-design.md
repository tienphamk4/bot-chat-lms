# EDU-DOCBOT — Thiết kế giai đoạn 1

> Ngày: 13/08/2026 · Phạm vi: Giai đoạn 1 của lộ trình trong `CLAUDE.md` (mục 11)
> Mục tiêu: chatbot **tra cứu tài liệu** chạy được và demo được — chưa có RAG sinh câu trả lời.

## 1. Phạm vi

**Trong phạm vi giai đoạn 1**

1. Kiểm tra phiên bản MariaDB + probe khả năng VECTOR (để biết trước giai đoạn 2 có phải nâng cấp).
2. Cấu hình full-text tiếng Việt (`innodb_ft_min_token_size=1`, tắt stopword) — **phải làm trước khi nạp dữ liệu**.
3. Schema đầy đủ trừ phần vector.
4. Chuẩn hóa tiếng Việt `vn_norm()` ở tầng ứng dụng + các cột `_norm`.
5. Pipeline nạp tài liệu: checksum → trích xuất → phát hiện scan → chia chunk → gán metadata → ghi DB.
6. Nhánh metadata (KT-01): SQL + template, 0 token sinh.
7. Router hai tầng: rule-based trước, Vertex Flash-Lite sau (KT-04 `thinking_budget=0` ngay từ dòng code LLM đầu tiên).
8. API FastAPI đầy đủ, **SSE streaming** — hợp đồng API cố định từ giai đoạn 1, giai đoạn 2 thay ruột không đổi hợp đồng.
9. Lọc quyền trước truy hồi (`allowed_doc_ids`).
10. `query_logs` + `token_quota` + cache tầng hash (KT-10 bắt buộc; hạ tầng đo lường là điều kiện tiên quyết của cả chiến lược).

**Ngoài phạm vi giai đoạn 1** — để lại giai đoạn 2+

- Embedding, cột `VECTOR(768)`, VECTOR INDEX, hybrid search, RRF, rerank local, sinh câu trả lời bằng LLM.
- Semantic cache tầng vector (KT-02 tầng 1), Batch API (KT-09), context caching (KT-03).
- OCR bằng Gemini vision (giai đoạn 1 chỉ đánh dấu tài liệu cần OCR; Tesseract là tuỳ chọn).
- Frontend Next.js hoàn chỉnh, systemd/nginx trên máy chủ thật.

## 2. Quyết định chốt — khác với `CLAUDE.md`

| # | Quyết định | Lý do |
|---|---|---|
| QĐ-1 | Gộp `courses` + `course_aliases` + `course_offerings` thành **một bảng `courses`**, 1 dòng = 1 môn, `offerings` là cột JSON | Yêu cầu của chủ dự án. Nhánh metadata hết JOIN. Giá phải trả: xem RR-1. |
| QĐ-2 | LLM chạy qua **Google Cloud Vertex AI**, không dùng Gemini Developer API key | Hạ tầng sẵn có. Xác thực bằng ADC / service account. |
| QĐ-3 | Giai đoạn 1 **không tạo cột `VECTOR`**. `001_init.sql` chạy được trên MariaDB ≥ 10.6; `002_vector.sql` (giai đoạn 2) mới thêm cột + index | Gỡ phụ thuộc phiên bản khỏi đường tới hạn của demo. Vẫn probe phiên bản ngay để biết sớm. |
| QĐ-4 | Thêm bảng `users` + `user_courses` độc lập, có `external_id` để đồng bộ về sau | Không có danh tính thì không demo được lọc quyền, mà "rò rỉ = 0" là NFR cứng. |
| QĐ-5 | `intent=ask_content` giai đoạn 1 chạy **nhánh B rút gọn**: FULLTEXT trên `document_chunks.content_norm`, trả trích đoạn thật + trích dẫn, **0 token sinh** | Dùng lại hạ tầng đã có; demo trả lời được câu hỏi nội dung; giai đoạn 2 thay ruột, API không đổi. |
| QĐ-6 | Trích metadata từ tên file theo **3 tầng**: nhiều mẫu regex → cấu trúc thư mục → `manifest.csv` ghi đè | Chưa biết quy ước tên file thật. Không ép đổi tên hàng nghìn file; cái nào trượt thì rơi vào `pending_review`, không hỏng cả mẻ. |
| QĐ-7 | Cache tầng hash bật ngay ở giai đoạn 1 (tài liệu xếp KT-02 ở giai đoạn 3) | Tầng hash gần như miễn phí và không có rủi ro trả nhầm ngữ nghĩa. Tầng vector vẫn để giai đoạn 3. |

### Hệ quả của QĐ-2 cần biết trước

- **KT-03 (context caching) gần như chắc chắn không dùng được.** Explicit caching trên Vertex có ngưỡng token tối thiểu (~1024–2048 tuỳ model); system prompt router/RAG chỉ ~200–250 token. Tài liệu đã xếp KT-03 cuối và ghi "có thể lỗ" — nay là "không đủ điều kiện kỹ thuật". Vẫn hưởng implicit caching miễn phí bằng cách xếp phần bất biến lên đầu prompt.
- **KT-09 (Batch API) đổi cơ chế.** Vertex batch chạy qua GCS/BigQuery chứ không phải Batch API của Gemini Developer API. Ảnh hưởng giai đoạn 3, cần tính lại công triển khai.
- Giá tham chiếu Vertex ngang Gemini API cho cùng model, nhưng **phải kiểm tra lại** trước khi báo cáo chi phí.

## 3. Schema

Database `edu_docbot`, `utf8mb4_unicode_ci`, tất cả `ENGINE=InnoDB`.
Quy ước thời gian: **mọi `DATETIME` là UTC**, bảo đảm bằng `SET time_zone='+00:00'` trên mỗi kết nối, nên `CURRENT_TIMESTAMP` sinh ra giá trị UTC.

### 3.1 `faculties`

```sql
CREATE TABLE faculties (
  id         SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(16)  NOT NULL,
  name       VARCHAR(255) NOT NULL,
  name_norm  VARCHAR(255) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_faculties_code (code)
) ENGINE=InnoDB;
```

### 3.2 `courses` — bảng gộp (QĐ-1)

```sql
CREATE TABLE courses (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  faculty_id     SMALLINT UNSIGNED NULL,
  code           VARCHAR(32)  NOT NULL,          -- 'MAT1093'
  code_norm      VARCHAR(32)  NOT NULL,          -- 'mat1093'
  name           VARCHAR(255) NOT NULL,          -- 'Giải tích 1'
  name_norm      VARCHAR(255) NOT NULL,          -- 'giai tich 1'
  credits        TINYINT UNSIGNED NULL,

  -- nguồn sự thật, dạng JSON
  aliases        LONGTEXT NOT NULL DEFAULT '[]'  CHECK (JSON_VALID(aliases)),
  offerings      LONGTEXT NOT NULL DEFAULT '[]'  CHECK (JSON_VALID(offerings)),

  -- cột dẫn xuất, do tầng ứng dụng ghi (xem RR-1)
  aliases_norm   TEXT NOT NULL,        -- '|gt1|giai tich 1|gt 1|'
  lecturers_norm TEXT NOT NULL,        -- '|nguyen van a|tran thi b|'
  semesters      VARCHAR(255) NOT NULL,-- '|20261|20252|'
  search_norm    TEXT NOT NULL,        -- 'mat1093 giai tich 1 gt1 gt 1'  → FULLTEXT

  active         TINYINT(1) NOT NULL DEFAULT 1,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  UNIQUE KEY uq_courses_code_norm (code_norm),
  KEY ix_courses_faculty (faculty_id),
  FULLTEXT KEY ft_courses_search (search_norm),
  CONSTRAINT fk_courses_faculty FOREIGN KEY (faculty_id) REFERENCES faculties(id)
) ENGINE=InnoDB;
```

`offerings` có dạng `[{"semester":"20261","lecturer":"Nguyễn Văn A","class_code":"CQ01"}]`.

Ba cột dẫn xuất dùng ký tự `|` bao hai đầu mỗi phần tử để tra khớp chính xác bằng `LIKE '%|gt1|%'` mà không dính khớp một phần (`gt1` không khớp nhầm `gt10`). Bảng `courses` cỡ vài trăm–vài nghìn dòng nên quét bảng ở đây chấp nhận được.

### 3.3 `users`, `user_courses` (QĐ-4)

```sql
CREATE TABLE users (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  external_id VARCHAR(64) NULL,             -- mã sinh viên / mã CB, để đồng bộ về sau
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
) ENGINE=InnoDB;

CREATE TABLE user_courses (
  user_id   BIGINT UNSIGNED NOT NULL,
  course_id INT UNSIGNED NOT NULL,
  semester  CHAR(5) NULL,
  PRIMARY KEY (user_id, course_id),
  CONSTRAINT fk_uc_user   FOREIGN KEY (user_id)   REFERENCES users(id)   ON DELETE CASCADE,
  CONSTRAINT fk_uc_course FOREIGN KEY (course_id) REFERENCES courses(id) ON DELETE CASCADE
) ENGINE=InnoDB;
```

### 3.4 `documents`

```sql
CREATE TABLE documents (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  course_id     INT UNSIGNED NULL,
  faculty_id    SMALLINT UNSIGNED NULL,
  title         VARCHAR(500) NOT NULL,
  title_norm    VARCHAR(500) NOT NULL,
  type          ENUM('giao_trinh','slide','de_cuong','de_thi','bai_tap',
                     'tai_lieu_tham_khao','quy_che') NOT NULL,
  semester      CHAR(5) NULL,                 -- '20261'
  lecturer      VARCHAR(255) NULL,
  lecturer_norm VARCHAR(255) NULL,
  year          SMALLINT UNSIGNED NULL,
  access        ENUM('public','faculty','course','staff') NOT NULL DEFAULT 'public',
  status        ENUM('pending','pending_review','indexed','failed','needs_ocr')
                NOT NULL DEFAULT 'pending',
  file_path     VARCHAR(500) NOT NULL,        -- tương đối trong /srv/edu-docbot/files
  file_ext      VARCHAR(10)  NOT NULL,
  file_size     BIGINT UNSIGNED NOT NULL,
  page_count    INT UNSIGNED NULL,
  checksum      CHAR(64) NOT NULL,            -- SHA-256
  error_message TEXT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  indexed_at    DATETIME NULL,
  UNIQUE KEY uq_documents_checksum (checksum),
  KEY ix_doc_lookup (course_id, type, semester, status),
  KEY ix_doc_access (access, faculty_id),
  KEY ix_doc_status (status),
  FULLTEXT KEY ft_doc_title (title_norm),
  CONSTRAINT fk_doc_course  FOREIGN KEY (course_id)  REFERENCES courses(id),
  CONSTRAINT fk_doc_faculty FOREIGN KEY (faculty_id) REFERENCES faculties(id)
) ENGINE=InnoDB;
```

So với `CLAUDE.md`, ENUM `status` thêm hai trạng thái: `pending_review` (regex trượt metadata, chờ người xác nhận — QĐ-6) và `needs_ocr` (phát hiện file scan, chưa OCR).

### 3.5 `document_chunks`

```sql
CREATE TABLE document_chunks (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  document_id  BIGINT UNSIGNED NOT NULL,
  chunk_index  INT UNSIGNED NOT NULL,
  content      MEDIUMTEXT NOT NULL,   -- bản gốc, dùng cho prompt & hiển thị
  content_norm MEDIUMTEXT NOT NULL,   -- bỏ dấu, cho FULLTEXT
  heading_path VARCHAR(500) NULL,     -- 'Chương 3 > 3.2 Giới hạn'
  page_from    INT UNSIGNED NULL,
  page_to      INT UNSIGNED NULL,
  token_count  INT UNSIGNED NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_chunk (document_id, chunk_index),
  KEY ix_chunk_doc (document_id),
  FULLTEXT KEY ft_chunk_content (content_norm),
  CONSTRAINT fk_chunk_doc FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB;
```

Giai đoạn 2 (`002_vector.sql`) sẽ:

```sql
ALTER TABLE document_chunks ADD COLUMN embedding VECTOR(768) NULL;
-- nạp embedding cho toàn bộ chunk ...
ALTER TABLE document_chunks MODIFY COLUMN embedding VECTOR(768) NOT NULL;
ALTER TABLE document_chunks ADD VECTOR INDEX vi_chunk (embedding) M=16 DISTANCE=cosine;
```

Đúng thứ tự bắt buộc trong `CLAUDE.md`: cột có VECTOR INDEX phải `NOT NULL`, và index tạo **sau** khi nạp xong.

### 3.6 `query_cache` (giai đoạn 1: chỉ tầng hash)

```sql
CREATE TABLE query_cache (
  id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  question_hash  CHAR(64) NOT NULL,      -- sha256(vn_norm(standalone_question))
  question_norm  TEXT NOT NULL,
  answer         MEDIUMTEXT NOT NULL,
  citations      LONGTEXT NOT NULL DEFAULT '[]' CHECK (JSON_VALID(citations)),
  route          VARCHAR(16) NOT NULL,
  access_scope   ENUM('public','faculty','course','staff') NOT NULL DEFAULT 'public',
  faculty_scope  SMALLINT UNSIGNED NOT NULL DEFAULT 0,   -- 0 = không giới hạn khoa
  hit_count      INT UNSIGNED NOT NULL DEFAULT 0,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_hit_at    DATETIME NULL,
  expires_at     DATETIME NOT NULL,
  UNIQUE KEY uq_cache_scope (question_hash, access_scope, faculty_scope),
  KEY ix_cache_expires (expires_at)
) ENGINE=InnoDB;
```

`faculty_scope` dùng `0` thay cho `NULL` là có chủ đích: trong MariaDB/MySQL, `NULL` trong UNIQUE KEY được coi là khác nhau, nên để `NULL` sẽ cho phép chèn trùng và làm thủng khoá cache.

### 3.7 `query_logs`, `token_quota`, `feedback`

```sql
CREATE TABLE query_logs (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  session_id          CHAR(36) NOT NULL,
  user_id             BIGINT UNSIGNED NULL,
  question            TEXT NOT NULL,
  standalone_question TEXT NULL,          -- để soi chất lượng KT-07
  intent              VARCHAR(24) NULL,
  route               VARCHAR(16) NOT NULL,  -- cache|metadata|fulltext|content|static
  router_source       VARCHAR(16) NOT NULL,  -- rule|llm|cache
  model               VARCHAR(64) NULL,
  tokens_in           INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_out          INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_cached       INT UNSIGNED NOT NULL DEFAULT 0,
  cost_usd            DECIMAL(12,8) NOT NULL DEFAULT 0,
  latency_ms          INT UNSIGNED NOT NULL,
  result_count        INT UNSIGNED NOT NULL DEFAULT 0,
  degraded            TINYINT(1) NOT NULL DEFAULT 0,
  created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_logs_created (created_at),
  KEY ix_logs_route   (route, created_at),
  KEY ix_logs_user    (user_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE token_quota (
  user_id       BIGINT UNSIGNED NOT NULL,
  usage_date    DATE NOT NULL,
  tokens_in     INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_out    INT UNSIGNED NOT NULL DEFAULT 0,
  tokens_total  INT UNSIGNED NOT NULL DEFAULT 0,
  request_count INT UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, usage_date)
) ENGINE=InnoDB;

CREATE TABLE feedback (
  id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  log_id     BIGINT UNSIGNED NOT NULL,
  helpful    TINYINT(1) NOT NULL,
  comment    TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY ix_feedback_log (log_id)
) ENGINE=InnoDB;
```

Cộng quota bằng một câu duy nhất, tránh race:

```sql
INSERT INTO token_quota (user_id, usage_date, tokens_in, tokens_out, tokens_total, request_count)
VALUES (:uid, UTC_DATE(), :ti, :to, :ti + :to, 1)
ON DUPLICATE KEY UPDATE
  tokens_in     = tokens_in     + VALUES(tokens_in),
  tokens_out    = tokens_out    + VALUES(tokens_out),
  tokens_total  = tokens_total  + VALUES(tokens_total),
  request_count = request_count + 1;
```

## 4. Chuẩn hóa tiếng Việt

`vn_norm(s)`: NFD → bỏ ký tự Mn → `đ/Đ → d/D` → NFC → lowercase → gộp khoảng trắng.
Hàm này là **một nguồn duy nhất** (`app/vn.py`), mọi cột `_norm` đều đi qua nó. Có test bảng đối chiếu (`"Giải tích 1" → "giai tich 1"`, `"ĐỀ THI" → "de thi"`, `"Xác suất – Thống kê" → "xac suat - thong ke"`).

Cấu hình server bắt buộc **trước lần nạp đầu tiên** (`/etc/mysql/mariadb.conf.d/50-server.cnf`), y như `CLAUDE.md` mục 3. Đổi `innodb_ft_min_token_size` sau khi đã có FULLTEXT index thì phải restart **và dựng lại toàn bộ index** — nên `scripts/00-preflight.sql` kiểm tra giá trị này và cảnh báo nếu sai.

## 5. Pipeline nạp tài liệu

```
scan thư mục / upload
  → SHA-256 → trùng checksum? bỏ qua
  → trích xuất: PyMuPDF (.pdf) | python-docx (.docx) | python-pptx (.pptx) | text (.txt/.md)
  → phát hiện scan: < 50 ký tự/trang → status='needs_ocr', dừng (OCR để giai đoạn sau)
  → chia chunk: target 500 token, overlap 80, min 120
     ưu tiên cắt: heading cấp 1-3 → ngắt trang → đoạn văn → câu
  → gán metadata 3 tầng (QĐ-6): regex tên file → cấu trúc thư mục → manifest.csv
     không xác định được course/type → status='pending_review'
  → ghi documents + document_chunks trong một transaction
  → status='indexed', indexed_at=UTC now
```

Nạp lại: checksum đổi → `DELETE FROM document_chunks WHERE document_id=?` rồi nạp lại đúng tài liệu đó.

CLI: `python -m ingest.cli scan --dir files/`, `ingest one <path>`, `reindex <doc_id>`, `resync-derived` (dựng lại các cột dẫn xuất của `courses` — xem RR-1).

## 6. Luồng truy vấn giai đoạn 1

```
Câu hỏi
  → [Tầng 0] cache hash (khớp cả access_scope + faculty_scope)     → hit: trả ngay
  → [Guard] token_quota: chạm trần → degraded=1, ép route metadata (KT-10)
  → [Router tầng 0] rule-based: chào hỏi / ngoài phạm vi / mã môn + loại tài liệu rõ ràng
        → khớp: 0 token, đi thẳng nhánh tương ứng
  → [Router tầng 1] Vertex Flash-Lite, structured output, thinking_budget=0
        → {intent, course_code, course_hint, doc_type, semester, lecturer, keywords, standalone_question}
  → intent:
     greeting / out_of_scope → câu mẫu tĩnh, 0 token
     lookup_doc  → nhánh A: SQL metadata (0 kết quả → FULLTEXT title_norm → vẫn 0 → nhánh B rút gọn)
     ask_content → nhánh B rút gọn: FULLTEXT document_chunks.content_norm, trả trích đoạn + trích dẫn
  → Hậu xử lý: ghi query_cache, query_logs, cộng token_quota
```

### Router tầng 0 (rule-based) — điều kiện khớp

Khớp khi **cả hai** điều kiện sau đúng, nếu không thì rơi xuống tầng 1:

1. Câu hỏi chứa mã môn (`\b[A-Z]{2,4}\s?\d{3,4}\b`) **hoặc** một alias/`name_norm` trong `courses` (tra bằng `aliases_norm` LIKE `|x|`).
2. Câu hỏi chứa từ khoá loại tài liệu rõ ràng (`giáo trình`, `slide`/`bài giảng`, `đề thi`/`đề kiểm tra`, `đề cương`, `bài tập`, `quy chế`/`quy định`).

Cộng thêm hai luật độc lập: chào hỏi (`chào`, `hi`, `hello`, `alo`…) và ngoài phạm vi (`giải giúp`, `làm hộ`, `viết tiểu luận`, `tra điểm`, `học phí`, `thời khóa biểu`…).

Ước tính nuốt 50–60% truy vấn tra cứu → chi phí router giai đoạn 1 khoảng **1 USD/tháng** ở mức 50.000 truy vấn.

### Nhánh A — SQL metadata

WHERE dựng động, chỉ thêm điều kiện cho trường khác `NULL`; **luôn** kèm `status='indexed'` và mệnh đề quyền. Tham số ràng buộc 100%, `IN (...)` sinh đúng số placeholder.

```sql
SELECT d.id, d.title, d.type, d.semester, d.lecturer, d.page_count, c.code, c.name
FROM documents d
LEFT JOIN courses c ON c.id = d.course_id
WHERE d.status = 'indexed'
  AND ( d.access = 'public'
     OR (d.access = 'faculty' AND d.faculty_id = :user_faculty_id)
     OR (d.access = 'course'  AND d.course_id IN (:enrolled_ids...))
     OR (d.access = 'staff'   AND :is_staff = 1) )
  [AND d.course_id = :course_id]
  [AND d.type      = :doc_type]
  [AND d.semester  = :semester]
  [AND d.lecturer_norm LIKE :lecturer_like]
ORDER BY FIELD(d.type,'giao_trinh','de_cuong','slide','bai_tap','de_thi',
               'tai_lieu_tham_khao','quy_che'), d.semester DESC, d.title
LIMIT 10;
```

Kết quả render bằng template Jinja — **0 token sinh**.

## 7. API

Tiền tố `/api/v1`. Định danh người dùng giai đoạn 1: header `X-User-Id` (sẽ thay bằng phiên đăng nhập thật khi đồng bộ `users`). Không có header → coi là ẩn danh, chỉ thấy tài liệu `public`.

### 7.1 `POST /chat` — SSE

Request:

```json
{ "session_id": "8f1c…", "message": "giáo trình giải tích 1 có không", "stream": true }
```

Response header bắt buộc: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.
Heartbeat `: ping` mỗi 15 giây để proxy không cắt kết nối.

| event | data | Thời điểm |
|---|---|---|
| `route` | `{intent, branch, course_code, doc_type, semester, lecturer, standalone_question, cached, degraded, log_id}` | Ngay sau router |
| `token` | `{text}` | Lặp. Giai đoạn 1 phát template theo từng câu; giai đoạn 2 là token thật từ LLM — **client không đổi gì** |
| `citations` | `{citations:[{n, document_id, title, type, semester, page, url}]}` | Trước `done` |
| `done` | `{log_id, latency_ms, route, tokens:{in,out,cached}, cost_usd, degraded}` | Kết thúc |
| `error` | `{code, message, degraded_to}` | `quota_exceeded` \| `upstream_error` \| `invalid_request` \| `internal` |

`stream:false` → `application/json` với đúng nội dung đó gộp một cục. Bộ eval 200 câu dùng đường này, không phải parse SSE.

Quy tắc bất biến: `citations` chỉ chứa tài liệu đã qua lọc quyền; dấu `[n]` trong `answer` không ánh xạ được về nguồn thật thì **loại bỏ câu chứa nó** trước khi phát.

### 7.2 Các endpoint còn lại

| Method | Endpoint | Ghi chú |
|---|---|---|
| GET | `/documents` | Lọc `course`, `type`, `semester`, `lecturer`, `q`; phân trang `page`, `page_size` (mặc định 20, trần 100) |
| GET | `/documents/{id}` | 404 nếu ngoài quyền — **không** trả 403, để không lộ sự tồn tại |
| GET | `/documents/{id}/download` | Kiểm quyền → `X-Accel-Redirect: /protected-files/<file_path>` |
| GET | `/courses?q=` | Gợi ý môn (khớp `code_norm`, `aliases_norm`, FULLTEXT `search_norm`) |
| POST | `/feedback` | `{log_id, helpful, comment}` |
| POST | `/admin/documents` | Upload đa phần, trả `document_id` + `status=pending` |
| POST | `/admin/reindex/{id}` | Nạp lại một tài liệu |
| GET | `/admin/metrics` | Chi phí/ngày, tỷ lệ cache hit, phân bố route, số tài liệu theo `status` |
| GET | `/healthz`, `/readyz` | `readyz` kiểm tra kết nối DB |

## 8. Bố cục mã nguồn

```
app/
  main.py            # tạo FastAPI, lifespan, middleware
  config.py          # pydantic-settings, đọc .env
  db.py              # pool kết nối, SET time_zone='+00:00', helper truy vấn tham số hóa
  vn.py              # vn_norm() — nguồn duy nhất
  schemas.py         # pydantic models cho request/response
  api/               # chat.py documents.py courses.py feedback.py admin.py health.py
  core/
    router_rules.py  # router tầng 0
    router_llm.py    # router tầng 1 (Vertex)
    retrieval.py     # nhánh A + FULLTEXT + nhánh B rút gọn
    access.py        # dựng mệnh đề quyền
    render.py        # template trả lời 0 token
    cache.py quota.py cost.py sse.py
  llm/
    vertex.py        # google-genai, vertexai=True, thinking_budget=0
    prompts.py       # phần bất biến xếp đầu prompt
ingest/
  cli.py extract.py chunk.py meta.py loader.py
migrations/
  001_init.sql
  002_seed_demo.sql
scripts/
  00-preflight.sql   # SELECT VERSION(); probe VECTOR; kiểm tra biến full-text
web/                 # trang chat tĩnh tối giản
tests/
```

## 9. Kiểm thử & nghiệm thu giai đoạn 1

**Unit** (không cần DB): `vn_norm`, chia chunk, regex metadata, dựng WHERE động + đếm placeholder, router rule-based, phân tích sự kiện SSE.
**Tích hợp** (cần DB, đánh dấu `@pytest.mark.db`): migration chạy sạch, nạp một tài liệu mẫu, nhánh A trả đúng, lọc quyền.
**Quyền:** ma trận 4 `access` × 4 vai trò × (đúng khoa / khác khoa) — khẳng định **0 rò rỉ**.

Nghiệm thu giai đoạn 1:

1. `scripts/00-preflight.sql` chạy trên server, ghi lại phiên bản + kết quả probe VECTOR.
2. Migration chạy sạch trên `edu_docbot`.
3. Nạp được tài liệu thật, `status='indexed'`, `page_count` đúng.
4. Bộ 20 câu tra cứu mẫu: đúng ≥ 90%.
5. p95 nhánh metadata < 800 ms.
6. Ma trận quyền: 0 rò rỉ.
7. SSE qua nginx không bị buffer (`proxy_buffering off`).

## 10. Rủi ro

| Mã | Rủi ro | Giảm thiểu |
|---|---|---|
| RR-1 | Cột dẫn xuất của `courses` lệch với `aliases`/`offerings` JSON (hệ quả QĐ-1) | Mọi ghi đi qua một hàm `save_course()` duy nhất; thêm `ingest.cli resync-derived` dựng lại toàn bộ; test khẳng định hàm này là đường ghi duy nhất |
| RR-2 | Router tầng 0 khớp sai → trả sai mà mọi tầng sau vẫn "xanh" | Ghi `router_source` + `standalone_question` vào `query_logs` từ ngày đầu; bộ 20 câu chạy lại sau mỗi lần sửa luật |
| RR-3 | `innodb_ft_min_token_size` chưa đổi trước khi nạp → phải dựng lại toàn bộ FULLTEXT | `00-preflight.sql` kiểm tra và chặn trước khi nạp |
| RR-4 | Server MariaDB < 11.7 → giai đoạn 2 tắc | QĐ-3 tách migration; probe ngay giai đoạn 1 để còn thời gian nâng cấp |
| RR-5 | Tên file thật không khớp mẫu regex nào | QĐ-6 ba tầng + `pending_review`; bổ sung mẫu khi có tên file thật |
| RR-6 | Chưa có tài liệu thật để demo | Cần bạn thả ~10 file vào `files/` trước buổi demo; seed sẵn `faculties`/`courses`/`users` |

## 11. Việc cần chủ dự án cung cấp

1. **DSN + quyền** để chạy migration (hoặc tự chạy `migrations/001_init.sql` rồi báo kết quả).
2. Output của `scripts/00-preflight.sql`.
3. `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, đường dẫn service account (đặt trong `.env`, không commit).
4. ~10 tài liệu thật + tên file thật để chỉnh mẫu regex.
5. Danh sách khoa/môn thật (hoặc dùng seed demo trước).
