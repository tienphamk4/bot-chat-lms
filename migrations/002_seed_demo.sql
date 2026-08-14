-- ---------------------------------------------------------------------------
-- EDU-DOCBOT · 002_seed_demo.sql — dữ liệu test
--
--   MariaDB:  mariadb -u root -p edu_docbot < migrations/002_seed_demo.sql
--   MySQL:    mysql   -u root -p edu_docbot < migrations/002_seed_demo.sql
--
-- CHỈ dùng cho môi trường demo/test. Khoa, môn, người dùng ở đây là giả lập;
-- tài liệu KHÔNG seed — bạn nạp tài liệu thật bằng `python -m ingest.cli scan`.
--
-- Lưu ý cột dẫn xuất: file này chèn thẳng cả cột JSON lẫn cột dẫn xuất cho
-- khớp nhau. Nếu về sau sửa JSON bằng tay, chạy:
--   python -m ingest.cli resync-derived
-- ---------------------------------------------------------------------------

USE edu_docbot;
SET NAMES utf8mb4;

INSERT INTO faculties (code, name, name_norm) VALUES
  ('CNTT', 'Công nghệ thông tin', 'cong nghe thong tin'),
  ('TOAN', 'Toán - Cơ - Tin học', 'toan - co - tin hoc'),
  ('KTQT', 'Kinh tế quốc tế',     'kinh te quoc te')
ON DUPLICATE KEY UPDATE name = VALUES(name), name_norm = VALUES(name_norm);

-- Môn học: aliases là tên gọi tắt sinh viên hay gõ; offerings gồm học kỳ +
-- giảng viên + mã lớp.
INSERT INTO courses
  (faculty_id, code, code_norm, name, name_norm, credits,
   aliases, offerings, aliases_norm, lecturers_norm, semesters, search_norm)
VALUES
  (
    (SELECT id FROM faculties WHERE code = 'TOAN'),
    'MAT1093', 'mat1093', 'Giải tích 1', 'giai tich 1', 4,
    '["GT1", "giải tích một"]',
    '[{"semester":"20261","lecturer":"Nguyễn Văn An","class_code":"CQ01"},
      {"semester":"20252","lecturer":"Trần Thị Bình","class_code":"CQ02"}]',
    '|gt1|giai tich mot|',
    '|nguyen van an|tran thi binh|',
    '|20261|20252|',
    'mat1093 giai tich 1 gt1 giai tich mot'
  ),
  (
    (SELECT id FROM faculties WHERE code = 'TOAN'),
    'MAT1101', 'mat1101', 'Xác suất thống kê', 'xac suat thong ke', 3,
    '["XSTK", "xác suất"]',
    '[{"semester":"20261","lecturer":"Lê Hoàng Cúc","class_code":"CQ01"}]',
    '|xstk|xac suat|',
    '|le hoang cuc|',
    '|20261|',
    'mat1101 xac suat thong ke xstk xac suat'
  ),
  (
    (SELECT id FROM faculties WHERE code = 'CNTT'),
    'INT2204', 'int2204', 'Lập trình hướng đối tượng', 'lap trinh huong doi tuong', 4,
    '["OOP", "LTHĐT"]',
    '[{"semester":"20261","lecturer":"Phạm Minh Đức","class_code":"CQ01"},
      {"semester":"20261","lecturer":"Phạm Minh Đức","class_code":"CQ02"}]',
    '|oop|lthdt|',
    '|pham minh duc|',
    '|20261|',
    'int2204 lap trinh huong doi tuong oop lthdt'
  ),
  (
    (SELECT id FROM faculties WHERE code = 'CNTT'),
    'INT3306', 'int3306', 'Phát triển ứng dụng Web', 'phat trien ung dung web', 3,
    '["web", "lập trình web"]',
    '[{"semester":"20252","lecturer":"Vũ Thu Giang","class_code":"CQ01"}]',
    '|web|lap trinh web|',
    '|vu thu giang|',
    '|20252|',
    'int3306 phat trien ung dung web web lap trinh web'
  )
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- Người dùng test — một người mỗi kịch bản quyền trong ma trận kiểm thử.
INSERT INTO users (external_id, username, full_name, role, faculty_id) VALUES
  ('SV001', 'sv_toan',  'Sinh viên khoa Toán',  'student',  (SELECT id FROM faculties WHERE code = 'TOAN')),
  ('SV002', 'sv_cntt',  'Sinh viên khoa CNTT',  'student',  (SELECT id FROM faculties WHERE code = 'CNTT')),
  ('GV001', 'gv_toan',  'Giảng viên khoa Toán', 'lecturer', (SELECT id FROM faculties WHERE code = 'TOAN')),
  ('AD001', 'admin',    'Quản trị hệ thống',    'admin',    NULL)
ON DUPLICATE KEY UPDATE full_name = VALUES(full_name);

-- sv_toan học GT1 + XSTK; sv_cntt học OOP. Ai học lớp nào mới thấy tài liệu
-- access='course' của lớp đó.
INSERT INTO user_courses (user_id, course_id, semester) VALUES
  ((SELECT id FROM users WHERE username = 'sv_toan'), (SELECT id FROM courses WHERE code = 'MAT1093'), '20261'),
  ((SELECT id FROM users WHERE username = 'sv_toan'), (SELECT id FROM courses WHERE code = 'MAT1101'), '20261'),
  ((SELECT id FROM users WHERE username = 'sv_cntt'), (SELECT id FROM courses WHERE code = 'INT2204'), '20261')
ON DUPLICATE KEY UPDATE semester = VALUES(semester);

SELECT 'seed demo OK' AS ket_qua;
SELECT (SELECT COUNT(*) FROM faculties) AS faculties,
       (SELECT COUNT(*) FROM courses)   AS courses,
       (SELECT COUNT(*) FROM users)     AS users;
