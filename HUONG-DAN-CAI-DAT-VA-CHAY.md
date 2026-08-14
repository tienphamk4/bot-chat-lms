# EDU-DOCBOT — Hướng dẫn cài đặt & chạy (giai đoạn 1)

> Áp dụng cho bản giai đoạn 1: tra cứu tài liệu metadata-first, chưa có RAG.
> Có hai kịch bản: **A. Chạy thử trên máy dev** (Windows/Mac/Linux) và
> **B. Triển khai trên server Linux** (systemd + nginx, đúng thiết kế).
> Phần lỗi thường gặp ở cuối file.

---

## 0. Yêu cầu

| Thành phần | Yêu cầu | Ghi chú |
|---|---|---|
| MariaDB | **≥ 10.6** cho giai đoạn 1; **≥ 11.7** mới chạy được giai đoạn 2 (VECTOR) | Đã có sẵn trên server |
| Python | 3.12 | Server: `python3.12`; máy dev Windows có thể dùng `uv` |
| nginx | bất kỳ bản còn hỗ trợ | Chỉ cần cho production |
| Vertex AI | project GCP + service account có quyền `aiplatform.user` | Chỉ cần khi bật router LLM; tắt đi vẫn demo được |

---

## 1. Kiểm tra & cấu hình CSDL — LÀM TRƯỚC MỌI THỨ

> **Server dùng MySQL thay vì MariaDB?** Vẫn chạy được giai đoạn 1 với MySQL
> **≥ 8.0.16**: thay lệnh `mariadb` bằng `mysql` trong mọi bước dưới đây; file
> cấu hình thường là `/etc/mysql/mysql.conf.d/mysqld.cnf` và section là
> `[mysqld]` thay cho `[mariadb]` (các biến `innodb_ft_*` giữ nguyên tên; hai
> biến `mhnsw_*` không tồn tại trên MySQL — bỏ qua). Bỏ bước probe
> `mariadb-only-probe-vector.sql` — file đó của MariaDB, trên MySQL báo 1064
> là kết quả mong đợi.
>
> **Server là Cloud SQL for MySQL (version có đuôi `-google`)?** Đây chính là
> môi trường thật của dự án. Giai đoạn 1 chạy nguyên trạng. Giai đoạn 2 dùng
> vector search gốc của Cloud SQL (thuật toán ScaNN, đủ COSINE): bật flag
> `cloudsql_vector=on` cho instance rồi chạy probe riêng
> `scripts/01b-probe-vector-cloudsql.sql` để xác nhận. Trên Cloud SQL không
> sửa được my.cnf trực tiếp — các biến như `innodb_ft_min_token_size` đặt qua
> database flags: `gcloud sql instances patch <INSTANCE>
> --database-flags=innodb_ft_min_token_size=1,cloudsql_vector=on` (instance sẽ
> restart).

### 1.1 Chạy preflight (chỉ đọc, không sửa gì)

```bash
mariadb -u root -p < scripts/00-preflight.sql
```

Đọc output theo bảng:

| Mục | Phải là | Nếu sai |
|---|---|---|
| `character_set_server` | `utf8mb4` | Sửa cấu hình (bước 1.2), restart |
| `collation_server` | `utf8mb4_unicode_ci` | Sửa cấu hình, restart |
| `innodb_ft_min_token_size` | **1** | **BẮT BUỘC sửa trước khi nạp dữ liệu.** Sửa sau khi đã nạp = restart + dựng lại toàn bộ FULLTEXT index |
| `innodb_ft_enable_stopword` | `OFF` | Sửa cấu hình, restart |
| `VERSION()` | ≥ 11.7 nếu muốn làm giai đoạn 2 | < 11.7 vẫn chạy giai đoạn 1 bình thường, nhưng lên lịch nâng cấp ngay |

### 1.2 Cấu hình (nếu chưa đúng)

Thêm vào `/etc/mysql/mariadb.conf.d/50-server.cnf`:

```ini
[mariadb]
character-set-server      = utf8mb4
collation-server          = utf8mb4_unicode_ci
innodb_ft_min_token_size  = 1     # âm tiết tiếng Việt 1-2 ký tự ("lý", "đề")
innodb_ft_enable_stopword = OFF
mhnsw_max_cache_size      = 268435456   # 256 MB — dùng ở giai đoạn 2
mhnsw_ef_search           = 40
```

```bash
sudo systemctl restart mariadb
```

