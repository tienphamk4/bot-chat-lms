-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · 01b-probe-vector-cloudsql.sql
-- Probe vector search cho CLOUD SQL FOR MYSQL (server 8.4.x-google).
-- Thay thế 01-probe-vector.sql (file đó chỉ dành cho MariaDB).
--
--   mysql -u root -p edu_docbot < scripts/01b-probe-vector-cloudsql.sql
--
-- Yêu cầu bật vector trên instance TRƯỚC khi chạy (làm một lần, cần restart):
--   gcloud sql instances patch <INSTANCE> \
--     --database-flags=cloudsql_vector=on
--
-- Đọc kết quả:
--   Chạy trọn vẹn, in 'PROBE CLOUDSQL VECTOR OK' → giai đoạn 2 dùng được
--     vector search gốc của Cloud SQL (ScaNN), không cần fallback NumPy.
--   Lỗi ở CREATE TABLE (không nhận VECTOR ... USING VARBINARY) → maintenance
--     version quá cũ, cần cập nhật instance.
--   Lỗi ở CALL mysql.create_vector_index → flag cloudsql_vector chưa bật.
--
-- Khác biệt cú pháp so với MariaDB (ảnh hưởng code giai đoạn 2):
--   VECTOR(n) USING VARBINARY        thay cho  VECTOR(n) NOT NULL
--   string_to_vector('[..]')         thay cho  VEC_FromText('[..]')
--   vector_to_string(v)              thay cho  VEC_ToText(v)
--   cosine_distance(a, b)            thay cho  VEC_DISTANCE_COSINE(a, b)
--   CALL mysql.create_vector_index   thay cho  ALTER TABLE ... ADD VECTOR INDEX
--   WHERE NEAREST(v) TO (..., 'num_neighbors=k')  thay cho  ORDER BY ... LIMIT k
--   Giới hạn: 1 cột vector/bảng (khớp thiết kế), tối đa 16.000 chiều (768 dư sức).
-- ---------------------------------------------------------------------------

-- Tự chọn đúng database — CALL create_vector_index bên dưới tham chiếu
-- 'edu_docbot.' nên bảng probe bắt buộc phải nằm ở đây, không phụ thuộc
-- schema đang active của SQL client.
USE edu_docbot;

DROP TABLE IF EXISTS _probe_vector_csql;

CREATE TABLE _probe_vector_csql (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  v  VECTOR(4) USING VARBINARY
) ENGINE=InnoDB;

INSERT INTO _probe_vector_csql (v) VALUES
  (string_to_vector('[1,0,0,0]')),
  (string_to_vector('[0,1,0,0]')),
  (string_to_vector('[0.7,0.7,0,0]'));

-- KNN chính xác — không cần index, dùng được ngay cả khi chưa bật flag.
SELECT id,
       cosine_distance(v, string_to_vector('[1,0,0,0]')) AS d
FROM _probe_vector_csql
ORDER BY d
LIMIT 3;

-- ANN qua index ScaNN — phần này mới cần cloudsql_vector=on.
-- BRUTE_FORCE cho bảng nhỏ; dữ liệu thật (>= 10k chunk) dùng TREE_SQ.
CALL mysql.create_vector_index('_probe_vi',
  'edu_docbot._probe_vector_csql',
  'v',
  'index_type=BRUTE_FORCE, distance_measure=COSINE');

SELECT id
FROM _probe_vector_csql
WHERE NEAREST(v) TO (string_to_vector('[1,0,0,0]'), 'num_neighbors=2');

SELECT vector_to_string(v) AS doc_lai_vector FROM _probe_vector_csql LIMIT 1;

-- Dọn dẹp. DROP TABLE gỡ luôn index của bảng probe.
DROP TABLE _probe_vector_csql;

SELECT 'PROBE CLOUDSQL VECTOR OK — giai đoạn 2 dùng được ScaNN' AS ket_qua;
