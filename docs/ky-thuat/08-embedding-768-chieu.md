# KT-08 · Embedding 768 chiều (Matryoshka)

> Cắt vector `gemini-embedding-001` từ 3072 xuống 768 chiều — giảm 4 lần dung lượng lưu trữ và tăng tốc HNSW, chất lượng giảm không đáng kể nhờ thiết kế Matryoshka.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★☆☆☆ (không giảm phí API, giảm chi phí hạ tầng) | Rất thấp (một tham số + một bước chuẩn hóa) | Thấp (recall giảm nhẹ, đo được) | **Đáng dùng** — quyết định một lần trước khi nạp |

## Vấn đề

Vector 3072 chiều × 4 byte = 12KB mỗi chunk. Với hàng trăm nghìn chunk, đồ thị HNSW phình theo số chiều: chậm hơn khi truy vấn, và dễ tràn `mhnsw_max_cache_size` (256MB) — mà đồ thị không nằm gọn trong cache là độ trễ vector search tăng vọt (rủi ro R-04 của thiết kế).

## Cách áp dụng trong EDU-DOCBOT

- `gemini-embedding-001` huấn luyện theo kỹ thuật **Matryoshka Representation Learning**: thông tin quan trọng dồn về các chiều đầu, nên cắt lấy 768 chiều đầu vẫn giữ gần nguyên chất lượng xếp hạng.
- Khai báo cột `VECTOR(768) NOT NULL` ở cả `document_chunks` lẫn `query_cache`.
- Ghi vector bằng bytes float32 little-endian (`np.asarray(v, dtype=np.float32).tobytes()`) — nhanh ~5 lần so với `VEC_FromText`.
- **Lưu ý bắt buộc:** sau khi cắt chiều phải **chuẩn hóa lại vector** (chia cho norm) — vector gốc chuẩn hóa trên 3072 chiều, cắt xong không còn norm 1; không chuẩn hóa lại thì khoảng cách cosine giữa các vector bị lệch.

## Ưu điểm

- **Giảm 4 lần** dung lượng vector (3KB thay vì 12KB/chunk) → đồ thị HNSW của ~200k chunk nằm gọn trong 256MB cache → truy vấn vector nhanh và ổn định.
- Embedding nhanh hơn ở mọi khâu: ghi, đọc, tính khoảng cách, RRF.
- Backup nhỏ hơn (cột VECTOR nằm trong mysqldump `--hex-blob`).
- Triển khai gần như miễn phí: một tham số `output_dimensionality` + một dòng chuẩn hóa.

## Nhược điểm & đánh đổi

- **Không giảm một đồng phí API nào** — phí embedding tính theo token đầu vào, không theo số chiều đầu ra. Đây là kỹ thuật tối ưu *hạ tầng và độ trễ*, xếp trong nhóm "tối ưu chi phí" chỉ vì nó giữ cho MariaDB (nơi gánh cache + retrieval) chạy nhanh với RAM khiêm tốn.
- Recall giảm nhẹ so với 3072 chiều — thường không đáng kể với Matryoshka, nhưng "thường" không phải "chắc chắn" với corpus tiếng Việt chuyên ngành: cần đo bằng bộ eval (NFR-04: recall@5 ≥ 0,85).
- **Quyết định gần như không thể đảo rẻ**: `VECTOR(n)` là kiểu cố định độ dài — muốn đổi số chiều (hoặc đổi model embedding) là re-embed **toàn bộ** corpus và nạp lại (chính sách nạp lại của thiết kế). Chọn 768 là cam kết dài hạn.
- Vector cache (KT-02) và vector chunk phải **cùng model, cùng số chiều, cùng cách chuẩn hóa** — lệch một trong ba là khoảng cách vô nghĩa một cách âm thầm.

## Bẫy khi triển khai

- Ghi hằng số `EMBED_MODEL` + `EMBED_DIM = 768` vào một chỗ duy nhất trong config; mọi nơi khác import từ đó — đây là loại hằng số mà hai bản sao lệch nhau gây lỗi khó tìm nhất.
- Đừng quên chuẩn hóa lại cả **vector câu hỏi** lúc truy vấn, không chỉ vector chunk lúc nạp — phải cùng một hàm.
- Đọc vector từ DB để kiểm tra phải bọc `VEC_ToText(embedding)`; SELECT trực tiếp trả bytes vô nghĩa.

## Móc xích

- **Hạ tầng của** KT-02 (vector làm khóa cache) và KT-06 (kênh vector của hybrid retrieval).
- **Phục vụ** NFR-02 (độ trễ RAG) qua tốc độ HNSW — gián tiếp nhưng có thật.
- **Khớp với** quy tắc VECTOR MariaDB trong CLAUDE.md (NOT NULL, một index/bảng, DISTANCE=cosine, tạo index sau khi nạp).

## Đánh giá

**Đáng dùng, quyết định một lần rồi thôi.** Lợi ích là thật (4× dung lượng, HNSW nhanh) và chi phí gần bằng 0 — nhưng hãy gọi đúng tên: đây là tối ưu hạ tầng, không phải tối ưu token; kỳ vọng nó giảm hóa đơn Gemini là kỳ vọng sai chỗ. Việc cần làm nghiêm túc duy nhất: chạy bộ eval recall@5 trên chính corpus của trường ở 768 chiều **trước khi** nạp đại trà 5.000 tài liệu, vì sau thời điểm đó chi phí đổi ý là re-embed toàn bộ.
