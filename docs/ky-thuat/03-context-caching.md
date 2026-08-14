# KT-03 · Context caching cho system prompt

> Phần prompt lặp lại y hệt ở mọi lời gọi (system prompt của router và của RAG) được cache phía Gemini — mỗi lần hit chỉ trả ~10% giá input cho phần đó.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★☆☆☆ (tiết kiệm tuyệt đối nhỏ vì prompt ngắn) | Thấp–trung bình | Không ảnh hưởng chất lượng | **Ưu tiên thấp — đo trước khi làm** |

## Vấn đề

System prompt của router (~200 token) và của bước sinh RAG (~250 token) giống hệt nhau ở mọi truy vấn. Không cache thì mỗi tháng trả tiền input cho cùng một đoạn văn hàng chục nghìn lần.

## Cách áp dụng trong EDU-DOCBOT

- Tạo context cache cho 2 system prompt (router, RAG) qua API caching của Gemini.
- TTL 1 giờ, có job làm mới định kỳ để cache không hết hạn giữa giờ cao điểm.
- Bảng cấu hình theo thiết kế: router 250 token input thì 200 nằm trong cache; RAG 2.500 token input thì 250 nằm trong cache.

## Ưu điểm

- Cache hit chỉ tính **≈10% giá input** cho phần được cache — về tỷ lệ là mức giảm sâu.
- Không đánh đổi chất lượng: prompt gửi đến model y hệt, chỉ khác cách tính tiền.
- Triển khai một chỗ (lớp gọi LLM), không lan ra logic nghiệp vụ.

## Nhược điểm & đánh đổi

- **Tiết kiệm tuyệt đối rất nhỏ ở quy mô này.** Làm tròn: 20.000 lượt router × 200 token = 4M token/tháng × 0,10 USD/1M ≈ 0,40 USD; phần cache của RAG còn nhỏ hơn. Tổng tiết kiệm lý thuyết **dưới 0,5 USD/tháng** — trong khi context cache của Gemini có **phí lưu trữ tính theo giờ**, chạy 24/7 có thể ăn hết (thậm chí vượt) khoản tiết kiệm.
- **Ngưỡng token tối thiểu:** explicit caching của Gemini yêu cầu cache đạt kích thước tối thiểu (tùy model, thường ≥1.024 token). System prompt 200–250 token **có thể không đủ điều kiện** — phải kiểm tra với model cụ thể trước khi thiết kế code quanh nó.
- Thêm một trạng thái phải quản lý (tên cache, TTL, làm mới, xử lý khi cache biến mất giữa chừng) — độ phức tạp vận hành không tương xứng với 0,5 USD.
- Giá và cơ chế caching của Gemini thay đổi thường xuyên — thiết kế đã ghi chú "cần đối chiếu thêm phí lưu theo giờ".

## Bẫy khi triển khai

- Đừng gộp lịch sử hội thoại hay đoạn trích vào cache — chỉ cache phần **bất biến** (system prompt). Phần biến thiên cache miss liên tục sẽ phản tác dụng.
- Code phải chạy đúng cả khi cache hết hạn/không tồn tại (fallback gửi prompt đầy đủ), không được lỗi cứng.
- Nếu dùng implicit caching (Gemini tự cache prefix), chỉ cần **đặt phần bất biến lên đầu prompt** — gần như miễn phí công sức, nên làm ngay cả khi bỏ explicit caching.

## Móc xích

- **Áp lên** các lời gọi của KT-01 (router) và KT-06/nhánh RAG — không có vai trò trong luồng logic, thuần túy là cách tính tiền.
- **Bị giảm giá trị bởi** KT-02: cache ngữ nghĩa hit càng nhiều, số lời gọi LLM càng ít, phần tiết kiệm của KT-03 càng nhỏ.
- **Cộng hưởng với** KT-07: prompt không chứa lịch sử → phần bất biến chiếm tỷ trọng lớn → prefix caching hiệu quả hơn.

## Đánh giá

**Ưu tiên thấp nhất trong 10 kỹ thuật.** Về nguyên lý thì đúng, nhưng ở quy mô 50k truy vấn/tháng với prompt ngắn, đây là kỹ thuật duy nhất mà chi phí (phí lưu theo giờ + độ phức tạp + rủi ro không đủ ngưỡng token tối thiểu) có thể **lớn hơn khoản tiết kiệm**. Việc nên làm ngay: sắp phần bất biến lên đầu prompt để hưởng implicit caching miễn phí. Explicit caching chỉ bật sau khi dashboard (KT-10, `query_logs`) chứng minh bằng số liệu là có lãi.
