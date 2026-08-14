# EDU-DOCBOT — giai đoạn 1

Chatbot tra cứu tài liệu học tập tiếng Việt. Giai đoạn 1: **metadata-first** —
tra cứu theo môn/loại/học kỳ/giảng viên bằng SQL + FULLTEXT, 0 token sinh;
chưa có RAG (giai đoạn 2). Thiết kế đầy đủ: [CLAUDE.md](CLAUDE.md), spec giai
đoạn 1: [docs/superpowers/specs/2026-08-13-edu-docbot-gd1-design.md](docs/superpowers/specs/2026-08-13-edu-docbot-gd1-design.md).

> **Hướng dẫn cài đặt & chạy chi tiết** (dev + production, khai metadata, xử lý
> lỗi thường gặp): [HUONG-DAN-CAI-DAT-VA-CHAY.md](HUONG-DAN-CAI-DAT-VA-CHAY.md).
> Phần dưới đây là bản rút gọn.

## Dựng trên server (Linux)

### 1. Kiểm tra MariaDB — làm TRƯỚC mọi thứ

```bash
mariadb -u root -p < scripts/00-preflight.sql
```

Soát output theo hướng dẫn cuối file. Quan trọng nhất: `innodb_ft_min_token_size`
phải bằng **1** trước khi nạp dữ liệu (sửa sau là phải dựng lại toàn bộ FULLTEXT
index). Nếu chưa đúng, thêm vào `/etc/mysql/mariadb.conf.d/50-server.cnf`:

```ini
[mariadb]
character-set-server      = utf8mb4
collation-server          = utf8mb4_unicode_ci
innodb_ft_min_token_size  = 1
innodb_ft_enable_stopword = OFF
mhnsw_max_cache_size      = 268435456
mhnsw_ef_search           = 40
```

rồi `systemctl restart mariadb` và chạy lại preflight.

Probe khả năng VECTOR (cần cho giai đoạn 2, không chặn giai đoạn 1):

```bash
mysql -u root -p edu_docbot < scripts/01b-probe-vector-cloudsql.sql   # Cloud SQL for MySQL (server thật)
# mariadb -u root -p < scripts/mariadb-only-probe-vector.sql          # chỉ khi server là MariaDB
```

Báo lỗi `1064 near 'VECTOR'` = server < 11.7 → lên kế hoạch nâng cấp trước giai đoạn 2.

### 2. Schema + seed

```bash
mariadb -u root -p < migrations/001_init.sql
mariadb -u root -p edu_docbot < migrations/002_seed_demo.sql   # dữ liệu test
```

Tạo tài khoản ứng dụng (mẫu ở cuối `001_init.sql` — chỉ SELECT/INSERT/UPDATE/DELETE).

### 3. Ứng dụng

```bash
sudo mkdir -p /srv/edu-docbot/{app,files,web,logs}
# copy mã nguồn vào /srv/edu-docbot/app, web/ vào /srv/edu-docbot/web

cd /srv/edu-docbot
python3.12 -m venv venv
venv/bin/pip install -r app/requirements.txt

cp app/.env.example .env    # điền DB_DSN, GOOGLE_CLOUD_PROJECT...
chmod 600 .env
```

`.env` không commit; systemd nạp qua `EnvironmentFile`.

### 4. Nạp tài liệu

Thả file (`.pdf` `.docx` `.pptx` `.txt` `.md`) vào `/srv/edu-docbot/files/` rồi:

```bash
venv/bin/python -m ingest.cli scan
```

Metadata bóc theo 3 tầng — trượt hết thì file vào `pending_review`, xem bằng:

```bash
venv/bin/python -m ingest.cli review
```

Cách khai metadata (chọn một):

1. **Tên file** — có mã môn + từ khóa loại: `MAT1093_giao-trinh_giai-tich-1_20261.pdf`
2. **Thư mục** — `files/MAT1093/de_thi/giua-ky-2025.pdf`
3. **`files/manifest.csv`** — ghi đè từng file:
   ```csv
   file_path,title,course_code,doc_type,semester,lecturer,access
   uploads/tailieu.pdf,Giáo trình Giải tích 1,MAT1093,giao_trinh,20261,Nguyễn Văn An,public
   ```