Chạy lại preflight (1.1) để xác nhận.

### 1.3 Probe VECTOR (chuẩn bị cho giai đoạn 2 — không chặn giai đoạn 1)

Server thật của dự án (Cloud SQL for MySQL):

```bash
mysql -u root -p edu_docbot < scripts/01b-probe-vector-cloudsql.sql
```

Nếu server là MariaDB:

```bash
mariadb -u root -p edu_docbot < scripts/mariadb-only-probe-vector.sql
```

- Chạy trọn vẹn, cuối cùng in `PROBE VECTOR OK` → server sẵn sàng cho giai đoạn 2.
- Báo `ERROR 1064 ... near 'VECTOR'` → server < 11.7. Giai đoạn 1 vẫn triển khai
  bình thường; ghi lại việc nâng cấp vào kế hoạch.

### 1.4 Tạo schema + tài khoản ứng dụng + dữ liệu test

```bash
mariadb -u root -p < migrations/001_init.sql
```

```bash
mariadb -u root -p edu_docbot < migrations/002_seed_demo.sql
```

`002_seed_demo.sql` chỉ dành cho demo/test: 3 khoa, 4 môn (GT1, XSTK, OOP, Web),
4 người dùng test. **Không chạy trên môi trường thật.**

Tạo tài khoản ứng dụng (đổi mật khẩu):

```bash
mariadb -u root -p -e "CREATE USER IF NOT EXISTS 'docbot'@'localhost' IDENTIFIED BY 'DOI_MAT_KHAU_NAY'; GRANT SELECT, INSERT, UPDATE, DELETE ON edu_docbot.* TO 'docbot'@'localhost'; FLUSH PRIVILEGES;"
```

Tài khoản này cố ý **không có** DROP/CREATE/ALTER/GRANT.

---

## 2. Kịch bản A — chạy thử trên máy dev

### 2.1 Cài Python + thư viện

> Máy Windows dev của dự án **đã có sẵn `.venv`** (dựng bằng `uv`) — bỏ qua
> bước này, sang thẳng 2.2.

**Windows (PowerShell)** — dùng `uv`, tự tải Python 3.12 nếu máy chưa có:

```bash
uv venv .venv --python 3.12
```

```bash
uv pip install -r requirements.txt --python .venv
```

(Lệnh `python3.12` không tồn tại trên Windows; nếu đã cài Python từ python.org
thì thay bằng `py -3.12 -m venv .venv` rồi `.venv\Scripts\pip install -r requirements.txt`.)

**Linux/Mac:**

```bash
python3.12 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

### 2.2 Cấu hình `.env`

```bash
cp .env.example .env
```

Mở `.env` và điền — ý nghĩa từng nhóm:

| Biến | Điền gì |
|---|---|
| `DB_DSN` | `mysql+pymysql://docbot:MAT_KHAU@<host-db>:3306/edu_docbot?charset=utf8mb4`. **Cloud SQL (môi trường thật):** `<host-db>` là IP của instance — đúng host mà DBeaver đang kết nối; IP máy bạn phải nằm trong Authorized Networks; tài khoản app tạo với host `'docbot'@'%'` (không phải `@'localhost'`). Server tự quản: IP server + mở firewall/bind-address, hoặc SSH tunnel `ssh -L 3306:127.0.0.1:3306 user@server` |
| `FILES_ROOT` | Thư mục chứa tài liệu trên máy chạy app, ví dụ `./files` khi dev |
| `USE_LLM_ROUTER` | `0` = chỉ router rule-based, **không tốn token nào**, demo tra cứu vẫn chạy đủ. `1` = bật Vertex |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | Chỉ cần khi `USE_LLM_ROUTER=1` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Đường dẫn file JSON service account. Bỏ trống nếu máy đã `gcloud auth application-default login` |
| Còn lại | Để mặc định được |

### 2.3 Nạp tài liệu

Tạo thư mục và thả file vào (`.pdf` `.docx` `.pptx` `.txt` `.md`):

```bash
mkdir -p files
```

```bash
.venv/bin/python -m ingest.cli scan              # Windows: .venv\Scripts\python
```

Xem file nào chưa nạp được và vì sao:

```bash
.venv/bin/python -m ingest.cli review
```

Ba cách khai metadata (chọn một là đủ, xem chi tiết ở mục 4):
tên file có mã môn + loại · xếp theo thư mục `files/<MÃ MÔN>/<loại>/` · khai trong `files/manifest.csv`.

