# Triển khai EDU-DOCBOT lên GCP (VM cùng hạ tầng với Cloud SQL) + nâng quota Vertex

> Ưu tiên 1 & 2 của kế hoạch tối ưu (13/08/2026). Làm xong: câu metadata < 1s,
> RAG ~2,5–3,5s, hết các cú chậm ngẫu nhiên do 429.
> Project: `project-4fc51df2-b169-4533-bf5` · Cloud SQL: `34.71.3.102`.

---

## PHẦN 1 — Đưa app lên VM cùng vùng với Cloud SQL

### 1.1 Xem vùng (region) của Cloud SQL

Console → **SQL** → bấm vào instance → trang Overview → dòng **Region**
(ví dụ `us-central1`). VM PHẢI tạo cùng vùng này — khác vùng là mất tác dụng.
Cũng tại trang này, chép lại **Connection name** (dạng
`project-4fc51df2-b169-4533-bf5:us-central1:TEN_INSTANCE`) — bước 1.5 cần.

### 1.2 Tạo VM

Console → **Compute Engine → VM instances → Create instance**:

| Mục | Chọn |
|---|---|
| Name | `edu-docbot` |
| Region | **cùng region với Cloud SQL** (1.1) |
| Machine type | `e2-small` (2 vCPU/2GB — đủ demo; muốn bật rerank sau này thì `e2-medium` 4GB) |
| Boot disk | **Ubuntu 24.04 LTS** (có sẵn Python 3.12), 20 GB |
| Firewall | tick **Allow HTTP traffic** (và HTTPS nếu sẽ gắn TLS) |
| Identity and API access → Service account | Compute Engine default service account |
| → Access scopes | **Set access for each API** → Cloud Platform: Enabled (hoặc chọn "Allow full access to all Cloud APIs") |

Bấm Create. Ghi lại **External IP** của VM.

Gán quyền cho service account (một lần): Console → **IAM & Admin → IAM** → tìm
dòng `...-compute@developer.gserviceaccount.com` → Edit (bút chì) → Add role:

- **Vertex AI User** (gọi Gemini — VM tự có ADC, KHÔNG cần file key JSON)
- **Cloud SQL Client** (cho Auth Proxy ở 1.5)

### 1.3 Chép code lên VM

Cách A — có gcloud trên máy Windows (PowerShell, tại thư mục dự án):

```bash
gcloud compute scp --recurse app ingest migrations scripts web deploy requirements.txt .env.example edu-docbot:~/src --zone=<ZONE_CUA_VM>
```

```bash
gcloud compute scp --recurse files edu-docbot:~/src/files --zone=<ZONE_CUA_VM>
```

Cách B — không có gcloud: nén thư mục dự án thành `.zip` (loại trừ `.venv`),
mở **SSH** từ trang VM instances (nút SSH → cửa sổ trình duyệt), bấm bánh răng
góc phải → **Upload file** → tải zip lên → `unzip`.

### 1.4 Cài đặt trên VM (dán từng khối vào cửa sổ SSH)

```bash
sudo apt update && sudo apt install -y python3.12-venv nginx unzip
```

```bash
sudo useradd --system --shell /usr/sbin/nologin docbot 2>/dev/null; sudo mkdir -p /srv/edu-docbot/{files,web,logs} && sudo cp -r ~/src/{app,ingest,migrations,scripts,requirements.txt} /srv/edu-docbot/ && sudo cp -r ~/src/web/* /srv/edu-docbot/web/ && sudo cp -r ~/src/files/* /srv/edu-docbot/files/ 2>/dev/null; true
```

```bash
cd /srv/edu-docbot && sudo python3.12 -m venv venv && sudo venv/bin/pip install -r requirements.txt
```

### 1.5 Cloud SQL Auth Proxy (kết nối DB không cần mở IP)

```bash
sudo curl -Lo /usr/local/bin/cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.2/cloud-sql-proxy.linux.amd64 && sudo chmod +x /usr/local/bin/cloud-sql-proxy
```

Tạo service (THAY `CONNECTION_NAME` bằng giá trị ở bước 1.1):

```bash
sudo tee /etc/systemd/system/cloud-sql-proxy.service > /dev/null <<'EOF'
[Unit]
Description=Cloud SQL Auth Proxy
After=network.target

[Service]
ExecStart=/usr/local/bin/cloud-sql-proxy --address 127.0.0.1 --port 3306 CONNECTION_NAME
Restart=always
User=docbot

[Install]
WantedBy=multi-user.target
EOF
sudo sed -i "s/CONNECTION_NAME/project-4fc51df2-b169-4533-bf5:REGION:TEN_INSTANCE/" /etc/systemd/system/cloud-sql-proxy.service
sudo systemctl daemon-reload && sudo systemctl enable --now cloud-sql-proxy
```

