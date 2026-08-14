# EDU-DOCBOT — Hệ thống tra cứu & hỏi đáp tài liệu học tập

> Bản mô tả năng lực, phạm vi và hướng dẫn sử dụng — cập nhật 14/08/2026.
> Chi tiết kỹ thuật/kiến trúc: xem `CLAUDE.md` và `README.md`.

---

## 1. Bài toán

Sinh viên cần tìm tài liệu học tập (giáo trình, slide, đề cương, đề thi…) và
tra cứu quy chế đào tạo, nhưng tài liệu nằm rải rác, tên file lộn xộn, còn quy
chế thì dài hàng chục trang khó đọc. EDU-DOCBOT giải quyết việc đó bằng một
chatbot tiếng Việt:

- **Hỏi tự nhiên** — "giáo trình giải tích 1", "gt1", "môn oop có gì" đều hiểu.
- **Trả lời có căn cứ** — mọi câu trả lời nội dung đều kèm **nguồn + số trang**,
  bấm vào là mở đúng tài liệu. Không tìm thấy trong kho thì nói thẳng là không
  thấy, không bịa.
- **Đúng quyền truy cập** — tài liệu giới hạn theo khoa/lớp thì người ngoài
  không bao giờ thấy, kể cả qua câu trả lời của AI.
- **Chi phí vận hành cực thấp** — kiến trúc chặn-trước-LLM: phần lớn câu hỏi
  trả lời bằng SQL + cache (0 đồng), chỉ câu hỏi nội dung mới gọi model
  (~0,0002 USD/câu).

### Trong phạm vi

| Nhóm | Ví dụ |
|---|---|
| Tìm tài liệu theo môn/loại/kỳ | "slide XSTK", "đề thi giải tích 1 kỳ 1" |
| Hỏi nội dung bên trong tài liệu | "kế thừa là gì", "PHP hoạt động thế nào" |
| Tra cứu quy chế, quy định | "điều kiện dự thi cuối kỳ", "thi lại mấy lần" |
| Ngữ cảnh hội thoại | "môn này học gì?" khi đang mở trang bài học một môn |

### Ngoài phạm vi (bot sẽ từ chối lịch sự)

- Tra điểm cá nhân, học phí, thời khóa biểu, đăng ký học phần.
- Giải bài tập hộ, viết tiểu luận hộ.
- Chủ đề không liên quan việc học; ngôn ngữ ngoài Việt–Anh; giọng nói.

---

## 2. Hệ thống làm được gì (hiện trạng)

### Người dùng cuối — 2 giao diện chat

**Trang LMS `/`** — tra cứu tài liệu theo môn:
- Chọn "Đăng nhập thử" để giả lập người dùng (khách / SV Toán / SV CNTT / GV /
  admin) — hệ thống lọc tài liệu đúng quyền của người đó.
- Chọn "Ngữ cảnh" = trang bài học một môn → câu hỏi mơ hồ ("môn này học gì?",
  "có slide không?") tự hiểu là hỏi về môn đang mở; hỏi đích danh môn khác vẫn
  được ưu tiên theo lời hỏi.
- Màn hình trống có sẵn các câu gợi ý — bấm là gửi luôn.

**Trang Tư vấn học vụ `/tuvan.html`** — hỏi quy chế, không cần đăng nhập:
- Khoanh vùng sẵn trong các văn bản loại `quy_che` công khai.
- Dành cho cổng thông tin/website trường nhúng vào.

**Trải nghiệm trả lời:**
- Chữ chảy ra theo thời gian thực (SSE), có chỉ báo "đang gõ".
- Định dạng đẹp: đoạn văn + gạch đầu dòng, **in đậm**, trích dẫn là pill
  `tr. 14` đặt cuối ý — bấm mở tài liệu, hover xem đường mục lục
  ("Bài 1 > 4. Phương pháp hoạt động của php").
- Cuối câu trả lời có hàng thẻ **Nguồn** gộp theo tài liệu (một dòng một tài
  liệu, liệt kê các trang đã dùng).
- Nút **⏱** xem thời gian từng bước xử lý (cache → router → tìm kiếm → sinh).
- Hết quota ngày (20.000 token/SV) → tự hạ về chế độ chỉ-tìm-tài-liệu (miễn
  phí), không dừng phục vụ.

### Quản trị viên — trang `/admin.html`

