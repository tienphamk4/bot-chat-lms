# Kịch bản demo — EDU-DOCBOT

> **Cảnh báo (18/08/2026): tài liệu này mô tả giao diện CŨ — một trang chat duy
> nhất cho một trợ lý.** Hệ thống hiện là nền tảng nhiều trợ lý: chat nằm trong
> bong bóng nổi ở góc phải, mỗi trợ lý một popup, và quản trị gom về một trang
> có sidebar. Các kịch bản hỏi–đáp bên dưới vẫn đúng về nội dung, nhưng đường đi
> thao tác thì không. Xem [huong-dan-su-dung.md](huong-dan-su-dung.md) để biết
> luồng hiện tại.

> Điều kiện: server chạy (`readyz` ok), đã bấm "Nạp tài liệu" (3 file mẫu indexed).
> Mở `http://localhost:8080/`. Mỗi kịch bản ghi rõ: chọn user nào, gõ gì, kỳ vọng gì.
> Dòng chữ nhỏ trên mỗi câu trả lời (route · mã môn · ms) chính là bằng chứng
> kiến trúc đang chạy — hãy chỉ vào nó khi thuyết trình.

## KB1 — Tra cứu tài liệu (nhánh metadata, 0 token sinh)

User: `sv_toan`.

| Gõ | Kỳ vọng | Điểm nhấn |
|---|---|---|
| `giáo trình giải tích 1 có không` | Liệt kê "Giáo trình Giải tích 1 (bản demo)" + nút tải [1] | Chip `metadata · MAT1093` — SQL thuần, **0 token** |
| `tài liệu GT1` | Cùng kết quả trên | Nhận diện **tên gọi tắt** sinh viên hay dùng |
| `MAT1093` | Cùng kết quả | Mã môn trần cũng hiểu |
| `INT2204 có đề cương không` | Đề cương LTHĐT | Lọc đúng loại `de_cuong` |
| `môn giải tích 1 có những tài liệu gì` | Danh sách tài liệu của môn | Câu hỏi kiểu liệt kê |

## KB2 — Hỏi nội dung bên trong tài liệu (RAG — giai đoạn 2 đã chạy)

User: `sv_toan`. Chip route hiện **`rag`**.

| Gõ | Kỳ vọng | Điểm nhấn |
|---|---|---|
| `quy chế thi lại quy định thế nào` | Văn xuôi ≤5 câu tổng hợp từ Điều 12+13, mỗi ý gắn [n], nguồn kèm heading + trang | **Sinh trả lời + bắt buộc trích dẫn** — yêu cầu cốt lõi của đề bài |
| `chương 2 giáo trình giải tích nói gì về đạo hàm` | Tóm tắt chương 2 kèm [1] trỏ đúng "Chương 2. Đạo hàm và vi phân" | Hybrid: FULLTEXT + vector → RRF → sinh |
| `đạo hàm là gì` | Định nghĩa từ giáo trình GT1, có nguồn | Hỏi khái niệm — vector search bắt được cả khi từ ngữ không trùng khớp |

Điểm kỹ thuật nên nói: câu trả lời **chỉ được phép dùng trích đoạn** (temperature
0.2, trần 400 token, thinking=0); dấu [n] nào không ánh xạ được về chunk thật thì
**cả câu bị cắt** trước khi trả về — đó là cách giữ NFR "trích dẫn đúng ≥95%".
Nội dung chunk được bọc delimiter và tuyên bố là dữ liệu, không phải chỉ thị —
chống prompt injection từ tài liệu.

## KB3 — Chặn ngoài phạm vi & chào hỏi (0 token)

User nào cũng được.

| Gõ | Kỳ vọng |
|---|---|
| `chào bạn` | Câu chào + giới thiệu chức năng — chip `static` |
| `giải giúp mình bài 3 trang 20` | Từ chối: không giải bài hộ |
| `elon musk là ai` | Từ chối: ngoài phạm vi học liệu |
| `học phí kỳ này bao nhiêu` | Từ chối + nói rõ không tra được điểm/học phí/TKB |

## KB4 — Kiến trúc tiết kiệm chi phí (demo cho người kỹ thuật)

Nhìn **console uvicorn** song song với chat:

