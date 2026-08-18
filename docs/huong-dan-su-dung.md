# EDU-DOCBOT — Hướng dẫn sử dụng

> Cập nhật **18/08/2026**, khớp với mã nguồn hiện tại.
> Báo cáo dự án: [bao-cao-tien-do-20260818.md](bao-cao-tien-do-20260818.md) ·
> Kiến trúc: [word/EDU-DOCBOT-Tai-lieu-ky-thuat.docx](word/EDU-DOCBOT-Tai-lieu-ky-thuat.docx)

---

## 1. Hệ thống này là gì

Nền tảng trợ lý ảo tiếng Việt. **Một lần cài đặt phục vụ được nhiều trợ lý** —
mỗi trợ lý có kho tài liệu riêng, giọng nói riêng, phạm vi trả lời riêng và đầu
mối liên hệ riêng.

Hiện có hai trợ lý:

| Trợ lý | Phục vụ | Kho tài liệu |
|---|---|---|
| **Trợ lý tài liệu học tập** | Sinh viên tra cứu giáo trình, slide, đề cương, đề thi và hỏi nội dung bên trong | Tài liệu công khai + tài liệu theo khoa |
| **Trợ lý Tư vấn tuyển sinh** | Thí sinh, phụ huynh hỏi ngành học, điểm chuẩn, phương thức xét tuyển, học phí | Tài liệu công khai |

Điểm mấu chốt: **thêm trợ lý thứ ba không cần sửa mã**. Khai báo một bản ghi ở
trang quản trị là trang chat tự mọc thêm một bong bóng.

---

## 2. Dành cho người dùng cuối

### 2.1. Cách dùng

Góc phải dưới màn hình có các bong bóng nằm cạnh nhau, mỗi bong bóng một trợ lý.
Bấm vào cái nào thì mở popup của trợ lý đó. Mỗi popup giữ cuộc trò chuyện riêng,
không lẫn vào nhau.

Trang chủ cũng có thẻ giới thiệu từng trợ lý kèm vài câu hỏi mẫu — bấm thẻ là mở
đúng trợ lý ấy.

### 2.2. Đọc câu trả lời

- Trích dẫn hiện dạng pill `tr. 14` đặt cuối ý — bấm mở đúng tài liệu, đúng
  trang; rê chuột xem đường mục lục ("CHƯƠNG II › Điều 15").
- Cuối câu trả lời có hàng thẻ **Nguồn** gộp theo tài liệu.
- Nút **↻** trên thanh tiêu đề: bắt đầu lại cuộc trò chuyện.
- Nút **⏱** dưới câu trả lời: xem thời gian từng bước xử lý.

### 2.3. Hỏi thế nào cho trúng

| Muốn gì | Gõ như thế nào |
|---|---|
| Tìm tài liệu của một môn | "slide kiểm thử phần mềm", "INT3115 có tài liệu gì" |
| Hỏi nội dung bên trong tài liệu | "kiểm thử hồi quy là gì", "clustered index khác heap table thế nào" |
| Tra quy chế, quy định | "quy chế thi lại quy định thế nào" |

Vài điều nên biết:

- **Trợ lý chỉ trả lời từ tài liệu trong kho của nó.** Nhận "em chưa có thông tin
  này" nghĩa là kho chưa có, không phải trợ lý không hiểu.
- **Hỏi đúng trợ lý.** Hỏi điểm chuẩn ở trợ lý tài liệu học tập thì không ra —
  đó là việc của trợ lý tuyển sinh.
- **Câu hỏi cụt sẽ bị hỏi lại.** Gõ mỗi "là gì" thì trợ lý hỏi ngược lại cho rõ
  thay vì đoán bừa.
- **Kết quả phụ thuộc quyền của bạn.** Cùng một câu hỏi, khách chưa đăng nhập và
  sinh viên đúng khoa nhận kết quả khác nhau — tài liệu giới hạn theo khoa thì
  người ngoài khoa không thấy, kể cả qua câu trả lời của AI.

### 2.4. Trợ lý không làm gì

Tra điểm cá nhân, giải bài tập hộ, viết tiểu luận hộ. Riêng học phí và thời khóa
biểu thì tùy trợ lý — trợ lý tài liệu học tập từ chối, còn trợ lý tuyển sinh
nhận bình thường vì đó là việc của nó.

---

## 3. Dành cho quản trị viên

Toàn bộ nằm ở **một trang** `/admin.html`, sidebar bên trái có 6 mục.

### 3.1. Tổng quan

