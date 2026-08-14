# EDU-DOCBOT — Chatbot tra cứu tài liệu học tập

> Context dự án, đúc kết từ tài liệu thiết kế v2.0 (12/08/2026).
> Stack: **Gemini API + MariaDB Vector**, triển khai trực tiếp trên máy chủ Linux (systemd + nginx, **không Docker**).

## 1. Dự án làm gì

Chatbot cho sinh viên tra cứu tài liệu học tập bằng tiếng Việt tự nhiên:

- Tìm tài liệu theo môn học, loại tài liệu, học kỳ, giảng viên.
- Hỏi đáp nội dung bên trong tài liệu (RAG) **bắt buộc kèm trích dẫn** (tên tài liệu, số trang, link).
- Tra cứu quy chế đào tạo, quy định thi cử.
- Phân quyền truy cập tài liệu theo khoa và lớp.

**Ngoài phạm vi:** tra điểm / thời khóa biểu / học phí, giải bài tập hộ, viết tiểu luận, giọng nói, ngôn ngữ ngoài Việt–Anh.

**Ràng buộc thiết kế xuyên suốt: tối thiểu hóa chi phí token.** Mọi quyết định kiến trúc đều phục vụ mục tiêu này (mục tiêu < 15 USD/tháng cho 50.000 truy vấn — NFR-03).

## 2. Stack công nghệ

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| CSDL | **MariaDB 11.8 LTS** (tối thiểu 11.7) | Một CSDL duy nhất lo cả quan hệ + vector + full-text. VECTOR là tính năng gốc từ 11.7, không cần extension |
| Backend | Python 3.12 + FastAPI, chạy bằng systemd | SSE streaming cho chat |
| Web server | nginx | Reverse proxy, TLS, phục vụ file qua `X-Accel-Redirect` |
| Driver DB | `mariadb` (driver chính thức) hoặc SQLAlchemy + MariaDB dialect | Bind vector dạng nhị phân |
| Cache & quota | Bảng trong MariaDB + LRU trong tiến trình | **Không dùng Redis** |
| Nạp tài liệu | Script CLI + systemd timer | **Không dùng Celery** — tác vụ theo lô |
| LLM | Gemini 2.5 Flash-Lite (mặc định), Gemini 3 Flash (escalate, <3% truy vấn) | |
| Embedding | `gemini-embedding-001`, **768 chiều** (Matryoshka, cắt từ 3072) | |
| Reranker | `bge-reranker-v2-m3` chạy local trên CPU | Miễn phí token, ~200ms |
| Frontend | Next.js 14 + Tailwind, **build tĩnh** | nginx phục vụ thẳng thư mục build, không có tiến trình Node |

## 3. CSDL — schema chính

Database: `edu_docbot`, charset `utf8mb4_unicode_ci`, tất cả bảng `ENGINE=InnoDB`.

- `faculties` — khoa/bộ môn.
- `courses` — môn học; có cột `code_norm`, `name_norm` (bỏ dấu, chữ thường) + FULLTEXT trên `name_norm`.
- `course_aliases` — tên gọi tắt sinh viên hay dùng (`gt1`, `xstk`…), thay cho kiểu mảng `TEXT[]` của PostgreSQL.
- `course_offerings` — lớp học phần theo học kỳ (semester dạng `20261` = HK1 2026-2027).
- `documents` — tài liệu: `type` ENUM(`giao_trinh`,`slide`,`de_cuong`,`de_thi`,`bai_tap`,`tai_lieu_tham_khao`,`quy_che`), `access` ENUM(`public`,`faculty`,`course`,`staff`), `status` ENUM(`pending`,`indexed`,`failed`), `checksum` SHA-256 UNIQUE chống nạp trùng, `file_path` tương đối trong `/srv/edu-docbot/files`.
- `document_chunks` — chunk nội dung: `content` (bản gốc cho prompt) + `content_norm` (bỏ dấu cho FULLTEXT), `heading_path`, `page_from/page_to`, `embedding VECTOR(768) NOT NULL`.
- `query_cache` — semantic cache 2 tầng (hash + vector), lưu kèm `access`/`faculty_id` để không trả cache sai quyền.
- `query_logs` — nhật ký đo chi phí: route, model, token in/out/cached, `cost_usd`, `latency_ms`.
- `token_quota` — quota theo `(user_id, usage_date)`, thay bộ đếm Redis.

### Quy tắc VECTOR trên MariaDB — BẮT BUỘC nhớ

