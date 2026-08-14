# KT-06 · Rerank local (bge-reranker-v2-m3, top_k 15→4)

> Truy hồi rộng (2×20 ứng viên) rồi dùng reranker chạy local trên CPU chọn lại 4 chunk tốt nhất gửi lên LLM — đổi ~200ms CPU miễn phí lấy ~65% input token của nhánh RAG.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★☆ (−65% input nhánh RAG) | Trung bình–cao (vận hành model local) | Thấp (thường *tăng* chất lượng) | **Đáng dùng** — ROI cao nhất nhánh RAG |

## Vấn đề

Không rerank thì muốn đảm bảo recall phải gửi 12–15 chunk lên LLM (vì thứ hạng của vector search + FULLTEXT chưa đủ tin để chỉ lấy top 4) — tức ~7.500 token input thay vì 2.500. Gửi nhiều chunk tạp còn làm câu trả lời loãng và trích dẫn kém chính xác.

## Cách áp dụng trong EDU-DOCBOT

1. Hybrid retrieval: kênh vector + kênh FULLTEXT, mỗi kênh 20 kết quả, giới hạn trong `allowed_doc_ids` đã lọc quyền.
2. Hợp nhất bằng RRF (k=60) ở tầng ứng dụng → lấy 20 ứng viên đầu.
3. `bge-reranker-v2-m3` (cross-encoder, hỗ trợ đa ngữ trong đó có tiếng Việt) chấm điểm từng cặp (câu hỏi, chunk) → giữ **4 chunk** đưa vào prompt.
4. Chạy trên CPU máy chủ, qua `sentence-transformers` — không tốn token, ~200ms.

## Ưu điểm

- **Tiết kiệm ~65% input token** của nhánh RAG — khoản tiết kiệm lớn nhất trong các kỹ thuật thuộc nhánh này (nhánh RAG chiếm ~4,2/7,3 USD tổng chi phí tháng).
- **Tăng chất lượng thay vì đánh đổi**: cross-encoder đọc cả câu hỏi lẫn chunk cùng lúc, xếp hạng chính xác hơn hẳn khoảng cách vector thuần — 4 chunk sau rerank thường tốt hơn 15 chunk trước rerank. Phục vụ trực tiếp NFR-04 (recall@5 ≥ 0,85) và NFR-05 (trích dẫn đúng ≥ 95%).
- Ít context tạp → câu trả lời 5 câu (KT-05) khả thi, trích dẫn `[n]` ánh xạ sạch hơn.
- Miễn phí token, chạy offline hoàn toàn — không phụ thuộc API ngoài.

## Nhược điểm & đánh đổi

- **+200ms độ trễ** trên đường nóng của RAG — phải tính vào ngân sách NFR-02 (first token p95 < 2,5s). Trên CPU yếu hoặc khi tải cao, con số này phình.
- **Throughput CPU là nút cổ chai tiềm ẩn**: 100 truy vấn đồng thời (NFR-06) × 20 cặp cần chấm điểm là tải đáng kể cho CPU — cần đo tải thực tế, có thể phải giới hạn số worker rerank hoặc hàng đợi.
- **Chi phí vận hành mới**: model ~2GB RAM thường trú, thêm dependency (`sentence-transformers`, torch) vào server vốn được thiết kế tối giản. Load model phải làm **một lần lúc khởi động** — load theo request là chết độ trễ.
- Giữ đúng 4 chunk là con dao hai lưỡi: câu trả lời trải trên nhiều đoạn tài liệu (ví dụ quy chế nằm rải ở 3 điều khác nhau) có thể mất mảnh — số 4 nên là tham số cấu hình, kiểm chứng bằng bộ eval.

## Bẫy khi triển khai

- Rerank **sau** RRF, **trước** khi dựng prompt — đừng rerank 40 ứng viên thô (gấp đôi thời gian CPU vô ích), cũng đừng rerank sau khi đã cắt còn 4.
- Điểm số của bge-reranker là logit, không phải xác suất — chỉ dùng để **xếp hạng**, muốn có ngưỡng loại tuyệt đối thì phải calibrate riêng.
- Chạy trong FastAPI async: đẩy việc chấm điểm vào thread pool (`run_in_executor`), không block event loop đang phục vụ SSE.

## Móc xích

- **Nhận ứng viên từ** hybrid retrieval + RRF (hạ tầng của KT-08).
- **Là điều kiện khả thi của** KT-05: không có rerank thì trần 5 câu/4 chunk không giữ được recall.
- **Chỉ chạy trên phần lưu lượng còn lại** sau KT-02 (cache) và KT-01 (metadata) — kiến trúc phân tầng làm chi phí CPU của rerank chỉ áp lên ≤30% truy vấn.

## Đánh giá

**Đáng dùng — kỹ thuật hiếm hoi vừa giảm chi phí vừa tăng chất lượng.** Cái giá phải trả không nằm ở token mà ở vận hành (RAM, CPU, dependency, độ trễ) — đúng tinh thần "đổi tài nguyên rẻ (CPU sẵn có) lấy tài nguyên đắt (token)". Hai việc bắt buộc trước khi tin nó: benchmark throughput rerank trên chính máy chủ triển khai với tải 100 concurrent, và chạy bộ eval so recall@5 giữa "top-4 sau rerank" với "top-15 không rerank".