`doc_type` ∈ `giao_trinh slide de_cuong de_thi bai_tap tai_lieu_tham_khao quy_che`;
`access` ∈ `public faculty course staff`; học kỳ dạng `20261` (= HK1 2026-2027).

### 5. Chạy

Dev:

```bash
DEV_SERVE_FILES=1 venv/bin/uvicorn app.main:app --port 8080 --reload
```

Mở `http://localhost:8080/` — trang chat có sẵn, chọn người dùng test ở góc phải
để thử phân quyền. Swagger: `/docs`.

Sản xuất: copy 3 unit trong `deploy/` vào `/etc/systemd/system/`, nginx config
vào `sites-available`, rồi:

```bash
systemctl daemon-reload
systemctl enable --now edu-docbot edu-docbot-ingest.timer
```

**nginx phải có `proxy_buffering off` cho `/api/`** (đã có trong
`deploy/nginx-edu-docbot.conf`) — thiếu là SSE bị đơ.

## Thử nhanh

```bash
# Chat không stream (đường của bộ eval)
curl -s localhost:8080/api/v1/chat -H 'Content-Type: application/json' \
  -H 'X-User-Id: 1' \
  -d '{"session_id":"test-1","message":"giáo trình giải tích 1 có không","stream":false}'

# Chat SSE
curl -N localhost:8080/api/v1/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"test-1","message":"đề thi XSTK kỳ 1 2026","stream":true}'

# Danh sách tài liệu + gợi ý môn
curl -s 'localhost:8080/api/v1/documents?course=MAT1093'
curl -s 'localhost:8080/api/v1/courses?q=giai+tich'

# Metrics (cần user role=admin, seed: id=4)
curl -s localhost:8080/api/v1/admin/metrics -H 'X-User-Id: 4'
```

## Kiểm thử

```bash
venv/bin/pip install pytest httpx
venv/bin/python -m pytest tests/ -v            # unit, không cần CSDL
venv/bin/python -m pytest tests/ -v -m db      # tích hợp, cần CSDL đã migrate
```

## Cấu trúc

```
app/          FastAPI: api/ (endpoint), core/ (router, truy hồi, cache, quota), llm/ (Vertex)
ingest/       pipeline nạp: extract → chunk → meta 3 tầng → loader; CLI
migrations/   001_init.sql (không VECTOR — chạy từ MariaDB 10.6), 002_seed_demo.sql
scripts/      00-preflight.sql, 01b-probe-vector-cloudsql.sql, mariadb-only-probe-vector.sql
deploy/       systemd units + nginx
web/          trang chat tĩnh
tests/
```

## Điểm thiết kế cần nhớ khi sửa code

- **Lọc quyền trước truy hồi**: mọi truy vấn chạm `documents` phải mang mệnh đề
  của `app/core/access.py::access_clause`. Ngoài quyền trả 404, không 403.
- **`thinking_budget=0`** ép sẵn trong `app/llm/vertex.py` — đường gọi LLM duy nhất.
- **`vn_norm()`** (`app/vn.py`) là nguồn duy nhất của chuẩn hóa tiếng Việt; sửa nó
  là phải resync + dựng lại FULLTEXT.
- **Bảng `courses` gộp**: cột dẫn xuất chỉ được ghi qua `save_course()`;
  sửa JSON tay thì chạy `ingest.cli resync-derived`.
- Cache tầng hash khóa theo `(câu hỏi, access_scope, faculty_scope)`; kết quả có
  tài liệu `access='course'` **không được cache**.
- Giai đoạn 2 (RAG): chạy `migrations/002_vector.sql` (sẽ viết ở giai đoạn 2),
  thay ruột `chunk_search` bằng hybrid + rerank — hợp đồng API giữ nguyên.
