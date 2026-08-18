# Triển khai EDU-DOCBOT lên Google Cloud + CI/CD bằng GitHub Actions

> Sổ tay chạy từ số 0: dựng VM, nối Cloud SQL, lên sóng bằng IP tĩnh, rồi tự
> động deploy mỗi lần `git push`. Cập nhật 14/08/2026.

---

## 0. Bức tranh tổng thể

```
                    ┌──────────── VM Compute Engine (cùng region Cloud SQL) ────────────┐
Internet ─► IP tĩnh ─► nginx :80                                                        │
                    │    ├─ /                 → /srv/edu-docbot/web   (HTML/CSS/JS tĩnh) │
                    │    ├─ /api/             → uvicorn 127.0.0.1:8080 (proxy_buffering off) │
                    │    └─ /protected-files/ → /srv/edu-docbot/files  (internal)        │
                    │                              ▲                                     │
                    │   systemd: edu-docbot.service ┴─► cloud-sql-proxy 127.0.0.1:3306 ──┼─► Cloud SQL MySQL 8.4
                    │                                └─► Gemini API (sinh, embedding, OCR)│
                    └───────────────────────────────────────────────────────────────────┘
                                          ▲
GitHub push main ─► Actions: pytest ─xanh─┘ scp qua IAP → rsync → restart → kiểm /readyz
```

**Chi phí thêm mỗi tháng** (ngoài Cloud SQL đang chạy): VM `e2-small` ~12 USD +
IP tĩnh ~3 USD + đĩa 20 GB ~2 USD ≈ **17 USD**. GitHub Actions miễn phí ở mức
này. Con số chỉ để ước lượng — xem hóa đơn thật ở Billing.

**Thứ tự làm:** Phần A (dựng VM, ~45 phút, làm một lần) → Phần B (CI/CD,
~20 phút, làm một lần) → từ đó chỉ còn `git push`.

### Bảng giá trị của bạn — điền một lần, dùng cho cả tài liệu

| Ký hiệu trong tài liệu | Giá trị | Lấy ở đâu |
|---|---|---|
| `PROJECT_ID` | `project-4fc51df2-b169-4533-bf5` | đã biết |
| `PROJECT_NUMBER` | `320816968642` | lệnh ở A2 |
| `REGION` / `ZONE` | `us-central1` / `us-central1-a` | A1 — đã trùng region Cloud SQL |
| `CONNECTION_NAME` | `project-4fc51df2-b169-4533-bf5:us-central1:instance-ai` | A1 |
| `DB_PASSWORD` | đã đặt (ngẫu nhiên 31 ký tự, xoay ngày 14/08) | dòng `DB_DSN` trong `.env` |
| `ADMIN_TOKEN` | đã đặt (xoay ngày 14/08) | `sudo grep ADMIN_TOKEN /srv/edu-docbot/.env` |
| `VM_IP` | `34.69.71.91` | A3 |

---

## Trước khi bắt đầu

Pipeline deploy đưa lên server **đúng những gì có trên nhánh `main`**, không
phải những gì đang có trong thư mục làm việc. Ingest v2 đã nằm trên `main`
(commit `550330c`, 14/08) nên phần mã nguồn đã sẵn sàng.

Các file triển khai và CI/CD trong tài liệu này (`.github/`, `.gitattributes`,
`deploy/cloud-sql-proxy.service`, `deploy/remote-update.sh`) thì chưa — commit
chúng ở bước B5, sau khi VM đã chạy được.

Kiểm tra nhanh trước mỗi lần deploy:

```bash
git status -sb
```

---

# PHẦN A — Dựng VM (làm một lần)

## A1. Lấy thông tin Cloud SQL

**Đã lấy xong 14/08** — mọi lệnh trong tài liệu này đã điền sẵn giá trị thật,
dán thẳng được:

```
NAME         REGION       DATABASE_VERSION  CONNECTION_NAME
instance-ai  us-central1  MYSQL_8_4         project-4fc51df2-b169-4533-bf5:us-central1:instance-ai
```

VM vì thế đặt ở `us-central1-a`. Region **bắt buộc** trùng Cloud SQL: khác
region thì mỗi truy vấn đi vòng qua Internet, hỏng luôn mục tiêu p95 < 800 ms
của nhánh metadata.

