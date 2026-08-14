-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · mariadb-only-probe-vector.sql
--
-- ############################################################################
-- ##  CHỈ DÀNH CHO MARIADB >= 11.7.                                        ##
-- ##  Server thật của dự án là Cloud SQL for MySQL — ĐỪNG chạy file này    ##
-- ##  ở đó (chắc chắn báo 1064). Dùng: 01b-probe-vector-cloudsql.sql       ##
-- ############################################################################
--
--   mariadb -u root -p edu_docbot < scripts/mariadb-only-probe-vector.sql
--
-- Tách khỏi 00-preflight.sql vì file này CÓ THỂ BÁO LỖI — và lỗi chính là
-- kết quả cần biết. Chạy được trọn vẹn nghĩa là giai đoạn 2 (RAG) không vướng
-- gì về hạ tầng.
--
-- Lỗi mong đợi khi server là MariaDB < 11.7 hoặc là MySQL (mọi phiên bản
-- community — VECTOR index kiểu này là tính năng riêng của MariaDB):
--   ERROR 1064 ... near 'VECTOR(4) NOT NULL'
-- → Giai đoạn 1 vẫn triển khai bình thường (001_init.sql không dùng VECTOR).
--   Trước giai đoạn 2 chọn một trong hai: chuyển sang MariaDB >= 11.7, hoặc
--   dùng fallback trong thiết kế: lưu embedding BLOB + tính cosine bằng NumPy
--   trong RAM (chấp nhận được dưới 200.000 chunk).
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS _probe_vector;

-- Ba ràng buộc của MariaDB được kiểm luôn trong một câu:
--   cột có VECTOR INDEX phải NOT NULL · mỗi bảng một VECTOR INDEX · InnoDB.
CREATE TABLE _probe_vector (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  v  VECTOR(4) NOT NULL,
  VECTOR INDEX vi_probe (v) M=16 DISTANCE=cosine
) ENGINE=InnoDB;

-- Ghi vector dạng bytes float32 little-endian (cách ứng dụng sẽ dùng thật).
INSERT INTO _probe_vector (v) VALUES
  (VEC_FromText('[1,0,0,0]')),
  (VEC_FromText('[0,1,0,0]')),
  (VEC_FromText('[0.7,0.7,0,0]'));

-- Dạng truy vấn BẮT BUỘC để index được dùng: ORDER BY VEC_DISTANCE_COSINE + LIMIT.
-- Không bọc hàm khoảng cách trong biểu thức; muốn ra điểm tương đồng thì tính
-- ở SELECT bọc ngoài như dưới đây.
SELECT id,
       1 - d AS similarity
FROM (
  SELECT id, VEC_DISTANCE_COSINE(v, VEC_FromText('[1,0,0,0]')) AS d
  FROM _probe_vector
  ORDER BY VEC_DISTANCE_COSINE(v, VEC_FromText('[1,0,0,0]'))
  LIMIT 3
) t;

-- Xác nhận HNSW thật sự được dùng: kế hoạch phải nhắc tới vector index,
-- không phải "Using filesort" trên toàn bảng.
EXPLAIN
SELECT id FROM _probe_vector
ORDER BY VEC_DISTANCE_COSINE(v, VEC_FromText('[1,0,0,0]'))
LIMIT 3;

SELECT VEC_ToText(v) AS doc_lai_vector FROM _probe_vector LIMIT 1;

DROP TABLE _probe_vector;

SELECT 'PROBE VECTOR OK — server sẵn sàng cho giai đoạn 2' AS ket_qua;
