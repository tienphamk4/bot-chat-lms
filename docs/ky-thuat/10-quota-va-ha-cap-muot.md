# KT-10 · Quota người dùng + trần ngân sách ngày + hạ cấp mượt

> Hai lớp phanh trong MariaDB: quota 20.000 token/sinh viên/ngày và trần ngân sách 1 USD/ngày toàn hệ thống. Chạm trần không dừng phục vụ — hạ cấp mượt về chế độ chỉ tra cứu metadata (miễn phí).

| Tác động chi phí | Độ khó triển khai | Rủi ro chất lượng | Kết luận |
|---|---|---|---|
| ★★★★☆ (biến chi phí từ "không chặn" thành "có cận trên tuyệt đối") | Thấp | Thấp (UX bị khóa RAG khi chạm trần) | **Bắt buộc dùng** — bảo hiểm ngân sách |

## Vấn đề

Mọi kỹ thuật KT-01…09 đều là tối ưu *kỳ vọng* — không cái nào chặn được kịch bản xấu: một sinh viên viết script spam, một vòng lặp lỗi phía client, hay đơn giản là ngày cao điểm gấp 10 dự toán. Không có phanh cứng thì "mục tiêu <15 USD/tháng" chỉ là hy vọng.

## Cách áp dụng trong EDU-DOCBOT

Bảng `token_quota (user_id, usage_date, tokens, cost_usd)` — PRIMARY KEY `(user_id, usage_date)`, thay bộ đếm Redis:

```python
DAILY_BUDGET_USD = 1.00        # trần toàn hệ thống ≈ 30 USD/tháng
USER_TOKEN_LIMIT = 20_000      # trần mỗi sinh viên mỗi ngày

async def guard(db, user_id: int, est_cost: float):
    spend = await day_spend(db)          # cache trong tiến trình, làm mới mỗi 30 giây
    if spend + est_cost > DAILY_BUDGET_USD:
        raise BudgetExceeded(fallback="metadata_only")
    row = await db.fetch_one(
        "SELECT tokens FROM token_quota WHERE user_id=:u AND usage_date=CURDATE()", {"u": user_id})
    if row and row["tokens"] > USER_TOKEN_LIMIT:
        raise QuotaExceeded(fallback="metadata_only")
```

Ghi nhận sau mỗi lượt bằng UPSERT nguyên tử:

```sql
INSERT INTO token_quota (user_id, usage_date, tokens, cost_usd)
VALUES (:u, CURDATE(), :t, :c)
ON DUPLICATE KEY UPDATE
  tokens   = tokens   + VALUES(tokens),
  cost_usd = cost_usd + VALUES(cost_usd);
```

**Hạ cấp mượt:** chạm trần → chỉ khóa nhánh RAG (nhánh tốn token), nhánh metadata + cache vẫn phục vụ bình thường, kèm thông báo rõ ràng cho người dùng.

## Ưu điểm

- **Cận trên tuyệt đối**: dù mọi thứ khác hỏng, hóa đơn ngày không vượt ~1 USD + phần lố của lượt đang bay. Đây là kỹ thuật duy nhất bảo vệ được NFR-03 trong kịch bản xấu nhất.
- **Không dừng dịch vụ**: sinh viên chạm quota vẫn tìm và tải được tài liệu — chức năng cốt lõi còn nguyên, chỉ mất phần hỏi đáp nội dung đến hết ngày.
- Quota theo user chặn lạm dụng cá nhân (20k token ≈ vài chục câu RAG/ngày — dư cho dùng thật, chặt với spam).
- Rẻ và đơn giản: một bảng, một hàm guard, một UPSERT; bộ đếm ngày cache trong tiến trình nên không thêm truy vấn đáng kể trên đường nóng.
- `cost_usd` cộng dồn sẵn cho dashboard admin (FR-07).

## Nhược điểm & đánh đổi

- **Trần "mềm" ở mép**: bộ đếm toàn hệ thống làm mới mỗi 30 giây × 2 uvicorn worker → có thể lố trần một lượng nhỏ (vài lượt RAG) trước khi cả hai worker thấy con số mới. Chấp nhận được ở trần 1 USD; cần biết là nó tồn tại.
- Ước lượng `est_cost` trước khi gọi chỉ là ước lượng — chặn trước bằng ước lượng, đối soát sau bằng `usage_metadata` thật.
- UX: người dùng ngay thẳng chạm quota vào cuối ngày ôn thi sẽ khó chịu — thông báo phải nói rõ *còn dùng được gì* và *khi nào mở lại*, đừng chỉ "hết quota".
- Ngưỡng 20k/1 USD là dự đoán trước vận hành — phải xem lại theo phân phối sử dụng thật sau tháng đầu (log đã có đủ số liệu).

## Bẫy khi triển khai

- Guard đặt **sau** cache và **trước** router: cache hit không nên tính quota (gần như miễn phí), còn router trở đi là tiền thật.
- Cột `tokens` là INT UNSIGNED — cộng dồn cả input + output + thinking (nếu có) từ `usage_metadata`, thống nhất một định nghĩa.
- Nhánh metadata vẫn phải ghi `query_logs` khi hệ đang hạ cấp — không ghi thì dashboard "đẹp bất thường" đúng lúc đang có sự cố.
- Reset theo `usage_date` = CURDATE() của server — thống nhất múi giờ (thiết kế quy ước UTC trong DB, nhưng "ngày" của sinh viên là giờ Việt Nam; chọn một và ghi rõ).

## Móc xích

- **Lưới an toàn cuối cùng** phía sau mọi kỹ thuật khác: KT-01…09 giảm chi phí kỳ vọng, KT-10 chặn đuôi phân phối.
- **Fallback dựa trên** KT-01: chế độ "metadata_only" chỉ tồn tại được vì nhánh metadata 0 token — không có KT-01 thì hạ cấp mượt thành dừng phục vụ.
- **Ăn khớp với** `query_logs` (đo lường) và dashboard admin (`/api/v1/admin/metrics`).

## Đánh giá

**Bắt buộc dùng.** Đây không phải tối ưu chi phí theo nghĩa "giảm hóa đơn kỳ vọng" mà là **quản trị rủi ro**: biến chi phí từ biến ngẫu nhiên không chặn thành biến có cận trên. Chi phí triển khai một buổi, đổi lấy việc mọi kịch bản xấu (spam, bug client, ngày cao điểm) đều có sàn an toàn. Cặp với hạ cấp mượt, nó còn thể hiện đúng triết lý của cả hệ thống: tính năng đắt là tùy chọn, tra cứu cơ bản là cam kết.
