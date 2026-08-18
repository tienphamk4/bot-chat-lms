-- 009 — Bối cảnh cho từng chunk, và cờ đánh dấu bảng số liệu đã vỡ định dạng.
--
-- Hai lỗi đo được trên kho tuyển sinh thật, cùng nằm ở khâu DỮ LIỆU chứ không
-- phải khâu trả lời:
--
-- 1. Chunk mồ côi chủ đề. Chunk chứa danh sách học phần ngành An toàn thông
--    tin không hề nhắc tên ngành đó (tên ngành nằm ở khung trích dẫn đầu
--    trang, rơi vào chunk khác). Hỏi "ngành An toàn thông tin học những môn
--    gì" thì 4/6 chunk trả về thuộc ngành khác. `context_text` là dòng bối
--    cảnh gắn thêm, đi vào cả content_norm (FULLTEXT) lẫn embedding.
--
-- 2. Bảng bị bẹp thành dòng chữ. "Mã ngành: 7480104 | Chỉ tiêu: 50 | 2 | 0 |
--    2 | 5 | 26,38 | ..." — các con số còn đó nhưng đã mất hàng/cột, không
--    còn suy ra được số nào của ngành nào. Đây là nguồn bịa số nguy hiểm
--    nhất: điểm chuẩn ngành A gán cho ngành B. `is_fragment` để tầng RAG
--    loại chúng ra khỏi ngữ cảnh sinh trả lời.

ALTER TABLE document_chunks
  ADD COLUMN context_text VARCHAR(500) NULL AFTER content_norm,
  ADD COLUMN is_fragment  TINYINT(1) NOT NULL DEFAULT 0 AFTER context_text;

-- Lọc `is_fragment = 0` nằm trong MỌI truy vấn truy hồi nội dung.
CREATE INDEX idx_chunk_fragment ON document_chunks (is_fragment);