1. `đề thi XSTK` → log `Router [rule] ... (LLM không cần gọi)` — câu rõ ràng
   không tốn token nào.
2. `môn nào dạy về đạo hàm và tích phân` → log `Router [llm] model=... tokens=.../.. — GEMINI ĐÃ ĐƯỢC GỌI`
   — câu mơ hồ mới cần LLM, và LLM chỉ **phân loại** (JSON ~200 token), không sinh câu trả lời.
3. Gõ lại y hệt câu 2 → chip `cache` — lượt sau miễn phí.
4. User `admin` → mở `http://localhost:8080/docs` → `GET /api/v1/admin/metrics`:
   chi phí từng ngày bằng số, phân bố route, tỷ lệ cache hit. Đây là hạ tầng đo
   NFR-03 (< 15 USD / 50k truy vấn / tháng).

## KB5 — Phân quyền (NFR rò rỉ = 0)

Chuẩn bị trước 1 phút: cần một tài liệu giới hạn quyền. Thêm vào cuối
`files/manifest.csv` (tạo file nếu chưa có, giữ nguyên dòng header):

```csv
file_path,title,course_code,doc_type,semester,lecturer,access
noi-bo/de-thi-nhap-gt1.md,Đề thi nháp nội bộ GT1 (demo),MAT1093,de_thi,20261,,faculty
```

tạo file `files/noi-bo/de-thi-nhap-gt1.md` nội dung bất kỳ, bấm "Nạp tài liệu" (user admin).

| User | Gõ `đề thi giải tích 1` | Vì sao |
|---|---|---|
| `sv_toan` (khoa Toán) | **Thấy** đề thi nháp | Cùng khoa với tài liệu `access=faculty` |
| `sv_cntt` (khoa CNTT) | **Không thấy** — như thể không tồn tại | Khác khoa. Lọc TRƯỚC truy vấn, không phải giấu sau |
| Khách (bỏ chọn user) | Chỉ thấy tài liệu public | Ẩn danh = quyền tối thiểu |

Thử tải trực tiếp: lấy link tải của tài liệu faculty (từ phiên sv_toan), mở ở
tab ẩn danh không header → **404**, không phải 403 — không để lộ cả sự tồn tại.

## KB6 — Quản trị & vận hành

User: `admin`.

1. Nút **"Nạp tài liệu"** trên header — quét `files/`, báo kết quả từng file ngay trong chat.
2. Thả một file tên vu vơ (vd `tailieu123.pdf`) vào `files/` → nạp → thành
   `pending_review`, **không hỏng cả mẻ** — bổ sung metadata vào `manifest.csv`
   rồi reindex là xong.
3. `http://localhost:8080/docs` — Swagger đầy đủ API cho tích hợp sau này.

## KB7 — Chịu lỗi (nếu người xem muốn "vặn")

- Tắt mạng tới Gemini (hoặc `USE_LLM_ROUTER=0` + restart): câu mơ hồ vẫn được
  trả lời bằng tầng luật — **LLM chết không kéo hệ thống chết**.
- Hạ `QUOTA_DAILY_TOKENS=100` trong `.env` + restart, hỏi vài câu mơ hồ: chạm
  trần → chip thêm `chế độ tiết kiệm`, hệ thống chỉ tắt LLM chứ không dừng phục
  vụ (KT-10 — hạ cấp mượt).

## Chưa demo được (đừng hứa trước người xem)

| Chưa có | Thuộc | Ghi chú khi bị hỏi |
|---|---|---|
| Câu hỏi nối tiếp nhiều lượt ("thế còn chương 3?") | Sắp | `standalone_question` đã thiết kế sẵn; chưa nối lịch sử phiên |
| File scan (ảnh chụp) | GĐ sau | Được đánh dấu `needs_ocr`, không nạp sai |
| Semantic cache tầng vector, Batch API, escalate model | GĐ3 | Đã có chỗ cắm trong schema |
| Rerank local (bge-reranker-v2-m3) | Tùy chọn, đã có code | Bật bằng RERANK_ENABLED=1 sau khi cài requirements-rerank.txt; hiện dùng thứ tự RRF |

~~Câu trả lời văn xuôi + tìm theo ngữ nghĩa~~ — **đã có từ 13/08** (GĐ2 RAG chạy trên Cloud SQL vector).