### 2.4 Chạy

```bash
DEV_SERVE_FILES=1 .venv/bin/uvicorn app.main:app --port 8080 --reload
```

Windows (PowerShell):

```bash
$env:DEV_SERVE_FILES='1'; .venv\Scripts\uvicorn app.main:app --port 8080 --reload
```

`DEV_SERVE_FILES=1` cho phép tải file trực tiếp khi không có nginx — **chỉ dùng khi dev**.

Mở:

- `http://localhost:8080/` — trang chat. Góc phải có ô **"Đăng nhập thử"** đổi
  người dùng test để xem phân quyền hoạt động (khách chỉ thấy tài liệu public).
- `http://localhost:8080/docs` — Swagger UI thử từng endpoint.
- `http://localhost:8080/readyz` — phải trả `{"status":"ok","database":"ok"}`;
  nếu `degraded` là chưa nối được DB (xem mục Lỗi thường gặp).

### 2.5 Thử nhanh bằng curl

```bash
curl -s localhost:8080/api/v1/chat -H 'Content-Type: application/json' -H 'X-User-Id: 1' -d '{"session_id":"demo-1","message":"giáo trình giải tích 1 có không","stream":false}'
```

```bash
curl -N localhost:8080/api/v1/chat -H 'Content-Type: application/json' -d '{"session_id":"demo-1","message":"đề thi XSTK kỳ 1 2026","stream":true}'
```

```bash
curl -s 'localhost:8080/api/v1/documents?course=MAT1093' -H 'X-User-Id: 1'
```

```bash
curl -s localhost:8080/api/v1/admin/metrics -H 'X-User-Id: 4'
```

`X-User-Id` theo seed: `1`=sv_toan · `2`=sv_cntt · `3`=gv_toan · `4`=admin · bỏ header = khách.

### 2.6 Chạy test

```bash
.venv/bin/python -m pytest tests/ -v
```

Không cần CSDL — 95 test unit chạy trong ~1 giây. Test tích hợp (cần DB đã
migrate) chạy bằng `pytest -m db`.

---

## 3. Kịch bản B — triển khai server Linux (production)

### 3.1 Chuẩn bị thư mục & tài khoản hệ thống

```bash
sudo useradd --system --shell /usr/sbin/nologin docbot
```

```bash
sudo mkdir -p /srv/edu-docbot/{app,files,web,logs}
```

Copy mã nguồn (`app/ ingest/ migrations/ scripts/ requirements.txt`) vào
`/srv/edu-docbot/app/`, nội dung `web/` vào `/srv/edu-docbot/web/`.

### 3.2 Python + thư viện

```bash
cd /srv/edu-docbot && sudo python3.12 -m venv venv && sudo venv/bin/pip install -r app/requirements.txt
```

### 3.3 `.env`

```bash
sudo cp app/.env.example /srv/edu-docbot/.env
```

Điền như mục 2.2 (DB_DSN trỏ `127.0.0.1`, `FILES_ROOT=/srv/edu-docbot/files`), rồi khóa quyền:

```bash
sudo chown docbot:docbot /srv/edu-docbot/.env && sudo chmod 600 /srv/edu-docbot/.env
```

```bash
sudo chown -R docbot:docbot /srv/edu-docbot
```

**Không commit `.env`.** systemd nạp nó qua `EnvironmentFile`.

Nếu dùng service account cho Vertex: đặt file JSON tại
`/srv/edu-docbot/sa.json`, `chmod 600`, `chown docbot`, và trỏ
`GOOGLE_APPLICATION_CREDENTIALS=/srv/edu-docbot/sa.json` trong `.env`.

### 3.4 systemd

```bash
sudo cp deploy/edu-docbot.service deploy/edu-docbot-ingest.service deploy/edu-docbot-ingest.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now edu-docbot edu-docbot-ingest.timer
```

Kiểm tra:

```bash
systemctl status edu-docbot --no-pager && curl -s 127.0.0.1:8080/readyz
```

- `edu-docbot.service` — API (uvicorn 127.0.0.1:8080, 2 workers, hardening sẵn).
- `edu-docbot-ingest.timer` — quét `files/` nạp tài liệu mới lúc **2h sáng** hằng ngày.
  Chạy tay ngay: `sudo systemctl start edu-docbot-ingest`.

### 3.5 nginx

```bash
sudo cp deploy/nginx-edu-docbot.conf /etc/nginx/sites-available/edu-docbot
```

