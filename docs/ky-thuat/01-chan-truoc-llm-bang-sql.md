# KT-01 · Chặn trước LLM bằng SQL (metadata-first)

> Truy vấn nào trả lời được bằng SQL thì tuyệt đối không đưa qua LLM — nhánh metadata xử lý phần lớn truy vấn với **0 token sinh**.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★★ (nhánh chiếm ≥45% lưu lượng, 0 token) | Trung bình | Thấp | **Bắt buộc dùng** — xương sống của kiến trúc |

## Vấn đề

Phần lớn câu hỏi của sinh viên không phải câu hỏi "hiểu nội dung" mà là tra cứu thuần túy: *"cho mình slide Giải tích 1 kỳ này"*, *"đề thi XSTK năm ngoái"*. Nếu đưa các câu này qua RAG đầy đủ (retrieval + sinh), mỗi câu tốn ~2.750 token trong khi một câu SELECT trả lời được chính xác hơn.

## Cách áp dụng trong EDU-DOCBOT

1. Router (KT-07) trích các trường có cấu trúc: `course_code`, `course_hint`, `doc_type`, `semester`, `lecturer`.
2. Ứng dụng dựng mệnh đề WHERE **động** — chỉ thêm điều kiện cho trường khác null; luôn kèm `status='indexed'` và lọc quyền `(access='public' OR faculty_id=:user_faculty_id)`.
3. Khớp môn học qua 3 lớp: `code_norm` chính xác → `name_norm LIKE` → bảng `course_aliases` (gt1, xstk…).
4. Kết quả render bằng **template cố định** phía ứng dụng — không gọi LLM để "viết lại cho đẹp".
5. Bậc thang hạ cấp: 0 kết quả → thử FULLTEXT trên `title_norm` → vẫn 0 → mới chuyển sang nhánh RAG (KT-06).

```sql
SELECT DISTINCT d.id, d.title, d.type, d.file_path, d.semester, d.lecturer
FROM documents d
JOIN courses c             ON c.id = d.course_id
LEFT JOIN course_aliases a ON a.course_id = c.id
WHERE d.status = 'indexed'
  AND (d.access = 'public' OR d.faculty_id = :user_faculty_id)
  -- các điều kiện dưới chỉ thêm khi router trả về giá trị
  AND c.code_norm = :course_code
  AND d.type = :doc_type
  AND d.semester = :semester
ORDER BY d.year DESC, d.type, d.title
LIMIT 10;
```

## Ưu điểm

- **0 token sinh** cho nhánh chiếm mục tiêu ≥45% lưu lượng — đóng góp lớn nhất vào chênh lệch 7,3 USD so với 870 USD/tháng.
- Kết quả **xác định (deterministic)**: cùng câu hỏi luôn ra cùng danh sách, không bịa, dễ viết test.
- Độ trễ thấp nhất hệ thống (mục tiêu p95 < 800 ms), dễ đạt vì chỉ là SQL có index.
- Lọc quyền nằm ngay trong WHERE — an toàn hơn mọi cách lọc sau sinh.
- Dễ debug: sai thì nhìn SQL và dữ liệu, không phải "đoán" hành vi model.

## Nhược điểm & đánh đổi

- **Sống chết theo chất lượng router**: router trích sai `course_code`/`doc_type` thì SQL trả kết quả sai hoặc rỗng một cách "tự tin". Lỗi ở đây khó phát hiện hơn lỗi RAG vì không có bước sinh nào để "chữa".
- Câu trả lời template **cứng nhắc**, kém tự nhiên so với LLM viết — chấp nhận được cho danh sách tài liệu, nhưng không mở rộng được sang câu hỏi mở.
- Bảng `course_aliases` phải **điền và duy trì thủ công**; alias thiếu → câu hỏi lẽ ra thuộc nhánh A bị rơi xuống nhánh B tốn tiền.
- Giả định "≥45% truy vấn là tra cứu metadata" cần kiểm chứng bằng log thực tế — nếu thực tế sinh viên chủ yếu hỏi nội dung, lợi ích giảm mạnh.

## Bẫy khi triển khai

- WHERE quá hẹp (đủ cả 4 điều kiện) dễ trả 0 kết quả dù tài liệu tồn tại — phải có bậc thang nới lỏng dần trước khi chuyển nhánh.
- Đừng quên `DISTINCT` khi JOIN với `course_aliases` (một môn nhiều alias → trùng dòng).
- Template phải xử lý cả trường hợp 0 kết quả một cách thân thiện (gợi ý từ khóa, không im lặng).

## Móc xích

- **Nhận đầu vào từ** KT-07 (router trích trường + câu hỏi độc lập).
- **Giảm tải cho** KT-06, KT-05, KT-04: mọi truy vấn bị chặn ở đây không bao giờ chạm tới RAG.
- **Được bảo vệ bởi** KT-02: cache hit còn chặn trước cả router.
- **Là chế độ fallback của** KT-10: chạm quota thì hệ thống chỉ còn chạy nhánh này.

## Đánh giá

**Đáng dùng vô điều kiện.** Đây là kỹ thuật rẻ nhất trên mỗi phần trăm chi phí tiết kiệm được, đồng thời *tăng* chất lượng (kết quả chính xác, có link tải) thay vì đánh đổi. Điều kiện tiên quyết duy nhất: đầu tư nghiêm túc vào router và bộ alias môn học, và theo dõi phân bố route thực tế (mục tiêu metadata ≥45%) để xác nhận giả định.