Cần lấy lại về sau:

```bash
gcloud sql instances list --format="table(name,region,databaseVersion,connectionName)"
```

## A2. Bật API và lấy số hiệu dự án

Chạy trên máy Windows (PowerShell, `gcloud` đã cài sẵn ở
`C:\Users\Admin\AppData\Local\Google\Cloud SDK`):

```bash
gcloud auth login
```

```bash
gcloud config set project project-4fc51df2-b169-4533-bf5
```

```bash
gcloud services enable compute.googleapis.com iap.googleapis.com iamcredentials.googleapis.com sqladmin.googleapis.com
```

```bash
gcloud projects describe project-4fc51df2-b169-4533-bf5 --format="value(projectNumber)"
```

Kết quả lệnh cuối là `PROJECT_NUMBER` — ghi vào bảng ở mục 0, Phần B cần.

## A3. IP tĩnh, VM, tường lửa

IP tĩnh:

```bash
gcloud compute addresses create edu-docbot-ip --region=us-central1
```

VM (`enable-oslogin=TRUE` là bắt buộc để Phần B SSH được):

```bash
gcloud compute instances create edu-docbot --zone=us-central1-a --machine-type=e2-small --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=20GB --boot-disk-type=pd-balanced --address=edu-docbot-ip --tags=http-server --scopes=https://www.googleapis.com/auth/cloud-platform --metadata=enable-oslogin=TRUE
```

Mở cổng 80 cho Internet, và cổng 22 **chỉ cho dải IAP** (`35.235.240.0/20` là
dải cố định của Google, không phải Internet mở):

```bash
gcloud compute firewall-rules create edu-docbot-allow-http --allow=tcp:80 --target-tags=http-server --source-ranges=0.0.0.0/0
```

```bash
gcloud compute firewall-rules create allow-ssh-from-iap --allow=tcp:22 --source-ranges=35.235.240.0/20
```

(Quy tắc đã tồn tại thì gcloud báo lỗi trùng tên — bỏ qua được.)

Xem IP vừa cấp:

```bash
gcloud compute instances describe edu-docbot --zone=us-central1-a --format="value(networkInterfaces[0].accessConfigs[0].natIP)"
```

## A4. Quyền cho service account của VM

VM dùng service account mặc định `320816968642-compute@developer.gserviceaccount.com`.
Cấp quyền nối Cloud SQL:

```bash
gcloud projects add-iam-policy-binding project-4fc51df2-b169-4533-bf5 --member="serviceAccount:320816968642-compute@developer.gserviceaccount.com" --role="roles/cloudsql.client"
```

Và quyền gọi Gemini qua Vertex AI — nhờ nó mà `.env` trên server **không cần
chứa khóa API nào**:

```bash
gcloud projects add-iam-policy-binding project-4fc51df2-b169-4533-bf5 --member="serviceAccount:320816968642-compute@developer.gserviceaccount.com" --role="roles/aiplatform.user"
```

> Cấp quyền xong đừng thử ngay: IAM mất **vài phút** để lan truyền. Thử sớm sẽ
> nhận `403 PERMISSION_DENIED` trên `aiplatform.endpoints.predict` — đó là độ
> trễ, không phải cấu hình sai. Chờ rồi thử lại trước khi đi tìm nguyên nhân khác.

## A5. Chép mã nguồn lần đầu

Chỉ lần này làm tay; từ Phần B trở đi GitHub Actions lo. Chạy tại thư mục dự
án trên Windows:

```bash
gcloud compute ssh edu-docbot --zone=us-central1-a --tunnel-through-iap --quiet --command="mkdir -p /tmp/src"
```

```bash
gcloud compute scp --recurse app ingest eval migrations scripts web deploy requirements.txt requirements-rerank.txt edu-docbot:/tmp/src --zone=us-central1-a --tunnel-through-iap --quiet
```

Hai điều đã vấp phải khi chạy thật:

- **Đích phải là đường dẫn tuyệt đối `/tmp/src`, không được dùng `~/src`.**
  Trên Windows `gcloud compute scp` gọi `pscp.exe`, mà pscp không nở dấu `~` →
  báo `remote filespec ~/src: not a directory`.
- Lần chạy đầu gcloud tự sinh khóa SSH và hỏi lưu host key — thêm `--quiet` để
  nó tự trả lời, khỏi treo giữa chừng.