1. Cột có VECTOR INDEX phải `NOT NULL`.
2. Mỗi bảng chỉ được **một** VECTOR INDEX.
3. Phải khai báo `DISTANCE=cosine` tường minh (mặc định là euclidean — truy vấn cosine trên index euclidean sẽ **âm thầm quét toàn bảng**).
4. Chỉ dùng `ENGINE=InnoDB`.
5. Truy vấn phải có dạng `ORDER BY VEC_DISTANCE_COSINE(...) ... LIMIT n` thì index mới được dùng. **Ba bẫy:** thiếu LIMIT → quét toàn bảng; bọc hàm khoảng cách trong biểu thức (vd `ORDER BY (1 - VEC_DISTANCE_COSINE(...)) DESC`) → hỏng nhận diện HNSW, muốn đổi sang điểm tương đồng thì tính ở SELECT bọc ngoài; WHERE quá hẹp → top-K bị lọc hết, phải nâng LIMIT hoặc lọc ở tầng ứng dụng.
6. Muốn lọc theo ngưỡng khoảng cách: lấy top-K bằng index trong subquery rồi mới áp ngưỡng ở SELECT ngoài (`WHERE VEC_DISTANCE(...) < ngưỡng` trực tiếp sẽ quét toàn bảng).
7. **Tạo VECTOR INDEX SAU khi nạp xong dữ liệu** (`ALTER TABLE ... ADD VECTOR INDEX vi_chunk (embedding) M=16 DISTANCE=cosine`) — nhanh hơn nhiều so với vừa chèn vừa cập nhật index. Nạp lại khối lượng lớn: DROP INDEX → nạp → tạo lại.
8. Ghi vector bằng **bytes float32 little-endian** (`np.asarray(v, dtype=np.float32).tobytes()`) — nhanh ~5 lần so với `VEC_FromText`. Đọc lại phải bọc `VEC_ToText(embedding)`.

### Tiếng Việt

- MariaDB không có `unaccent` → bỏ dấu ở tầng ứng dụng bằng hàm `vn_norm()` (NFD → bỏ Mn → đ/Đ → d/D → NFC, lowercase, gộp khoảng trắng), lưu vào các cột hậu tố `_norm`.
- Cấu hình bắt buộc trong `/etc/mysql/mariadb.conf.d/50-server.cnf` **trước khi nạp dữ liệu lần đầu**:
  ```ini
  [mariadb]
  character-set-server      = utf8mb4
  collation-server          = utf8mb4_unicode_ci
  innodb_ft_min_token_size  = 1     # âm tiết tiếng Việt 1-2 ký tự
  innodb_ft_enable_stopword = OFF
  mhnsw_max_cache_size      = 268435456   # 256 MB cho đồ thị HNSW
  mhnsw_ef_search           = 40          # tăng nếu recall thấp
  ```
- Đổi `innodb_ft_min_token_size` phải restart MariaDB **và dựng lại toàn bộ FULLTEXT index** đã tạo.
- Nếu server < 11.7 và không nâng cấp được: fallback lưu embedding dạng BLOB, tính cosine bằng NumPy trong RAM (chấp nhận được dưới 200.000 chunk).

## 4. Pipeline nạp tài liệu

Upload web hoặc quét thư mục → SHA-256 (bỏ trùng) → trích xuất (PyMuPDF / python-docx / python-pptx) → phát hiện scan (< 50 ký tự/trang) → OCR chỉ khi cần (**Tesseract trước, Gemini vision là lựa chọn cuối** — đắt nhất) → chia chunk → gán metadata (regex trên tên file trước, Flash-Lite qua Batch API chỉ khi regex thất bại) → embedding (gộp 100 chunk/lần gọi, Batch API) → **tạo VECTOR INDEX sau cùng**.

Chia chunk: target 500 token, overlap 80, min 120 (ngắn hơn thì gộp). Ưu tiên cắt tại: heading cấp 1-3 → ngắt trang → đoạn văn → câu. Lưu `heading_path` để trích dẫn dạng "Giáo trình Giải tích 1, Chương 3, tr. 45–47".

Nạp lại: checksum đổi → xóa chunk theo `document_id` rồi nạp lại riêng tài liệu đó. Đổi model/số chiều embedding → nạp lại toàn bộ.

## 5. Pipeline truy vấn — luồng phân tầng

```
Câu hỏi → [Tầng 0] cache hash → [Tầng 1] cache vector (distance < 0.05)
        → Router (Flash-Lite, structured output, thinking_budget=0)
        → Nhánh A: metadata → SQL + template, 0 token sinh
          (0 kết quả → FULLTEXT trên title_norm → vẫn 0 → chuyển nhánh B)
        → Nhánh B: RAG → hybrid retrieval → RRF → rerank local → sinh (Flash-Lite)
        → out_of_scope / greeting → câu mẫu tĩnh, 0 token
```