Sửa `server_name` theo tên miền thật, rồi:

```bash
sudo ln -s /etc/nginx/sites-available/edu-docbot /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx
```

File config đã có sẵn 3 điểm bắt buộc — đừng bỏ khi tự viết lại:

1. `proxy_buffering off` cho `/api/` — **thiếu là SSE đơ** (chat đứng im rồi hiện hết một lúc).
2. `location /protected-files/ { internal; ... }` — file chỉ ra ngoài qua
   `X-Accel-Redirect` sau khi backend kiểm quyền.
3. `client_max_body_size 100m` — khớp trần upload của API.

TLS: `sudo certbot --nginx -d <tên miền>` (khuyến nghị).

### 3.6 Nạp tài liệu trên server

```bash
sudo -u docbot /srv/edu-docbot/venv/bin/python -m ingest.cli scan
```

(hoặc để timer tự chạy đêm). Soát file lỗi:

```bash
sudo -u docbot /srv/edu-docbot/venv/bin/python -m ingest.cli review
```

### 3.7 Sao lưu hằng ngày

Thiết kế yêu cầu `mysqldump --single-transaction --hex-blob` (**`--hex-blob` bắt
buộc** — từ giai đoạn 2 cột VECTOR là nhị phân, thiếu flag là bản dump hỏng):

```bash
mysqldump --single-transaction --hex-blob edu_docbot | gzip > /backup/edu_docbot_$(date +%F).sql.gz
```

kèm `rsync` thư mục `files/`. Giữ 7 bản ngày, 4 bản tuần.

---

## 4. Khai metadata cho tài liệu — 3 cách

Trình nạp thử lần lượt: **tên file → thư mục → manifest.csv** (manifest thắng
tất cả). File không xác định được môn/loại sẽ vào trạng thái `pending_review` —
không hỏng mẻ nạp, xem bằng `ingest.cli review`.

**Cách 1 — đặt tên file** có mã môn (chữ dính liền số: `MAT1093`) và từ khóa loại:

```text
MAT1093_giao-trinh_giai-tich-1_20261.pdf
INT2204 de thi cuoi ky 2025.pdf
slide_bai_giang_tuan1_INT2204.pptx
quy-che-dao-tao-2026.pdf          ← quy chế không cần mã môn
```

**Cách 2 — xếp thư mục:**

```text
files/MAT1093/de_thi/giua-ky-2025.pdf
files/INT2204/giao_trinh/oop-c++.pdf
```

**Cách 3 — `files/manifest.csv`** (ghi đè từng file, dùng cho file không đổi tên được):

```csv
file_path,title,course_code,doc_type,semester,lecturer,access
uploads/tai-lieu-cu.pdf,Giáo trình Giải tích 1,MAT1093,giao_trinh,20261,Nguyễn Văn An,public
noi-bo/de-thi-nhap.pdf,Đề thi giữa kỳ,MAT1093,de_thi,20252,,faculty
```

Giá trị hợp lệ:

- `doc_type`: `giao_trinh` `slide` `de_cuong` `de_thi` `bai_tap` `tai_lieu_tham_khao` `quy_che`
- `access`: `public` (ai cũng xem) · `faculty` (đúng khoa) · `course` (đúng lớp học phần) · `staff` (giảng viên/cán bộ)
- `semester`: dạng `20261` = học kỳ 1 năm học 2026-2027

Sau khi sửa manifest cho file đã nạp lỗi: chạy lại `ingest.cli scan` (file cũ
giữ nguyên checksum sẽ được bỏ qua; file đang `pending_review` cần
`ingest.cli reindex <id>` với id lấy từ lệnh `review`).

---

## 4b. Giai đoạn 2 — RAG (đã triển khai)