Số liệu nhanh: tài liệu trong kho, đã đánh chỉ mục, chờ xử lý, thiếu vector, số
trợ lý, tài liệu chưa gán trợ lý. Kèm hai nút chạy nền: **Quét thư mục server**
và **Xử lý lại toàn bộ**.

### 3.2. Trợ lý ảo — nơi cấu hình mọi thứ về bot

Chọn một trợ lý bên trái, biểu mẫu bên phải có 7 nhóm:

| Nhóm | Cấu hình gì |
|---|---|
| **Nhận dạng** | Mã định danh, tên, dòng phụ, thứ tự hiện, bật/tắt |
| **Giọng nói** | Tên gọi kho tài liệu, câu mời đi tiếp, và bốn câu tĩnh: chào, ngoài phạm vi, hỏi lại, từ chối ngôn từ xúc phạm |
| **Phạm vi** | Loại tài liệu · mức quyền phục vụ · **tài liệu riêng của bot** |
| **Tham số sinh** | Độ tự nhiên, trần token trả lời, ngân sách suy luận |
| **Khi không trả lời được** | Hotline và website riêng của trợ lý |
| **Màn hình trống** | Tiêu đề, biểu tượng, lời giới thiệu, câu hỏi gợi ý |
| **Màu nhận diện** | Chọn bảng màu |

**Ba thứ quyết định trợ lý đọc được gì**, đều nằm trong nhóm Phạm vi:

1. **Loại tài liệu** — không chọn = mọi loại.
2. **Mức quyền phục vụ** — chọn nhiều được. Đây là bộ lọc *chồng lên* quyền của
   người hỏi, **chỉ thu hẹp chứ không nới**: trợ lý khai phục vụ mức "Cán bộ"
   cũng không làm sinh viên đọc được tài liệu cán bộ.
3. **Tài liệu riêng** — tick từ danh sách. Không tick gì = đọc toàn kho theo hai
   bộ lọc trên; tick từ một tài liệu trở lên = **chỉ** đọc đúng những cái đó.

> **Ngân sách suy luận: để 0 với hầu hết trợ lý.** Token suy luận tính vào
> output, mà output đắt gấp 4 lần input — bật 500 token là chi phí mỗi câu tăng
> khoảng gấp đôi, bật 1.000 là gấp ba, kèm thêm 1–2 giây trước chữ đầu tiên. Chỉ
> đáng bật cho trợ lý hay gặp câu so sánh, tổng hợp nhiều tài liệu.

### 3.3. Kho tài liệu

Chỉ để **xem và nạp**. Việc gán tài liệu cho trợ lý làm ở mục Trợ lý ảo.

Bộ lọc trên cùng soi **đúng luật đọc thật** của hệ thống: bấm vào một trợ lý sẽ
hiện đúng những tài liệu trợ lý đó đọc được — đã gán thì hiện những cái đó, chưa
gán thì lọc toàn kho theo loại và mức quyền nó khai. Nhìn vào là biết, không
phải suy.

Nạp tài liệu: bấm **⬆ Nạp tài liệu**, kéo thả nhiều file. Nếu đang lọc theo một
trợ lý thì tài liệu **tự gán cho trợ lý đó**, loại và quyền lấy sẵn theo mặc
định của nó.

### 3.4. Chờ xử lý

File thiếu metadata hoặc hỏng chữ. Gán môn/loại rồi nạp lại, hoặc chạy **OCR
bằng AI** nếu là bản scan (nút trên web giới hạn 30 trang; tài liệu dày chạy
bằng dòng lệnh).

### 3.5. Chi phí

Bảy ngày gần nhất: số truy vấn, tổng chi phí, chi phí mỗi câu, tỉ lệ cache hit,
và phân bố nhánh xử lý.

### 3.6. Trang tư vấn

Nhúng thẳng trang chat thật vào trong trang quản trị. Sửa cấu hình trợ lý xong
bấm **Tải lại** là kiểm ngay, khỏi nhảy tab.

---

## 4. Nạp tài liệu — những gì hệ thống xử lý được

| Mục | Giá trị |
|---|---|
| Định dạng | `.pdf` `.docx` `.pptx` `.txt` `.md` |
| Kích thước tối đa | 100 MB mỗi file |
| Loại tài liệu | giáo trình · slide · đề cương · đề thi · bài tập · tài liệu tham khảo · văn bản |
| Quyền truy cập | `public` · `faculty` · `course` · `staff` |
| Học kỳ | dạng `20261` = học kỳ 1 năm học 2026–2027 |
| Chống trùng | SHA-256 toàn file — cùng nội dung không nạp hai lần dù đổi tên |

