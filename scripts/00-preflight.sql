-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · 00-preflight.sql
-- Chạy TRƯỚC MỌI THỨ KHÁC, kể cả trước 001_init.sql.
--
--   MariaDB:  mariadb -u root -p < scripts/00-preflight.sql
--   MySQL:    mysql   -u root -p < scripts/00-preflight.sql
--
-- File này chỉ ĐỌC, không sửa gì. Nhiệm vụ: xác nhận server đủ điều kiện
-- trước khi nạp dữ liệu, vì hai trong số các cấu hình bên dưới KHÔNG thể
-- sửa sau khi đã nạp mà không phải dựng lại index.
-- ---------------------------------------------------------------------------

SELECT '=== 1. Phiên bản ===' AS buoc;

-- Cần >= 11.7 cho kiểu VECTOR (giai đoạn 2). Giai đoạn 1 chạy được từ 10.6.
-- < 11.7 không chặn demo giai đoạn 1, nhưng phải lên kế hoạch nâng cấp ngay.
SELECT VERSION() AS mariadb_version;

SELECT '=== 2. Bộ ký tự (phải là utf8mb4 / utf8mb4_unicode_ci) ===' AS buoc;

SELECT @@character_set_server  AS character_set_server,
       @@collation_server      AS collation_server;

SELECT '=== 3. Full-text tiếng Việt ===' AS buoc;

-- innodb_ft_min_token_size PHẢI = 1: âm tiết tiếng Việt dài 1-2 ký tự
--   ("lý", "kỳ", "đề"). Để mặc định 3 là mất sạch các âm tiết này khỏi index.
-- innodb_ft_enable_stopword PHẢI = OFF.
--
-- ĐỔI HAI BIẾN NÀY SAU KHI ĐÃ NẠP = phải restart MariaDB VÀ dựng lại TOÀN BỘ
-- FULLTEXT index. Sửa bây giờ thì miễn phí.
SHOW VARIABLES LIKE 'innodb_ft_min_token_size';
SHOW VARIABLES LIKE 'innodb_ft_enable_stopword';
SHOW VARIABLES LIKE 'innodb_ft_server_stopword_table';

SELECT '=== 4. Tham số HNSW (chỉ cần cho giai đoạn 2) ===' AS buoc;

-- Không có kết quả nào = MariaDB < 11.7 HOẶC đây là MySQL (không phải MariaDB)
-- — cả hai trường hợp đều chưa có vector search gốc cho giai đoạn 2.
SHOW VARIABLES LIKE 'mhnsw_max_cache_size';
SHOW VARIABLES LIKE 'mhnsw_ef_search';

SELECT '=== 5. Múi giờ (ứng dụng tự ép +00:00 mỗi kết nối) ===' AS buoc;

SELECT @@global.time_zone AS global_tz, @@session.time_zone AS session_tz, UTC_TIMESTAMP() AS utc_now;

SELECT '=== 6. Database edu_docbot đã tồn tại chưa ===' AS buoc;

SELECT schema_name, default_character_set_name, default_collation_name
FROM information_schema.schemata
WHERE schema_name = 'edu_docbot';

SELECT '=== 7. Bảng đã có sẵn trong edu_docbot (rỗng = chưa migrate) ===' AS buoc;

SELECT table_name, engine, table_rows
FROM information_schema.tables
WHERE table_schema = 'edu_docbot'
ORDER BY table_name;

-- ---------------------------------------------------------------------------
-- ĐỌC KẾT QUẢ
--
--   Bước 2 khác utf8mb4          → sửa 50-server.cnf, restart, rồi chạy lại.
--   Bước 3 min_token_size khác 1 → sửa 50-server.cnf, restart, rồi chạy lại.
--                                  BẮT BUỘC sửa trước khi nạp dữ liệu.
--   Bước 1 < 11.7                → giai đoạn 1 vẫn chạy; lên lịch nâng cấp
--                                  trước giai đoạn 2 (RAG cần kiểu VECTOR).
--
-- Cấu hình cần có trong /etc/mysql/mariadb.conf.d/50-server.cnf:
--
--   [mariadb]
--   character-set-server      = utf8mb4
--   collation-server          = utf8mb4_unicode_ci
--   innodb_ft_min_token_size  = 1
--   innodb_ft_enable_stopword = OFF
--   mhnsw_max_cache_size      = 268435456
--   mhnsw_ef_search           = 40
--
-- Sau đó probe vector theo đúng loại server:
--   Cloud SQL for MySQL (server thật): scripts/01b-probe-vector-cloudsql.sql
--   MariaDB:                           scripts/mariadb-only-probe-vector.sql
-- ---------------------------------------------------------------------------