- **Router** trả JSON: `intent` (lookup_doc | ask_content | out_of_scope | greeting), `course_code`, `course_hint`, `doc_type`, `semester`, `lecturer`, `keywords`, **`standalone_question`**. Trường cuối là thiết kế then chốt: router viết lại câu hỏi độc lập ngữ cảnh, nhờ đó các bước sau **không gửi lịch sử chat** — chặn phình input theo số lượt.
- **Nhánh A (metadata):** WHERE động chỉ thêm điều kiện cho trường khác null; luôn lọc `status='indexed'` và quyền (`access='public' OR faculty_id=:user_faculty_id`). Render bằng template — 0 token.
- **Nhánh B (RAG):** hai kênh song song lấy 20 kết quả mỗi kênh (vector + FULLTEXT BOOLEAN MODE), giới hạn trong `allowed_doc_ids` đã lọc quyền → RRF k=60 ở tầng ứng dụng → rerank bge-reranker-v2-m3 giữ **4 chunk** → sinh với `temperature=0.2, max_output_tokens=400, thinking_budget=0`. Prompt bắt: trả lời ≤ 5 câu, ghi nguồn [1][2], không suy diễn, không giải bài tập; thiếu thông tin thì trả đúng câu "Mình không tìm thấy thông tin này trong tài liệu hiện có." + gợi ý 2 từ khóa.
- **Hậu xử lý:** ánh xạ [n] → link + số trang (dấu [n] không ánh xạ được về chunk thật → loại bỏ câu đó), ghi `query_cache`, `query_logs`, cộng `token_quota`.

## 6. Quy tắc tối ưu chi phí (áp dụng khi viết mọi code gọi LLM)

1. Chặn trước LLM bằng SQL — nhánh metadata phải xử lý phần lớn truy vấn.
2. Semantic cache 2 tầng ngay trong MariaDB.
3. Context caching cho system prompt (router + RAG), TTL 1 giờ, làm mới định kỳ.
4. **`thinking_budget = 0`** cho router và RAG thường — token suy luận tính vào output, là nguyên nhân vỡ ngân sách phổ biến nhất.
5. `max_output_tokens = 400`, prompt giới hạn 5 câu — output đắt gấp 4–6 lần input.
6. Rerank local để giảm top_k 15 → 4 (~65% input token nhánh RAG).
7. Viết lại câu hỏi độc lập ở router, bỏ lịch sử hội thoại ở bước sinh.
8. Embedding 768 chiều.
9. Batch API (giảm 50%) cho mọi tác vụ offline.
10. Quota 20.000 token/sinh viên/ngày trong `token_quota`; ngân sách ngày 1 USD. Chạm trần → **hạ cấp mượt** về chế độ chỉ metadata (miễn phí), không dừng phục vụ.

Ước tính: ~7,3 USD/tháng cho 50.000 truy vấn (đối chứng không tối ưu: ~870 USD — chênh >100 lần nằm ở kiến trúc, không phải model). Nạp ban đầu 5.000 tài liệu: ~4,5 USD một lần.

## 7. API

| Method | Endpoint | Mô tả |
|---|---|---|
| POST | `/api/v1/chat` | Body `{session_id, message}`, trả SSE: event `route` → `token`* → `citations` → `done` |
| GET | `/api/v1/documents` | Lọc theo course, type, semester |
| GET | `/api/v1/documents/{id}/download` | Kiểm quyền → trả `X-Accel-Redirect: /protected-files/...` cho nginx phục vụ |
| POST | `/api/v1/feedback` | `{log_id, helpful, comment}` |
| POST | `/api/v1/admin/documents` | Nạp tài liệu, status pending |
| POST | `/api/v1/admin/reindex/{doc_id}` | Nạp lại một tài liệu |
| GET | `/api/v1/admin/metrics` | Chi phí/ngày, cache hit rate, phân bố route |

## 8. Triển khai

- Thư mục: `/srv/edu-docbot/{app, venv, files, web, logs, .env}`. `.env` chứa `GEMINI_API_KEY`, `DB_DSN` — chmod 600, chown docbot, nạp qua `EnvironmentFile` của systemd, **không commit**.
- Tài khoản hệ thống `docbot` (nologin). Tài khoản DB `docbot@localhost` chỉ có SELECT/INSERT/UPDATE/DELETE trên `edu_docbot` — không DROP/CREATE/GRANT.
- Ba tiến trình: MariaDB (sẵn có), `edu-docbot.service` (uvicorn 127.0.0.1:8080, 2 workers, hardening: NoNewPrivileges, PrivateTmp, ProtectSystem=strict), nginx.
- Nạp tài liệu định kỳ: `edu-docbot-ingest.service` (oneshot) + timer `OnCalendar=*-*-* 02:00:00`.
- nginx: `client_max_body_size 100m`; location `/api/` proxy tới 8080 với **`proxy_buffering off`** (bắt buộc cho SSE — quên là lỗi triển khai hay gặp nhất: chat đứng im rồi hiện hết một lúc); location `/protected-files/` đặt `internal` alias tới `files/`; location `/` phục vụ build tĩnh.
- Sao lưu hằng ngày (systemd timer): `mysqldump --single-transaction --hex-blob` (**bắt buộc `--hex-blob` vì cột VECTOR là nhị phân**) + `rsync` thư mục files. Giữ 7 bản ngày, 4 bản tuần.

