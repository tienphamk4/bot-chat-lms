-- 006 — "Ngoài phạm vi" cũng phải theo từng bot.
--
-- Luật cũ chặn cứng trong mã: hễ câu hỏi có "học phí" là trả lời ngoài phạm
-- vi. Đúng với bot tra cứu tài liệu học tập, nhưng SAI hoàn toàn với bot tư
-- vấn tuyển sinh — học phí là một trong những câu hỏi tuyển sinh phổ biến
-- nhất. Nhân bản bot thứ ba lại phải sửa Python thêm lần nữa.
--
-- Từ đây danh sách chủ đề bị chặn nằm trên từng bản ghi. Những thứ CẢ HỆ
-- THỐNG không làm — giải bài tập hộ, viết tiểu luận hộ, tra điểm cá nhân —
-- vẫn ở trong mã vì đó là chính sách sản phẩm, không phải cấu hình của bot.

ALTER TABLE assistants
  ADD COLUMN blocked_topics JSON NOT NULL DEFAULT (JSON_ARRAY()) AFTER doc_types;

-- Bot tài liệu học tập: học phí, thời khóa biểu, đăng ký học phần đều ngoài việc.
UPDATE assistants
   SET blocked_topics = JSON_ARRAY('hoc phi', 'thoi khoa bieu', 'dang ky hoc phan')
 WHERE slug = 'tai-lieu';

-- Bot tuyển sinh: học phí và chỉ tiêu LÀ việc của nó, không chặn gì thêm.
UPDATE assistants SET blocked_topics = JSON_ARRAY() WHERE slug = 'tuyen-sinh';