## A6. Cài đặt trong VM

Mở SSH:

```bash
gcloud compute ssh edu-docbot --zone=us-central1-a --tunnel-through-iap
```

Từ đây dán từng khối vào cửa sổ SSH.

```bash
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
sudo -E apt-get update -qq && sudo -E apt-get install -y -qq python3.12-venv nginx rsync curl
```

(Hai biến môi trường ở dòng đầu chặn hộp thoại "cần khởi động lại dịch vụ nào"
của Ubuntu 24.04 — thiếu chúng thì lệnh treo im khi chạy qua `ssh --command`.)

Tài khoản hệ thống + thư mục. `files/` và `logs/` phải tồn tại **trước** khi
khởi động service: `ProtectSystem=strict` liệt kê chúng trong `ReadWritePaths`,
thiếu thư mục là systemd từ chối khởi động.

```bash
sudo useradd --system --shell /usr/sbin/nologin docbot 2>/dev/null; \
sudo mkdir -p /srv/edu-docbot/{files,logs,web} && \
sudo cp -r /tmp/src/{app,ingest,eval,migrations,scripts,deploy,requirements.txt,requirements-rerank.txt} /srv/edu-docbot/ && \
sudo cp -r /tmp/src/web/. /srv/edu-docbot/web/
```

```bash
cd /srv/edu-docbot && sudo python3.12 -m venv venv && sudo venv/bin/pip install -r requirements.txt
```

## A7. Cloud SQL Auth Proxy

```bash
sudo curl -Lo /usr/local/bin/cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.2/cloud-sql-proxy.linux.amd64 && sudo chmod +x /usr/local/bin/cloud-sql-proxy
```

Cài unit; connection name đã điền sẵn trong lệnh `sed` bên dưới:

```bash
sudo cp /srv/edu-docbot/deploy/cloud-sql-proxy.service /etc/systemd/system/ && \
sudo sed -i "s|PROJECT:REGION:INSTANCE|project-4fc51df2-b169-4533-bf5:us-central1:instance-ai|" /etc/systemd/system/cloud-sql-proxy.service && \
sudo systemctl daemon-reload && sudo systemctl enable --now cloud-sql-proxy && \
systemctl --no-pager status cloud-sql-proxy | head -5
```

Phải thấy `active (running)`. Nếu không: `journalctl -u cloud-sql-proxy -n 30`.

## A8. Tài khoản CSDL riêng cho ứng dụng

Máy dev đang nối bằng `root` qua IP public — trên server thì không. Tạo user
quyền hẹp (thay `DB_PASSWORD` bằng chuỗi bạn đặt; **tránh ký tự `$` và `%`**
vì systemd đọc `.env` sẽ hiểu nhầm):

```bash
sudo apt install -y mysql-client && mysql -h 127.0.0.1 -P 3306 -u root -p
```

Trong dấu nhắc `mysql>`:

```sql
CREATE USER IF NOT EXISTS 'docbot'@'%' IDENTIFIED BY 'DB_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE ON edu_docbot.* TO 'docbot'@'%';
FLUSH PRIVILEGES;
EXIT;
```

> Bốn quyền này đủ cho cả API lẫn pipeline nạp tài liệu. Chỉ khi nào chạy
> `ingest.cli build-vector-index` (thủ tục `mysql.create_vector_index`, cần
> khi kho vượt ~10.000 chunk) mới phải dùng `root` cho riêng lệnh đó.

## A9. Tệp `.env` sản xuất

Điền `DB_PASSWORD`, `GEMINI_API_KEY` (chép từ `.env` máy dev) và `ADMIN_TOKEN`:

```bash
sudo tee /srv/edu-docbot/.env > /dev/null <<'EOF'
DB_DSN=mysql+pymysql://docbot:DB_PASSWORD@127.0.0.1:3306/edu_docbot?charset=utf8mb4
FILES_ROOT=/srv/edu-docbot/files
XACCEL_PREFIX=/protected-files
DEV_SERVE_FILES=0
USE_LLM_ROUTER=1
GEMINI_API_KEY=
GOOGLE_CLOUD_PROJECT=project-4fc51df2-b169-4533-bf5
LLM_LOCATION=global
EMBED_LOCATION=us-central1
ROUTER_MODEL=gemini-3.5-flash-lite
GEN_MODEL=gemini-3.5-flash-lite
QUOTA_DAILY_TOKENS=200000
DAILY_BUDGET_USD=1.0
PRICE_IN_PER_MTOK=0.10
PRICE_OUT_PER_MTOK=0.40
CACHE_TTL_SECONDS=86400
RERANK_ENABLED=0
LOG_LEVEL=INFO
CORS_ORIGINS=
ADMIN_TOKEN=
EOF
sudo nano /srv/edu-docbot/.env
```

