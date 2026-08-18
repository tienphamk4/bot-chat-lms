-- 008 — Tham số sinh trả lời cũng phải cấu hình được, không viết cứng trong mã.
--
-- Ba tham số quyết định câu trả lời dài bao nhiêu, tự nhiên tới đâu và có suy
-- luận trước khi trả lời hay không. Trước bản này chúng nằm trong config.py và
-- vertex.py, muốn đổi phải sửa mã rồi triển khai lại — trong khi chúng là thứ
-- người vận hành cần chỉnh theo từng bot.
--
-- NULL = dùng mặc định của hệ thống. Nhờ vậy bot cũ không đổi hành vi.

ALTER TABLE assistants
  ADD COLUMN gen_temperature     DECIMAL(3,2) NULL AFTER default_doc_type,
  ADD COLUMN gen_max_tokens      INT          NULL AFTER gen_temperature,
  ADD COLUMN gen_thinking_budget INT NOT NULL DEFAULT 0 AFTER gen_max_tokens;

-- Hai bot hiện có giữ nguyên hành vi: chưa suy luận, dùng mặc định hệ thống.
UPDATE assistants SET gen_thinking_budget = 0;