- **Thống kê nhanh**: tổng tài liệu / đã đánh chỉ mục / chờ xử lý / chunk thiếu
  vector.
- **Nạp tài liệu**: popup kéo-thả nhiều file, mỗi file một hàng tự chọn
  môn/loại/quyền, một nút tải tất cả (PDF, DOCX, PPTX, TXT, MD; tối đa 100 MB).
- **Kho tài liệu**: bảng đầy đủ, tick chọn → nạp lại hoặc xóa vĩnh viễn (xóa cả
  file gốc + manifest); nút quét thư mục server; nút xử lý lại toàn bộ.
- **Chờ xử lý**: file thiếu metadata hoặc chữ hỏng — gán môn/loại thủ công rồi
  nạp lại, hoặc bấm **🔍 OCR bằng AI** cho file scan/hỏng font.

### Pipeline nạp tài liệu (điểm mạnh nhất của hệ thống)

Chạy tự động khi upload/quét, thiết kế "chậm cũng được, phải đúng":

1. **Chống trùng** — SHA-256, file trùng nội dung không nạp hai lần.
2. **Trích xuất hiểu bố cục (PDF)** — đo cỡ chữ toàn văn bản để nhận diện
   heading; tự lột header/footer lặp; bỏ nguyên trang mục lục; nhận diện bảng
   đề cương để không nhầm dòng bảng thành đề mục.
3. **Hộ chiếu chất lượng chữ** — đo từng trang 4 tín hiệu (dấu tiếng Việt, chữ
   lạ mojibake, từ-xương mất dấu); >10% trang hỏng → chặn nạp, yêu cầu OCR.
   Đây là lá chắn chống "đầu độc kho" bằng PDF hỏng font TCVN3/VNI.
4. **OCR bằng Gemini vision** — đọc lại từng trang thành chữ sạch; nút bấm trên
   web (trần 30 trang) hoặc CLI cho giáo trình dày
   (`python -m ingest.cli ocr <id> --max-pages 200`).
5. **Chia chunk theo văn phạm đề mục tiếng Việt** — Phần > Chương/Bài > Mục/Điều
   > n.m > n.m.k, nối tiêu đề bị xuống dòng, path dạng
   "CHƯƠNG II > Điều 15" để trích dẫn chỉ đúng chỗ.
6. **Metadata 5 tầng** — tên file (kể cả tên gọi tắt: `oop_lthdt.pdf` →
   INT2204) → thư mục → manifest.csv → nội dung trang bìa → LLM (chỉ khi 4 tầng
   máy móc bó tay, và không được bịa môn ngoài danh mục).
7. **Title Engine** — tiêu đề thật lấy từ trang bìa thay vì tên file:
   "16_lap_trinh_web_nang_cao_0193.pdf" → "Giáo Trình Lập Trình Web Nâng Cao";
   văn bản hành chính ghép "Quyết định + Về việc…".
8. **Embedding** — gemini-embedding-001, 768 chiều, gộp lô 100 chunk/lần.

### Chất lượng & an toàn

- Trích dẫn **hậu kiểm**: dấu [n] không khớp được với đoạn trích thật → câu đó
  bị loại; câu trả lời không có trích dẫn nào → coi như không tìm thấy.
- Lọc quyền **trước khi truy hồi** — LLM không bao giờ nhìn thấy đoạn trích
  ngoài quyền của người hỏi; cache cũng khóa theo phạm vi quyền.
- Chống prompt injection: nội dung tài liệu được bọc phân tách, hệ thống coi đó
  là dữ liệu, không phải mệnh lệnh.
- Bộ eval 38 câu chạy lại được sau mỗi lần đổi prompt/tham số
  (`python -m eval.run_eval`).

---

## 3. Cách sử dụng

### 3.1. Sinh viên / người hỏi

1. Mở trang chat (LMS `/` hoặc Tư vấn `/tuvan.html`).
2. Gõ câu hỏi tự nhiên, hoặc bấm một câu gợi ý trên màn hình trống.
3. Đọc câu trả lời; bấm pill `tr. n` hoặc thẻ Nguồn để mở đúng tài liệu, đúng
   trang mà kiểm chứng.
4. Muốn tài liệu của môn cụ thể: nêu tên/mã môn ("slide INT2204", "đề thi xstk")
   hoặc chọn ngữ cảnh bài học rồi hỏi ngắn gọn.