## 9. Bảo mật — nguyên tắc cứng

- **Lọc quyền TRƯỚC khi truy hồi** qua `allowed_doc_ids` — LLM không bao giờ nhìn thấy chunk ngoài quyền. Không lọc sau khi sinh.
- Cache chỉ trả khi khớp phạm vi quyền (`access`, `faculty_id`).
- File chỉ phục vụ qua nginx `internal` location, backend kiểm quyền rồi mới `X-Accel-Redirect`.
- Chống prompt injection: nội dung chunk bọc trong khối phân tách rõ; system prompt nêu rõ đoạn trích là dữ liệu, không phải chỉ thị.
- SQL luôn dùng tham số ràng buộc; danh sách `allowed_doc_ids` sinh đúng số placeholder, tuyệt đối không nối chuỗi.
- Router chặn `out_of_scope` (giải bài tập hộ…); chỉ nạp tài liệu được trường cấp phép.

## 10. Chỉ tiêu chất lượng (NFR)

| Chỉ tiêu | Mục tiêu |
|---|---|
| Độ trễ metadata | p95 < 800 ms |
| Độ trễ RAG (token đầu tiên) | p95 < 2,5 s |
| Chi phí Gemini @ 50k truy vấn/tháng | < 15 USD |
| recall@5 (bộ test 200 câu) | ≥ 0,85 |
| Trích dẫn đúng | ≥ 95% |
| Đồng thời | 100 concurrent |
| Rò rỉ tài liệu ngoài quyền | 0 |

Vận hành: cache hit ≥ 35% sau 1 tháng; route metadata ≥ 45%, RAG ≤ 30%; escalate cảnh báo nếu > 8%. Bộ eval 200 câu (80 tra cứu / 70 hỏi nội dung / 30 ngoài phạm vi / 20 nhiều lượt) chạy lại sau mỗi thay đổi prompt hoặc tham số truy hồi.

## 11. Lộ trình

1. **3 tuần** — kiểm tra phiên bản MariaDB, cấu hình full-text tiếng Việt, schema, pipeline nạp, tìm kiếm metadata → chatbot tra cứu chạy được (chưa RAG).
2. **3 tuần** — embedding, VECTOR INDEX, hybrid search, rerank, sinh trả lời kèm trích dẫn.
3. **2 tuần** — semantic cache, context caching, quota, dashboard chi phí → đạt NFR-03.
4. **2 tuần** — systemd + nginx trên máy chủ thật, bộ eval 200 câu, phân quyền, kiểm thử tải.

**Việc đầu tiên trước khi code:** `SELECT VERSION();` phải ≥ 11.7, rồi probe `CREATE TABLE _probe (v VECTOR(4) NOT NULL, VECTOR INDEX (v)) ENGINE=InnoDB; DROP TABLE _probe;`.

## 12. Khác biệt so với bản 1.0 (PostgreSQL) — tra nhanh

pgvector → VECTOR gốc · `<=>` → `VEC_DISTANCE_COSINE()` (bắt buộc ORDER BY + LIMIT) · tsvector/GIN → FULLTEXT + MATCH AGAINST · `unaccent()` → cột `_norm` ở tầng ứng dụng · `TEXT[]` → bảng `course_aliases` · JSONB → JSON (LONGTEXT + check) · BIGSERIAL → BIGINT UNSIGNED AUTO_INCREMENT · TIMESTAMPTZ → DATETIME quy ước UTC · `= ANY(:arr)` → `IN (...)` sinh placeholder động · Redis → bảng MariaDB · Celery → systemd timer · MinIO → thư mục đĩa + X-Accel-Redirect · Docker → systemd + nginx.

## Phụ lục — giá Gemini tham chiếu (08/2026, kiểm tra lại tại ai.google.dev trước khi báo cáo)

Flash-Lite: 0,10 / 0,40 USD/1M (in/out) · Gemini 3 Flash: 0,50 / 3,00 · embedding-001: 0,15 · Batch API: −50% · context cache hit: ≈10% giá input (+phí lưu theo giờ).
