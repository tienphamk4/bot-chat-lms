-- 004 — Trợ lý ảo là DỮ LIỆU, không phải mã.
--
-- Trước bản này, tên bot, câu chào, câu từ chối, phạm vi tài liệu và đầu mối
-- liên hệ đều nằm rải trong Python và HTML. Hệ quả: bot tư vấn học vụ đi mời
-- người hỏi "cho em biết cần tài liệu môn nào để tự ôn" — câu của bot tra cứu
-- tài liệu. Nhân bản thêm bot thứ ba là chép lỗi thêm một lần nữa.
--
-- Từ đây mỗi trợ lý là một dòng trong bảng này. Thêm bot = INSERT một dòng,
-- không sửa mã, không deploy lại. Frontend đọc /api/v1/assistants rồi tự dựng
-- bong bóng; backend đọc cùng bản ghi để chọn câu trả lời và phạm vi truy hồi.

CREATE TABLE IF NOT EXISTS assistants (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  slug          VARCHAR(64)  NOT NULL,
  name          VARCHAR(160) NOT NULL,
  tagline       VARCHAR(255) NOT NULL DEFAULT '',

  -- Tên gọi kho tài liệu của riêng bot này, đi thẳng vào câu trả lời:
  -- "em chưa có thông tin trong {corpus_label}". Bot tuyển sinh nói "tài liệu
  -- tuyển sinh", bot tài liệu nói "tài liệu học tập" — cùng một câu khuôn.
  corpus_label  VARCHAR(120) NOT NULL DEFAULT 'tài liệu hiện có',

  -- Câu mời đi tiếp, dùng chung cho mọi lối rẽ tĩnh (ngoài phạm vi, hỏi lại,
  -- không tìm thấy). Đây là chỗ giữ cho bot không mời sai việc.
  offer         VARCHAR(400) NOT NULL DEFAULT '',

  -- Khoanh vùng truy hồi. Mảng rỗng = toàn kho. Ví dụ bot hỗ trợ sinh viên
  -- chỉ đọc ['quy_che'].
  doc_types     JSON NOT NULL DEFAULT (JSON_ARRAY()),

  -- Ngữ cảnh mặc định gửi kèm mỗi câu hỏi (khớp ChatContext.screen).
  screen        VARCHAR(32) NULL,

  -- {greeting, out_of_scope, clarify, abusive} — thiếu khóa nào thì rơi về
  -- câu mặc định trong app/core/render.py.
  replies       JSON NOT NULL DEFAULT (JSON_OBJECT()),

  -- {hotline, website} riêng từng bot: tuyển sinh chỉ về Phòng Tuyển sinh,
  -- không phải quản trị kho tài liệu.
  contact       JSON NOT NULL DEFAULT (JSON_OBJECT()),

  -- {title, text, chips[]} cho màn hình trống.
  welcome       JSON NOT NULL DEFAULT (JSON_OBJECT()),

  -- {c1, c2, soft, ink, shadow, icon} — nhận diện màu, frontend dùng thẳng.
  brand         JSON NOT NULL DEFAULT (JSON_OBJECT()),

  sort_order    INT NOT NULL DEFAULT 0,
  is_active     TINYINT(1) NOT NULL DEFAULT 1,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id),
  UNIQUE KEY uq_assistants_slug (slug),
  KEY ix_assistants_active (is_active, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- --- Hai trợ lý hiện có ------------------------------------------------------

INSERT INTO assistants
  (slug, name, tagline, corpus_label, offer, doc_types, screen,
   replies, contact, welcome, brand, sort_order)
VALUES
(
  'tai-lieu',
  'Trợ lý tài liệu học tập',
  'Giáo trình · slide · đề thi · quy chế',
  'tài liệu học tập hiện có',
  'Anh/chị cần tài liệu môn nào thì cho em biết, em tìm giúp ngay ạ.',
  JSON_ARRAY(),
  NULL,
  JSON_OBJECT(
    'greeting',
      'Em chào anh/chị. Em là trợ lý tra cứu tài liệu học tập — giáo trình, slide, đề cương, đề thi, bài tập và quy chế đào tạo, anh/chị đều hỏi em được ạ.',
    'out_of_scope',
      'Nội dung này nằm ngoài phạm vi hỗ trợ của em ạ.',
    'clarify',
      'Dạ, anh/chị đang muốn hỏi về khái niệm hay thông tin cụ thể nào ạ? Anh/chị có thể nói rõ hơn để em hỗ trợ mình chính xác nhất nhé.',
    'abusive',
      'Em xin lỗi, em không thể tiếp tục cuộc trò chuyện với những ngôn từ như vậy ạ.'
  ),
  JSON_OBJECT(),
  JSON_OBJECT(
    'title', 'Anh/chị cần tìm tài liệu gì ạ?',
    'text',  'Em tìm giáo trình, slide, đề cương, đề thi theo môn — và trả lời câu hỏi về nội dung bên trong tài liệu, luôn kèm nguồn với số trang ạ.',
    'icon',  '🎓',
    'chips', JSON_ARRAY()
  ),
  JSON_OBJECT('c1', '#6c4de6', 'c2', '#5a3fd0', 'soft', '#efeaff',
              'ink', '#4a32b8', 'shadow', 'rgba(76, 48, 190, .38)', 'icon', 'robot'),
  10
),
(
  'tuyen-sinh',
  'Trợ lý Tư vấn tuyển sinh',
  'Ngành học · điểm chuẩn · xét tuyển · học phí',
  'tài liệu tuyển sinh hiện có',
  'Anh/chị cần em hỗ trợ thông tin gì về các ngành học hay phương thức xét tuyển của trường không ạ?',
  JSON_ARRAY(),
  NULL,
  JSON_OBJECT(
    'greeting',
      'Chào anh/chị, em là Trợ lý Tư vấn tuyển sinh.',
    'out_of_scope',
      'Về vấn đề này, em chưa có thông tin trong tài liệu tuyển sinh của trường ạ.',
    'clarify',
      'Dạ, anh/chị đang quan tâm tới ngành học nào hay phương thức xét tuyển nào ạ? Anh/chị nói rõ hơn giúp em để em tư vấn chính xác nhất nhé.',
    'abusive',
      'Em xin lỗi, em không thể tiếp tục cuộc trò chuyện với những ngôn từ như vậy ạ.'
  ),
  -- Điền hotline/website thật của Phòng Tuyển sinh trước khi chạy thật.
  JSON_OBJECT('hotline', '', 'website', ''),
  JSON_OBJECT(
    'title', 'Anh/chị cần tư vấn tuyển sinh gì ạ?',
    'text',  'Em tra cứu thông tin ngành học, chỉ tiêu, điểm chuẩn các năm, phương thức xét tuyển và học phí — trả lời kèm nguồn để anh/chị đối chiếu ạ.',
    'icon',  '🎓',
    'chips', JSON_ARRAY(
      'Trường có những ngành nào?',
      'Điểm chuẩn năm ngoái bao nhiêu?',
      'Có mấy phương thức xét tuyển?'
    )
  ),
  JSON_OBJECT('c1', '#5b5bd6', 'c2', '#4646b8', 'soft', '#e9e9fb',
              'ink', '#3a3a99', 'shadow', 'rgba(70, 70, 184, .38)', 'icon', 'advisor'),
  20
)
ON DUPLICATE KEY UPDATE
  name = VALUES(name), tagline = VALUES(tagline), corpus_label = VALUES(corpus_label),
  offer = VALUES(offer), doc_types = VALUES(doc_types), screen = VALUES(screen),
  replies = VALUES(replies), contact = VALUES(contact),
  welcome = VALUES(welcome), brand = VALUES(brand), sort_order = VALUES(sort_order);