Mẹo: bot chỉ trả lời từ tài liệu trong kho. Nếu nhận "Mình không tìm thấy…",
thử lại với từ khóa bot gợi ý, hoặc báo quản trị viên bổ sung tài liệu.

### 3.2. Quản trị viên

**Nạp tài liệu mới:**
1. Vào `/admin.html` → "⬆ Nạp tài liệu mới".
2. Kéo thả file → mỗi file chọn Môn + Loại (+ Quyền nếu cần) → "Tải lên tất cả".
   - Quy chế không cần chọn môn. Chọn tay luôn thắng mọi cách đoán tự động.
3. Xem bảng kho: trạng thái `indexed` + cột Vector ✓ nghĩa là đã tìm kiếm được.

**Khi file vào mục Chờ xử lý:**
- *Thiếu metadata* → chọn môn/loại → "Gán & nạp lại".
- *needs_ocr (chữ hỏng/scan)* → bấm "🔍 OCR bằng AI" (file ≤30 trang);
  giáo trình dày chạy CLI: `python -m ingest.cli ocr <id> --max-pages 200`.

**Nạp hàng loạt từ thư mục server:** chép file vào `files/` (đặt tên có mã môn
hoặc xếp vào thư mục theo môn/loại), khai `files/manifest.csv` nếu muốn chỉ
định chính xác, rồi bấm "Quét file mới" (hoặc để systemd timer 2h sáng tự chạy).

**Quyền truy cập** (`access`): `public` — ai cũng thấy · `faculty` — đúng khoa
· `course` — đúng lớp học phần · `staff` — giảng viên/cán bộ.

### 3.3. Vận hành / kỹ thuật

```bash
# Chạy server dev
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8080

# CLI nạp liệu
python -m ingest.cli scan            # quét + nạp file mới + embed
python -m ingest.cli review          # liệt kê file chờ xử lý
python -m ingest.cli reindex-all     # nạp lại TOÀN BỘ kho qua pipeline hiện hành
python -m ingest.cli renorm --dry-run  # đo lệch chuẩn hóa sau khi nâng cấp vn_norm
python -m ingest.cli ocr 23 --max-pages 200   # OCR tài liệu dày

# Kiểm thử
.venv/Scripts/python -m pytest tests/ -q      # 160 unit tests
.venv/Scripts/python -m eval.run_eval         # bộ eval 38 câu, in báo cáo + JSON
```

Cấu hình qua `.env` (xem `README.md`): `DB_DSN`, `GEMINI_API_KEY`/credential
Vertex, `LLM_LOCATION` (global), `EMBED_LOCATION` (us-central1), `FILES_ROOT`,
`ADMIN_TOKEN` (bật xác thực trang quản trị), `USE_LLM_ROUTER`.

**API chính** (chi tiết schema trong `app/api/`):

| Method | Endpoint | Mô tả |
|---|---|---|
| POST | `/api/v1/chat` | `{session_id, message, context?}` — SSE: `route` → `token`* → `citations` → `done` |
| GET | `/api/v1/documents` | Lọc theo course/type/semester |
| GET | `/api/v1/documents/{id}/download` | Kiểm quyền rồi trả file |
| POST | `/api/v1/admin/documents` | Upload multipart + query môn/loại/quyền |
| POST | `/api/v1/admin/scan` · `/reindex/{id}` · `/ocr/{id}` · `/assign` · `/documents/delete` | Quản trị kho |
| GET | `/api/v1/admin/metrics` | Chi phí/ngày, cache hit, phân bố route |

---

## 4. Giới hạn hiện tại — nói thẳng

- **Kho demo còn nhỏ** (6 tài liệu, 4 môn) — chất lượng câu trả lời phụ thuộc
  kho; muốn dùng thật phải nạp đủ giáo trình các môn.
- Câu hỏi **so sánh/tổng hợp nhiều tài liệu** ("so sánh chương 1 hai giáo
  trình") mới trả lời được ở mức ghép trích đoạn.
- OCR bằng AI có phí (~1,5–2 nghìn đồng/100 trang) và cần chạy chủ động.
- Chưa có đăng nhập SSO thật — đang giả lập bằng danh sách user demo, chờ tích
  hợp với hệ thống định danh của trường.
- ID tài liệu đổi khi "nạp lại" (xóa + tạo mới) — link cũ đã chia sẻ có thể
  đứt; sẽ xử lý ở bản Provenance (giữ ID cố định).