```bash
sudo chmod 600 /srv/edu-docbot/.env && sudo chown -R docbot:docbot /srv/edu-docbot
```

Ba dòng đáng chú ý:

- **`ADMIN_TOKEN` bắt buộc có giá trị.** Để trống là chế độ demo: mọi endpoint
  `/api/v1/admin/*` mở tự do, ai biết IP cũng upload và xóa tài liệu được
  ([`app/api/deps.py:50`](../app/api/deps.py:50)). Sinh chuỗi ngẫu nhiên:
  `openssl rand -hex 16`. Trang admin sẽ hỏi mã này một lần rồi nhớ trong
  localStorage.
- **`DEV_SERVE_FILES=0`** — trên server nginx phục vụ file qua
  `X-Accel-Redirect`; bật cờ này là để FastAPI tự trả file, chỉ dùng lúc dev.
- **`GEMINI_API_KEY` để TRỐNG.** Khác máy dev ở đúng điểm này. Để trống thì
  code tự chuyển sang Vertex AI và xác thực bằng service account của VM
  ([`app/llm/vertex.py:94`](../app/llm/vertex.py:94)) — server không giữ khóa
  nào, mất VM cũng không lộ gì. Đã đo trên chính VM này (14/08):
  `gemini-3.5-flash-lite` chạy ở `global` (ở `us-central1` báo 404),
  `gemini-embedding-001` trả đúng 768 chiều. Điền khóa vào là code quay lại
  đường Developer API vì khóa thắng `GOOGLE_CLOUD_PROJECT`
  ([`vertex.py:82`](../app/llm/vertex.py:82)) — chỉ làm vậy khi cần thoát 429,
  xem Phụ lục E2.

## A10. systemd + nginx

```bash
sudo cp /srv/edu-docbot/deploy/edu-docbot.service /srv/edu-docbot/deploy/edu-docbot-ingest.service /srv/edu-docbot/deploy/edu-docbot-ingest.timer /etc/systemd/system/ && \
sudo systemctl daemon-reload && sudo systemctl enable --now edu-docbot edu-docbot-ingest.timer
```

```bash
sudo cp /srv/edu-docbot/deploy/nginx-edu-docbot.conf /etc/nginx/sites-available/edu-docbot && \
sudo ln -sf ../sites-available/edu-docbot /etc/nginx/sites-enabled/edu-docbot && \
sudo rm -f /etc/nginx/sites-enabled/default && \
sudo nginx -t && sudo systemctl reload nginx
```

## A11. Nghiệm thu

```bash
curl -s 127.0.0.1:8080/readyz; echo
```

Phải ra `{"status":"ok","database":"ok"}`. Ra `degraded` thì xem
`journalctl -u edu-docbot -n 30` (thường sai mật khẩu trong `DB_DSN`).

Rồi từ máy bạn mở `http://VM_IP/` — trang chat phải lên. Hỏi thử một câu và
bấm nút ⏱: các bước `(DB)` phải còn vài chục mili-giây, đó là bằng chứng VM
nằm đúng region với Cloud SQL.

Kiểm tra luôn hàng rào admin: mở `http://VM_IP/admin.html`, trang phải hỏi mã
quản trị. Không hỏi nghĩa là `ADMIN_TOKEN` còn trống — quay lại A9.

---

# PHẦN B — CI/CD (làm một lần)

Mục tiêu: `git push origin main` → test chạy → xanh thì code lên VM và service
khởi động lại, không khóa dài hạn nào nằm trong GitHub.

## B1. Tài khoản dịch vụ cho GitHub

```bash
gcloud iam service-accounts create github-deployer --display-name="GitHub Actions deployer"
```

