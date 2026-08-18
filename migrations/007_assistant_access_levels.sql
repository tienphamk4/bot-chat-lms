-- 007 — Quyền truy cập của trợ lý là DANH SÁCH, không phải một giá trị.
--
-- Một trợ lý thường phục vụ tài liệu ở nhiều mức quyền cùng lúc: trợ lý tuyển
-- sinh đọc tài liệu công khai, trợ lý hỗ trợ sinh viên đọc cả công khai lẫn
-- theo khoa. Ép một giá trị duy nhất là buộc người quản trị hoặc nạp trùng tài
-- liệu, hoặc hạ quyền tài liệu xuống cho vừa cấu hình bot — cả hai đều sai.
--
-- Cột default_access (VARCHAR, thêm ở migration 005) bị thay bằng access_levels
-- kiểu JSON. Không MODIFY tại chỗ được vì giá trị cũ 'public' không phải JSON
-- hợp lệ, nên thêm cột mới → chuyển dữ liệu → bỏ cột cũ.

ALTER TABLE assistants
  ADD COLUMN access_levels JSON NOT NULL DEFAULT (JSON_ARRAY('public')) AFTER default_doc_type;

-- Chuyển giá trị đơn lẻ cũ thành mảng một phần tử.
UPDATE assistants
   SET access_levels = JSON_ARRAY(COALESCE(NULLIF(default_access, ''), 'public'));

ALTER TABLE assistants DROP COLUMN default_access;

-- Hai trợ lý sẵn có: cả hai đều phục vụ tài liệu công khai; trợ lý tài liệu
-- học tập đọc thêm tài liệu giới hạn theo khoa.
UPDATE assistants SET access_levels = JSON_ARRAY('public', 'faculty') WHERE slug = 'tai-lieu';
UPDATE assistants SET access_levels = JSON_ARRAY('public')            WHERE slug = 'tuyen-sinh';
