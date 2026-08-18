# EDU-DOCBOT — Báo cáo dự án

> Chốt số liệu **18/08/2026**. Mọi con số đo trực tiếp từ mã nguồn và cơ sở dữ
> liệu sản xuất tại thời điểm viết, không lấy từ tài liệu thiết kế.
> Bản Word trình lãnh đạo: [word/EDU-DOCBOT-Bao-cao-du-an.docx](word/EDU-DOCBOT-Bao-cao-du-an.docx) ·
> Kiến trúc chi tiết: [word/EDU-DOCBOT-Tai-lieu-ky-thuat.docx](word/EDU-DOCBOT-Tai-lieu-ky-thuat.docx)

---

## 1. Tóm tắt

- **Hệ thống đang chạy và trả lời được.** Tra cứu tài liệu và hỏi đáp nội dung
  đều hoạt động đầy đủ, mọi câu trả lời về nội dung đều dẫn nguồn kèm số trang.
- **Một hệ thống, nhiều trợ lý.** Hiện có 2 trợ lý — tra cứu tài liệu học tập và
  tư vấn tuyển sinh. Thêm trợ lý mới chỉ cần khai báo trên trang quản trị: không
  sửa mã, không cài đặt lại, không dừng dịch vụ.
- **Chi phí 0,000205 USD mỗi câu hỏi**, đo trên 315 lượt hỏi thật. Quy ra 50.000
  lượt/tháng khoảng 10 USD, dưới trần 15 USD đặt ra ban đầu.
- **254 kiểm thử tự động, toàn bộ đạt.** Chạy tự động mỗi lần cập nhật mã.
- **Việc chặn lớn nhất là thiếu tài liệu.** Kho mới có 7 tài liệu thuộc 2 môn,
  nên chưa đo được recall@5 và tỉ lệ trích dẫn đúng.
- **Hai việc cần lãnh đạo quyết** (mục 6), đều nằm ngoài quyền xử lý của đội.

---

## 2. Hệ thống trả lời được những gì

| Nhóm câu hỏi | Ví dụ người dùng gõ | Cách xử lý | Chi phí |
|---|---|---|---|
| Tìm tài liệu theo môn, loại, học kỳ | "slide kiểm thử phần mềm", "INT3115 có tài liệu gì" | SQL + template | **0 token sinh** |
| Hỏi nội dung bên trong tài liệu | "kiểm thử hồi quy là gì", "vì sao có index rồi mà query vẫn chậm" | Hybrid retrieval → RRF → sinh kèm trích dẫn | Tốn phí |
| Chào hỏi, cảm ơn | "chào bạn" | Câu mẫu riêng của từng trợ lý | **0 token** |
| Ngoài phạm vi, ngôn từ xúc phạm, câu cụt | "giải giúp bài tập này", "là gì" | Từ chối lịch sự hoặc hỏi lại cho rõ | **0 token** |

Phân bố thật trên 315 lượt đã ghi nhật ký:

| Nhánh | Số lượt | Tốn token sinh |
|---|---|---|
| `rag` | 156 | có |
| `static` | 73 | không |
| `metadata` | 53 | không |
| `cache` | 17 | không |
| `fulltext` | 11 | không |
| `content` | 5 | có |

**154/315 lượt (49%) trả lời trọn vẹn mà không gọi mô hình ngôn ngữ.** Đây là
kết quả trực tiếp của kiến trúc chặn-trước-bằng-SQL, và là lý do chi phí giữ
được ở mức thấp.

### 2.1. Ba cam kết về chất lượng

1. **Không bịa.** Mỗi dấu `[n]` phải ánh xạ về một đoạn trích thật. Không ánh xạ
   được thì cắt cả câu; không còn trích dẫn nào thì báo không tìm thấy.
2. **Không lộ tài liệu ngoài quyền.** Điều kiện phân quyền nằm trong mệnh đề
   `WHERE`, nên mô hình không bao giờ nhìn thấy chunk ngoài quyền người hỏi.
3. **Mỗi trợ lý đúng giọng, đúng phạm vi.** Trợ lý tuyển sinh không chìa ra giáo
   trình kiểm thử phần mềm, và ngược lại.

---

## 3. Hạn chế hiện tại

| Hạn chế | Người dùng gặp gì | Gỡ bằng cách nào |
|---|---|---|
| Kho mới 7 tài liệu / 2 môn | Hỏi ngoài hai môn đó thì báo không tìm thấy — đúng, nhưng dễ bị hiểu là hệ thống kém | Nạp thêm tài liệu; không phải việc của mã nguồn |
| Câu so sánh, tổng hợp nhiều tài liệu | Mới ghép trích đoạn, chưa thực sự so sánh | Bật ngân sách suy luận, đổi lại chi phí tăng ~gấp đôi |
| Chưa nối SSO của trường | Phân quyền chạy trên danh sách người dùng mẫu | Chờ tích hợp; tầng phân quyền phía dưới đã sẵn sàng |
| Câu trả lời nội dung mất 4–10 giây | Chờ lâu hơn mong đợi | Phần lớn do hạn mức Vertex còn chật — xem mục 6 |
| Mã tài liệu đổi sau mỗi lần nạp lại | Link đã chia sẻ có thể hỏng | Việc kỹ thuật, đội tự xử lý |

---

## 4. Số liệu

