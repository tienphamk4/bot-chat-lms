# KT-09 · Batch API cho mọi tác vụ offline (−50%)

> Mọi lời gọi Gemini không cần trả lời ngay — embedding khi nạp tài liệu, trích metadata, sinh tóm tắt — đi qua Batch API để hưởng giảm giá 50%.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★☆☆☆ (−50% nhưng chỉ trên phần offline vốn đã nhỏ) | Trung bình (mô hình submit/poll bất đồng bộ) | Không (kết quả y hệt) | **Đáng dùng cho ingest** — đừng làm sớm |

## Vấn đề

Nạp 5.000 tài liệu ban đầu tốn ~60M token embedding. Giá realtime: ~9 USD; giá batch: ~4,5 USD. Với các tác vụ chạy lúc 2 giờ sáng bằng systemd timer, trả giá realtime để nhận kết quả "ngay lập tức" là trả tiền cho thứ không ai cần.

## Cách áp dụng trong EDU-DOCBOT

Áp cho pipeline nạp (chạy bằng `edu-docbot-ingest.timer`, không có người dùng chờ):

1. **Embedding chunk** — gộp 100 chunk/lần gọi, đẩy qua Batch API.
2. **Trích metadata bằng Flash-Lite** — chỉ với file mà regex trên tên file thất bại; gom lại thành lô.
3. **Sinh tóm tắt tài liệu** (nếu có) — cùng cơ chế.

Luồng: gom yêu cầu → submit batch job → poll trạng thái → tải kết quả → ghi vào MariaDB → (sau khi nạp xong toàn bộ) tạo VECTOR INDEX.

**Không bao giờ** dùng cho đường online: router, sinh câu trả lời, embedding câu hỏi của người dùng — các lời gọi này cần độ trễ mili giây, Batch API tính bằng phút–giờ.

## Ưu điểm

- **Giảm giá 50% vô điều kiện** trên phần áp dụng được — không đánh đổi chất lượng, kết quả giống hệt lời gọi thường.
- Khớp tự nhiên với kiến trúc đã chọn: nạp tài liệu vốn là tác vụ theo lô chạy đêm (đã bỏ Celery, dùng systemd timer) — không phải bẻ kiến trúc để dùng batch.
- Batch job chịu được khối lượng lớn mà không lo rate limit như khi bắn hàng nghìn request realtime liên tiếp.

## Nhược điểm & đánh đổi

- **Khoản tiết kiệm tuyệt đối nhỏ**: ~4,5 USD một lần cho nạp ban đầu, và phần nạp bổ sung hằng ngày còn nhỏ hơn nhiều. Đây là tối ưu "tiện tay thì làm", không phải trụ cột.
- **Code phức tạp hơn đáng kể** so với gọi thẳng: quản lý vòng đời job (submit/poll/timeout/retry), ghép kết quả về đúng chunk, xử lý lô thất bại một phần. Pipeline ingest từ hàm tuần tự thành máy trạng thái.
- **Độ trễ không đảm bảo** (thường trong 24h): tài liệu admin cần "lên ngay trong hôm nay" không đi đường này được — cần thêm đường realtime cho `POST /api/v1/admin/reindex/{doc_id}`, tức duy trì **hai đường code** cho cùng một việc.
- Debug khó hơn: lỗi xuất hiện ở kết quả batch nhiều giờ sau khi submit, không stack trace tại chỗ.

## Bẫy khi triển khai

- Ghi trạng thái batch job vào DB (bảng phụ hoặc cột trên `documents`): timer chạy 2 giờ sáng, kết quả về lúc nào không chắc — chu kỳ chạy sau phải biết job nào đang treo, job nào cần thu hoạch.
- Job thất bại một phần: chỉ nạp lại phần thiếu theo `document_id` (chính sách nạp lại của thiết kế), đừng chạy lại cả lô.
- `documents.status` (`pending`/`indexed`/`failed`) phải phản ánh đúng vòng đời batch — tài liệu kẹt ở `pending` quá một chu kỳ là tín hiệu job chết.

## Móc xích

- **Phục vụ** pipeline nạp: embedding (hạ tầng của KT-08), trích metadata (đầu vào của KT-01).
- **Độc lập với đường online** — kỹ thuật duy nhất trong 10 kỹ thuật không nằm trên luồng truy vấn của người dùng.
- **Cộng hưởng với** quy tắc "tạo VECTOR INDEX sau khi nạp": cả hai cùng dồn việc nặng về cửa sổ offline ban đêm.

## Đánh giá

**Đáng dùng, nhưng đúng thời điểm.** Về bản chất đây là giảm giá miễn phí — không có lý do từ chối. Nhưng ở quy mô vài USD, sự phức tạp bất đồng bộ không đáng để làm sớm: giai đoạn 1–2 cứ gọi realtime cho pipeline chạy đúng đã (chênh lệch ~4,5 USD một lần), chuyển sang batch ở giai đoạn 3 khi tối ưu chi phí, và chỉ cho đường ingest định kỳ. Nếu ngày nào đó trường nạp thêm 50.000 tài liệu thì kỹ thuật này mới thành bắt buộc.