Kiểm tra: `systemctl status cloud-sql-proxy` phải `active (running)`.

### 1.6 Cấu hình `.env`

```bash
sudo tee /srv/edu-docbot/.env > /dev/null <<'EOF'
DB_DSN=mysql+pymysql://docbot:MAT_KHAU@127.0.0.1:3306/edu_docbot?charset=utf8mb4
FILES_ROOT=/srv/edu-docbot/files
XACCEL_PREFIX=/protected-files
USE_LLM_ROUTER=1
GEMINI_API_KEY=
GOOGLE_CLOUD_PROJECT=project-4fc51df2-b169-4533-bf5
LLM_LOCATION=global
EMBED_LOCATION=us-central1
ROUTER_MODEL=gemini-3.5-flash-lite
GEN_MODEL=gemini-3.5-flash-lite
QUOTA_DAILY_TOKENS=20000
DAILY_BUDGET_USD=1.0
CACHE_TTL_SECONDS=86400
LOG_LEVEL=INFO
EOF
sudo chmod 600 /srv/edu-docbot/.env && sudo chown -R docbot:docbot /srv/edu-docbot
```

(Sửa `MAT_KHAU` = mật khẩu user `docbot@'%'` đã tạo. Không có
`GOOGLE_APPLICATION_CREDENTIALS` — VM tự xác thực bằng service account.)

### 1.7 Chạy service + nginx

```bash
sudo cp ~/src/deploy/edu-docbot.service ~/src/deploy/edu-docbot-ingest.service ~/src/deploy/edu-docbot-ingest.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now edu-docbot edu-docbot-ingest.timer
```

```bash
sudo cp ~/src/deploy/nginx-edu-docbot.conf /etc/nginx/sites-available/edu-docbot && sudo sed -i "s/server_name .*/server_name _;/" /etc/nginx/sites-available/edu-docbot && sudo ln -sf /etc/nginx/sites-available/edu-docbot /etc/nginx/sites-enabled/ && sudo rm -f /etc/nginx/sites-enabled/default && sudo nginx -t && sudo systemctl reload nginx
```

### 1.8 Nghiệm thu

```bash
curl -s 127.0.0.1:8080/readyz
```

→ phải ra `{"status":"ok","database":"ok"}`. Rồi từ máy bạn mở
`http://<EXTERNAL_IP_CUA_VM>/` — trang chat chạy, hỏi thử và **bấm nút ⏱**:
các bước `(DB)` phải còn vài chục ms.

Lỗi hay gặp: `readyz` degraded → xem `journalctl -u edu-docbot -n 30` (thường
sai mật khẩu trong DSN) và `journalctl -u cloud-sql-proxy -n 30` (sai
connection name / thiếu role Cloud SQL Client).

---

## PHẦN 2 — Nâng quota Vertex (hết 429)

1. Console → thanh tìm kiếm gõ **"Quotas"** → **IAM & Admin → Quotas & System Limits**.
2. Ô Filter: **Service: Vertex AI API**.
3. Tìm và nâng các quota sau (bấm vào dòng → **Edit quota** → điền giá trị + lý do "chatbot RAG for university, production testing" → Submit):

| Quota | Dimension cần chú ý | Đề xuất |
|---|---|---|
| Generate content requests per minute per project per base model | base_model = `gemini-3.5-flash-lite` (và/hoặc `gemini-2.5-flash-lite`), region = `global` | 200/phút |
| Embed content requests per minute (hoặc Prediction requests với gemini-embedding) | model embedding, region = `us-central1` | 200/phút |

4. Mức nhỏ thường được duyệt tự động trong vài phút; mức lớn chờ email.
5. Kiểm tra thêm **Billing**: project đang ở free trial/không gắn thẻ thì trần
   rất thấp — Billing → xác nhận tài khoản thanh toán active.

Kiểm chứng sau khi nâng: hỏi liên tiếp 5-6 câu RAG khác nhau — timeline không
còn bước `(Vertex)` nào nhảy lên hàng chục giây.

---

## Sau khi xong cả hai

- Demo chạy ở `http://<IP_VM>/` — cố định, không phụ thuộc máy dev.
- Máy dev vẫn dùng `.env` cũ chạy song song được (DB chung).
- Việc còn nên làm tiếp trên VM: gắn tên miền + TLS (`certbot --nginx`), đổi
  đầu đọc `X-User-Id` thành đăng nhập thật khi đồng bộ users, bật rerank nếu
  chọn máy 4GB.