Bốn quyền, mỗi quyền một lệnh (thay `ZONE`, `PROJECT_NUMBER`):

```bash
gcloud compute instances add-iam-policy-binding edu-docbot --zone=us-central1-a --member="serviceAccount:github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com" --role="roles/compute.osAdminLogin"
```

```bash
gcloud projects add-iam-policy-binding project-4fc51df2-b169-4533-bf5 --member="serviceAccount:github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com" --role="roles/compute.viewer"
```

```bash
gcloud projects add-iam-policy-binding project-4fc51df2-b169-4533-bf5 --member="serviceAccount:github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com" --role="roles/iap.tunnelResourceAccessor"
```

```bash
gcloud iam service-accounts add-iam-policy-binding 320816968642-compute@developer.gserviceaccount.com --member="serviceAccount:github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com" --role="roles/iam.serviceAccountUser"
```

Lệnh cuối hay bị bỏ sót: `gcloud compute ssh` vào một VM **có gắn service
account** đòi người gọi phải có quyền "dùng" service account đó, nếu không sẽ
báo `Required 'compute.instances.get'`-kiểu-khó-hiểu hoặc từ chối thẳng.

`osAdminLogin` cấp ở **cấp instance** chứ không phải cấp dự án: GitHub chỉ
SSH được vào đúng máy này, không phải mọi VM trong dự án.

## B2. Workload Identity Federation

```bash
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github-provider --location=global --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='tienphamk4/bot-chat-lms'"
```

`--attribute-condition` là chốt an toàn: chỉ token phát ra từ đúng repo
`tienphamk4/bot-chat-lms` mới đổi được quyền. Thiếu nó thì **bất kỳ** repo
GitHub nào trên thế giới cũng đổi được — gcloud cũng sẽ bắt bạn khai.

## B3. Cho phép repo dùng tài khoản dịch vụ

```bash
gcloud iam service-accounts add-iam-policy-binding github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com --role="roles/iam.workloadIdentityUser" --member="principalSet://iam.googleapis.com/projects/320816968642/locations/global/workloadIdentityPools/github/attribute.repository/tienphamk4/bot-chat-lms"
```

Lấy chuỗi định danh provider để dán vào GitHub:

```bash
gcloud iam workload-identity-pools providers describe github-provider --location=global --workload-identity-pool=github --format="value(name)"
```

## B4. Khai báo trong GitHub

Repo → **Settings** → **Secrets and variables** → **Actions**.

Tab **Secrets** → *New repository secret*:

| Tên | Giá trị |
|---|---|
| `GCP_WIF_PROVIDER` | kết quả lệnh `describe` ở B3 (`projects/320816968642/locations/global/workloadIdentityPools/github/providers/github-provider`) |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@project-4fc51df2-b169-4533-bf5.iam.gserviceaccount.com` |

Tab **Variables** → *New repository variable*:

| Tên | Giá trị |
|---|---|
| `GCP_PROJECT` | `project-4fc51df2-b169-4533-bf5` |
| `GCE_INSTANCE` | `edu-docbot` |
| `GCE_ZONE` | `us-central1-a` |

## B5. Chạy thử

```bash
git add .github .gitattributes deploy docs && git commit -m "CI/CD: GitHub Actions deploy lên VM qua WIF" && git push origin main
```

Mở tab **Actions** trên GitHub. Workflow *Deploy* phải xanh cả hai job. Job
`Deploy lên VM` in ra `==> Deploy xong.` kèm `{"status":"ok","database":"ok"}`.

Muốn deploy lại mà không sửa code: Actions → *Deploy* → **Run workflow**.

---

# PHẦN C — Vận hành

## C1. Đưa thay đổi lên server

```bash
git push origin main
```

Hết. Test đỏ thì deploy không chạy. `/readyz` không lên sau 20 giây thì job đỏ
kèm 40 dòng log — không có chuyện pipeline xanh mà server chết.

`requirements.txt` không đổi thì bước `pip install` bị bỏ qua, deploy mất
khoảng một phút.

## C2. Nạp tài liệu

> **Nạp trên VM, đừng nạp trên máy dev.** Hai máy dùng chung Cloud SQL nhưng
> đĩa riêng. Nạp ở máy dev thì CSDL có bản ghi và chunk (chat trả lời được),
> nhưng file thật nằm lại trên đĩa máy dev — bấm xem nguồn trên web sẽ ra 404.
> Đúng lỗi đã gặp ngày 14/08, phải chép tay 17 MB sang mới hết.

Ba đường nạp:

1. **Trang admin** `http://VM_IP/admin.html` → nút Upload. Dễ nhất.
2. **Chép hàng loạt rồi quét** — đặt file vào `/srv/edu-docbot/files/` rồi:

   ```bash
   cd /srv/edu-docbot && sudo -u docbot venv/bin/python -m ingest.cli scan
   ```

