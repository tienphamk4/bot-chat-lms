# KT-07 · Viết lại câu hỏi độc lập (`standalone_question`)

> Router vừa phân loại vừa viết lại câu hỏi thành dạng độc lập ngữ cảnh ngay từ đầu pipeline — mọi bước sau không cần lịch sử hội thoại, chặn đứng kiểu phình input tuyến tính theo số lượt chat.

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★☆ (input O(1) thay vì O(n) theo lượt) | Thấp (gộp vào router, không thêm lời gọi) | Trung bình (viết lại sai → cả pipeline sai) | **Đáng dùng** — thiết kế then chốt |

## Vấn đề

Hội thoại nhiều lượt (FR-04: hiểu "cái đó", "môn vừa nói") theo cách ngây thơ là gửi kèm toàn bộ lịch sử ở mọi bước. Input khi đó phình tuyến tính: lượt 10 của phiên chat gửi gấp nhiều lần lượt 1, và phình ở **mọi** lời gọi phía sau (retrieval, sinh). Đây là một trong hai nguyên nhân chính của phương án đối chứng 870 USD/tháng.

## Cách áp dụng trong EDU-DOCBOT

Router (vốn đã phải chạy để phân loại intent) trả thêm trường `standalone_question` trong cùng một JSON:

```json
{
  "intent": "ask_content",
  "course_hint": "giai tich 1",
  "keywords": ["gioi han", "ham so"],
  "standalone_question": "Định nghĩa giới hạn hàm số trong giáo trình Giải tích 1 là gì?"
}
```

- Router là nơi **duy nhất** nhìn thấy lịch sử chat (và chỉ lịch sử ngắn).
- Câu hỏi viết lại được dùng cho: embedding tìm kiếm, khóa semantic cache, prompt sinh câu trả lời.
- Prompt router: *"Nếu câu hỏi tham chiếu lượt trước ('môn đó', 'cái vừa nói'), hãy viết lại đầy đủ vào standalone_question."*

## Ưu điểm

- **Input các bước sau là O(1)** theo số lượt: bước sinh RAG luôn nhận đúng 1 câu hỏi + 4 chunk, dù là lượt thứ 2 hay thứ 50.
- **Không tốn thêm lời gọi nào**: viết lại "đi nhờ" lời gọi router có sẵn — chi phí biên gần bằng 0 (thêm ~30–50 token output của router).
- **Mở khóa semantic cache (KT-02)**: "công thức đó chương mấy?" không cache được, nhưng câu viết lại "Công thức tích phân từng phần nằm ở chương mấy trong giáo trình Giải tích 1?" cache dùng chung được giữa mọi sinh viên.
- Retrieval chính xác hơn: embedding một câu hỏi đầy đủ tốt hơn embedding một câu mơ hồ kèm đống lịch sử.
- Kiến trúc sạch: các tầng sau stateless, dễ test từng tầng độc lập.

## Nhược điểm & đánh đổi

- **Điểm hỏng đơn (single point of failure) về ngữ nghĩa**: router viết lại sai ("môn đó" → nhầm môn) thì retrieval, cache, câu trả lời đều sai theo — và lỗi này **khó thấy** vì mỗi tầng sau đều "chạy đúng" với đầu vào sai. Debug phải lần ngược về JSON của router.
- Phụ thuộc năng lực Flash-Lite (model rẻ nhất) với tiếng Việt trong khi thinking đã tắt (KT-04) — các hội thoại tham chiếu chéo phức tạp là ca yếu nhất.
- Mất sắc thái hội thoại: bước sinh không biết người dùng đã hỏi gì trước đó, không thể "như mình vừa nói ở trên" — chấp nhận được với chatbot tra cứu, không ổn nếu sau này muốn chatbot dạy học.
- Lịch sử "ngắn" gửi cho router vẫn cần định nghĩa cụ thể (bao nhiêu lượt? chỉ user hay cả bot?) — quá ngắn thì thiếu ngữ cảnh để viết lại, quá dài thì lại phình chính lời gọi router.

## Bẫy khi triển khai

- Lượt đầu tiên của phiên (không có lịch sử): `standalone_question` phải bằng chính câu hỏi gốc — kiểm tra router không "sáng tác" thêm.
- Ghi `standalone_question` vào `query_logs` cùng câu hỏi gốc — không có nó thì không thể chẩn đoán lỗi viết lại.
- Khóa cache (KT-02) phải là **câu viết lại**, không phải câu gốc — dùng câu gốc thì hội thoại nhiều lượt phá hit rate.

## Móc xích

- **Mắt xích trung tâm của pipeline**: nuôi KT-01 (các trường metadata), KT-02 (khóa cache dùng chung), KT-06 (câu hỏi cho retrieval + rerank), KT-05 (input sinh gọn).
- **Chịu ràng buộc của** KT-04 (router không được suy nghĩ) — hai kỹ thuật này giằng nhau ở các ca hội thoại khó.
- **Được giám sát bởi** bộ eval: nhóm 20 câu hội thoại nhiều lượt đo trực tiếp "câu viết lại có chính xác không".

## Đánh giá

**Đáng dùng — đây là quyết định kiến trúc quan trọng nhất của pipeline truy vấn**, vì nó vừa chặn phình chi phí vừa là điều kiện để cache và retrieval hoạt động tốt. Đổi lại, toàn hệ thống đặt cược chất lượng vào một lời gọi Flash-Lite duy nhất: phải có logging đầy đủ (câu gốc + câu viết lại) và bộ eval multi-turn chạy sau mỗi lần sửa prompt router. Nếu tỷ lệ viết lại sai vượt ngưỡng chấp nhận, phương án chữa là nâng model cho riêng router — vẫn rẻ hơn nhiều so với gửi lịch sử xuống mọi tầng.