### 4.1. Tám bước của pipeline nạp

Triết lý: **chậm cũng được, phải đúng.** Một tài liệu hỏng lọt vào kho sẽ đầu độc
mọi câu trả lời liên quan về sau.

1. **Chống trùng** — băm SHA-256.
2. **Trích xuất hiểu bố cục** — với PDF: đo cỡ chữ để nhận ra tiêu đề, lột
   header/footer lặp, bỏ trang mục lục, nhận diện bảng đề cương.
3. **Hộ chiếu chất lượng chữ** — chấm từng trang theo tỉ lệ chữ có dấu, ký tự lạ,
   từ mất dấu. **Quá 10% số trang hỏng thì chặn nạp**, chuyển sang `needs_ocr`.
   Đây là lá chắn chống PDF hỏng font TCVN3/VNI.
4. **OCR bằng AI** — chỉ chạy khi bước 3 báo hỏng.
5. **Chia chunk theo văn phạm đề mục tiếng Việt** — Phần › Chương/Bài › Mục/Điều
   › n.m › n.m.k. Mục tiêu 500 token, chồng lấn 80, tối thiểu 120, trải tối đa
   4 trang.
6. **Gán metadata 5 tầng** — tên file → thư mục → `manifest.csv` → nội dung
   trang bìa → AI (chỉ khi bốn tầng máy móc bó tay).
7. **Title Engine** — lấy tiêu đề thật từ trang bìa thay vì tên file.
8. **Embedding** — 768 chiều, gộp 100 chunk mỗi lần gọi.

### 4.2. Ba cách khai metadata

**Cách 1 — đặt tên file:** `MAT1093_giao-trinh_giai-tich-1_20261.pdf`

**Cách 2 — xếp thư mục:** `files/MAT1093/de_thi/giua-ky-2025.pdf`

**Cách 3 — `files/manifest.csv`** (chính xác nhất, thắng mọi cách đoán):

```csv
file_path,title,course_code,doc_type,semester,lecturer,access
uploads/tailieu.pdf,Giáo trình Giải tích 1,MAT1093,giao_trinh,20261,Nguyễn Văn An,public
```

> **Bẫy đã gặp:** thao tác "xử lý lại" đọc lại metadata từ đầu. Tài liệu từng
> đổi `access` qua giao diện mà **không có dòng trong `manifest.csv`** sẽ rơi về
> `public`. Đổi quyền tài liệu nhạy cảm thì ghi vào manifest.

> **Tên file tiếng Việt:** tên trên đĩa buộc phải rút về ASCII an toàn, nhưng hệ
> thống giữ tên gốc vào manifest để làm tiêu đề. Tài liệu nạp trước 18/08 không
> có cơ chế này nên tiêu đề có thể mất dấu — nạp lại hoặc sửa tay trong manifest.

---

## 5. Dòng lệnh

```bash
python -m ingest.cli scan                     # quét thư mục, nạp file mới, embed
python -m ingest.cli review                   # liệt kê file chờ xử lý
python -m ingest.cli ocr 23 --max-pages 200   # OCR tài liệu dày
python -m ingest.cli reindex-all              # nạp lại toàn bộ kho
python -m ingest.cli resync-derived           # dựng lại cột dẫn xuất sau khi thêm môn
```

```bash
python -m pytest tests/ -q                    # 254 unit test, không cần CSDL
python -m eval.run_eval                       # bộ eval, in báo cáo + lưu JSON
```

Đổi mã Python xong phải **khởi động lại dịch vụ** — tiến trình không tự nạp lại.
Đổi tài sản tĩnh (JS/CSS) thì phải **nâng tem `?v=`** trong HTML, không thì trình
duyệt giữ bản cũ.

---

## 6. Giới hạn hiện tại

- **Kho mới 7 tài liệu thuộc 2 môn.** Chất lượng trả lời phụ thuộc hoàn toàn vào
  kho; hỏi ngoài hai môn đó thì báo không tìm thấy — đúng, nhưng dễ bị hiểu là
  hệ thống kém.
- **Câu so sánh, tổng hợp nhiều tài liệu** mới trả lời ở mức ghép trích đoạn.
- **Chưa có đăng nhập thật** — đang giả lập bằng danh sách người dùng mẫu.
- **Độ trễ nhánh nội dung 4–10 giây**, phần lớn do hạn mức Vertex còn chật.
- **Mã tài liệu đổi sau mỗi lần nạp lại**, link đã chia sẻ có thể hỏng.