3. **Tự động 2 giờ sáng** — `edu-docbot-ingest.timer` đã bật, quét thư mục
   `files/` mỗi đêm.

Tài liệu scan (giáo trình ảnh) rơi vào trạng thái `needs_ocr`, chạy riêng:

```bash
cd /srv/edu-docbot && sudo -u docbot venv/bin/python -m ingest.cli ocr <doc_id> --max-pages 30
```

Thêm môn học: sửa `scripts/add-courses-example.sql` rồi chạy, **sau đó bắt
buộc** `python -m ingest.cli resync-derived` để dựng lại cột `_norm` và chỉ mục
môn trong RAM.

## C3. Migration CSDL

Pipeline **không** tự chạy `migrations/*.sql` (không có bảng theo dõi phiên
bản, chạy lại máy móc là hỏng dữ liệu). Khi có migration mới, chạy tay trên VM:

```bash
mysql -h 127.0.0.1 -P 3306 -u root -p edu_docbot < /srv/edu-docbot/migrations/00X_ten_file.sql
```

Tính tới 18/08/2026 có **8 migration**, chạy theo đúng thứ tự số:

| File | Nội dung |
|---|---|
| `001_init.sql` | Schema gốc: khoa, môn, tài liệu, chunk, cache, quota, nhật ký |
| `002_seed_demo.sql` | Dữ liệu mẫu để chạy thử |
| `003_vector_cloudsql.sql` | Cột `embedding VECTOR(768)` theo cú pháp Cloud SQL |
| `004_assistants.sql` | Bảng trợ lý ảo — tên, giọng, phạm vi, liên hệ, màu |
| `005_assistant_documents.sql` | Quan hệ nhiều-nhiều tài liệu ↔ trợ lý |
| `006_blocked_topics.sql` | Chủ đề bị chặn theo từng trợ lý |
| `007_assistant_access_levels.sql` | Mức quyền trợ lý phục vụ (nhiều lựa chọn) |
| `008_assistant_gen_params.sql` | Tham số sinh trả lời theo từng trợ lý |

Từ `004` trở đi đều liên quan tới nền tảng nhiều trợ lý. Bỏ sót một file là
trang quản trị báo lỗi cột không tồn tại, còn chat thì rơi về persona mặc định
— hệ thống vẫn chạy nhưng mọi trợ lý nói cùng một giọng.

## C4. Log và trạng thái

```bash
sudo journalctl -u edu-docbot -f
```

```bash
systemctl status edu-docbot cloud-sql-proxy nginx --no-pager
```

## C5. Quay lui khi bản mới hỏng

Nhanh nhất, không cần máy dev: GitHub → **Actions** → chọn lần chạy *Deploy*
xanh gần nhất → **Re-run all jobs**. Nó dựng lại đúng commit đó lên server.

Cách chuẩn mực hơn:

```bash
git revert HEAD && git push origin main
```

## C6. Sao lưu

Hai thứ phải sao lưu, ở hai nơi khác nhau:

- **CSDL** → Cloud SQL đã có automated backup. Kiểm tra đang bật:
  Console → SQL → instance → Backups.
- **Thư mục `/srv/edu-docbot/files`** → **không có trong repo và không có trong
  Cloud SQL backup**. Mất VM là mất sạch tài liệu đã nạp. Sao lưu định kỳ:

  ```bash
  gcloud compute scp --recurse edu-docbot:/srv/edu-docbot/files ./backup-files --zone=us-central1-a --tunnel-through-iap
  ```

---

## C7. Trạng thái bảo mật hiện tại (14/08/2026)

