-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · 003_vector_cloudsql.sql · Giai đoạn 2 — cột embedding
--
--   mysql -u root -p edu_docbot < migrations/003_vector_cloudsql.sql
--   (hoặc: .venv/Scripts/python -m ingest.cli migrate-vector — tự kiểm tra idempotent)
--
-- Cú pháp CLOUD SQL FOR MYSQL (server thật của dự án), KHÔNG phải MariaDB.
-- Yêu cầu flag cloudsql_vector=on (đã bật, đã probe OK).
--
-- Cột để NULL có chủ đích: chunk mới chèn chưa có embedding, bước embed chạy
-- sau (ingest.cli embed / tự động cuối scan) sẽ lấp dần. Truy vấn vector luôn
-- lọc `embedding IS NOT NULL`.
--
-- VECTOR INDEX (ScaNN) KHÔNG tạo ở đây — tạo SAU khi embed xong toàn bộ, bằng:
--   python -m ingest.cli build-vector-index
-- Dưới ~10.000 chunk, truy vấn exact KNN bằng cosine_distance() không cần index
-- vẫn đủ nhanh, nên demo không bắt buộc bước index.
-- ---------------------------------------------------------------------------

USE edu_docbot;

ALTER TABLE document_chunks
  ADD COLUMN embedding VECTOR(768) USING VARBINARY NULL;

SELECT 'migration 003_vector_cloudsql OK' AS ket_qua;
