# KT-05 · Giới hạn output (`max_output_tokens = 400` + prompt ≤ 5 câu)

> Output đắt gấp 4–6 lần input — chặn trần bằng cả hai đầu: prompt yêu cầu trả lời tối đa 5 câu (chặn mềm) và `max_output_tokens=400` (chặn cứng).

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★☆ | Rất thấp | Trung bình (cắt cụt, câu tổng hợp bị bóp) | **Đáng dùng** — chỉnh trần đủ dư |

## Vấn đề

Ở bảng giá Gemini, output đắt gấp 4 lần input (Flash-Lite: 0,40 so với 0,10 USD/1M). LLM không tự ngắn gọn: không giới hạn thì câu trả lời 5 ý thành bài văn 600 token. Kiểm soát output là kiểm soát phần đắt nhất của mỗi lời gọi.

## Cách áp dụng trong EDU-DOCBOT

Hai lớp, chủ động và bị động:

1. **Chặn mềm trong prompt** (lớp chính): *"Trả lời NGẮN GỌN bằng tiếng Việt, tối đa 5 câu, chỉ dựa trên các đoạn trích được cung cấp"* — model chủ động viết ngắn, câu trả lời trọn vẹn.
2. **Chặn cứng bằng config** (lưới an toàn): `max_output_tokens=400` — nếu model vẫn dài dòng thì API tự cắt.

Kèm `temperature=0.2` — trả lời tra cứu cần bám sát nguồn, không cần "sáng tạo", và ít lan man hơn.

## Ưu điểm

- Cắt thẳng vào **đơn giá cao nhất**: giảm output từ ~600 xuống ~250 token/câu trả lời RAG là giảm gần 60% phần đắt nhất của lời gọi.
- Trả lời ngắn **hợp UX chat**: sinh viên cần đáp án + trích dẫn + link, không cần tiểu luận.
- Trần cứng làm chi phí mỗi truy vấn **có cận trên tuyệt đối** — dự toán ngân sách (KT-10) mới đáng tin.
- Chi phí triển khai gần bằng 0.

## Nhược điểm & đánh đổi

- **Cắt cụt giữa chừng là lỗi xấu nhất của kỹ thuật này**: nếu model chạm trần 400 khi chưa viết xong, câu cuối mất dấu trích dẫn `[n]` → bước hậu xử lý (vốn loại bỏ câu không ánh xạ được trích dẫn) sẽ xóa câu đó → người dùng mất thông tin mà không ai biết. Trần cứng phải **đủ dư** so với 5 câu tiếng Việt (~200–300 token): 400 là hợp lý, đừng "tối ưu" xuống 250.
- Câu hỏi tổng hợp chính đáng (so sánh quy chế cũ–mới, tóm tắt chương) bị bóp trong 5 câu — cần đường escalate với trần cao hơn, hoặc chấp nhận trả lời kèm gợi ý đọc tài liệu gốc.
- Giới hạn "5 câu" trong prompt là mềm — model thỉnh thoảng vẫn vượt; đừng dựa vào nó một mình (vì thế mới cần lớp 2).

## Bẫy khi triển khai

- Kiểm tra `finish_reason` của response: nếu là `MAX_TOKENS` thì log lại (đếm tần suất trên dashboard) — tần suất cao nghĩa là trần đang cắt vào nội dung thật, phải nâng.
- `max_output_tokens` phải đặt **sau khi** đã tắt thinking (KT-04): nếu thinking bật, token suy nghĩ chiếm trần trước cả khi câu trả lời bắt đầu.
- Nhánh escalate dùng trần riêng (thiết kế: 400 output, có thể nới) — đừng dùng chung một hằng số cho mọi nhánh.

## Móc xích

- **Cặp bài trùng với** KT-04: cùng nhau tạo trần output kín (thinking + answer).
- **Khả thi nhờ** KT-06: chỉ còn 4 chunk đầu vào chất lượng cao thì 5 câu mới đủ trả lời; đưa 15 chunk tạp thì 5 câu là không thể.
- **Tương tác với hậu xử lý trích dẫn**: quy tắc "loại câu không có trích dẫn hợp lệ" khuếch đại thiệt hại của việc cắt cụt — hai bên phải được thiết kế cùng nhau.

## Đánh giá

**Đáng dùng — đơn giản, đúng chỗ đắt nhất.** Lưu ý duy nhất nhưng quan trọng: đây là kỹ thuật *giới hạn*, không phải *tối ưu* — giá trị của nó phụ thuộc vào việc trần được đặt đúng. Đặt trần bằng số liệu (phân phối độ dài output thực tế từ `query_logs` + tần suất `MAX_TOKENS`), không phải bằng cảm giác. Con số 400/5 câu của thiết kế là điểm khởi đầu tốt.