| Mặt | Trạng thái |
|---|---|
| Cloud SQL mở ra Internet | **Đã đóng.** `authorized-networks` chỉ còn `59.153.220.155/32` (IP máy dev, để DBeaver vào được). Trước đó là `0.0.0.0/0` |
| Mật khẩu `root` | Đã xoay 14/08 — chuỗi ngẫu nhiên 31 ký tự. Bản cũ từng lộ trong ảnh chụp màn hình |
| `ADMIN_TOKEN` | Đã xoay 14/08, cùng lý do |
| Khóa Gemini trên server | **Không có.** VM xác thực qua Vertex AI + ADC |
| Cổng 22 của VM | Chỉ mở cho dải IAP `35.235.240.0/20`, không mở ra Internet |
| Truyền tải web | HTTP trần — mã quản trị đi dạng rõ. Gắn HTTPS (E1) khi có tên miền |

Hai việc còn nợ, không gấp nhưng nên làm:

- **VM đang nối CSDL bằng `root`.** Tạo user `docbot` quyền hẹp theo A8 rồi đổi
  một dòng `DB_DSN` là xong. Hiện tiến trình mở ra Internet đang cầm toàn quyền
  trên mọi database của instance.
- **CGNAT làm IP máy dev đổi theo thời gian.** Khi DBeaver timeout, lấy IP thật
  trong thông báo lỗi MySQL rồi chạy lại lệnh patch — xem bảng Phần D.

---

# PHẦN D — Khắc phục sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Deploy đỏ ở bước **Chép gói lên VM**: `Broken pipe` / `scp: Connection closed` / `exit code 255` | VM bị dừng hoặc khởi động lại giữa lúc CI đang chép — hay gặp nhất là khi đổi `machine-type`, vì thao tác đó bắt buộc stop/start | Đợi VM `RUNNING` và `/readyz` trả 200 rồi chạy lại workflow. **Đừng đụng vào VM khi Actions đang chạy**: hai việc này không biết nhau, và scp đứt nửa chừng để lại gói dở trong `/tmp` chứ không tự dọn |
| `/readyz` trả `degraded` | Sai mật khẩu trong `DB_DSN`, hoặc proxy chết | `journalctl -u edu-docbot -n 30` và `journalctl -u cloud-sql-proxy -n 30` |
| `cloud-sql-proxy` không lên | Sai `CONNECTION_NAME`, hoặc thiếu `roles/cloudsql.client` | Kiểm tra lại A1 và A4 |
| Service chết ngay khi start, log báo `ModuleNotFoundError: app` | `WorkingDirectory` trỏ vào `/srv/edu-docbot/app` | Phải là `/srv/edu-docbot` — đã sửa trong `deploy/edu-docbot.service` |
| Service không start, log báo `ReadWritePaths` | Thiếu `/srv/edu-docbot/files` hoặc `logs` | `sudo mkdir -p /srv/edu-docbot/{files,logs}` rồi restart |
| Chat đứng im rồi hiện hết một lúc | Thiếu `proxy_buffering off` trong nginx | Lỗi triển khai kinh điển của SSE — kiểm tra `location /api/` |
| Actions đỏ ở bước auth: `Unable to acquire impersonated credentials` | Thiếu binding ở B3, hoặc `attribute-condition` không khớp tên repo | Chạy lại B3, kiểm tra chính xác `tienphamk4/bot-chat-lms` |
| Actions đỏ ở bước ssh: `Permission denied (publickey)` | Chưa bật OS Login, hoặc thiếu `osAdminLogin`/`serviceAccountUser` | Kiểm tra metadata `enable-oslogin=TRUE` (A3) và bốn binding ở B1 |
| Actions đỏ: `bad interpreter: /usr/bin/env bash^M` | `remote-update.sh` bị commit dạng CRLF | `.gitattributes` đã chặn; nếu file lỡ vào repo sai: `git rm --cached deploy/remote-update.sh && git add deploy/remote-update.sh` |
| Trang admin không hỏi mã | `ADMIN_TOKEN` trống | Điền vào `.env` rồi `sudo systemctl restart edu-docbot` |
| Trang chat đứng hình, console báo `crypto.randomUUID is not a function` | Phục vụ qua `http://<IP>/` — `randomUUID` chỉ tồn tại trong secure context (HTTPS hoặc localhost), nên máy dev không lộ lỗi này | Đã vá sẵn: `web/chat.js` dùng `newSessionId()` có đường lui bằng `getRandomValues`. Gặp lại nghĩa là bản trên server cũ hơn repo |
| `Access denied for user ... (using password: YES)` dù mật khẩu "đúng" | Mật khẩu dán qua terminal bị rụng ký tự, hay gặp nhất với `%` và `@` | Đừng gõ lại bằng mắt — so vân tay: `python -c "import hashlib;print(hashlib.sha256(b'...').hexdigest()[:12])"` ở hai đầu. Ký tự đặc biệt trong DSN phải mã hóa URL (`@` → `%40`) |
| Máy dev / DBeaver timeout sau khi siết `authorized-networks` | ISP dùng CGNAT: IP mà dịch vụ tra-IP qua HTTPS báo **khác** IP thật khi mở phiên 3306 | Đừng tin `api.ipify.org`. Lấy IP thật từ chính thông báo lỗi MySQL — `Access denied for user 'root'@'<IP thật>'` — rồi `gcloud sql instances patch instance-ai --authorized-networks=<IP>/32`. CGNAT đổi IP theo thời gian, timeout lại thì lặp lại đúng cách này |
| Tài liệu nạp xong nhưng chat bảo không tìm thấy | Chưa `resync-derived` sau khi thêm môn, hoặc chunk chưa có embedding | `python -m ingest.cli resync-derived`; xem trạng thái tài liệu ở trang admin |

