# KT-02 · Semantic cache hai tầng trong MariaDB

> Câu hỏi đã trả lời rồi thì không trả lời lại: tầng 0 khớp tuyệt đối bằng hash, tầng 1 khớp ngữ nghĩa bằng vector — tất cả nằm ngay trong MariaDB, không thêm dịch vụ.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★☆ (mục tiêu hit ≥35% sau 1 tháng) | Trung bình | **Trung bình–cao** (trả nhầm, trả cũ) | **Đáng dùng**, kèm invalidation + tinh chỉnh ngưỡng |

## Vấn đề

Câu hỏi của sinh viên có **tính mùa vụ rất mạnh**: đầu học kỳ hàng nghìn người hỏi cùng một thứ ("đề cương triết kỳ này", "slide chương 1 giải tích"). Trả lời lại từng người bằng LLM là đốt tiền cho cùng một output.

## Cách áp dụng trong EDU-DOCBOT

Bảng `query_cache` lưu: `query_hash`, `query_norm`, `embedding VECTOR(768)`, `answer`, `citations`, `access`, `faculty_id`, `hit_count`, `expires_at`.

**Tầng 0 — hash (gần như miễn phí):** khớp tuyệt đối `query_hash` trên UNIQUE KEY sau khi chuẩn hóa câu hỏi bằng `vn_norm()`.

**Tầng 1 — vector:** embed câu hỏi (~25 token), lấy top-K bằng VECTOR INDEX rồi mới áp ngưỡng ở SELECT bọc ngoài (áp `WHERE VEC_DISTANCE < ngưỡng` trực tiếp sẽ quét toàn bảng):

```sql
SELECT * FROM (
  SELECT id, answer, citations, access, faculty_id, expires_at,
         VEC_DISTANCE_COSINE(embedding, :qvec) AS distance
  FROM query_cache
  ORDER BY distance
  LIMIT 5
) t
WHERE t.distance < 0.05
  AND t.expires_at > NOW()
  AND (t.access = 'public' OR t.faculty_id = :user_faculty_id)
LIMIT 1;
```

## Ưu điểm

- Mỗi cache hit tiết kiệm **toàn bộ** chi phí router + RAG phía sau (chỉ còn ~25 token embedding); với mục tiêu hit 35%, đây là kỹ thuật cắt lưu lượng lớn thứ hai sau KT-01.
- Không thêm dịch vụ vận hành (đã bỏ Redis) — cache sống trong cùng CSDL, backup cùng một chỗ.
- Khớp ngữ nghĩa bắt được các biến thể diễn đạt ("điều kiện qua môn triết" ≈ "triết học điểm bao nhiêu thì qua") mà hash không bao giờ bắt được.
- `hit_count` cho số liệu trực tiếp lên dashboard (NFR vận hành: hit ≥ 35%).

## Nhược điểm & đánh đổi

- **Rủi ro trả nhầm là rủi ro thật:** ngưỡng 0.05 quá lỏng sẽ trả câu trả lời của một câu hỏi *khác nghĩa* nhưng gần về vector — đặc biệt nguy hiểm với tiếng Việt đã bỏ dấu, nơi hai câu khác nhau có thể rất gần nhau. Ngưỡng phải được tinh chỉnh bằng bộ eval, không lấy 0.05 làm chân lý.
- **Trả lời cũ (stale):** tài liệu được cập nhật/gỡ bỏ nhưng cache vẫn trả bản cũ đến khi `expires_at`. Thiết kế hiện chỉ có TTL — nên bổ sung invalidation theo `document_id` khi reindex.
- Vẫn tốn phí embedding cho **mọi** truy vấn (kể cả miss), cộng thêm một vòng truy vấn vector trước khi làm việc thật — miss thì đây là chi phí thuần.
- Cache phân mảnh theo quyền (`access`, `faculty_id`): cùng câu hỏi ở hai khoa khác nhau là hai entry — hit rate thực tế thấp hơn con số lý thuyết.
- Bảng phình theo thời gian; cần dọn theo `expires_at` (index đã có).

## Bẫy khi triển khai

- **Không bao giờ** trả cache khi phạm vi quyền không khớp — lọc quyền nằm trong chính câu SQL cache, không phải sau đó.
- `query_cache` cũng có VECTOR INDEX → tuân thủ đủ quy tắc: NOT NULL, DISTANCE=cosine, ORDER BY + LIMIT.
- Chỉ ghi cache cho câu trả lời "thành công" — đừng cache câu "không tìm thấy" quá lâu (tài liệu có thể được nạp thêm ngay ngày mai).

## Móc xích

- **Đứng trước tất cả**: hit ở đây thì KT-01, KT-04, KT-05, KT-06, KT-07 đều không chạy.
- **Hưởng lợi trực tiếp từ** KT-07: câu hỏi viết lại độc lập ngữ cảnh mới cache dùng chung giữa các user/phiên được — không có KT-07 thì tầng vector gần như vô dụng với hội thoại nhiều lượt.
- **Dùng chung hạ tầng với** KT-08 (embedding 768 chiều làm khóa cache).

## Đánh giá

**Đáng dùng** — bối cảnh trường học là kịch bản lý tưởng của semantic cache (câu hỏi lặp theo mùa). Nhưng đây là kỹ thuật có **rủi ro chất lượng cao nhất** trong 10 kỹ thuật: sai ngưỡng là trả nhầm, thiếu invalidation là trả cũ, lọt quyền là sự cố bảo mật. Triển khai ở giai đoạn 3 (sau khi RAG đã đúng), bật tầng hash trước, tầng vector chỉ bật sau khi có bộ eval đo ngưỡng.