Trạng thái: **đang chạy trên Cloud SQL**. Câu hỏi nội dung ("quy chế thi lại quy
định thế nào") giờ trả lời bằng văn xuôi ≤5 câu kèm trích dẫn [n], sinh bởi
Flash-Lite trên các chunk truy hồi hybrid (FULLTEXT + vector, RRF k=60).

Thiết lập trên môi trường mới (làm một lần, theo thứ tự):

```bash
python -m ingest.cli migrate-vector    # thêm cột embedding VECTOR(768) — idempotent
```

```bash
python -m ingest.cli embed             # lấp embedding cho chunk đã có
```

Từ đó trở đi **không cần làm gì thêm**: mỗi lượt `scan` (CLI hoặc nút "Nạp tài
liệu") tự embed chunk mới ở cuối. Cần credential Gemini (mục API key) — không
có thì embed bị bỏ qua êm, câu hỏi nội dung rơi về chế độ trích nguyên văn.

Tùy chọn khi corpus lớn:

- `python -m ingest.cli build-vector-index` — tạo index ScaNN (chỉ đáng làm khi
  ≥ ~10k chunk; dưới đó exact KNN đủ nhanh và chính xác tuyệt đối).
- Rerank local (KT-06): `pip install -r requirements-rerank.txt` rồi đặt
  `RERANK_ENABLED=1` — giảm ~65% input token nhánh RAG. Không bật thì dùng thứ
  tự RRF, vẫn đúng.

Thang hạ cấp của nhánh nội dung (tự động, không cần cấu hình):
RAG → trích nguyên văn FULLTEXT → "không tìm thấy". Quota chạm trần → bỏ RAG
(KT-10). Trích dẫn [n] không ánh xạ được về chunk thật → **cắt cả câu** trước
khi trả về.

## 5. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Chat "đứng im" rồi hiện cả câu một lúc | nginx đang buffer SSE | `proxy_buffering off` trong location `/api/` rồi `nginx -s reload`. Config mẫu đã có sẵn |
| `/readyz` trả `degraded` | App không nối được DB | Kiểm `DB_DSN` trong `.env`; DB từ máy khác cần `bind-address` + firewall, hoặc SSH tunnel |
| Tìm "lý", "đề", "kỳ" không ra kết quả | `innodb_ft_min_token_size` vẫn là 3 khi nạp | Sửa cấu hình (mục 1.2), restart MariaDB, **nạp lại toàn bộ tài liệu** (`DELETE` documents rồi `scan`, hoặc `reindex` từng file) |
| File nào cũng vào `pending_review` | Tên file không có mã môn/loại, không có manifest | Dùng một trong 3 cách ở mục 4; gửi ~10 tên file thật để bổ sung mẫu regex |
| Tải file trả 404 khi dev | Không có nginx, chưa bật cờ dev | Chạy uvicorn với `DEV_SERVE_FILES=1` |
| Tải file trả trang trắng/404 trên production | Thiếu location `internal` | Kiểm `location /protected-files/` có `internal` + `alias` đúng tới thư mục `files/` |
| `ERROR 1064 near 'VECTOR'` khi chạy probe | MariaDB < 11.7 | Bình thường với giai đoạn 1. Nâng cấp trước khi làm giai đoạn 2 |
| Router lúc nào cũng `router_source=rule` | `USE_LLM_ROUTER=0`, thiếu `GOOGLE_CLOUD_PROJECT`, hoặc credential sai | Xem log service: `journalctl -u edu-docbot -n 50`. Hệ thống cố ý hạ cấp mượt thay vì báo lỗi cho người dùng |
| Trả lời có kèm `degraded: true` | Chạm quota ngày của user (20k token) hoặc ngân sách ngày ($1) | Đúng thiết kế KT-10 — chỉ tắt LLM, tra cứu vẫn chạy. Đổi trần trong `.env` |
| Sửa `aliases`/`offerings` bằng SQL tay xong tìm môn không ra | Cột dẫn xuất bị lệch | `python -m ingest.cli resync-derived` |

---

## 6. Tra cứu nhanh

| Việc | Lệnh |
|---|---|
| Nạp toàn bộ thư mục (kèm embed) | `python -m ingest.cli scan` |
| Embed chunk còn thiếu | `python -m ingest.cli embed` |
| Thêm cột vector (một lần) | `python -m ingest.cli migrate-vector` |
| Tạo index ScaNN (corpus ≥10k chunk) | `python -m ingest.cli build-vector-index` |
| Nạp một file | `python -m ingest.cli one files/x.pdf` |
| Nạp lại tài liệu #42 | `python -m ingest.cli reindex 42` |
| Danh sách file lỗi/chờ | `python -m ingest.cli review` |
| Dựng lại cột dẫn xuất courses | `python -m ingest.cli resync-derived` |
| Log API trên server | `journalctl -u edu-docbot -f` |
| Log nạp đêm qua | `journalctl -u edu-docbot-ingest --since yesterday` |
| Chi phí & phân bố route | `GET /api/v1/admin/metrics` (user role admin) |
| Test | `python -m pytest tests/ -v` |