---

# Phụ lục

## E1. Gắn tên miền + HTTPS

Trỏ bản ghi A của tên miền về `VM_IP`, mở thêm cổng 443, rồi trên VM:

```bash
sudo apt install -y certbot python3-certbot-nginx && sudo certbot --nginx -d docbot.ten-mien-cua-ban.vn
```

Certbot tự sửa `/etc/nginx/sites-available/edu-docbot`. `remote-update.sh` cố ý
**không** chép đè file nginx chính vì lý do này — nó chỉ in cảnh báo khi bản
trong repo khác bản đang chạy.

## E2. Lối thoát khi Vertex nghẽn quota (429)

Cấu hình mặc định đi đường Vertex AI + ADC. Nếu gặp `429 RESOURCE_EXHAUSTED`
rải rác, có hai cách, thử theo thứ tự:

**Cách 1 — nâng quota Vertex.** Console → **IAM & Admin → Quotas & System
Limits** → lọc *Service: Vertex AI API*. Hai hạn mức cần nâng:

| Quota | Chiều cần đúng | Đề xuất |
|---|---|---|
| Generate content requests per minute per project per base model | base_model `gemini-3.5-flash-lite`, region `global` | 200/phút |
| Embed content requests per minute | `gemini-embedding-001`, region `us-central1` | 200/phút |

Mức nhỏ thường duyệt tự động trong vài phút.

**Cách 2 — quay về Developer API.** Điền `GEMINI_API_KEY` vào `.env` rồi
`sudo systemctl restart edu-docbot`. Khóa thắng `GOOGLE_CLOUD_PROJECT`
([`vertex.py:82`](../app/llm/vertex.py:82)) nên không cần sửa gì khác — đây là
hàng đợi quota riêng, chuyển làn là thoát. Mất 30 giây, dùng được ngay giữa
buổi demo.

### Kết quả đo trên VM này (14/08/2026)

| Model | `global` | `us-central1` |
|---|---|---|
| `gemini-3.5-flash-lite` | chạy | **404** — không phục vụ ở đây |
| `gemini-2.5-flash-lite` | chạy | chạy |
| `gemini-embedding-001` (768 chiều) | chạy | chạy |

Vì vậy `LLM_LOCATION=global` là bắt buộc với model 3.5, còn
`EMBED_LOCATION=us-central1` giữ nguyên cho gần Cloud SQL.

## E3. Bật rerank

Cần RAM 4 GB → đổi máy sang `e2-medium` trước:

```bash
gcloud compute instances stop edu-docbot --zone=us-central1-a
```

```bash
gcloud compute instances set-machine-type edu-docbot --zone=us-central1-a --machine-type=e2-medium
```

```bash
gcloud compute instances start edu-docbot --zone=us-central1-a
```

Rồi trên VM: cài `requirements-rerank.txt` vào venv, đặt `RERANK_ENABLED=1`
trong `.env`, restart service.