| Chỉ số | Giá trị |
|---|---|
| Kiểm thử tự động | **254 / 254 đạt** |
| Chi phí mỗi câu (315 lượt thật) | **0,000205 USD** |
| Ước ở mức 50.000 lượt/tháng | ~10 USD (trần: 15 USD) |
| Tỉ lệ câu không tốn phí mô hình | **49%** |
| Trợ lý đang phục vụ | 2 |
| Tài liệu trong kho | 7 (6 sẵn sàng tra cứu, 1 chờ OCR) |
| Đoạn nội dung đã đánh chỉ mục | 470 (100% đã có vector) |
| Môn học có tài liệu | 2 / 10 |
| Endpoint API | 29 |
| Migration đã chạy | 8 |
| Quy mô mã nguồn | ~14.700 dòng |
| Chi phí hạ tầng thêm | ~17 USD/tháng |

---

## 5. Tiến độ

| GĐ | Nội dung | Trạng thái |
|---|---|---|
| 1 | Tra cứu tài liệu bằng SQL | ✓ Xong |
| 2 | Hỏi đáp nội dung kèm trích dẫn | ✓ Xong |
| 3 | Tối ưu chi phí | ◐ 6/10 kỹ thuật, **chi phí đã đạt mục tiêu** |
| 4 | Triển khai, kiểm thử, phân quyền | ◐ Đã lên Google Cloud; còn kiểm thử tải và mở rộng bộ eval |

### 5.1. Làm thêm ngoài kế hoạch

1. **Chuyển sang nền tảng nhiều trợ lý.** Ban đầu chỉ phục vụ một trợ lý, mọi
   câu chữ và phạm vi viết cứng trong mã. Nay mỗi trợ lý là một bản ghi: tên,
   giọng, kho tài liệu riêng, mức quyền phục vụ, đầu mối liên hệ riêng, tham số
   sinh riêng. Nhân bản trợ lý là việc của người vận hành.
2. **Nâng cấp khâu nạp tài liệu.** Thêm hộ chiếu chất lượng chữ từng trang để
   chặn tài liệu hỏng font lọt vào kho — loại lỗi âm thầm nhất, vì tài liệu vẫn
   "nạp thành công" nhưng nội dung đọc ra là ký tự vô nghĩa.
3. **Gộp trang quản trị, làm lại giao diện chat.** Chat thành bong bóng nổi góc
   màn hình; quản trị gom về một trang có sidebar.

### 5.2. Lỗi đã phát hiện và khắc phục

Tất cả đều phát hiện bằng chạy thử thật, không phải rà mã.

| Lỗi | Người dùng gặp phải điều gì |
|---|---|
| Gợi ý làm lộ tài liệu ngoài quyền | Khách chưa đăng nhập được mách đúng mã môn của tài liệu họ không có quyền đọc |
| Lời chào nuốt mất câu hỏi | "Chào bạn, cho mình hỏi slide kiểm thử phần mềm" chỉ nhận lại lời chào |
| Chửi bậy tiếng Anh không bị chặn | `fuck you` nhận lại câu tư vấn bình thường |
| Câu trả lời nói chuyện nội bộ | Người hỏi bị bảo đi "báo quản trị viên bổ sung tài liệu" |
| Tiêu đề tài liệu bóc sai | Một bộ slide hiển thị tên giảng viên thay vì tên bài; một tài liệu mất dấu tiếng Việt ở tiêu đề |
| Danh sách kết quả mô tả sai | "Không tìm thấy đề thi" rồi ngay dưới ghi "tìm thấy 1 đề thi" trong khi đó là slide môn khác |
| Ranh giới giữa các trợ lý rơi mất | Trợ lý tuyển sinh chìa ra giáo trình kiểm thử phần mềm — im lặng, không lỗi nào báo |

---

## 6. Việc cần lãnh đạo quyết

### 6.1. An toàn cơ sở dữ liệu — nên xử lý sớm

CSDL đang mở cho mọi địa chỉ Internet (`0.0.0.0/0`), cho phép kết nối không mã
hóa, ứng dụng dùng tài khoản `root`. Trước đó đã có một lần mở tương tự kéo dài
khoảng **32 giờ**.

Đề nghị: cho phép thu hẹp danh sách địa chỉ, đổi mật khẩu, tạo tài khoản riêng
quyền hạn chế. Khoảng 30 phút, nhưng cần thống nhất thời điểm vì có gián đoạn
ngắn.

### 6.2. Nâng hạn mức Vertex AI

Hạn mức hiện chật, khiến câu trả lời có lúc mất 15–40 giây thay vì dưới 1 giây.
Đây là nguyên nhân chính của cảm giác chậm, **không phải do mã nguồn**.

Đề nghị: gửi yêu cầu nâng hạn mức lên Google. Đội không tự làm được vì cần quyền
quản trị tài khoản.

### 6.3. Cấp tài liệu cho kho

Không phải quyết định kỹ thuật mà là điều phối: cần đầu mối cung cấp tài liệu
chính thức. Ưu tiên 4–6 môn có đủ giáo trình, slide, đề cương, đề thi, và bộ văn
bản quy chế. Có tài liệu mới đo được độ chính xác thật.

---

## 7. Việc đội làm tiếp

Không cần quyết định gì thêm:

- Bật lớp cache theo ngữ nghĩa để giảm tiếp chi phí và độ trễ.
- Mở rộng bộ câu hỏi đánh giá tự động khi kho đủ lớn.
- Kiểm thử tải.
- Giữ mã tài liệu cố định khi nạp lại.

---

*Chi tiết kiến trúc, mô hình dữ liệu và các tầng pipeline: xem tài liệu kỹ thuật
đi kèm. Hướng dẫn sử dụng cho người dùng cuối và quản trị viên:
[huong-dan-su-dung.md](huong-dan-su-dung.md).*
