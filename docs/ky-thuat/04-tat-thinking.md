# KT-04 · Tắt thinking (`thinking_budget = 0`)

> Token suy luận (thinking) được Gemini tính chung giá với output — nguồn vỡ ngân sách phổ biến nhất. Router và RAG thường tắt hẳn; chỉ nhánh escalate được cấp ngân sách suy luận thấp.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★★ (chặn nhân tố có thể x2–x10 chi phí output) | **Rất thấp** (một dòng config) | Thấp–trung bình (ca khó kém sâu) | **Bắt buộc dùng** |

## Vấn đề

Các model Gemini thế hệ mới mặc định có thể "suy nghĩ" trước khi trả lời. Token suy nghĩ **tính tiền như output** (đắt gấp 4 lần input) và người dùng không nhìn thấy. Một câu trả lời 250 token có thể kèm 500–2.000 token thinking âm thầm — chi phí thật gấp nhiều lần dự toán mà không tăng chút giá trị nào cho tác vụ đơn giản.

## Cách áp dụng trong EDU-DOCBOT

```python
generation_config = {
    "temperature": 0.2,
    "max_output_tokens": 400,
    "thinking_config": {"thinking_budget": 0},   # router + RAG thường
    "response_mime_type": "text/plain",
}
```

- **Router:** `thinking_budget=0` — phân loại intent + trích trường là tác vụ mẫu, không cần suy luận.
- **RAG thường (Flash-Lite):** `thinking_budget=0` — trả lời 5 câu từ 4 đoạn trích cho sẵn.
- **Escalate (Gemini 3 Flash, <3% truy vấn):** ngân sách suy luận **thấp** (không tắt hẳn) — đây chính là chỗ dành cho các câu cần tổng hợp nhiều nguồn.

## Ưu điểm

- Hiệu quả trên công sức **cao nhất toàn hệ thống**: một dòng cấu hình, chặn đứng nguồn phát sinh chi phí khó lường nhất.
- Chi phí trở nên **dự đoán được**: output bị chặn trần thực sự bởi `max_output_tokens` (KT-05) — không còn phần chìm.
- Giảm cả độ trễ: không mất thời gian sinh token suy nghĩ trước token đầu tiên (phục vụ NFR-02: first token p95 < 2,5s).

## Nhược điểm & đánh đổi

- Câu hỏi thật sự khó (so sánh giữa nhiều tài liệu, suy luận nhiều bước) trả lời **nông hơn** khi không có thinking — được bù bằng đường escalate, nhưng router phải nhận diện đúng ca khó thì đường bù mới hoạt động.
- Chất lượng viết lại `standalone_question` (KT-07) ở các hội thoại rối có thể giảm nhẹ khi router không được suy nghĩ — bộ eval 20 câu multi-turn là chỗ đo điều này.
- Phụ thuộc hành vi model: mỗi thế hệ Gemini xử lý `thinking_budget=0` khác nhau (có model chỉ coi là gợi ý). Cần xác nhận bằng `usage_metadata` thực tế, không tin config.

## Bẫy khi triển khai

- **Ghi log token thinking riêng**: `query_logs` đã có cột token — khi đối soát, lấy số liệu từ `usage_metadata` của API (bao gồm thoughts_token_count nếu có), đừng tự đếm từ text trả về.
- Khi nâng cấp/đổi model (rủi ro R-06 trong thiết kế), kiểm tra lại mặc định thinking của model mới — mặc định "bật" là bẫy tái phát.
- Đừng tắt thinking cho nhánh escalate — tắt nốt thì escalate mất luôn lý do tồn tại.

## Móc xích

- **Cặp bài trùng với** KT-05: KT-04 chặn phần chìm (thinking), KT-05 chặn phần nổi (answer) — thiếu một trong hai thì trần output không kín.
- **Điều kiện của** bảng chi phí ở KT-09/tổng kết: mọi con số dự toán (80 token output router, 250 output RAG) chỉ đúng khi thinking = 0.
- **Được bù bởi** đường escalate (Gemini 3 Flash, ngân sách thấp, <3% lưu lượng, cảnh báo nếu >8%).

## Đánh giá

**Bắt buộc dùng, gần như không có lý do để không.** Với tác vụ dạng phân loại và trả lời trích dẫn ngắn, thinking là chi phí thuần không tạo giá trị. Rủi ro duy nhất (ca khó kém sâu) đã có van xả là escalate. Đây nên là dòng config đầu tiên viết ra khi code lớp gọi LLM, kèm một assert trong eval: chi phí đo được ≈ chi phí dự toán.
