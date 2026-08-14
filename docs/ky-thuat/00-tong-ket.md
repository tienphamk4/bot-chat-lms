# Tổng kết 10 kỹ thuật tối ưu chi phí — móc xích & đánh giá

> File tổng hợp cho bộ tài liệu `docs/ky-thuat/`. Mỗi kỹ thuật có file riêng (bảng dưới); file này trả lời ba câu hỏi: chúng **nối với nhau thế nào**, cái nào **đáng dùng đến đâu**, và **ưu/nhược tổng thể** của cả chiến lược.

## 1. Bảng tổng quan

| # | Kỹ thuật | Tác động chi phí | Độ khó | Rủi ro chất lượng | Kết luận |
|---|---|---|---|---|---|
| [KT-01](01-chan-truoc-llm-bang-sql.md) | Chặn trước LLM bằng SQL | ★★★★★ | Trung bình | Thấp | **Bắt buộc** |
| [KT-02](02-semantic-cache-hai-tang.md) | Semantic cache 2 tầng | ★★★★☆ | Trung bình | **Trung bình–cao** | Đáng dùng, kèm điều kiện |
| [KT-03](03-context-caching.md) | Context caching | ★★☆☆☆ | Thấp–TB | Không | **Đo trước khi làm** |
| [KT-04](04-tat-thinking.md) | Tắt thinking | ★★★★★ | Rất thấp | Thấp–TB | **Bắt buộc** |
| [KT-05](05-gioi-han-output.md) | Giới hạn output 400/5 câu | ★★★★☆ | Rất thấp | Trung bình | Đáng dùng |
| [KT-06](06-rerank-local.md) | Rerank local 15→4 | ★★★★☆ | TB–cao | Thấp (thường *tăng*) | Đáng dùng |
| [KT-07](07-viet-lai-cau-hoi-doc-lap.md) | Câu hỏi độc lập | ★★★★☆ | Thấp | Trung bình | Đáng dùng — then chốt |
| [KT-08](08-embedding-768-chieu.md) | Embedding 768 chiều | ★★☆☆☆ | Rất thấp | Thấp | Đáng dùng — quyết một lần |
| [KT-09](09-batch-api.md) | Batch API −50% | ★★☆☆☆ | Trung bình | Không | Đáng dùng — làm sau |
| [KT-10](10-quota-va-ha-cap-muot.md) | Quota + hạ cấp mượt | ★★★★☆ | Thấp | Thấp | **Bắt buộc** |

Kết quả cộng gộp theo thiết kế: **~7,3 USD/tháng** cho 50.000 truy vấn, so với ~870 USD của phương án không tối ưu — chênh >100 lần, và toàn bộ nằm ở kiến trúc chứ không phải ở model.

## 2. Móc xích — vị trí từng kỹ thuật trên luồng truy vấn

```
Câu hỏi người dùng
   │
   ▼
┌ KT-02 · Semantic cache (hash → vector) ┐   hit ≥35% → dừng, ~25 token
   │ miss                                     (khóa cache = câu viết lại, nhờ KT-07)
   ▼
┌ KT-10 · Guard quota/ngân sách ┐             chạm trần → chỉ còn nhánh KT-01
   │ ok
   ▼
┌ Router · Flash-Lite ┐
   │  KT-04 tắt thinking · KT-03 cache system prompt
   │  KT-07 sinh standalone_question + các trường metadata
   ├───────────────► out_of_scope / greeting → câu tĩnh, 0 token
   ▼
┌ KT-01 · Nhánh metadata (SQL + template) ┐   ≥45% lưu lượng → 0 token sinh
   │ 0 kết quả (sau khi thử FULLTEXT)
   ▼
┌ Nhánh RAG (≤30% lưu lượng) ┐
   │  hybrid retrieval (vector KT-08 + FULLTEXT, trong allowed_doc_ids)
   │  → RRF k=60 → KT-06 rerank 20→4 chunk
   │  → sinh: KT-04 thinking=0 · KT-05 trần 400/5 câu · KT-03 cache prompt
   ▼
Hậu xử lý: ánh xạ [n]→link, ghi query_cache (nuôi lại KT-02),
           query_logs, cộng token_quota (nuôi lại KT-10)

Ngoài luồng online: KT-09 Batch API phục vụ pipeline nạp (embedding, metadata) lúc 2h sáng.
```

Ba quan hệ móc xích quan trọng nhất — đứt một mắt là các mắt sau yếu theo:

1. **KT-07 là mắt xích trung tâm.** Câu hỏi viết lại độc lập là *đầu vào* của cache (KT-02 chỉ hit chéo user khi khóa cache độc lập ngữ cảnh), của SQL metadata (KT-01 cần các trường trích đúng), và của retrieval (KT-06). Router hỏng → cả chuỗi hỏng, dù từng khâu vẫn "chạy đúng".
2. **KT-01 + KT-02 quyết định mẫu số.** Hai kỹ thuật chặn đầu này rút lưu lượng vào RAG xuống ≤30% — mọi kỹ thuật nhánh RAG (KT-04/05/06) chỉ tối ưu trên phần lưu lượng còn lại đó. Phân bố route lệch (metadata <45%) thì cả bảng dự toán chi phí phải tính lại.
3. **KT-04 + KT-05 là một cặp, KT-10 là lưới cuối.** Trần output chỉ kín khi chặn cả phần chìm (thinking) lẫn phần nổi (answer); và dù mọi thứ trên thất bại, KT-10 bảo đảm hóa đơn ngày có cận trên — với điều kiện KT-01 tồn tại để "hạ cấp mượt" có chỗ hạ.

Nghịch lý đáng chú ý: **KT-02 càng tốt thì KT-03 càng vô dụng** (cache ngữ nghĩa hit nhiều → ít lời gọi LLM → phần tiết kiệm của context caching co lại). Đây là lý do KT-03 xếp ưu tiên cuối.

## 3. Đánh giá: có đáng dùng không — ưu và nhược

### Chia ba nhóm theo mức cam kết

**Nhóm bắt buộc (làm ngay, không cần đo trước):** KT-01, KT-04, KT-10.
Điểm chung: chi phí triển khai thấp, tiết kiệm lớn hoặc chặn rủi ro lớn, và gần như không đánh đổi chất lượng. KT-04 là một dòng config; KT-10 là một bảng + một hàm guard; KT-01 tốn công nhất nhóm nhưng đồng thời *tăng* chất lượng (kết quả chính xác, có link). Không có kịch bản nào mà bỏ ba kỹ thuật này là quyết định đúng.

**Nhóm đáng dùng có điều kiện (làm theo lộ trình, đo bằng eval):** KT-05, KT-06, KT-07, KT-08.
Điểm chung: tiết kiệm thật nhưng mỗi cái cắm một "van chất lượng" cần giám sát — KT-05 có nguy cơ cắt cụt (theo dõi `finish_reason`), KT-06 cần benchmark throughput CPU và recall@5, KT-07 cần bộ eval 20 câu multi-turn, KT-08 phải chốt số chiều trước khi nạp đại trà. Cả bốn đều đáng dùng *nếu* bộ eval 200 câu tồn tại để làm phanh; không có eval thì đây là bốn chỗ chất lượng âm thầm rơi.

**Nhóm đo trước khi làm (ROI không chắc chắn ở quy mô này):** KT-02, KT-03, KT-09.
KT-02 có rủi ro chất lượng cao nhất bộ (trả nhầm, trả cũ, lọt quyền) — đáng dùng vì bối cảnh mùa vụ lý tưởng, nhưng bật tầng hash trước, tầng vector sau khi tinh chỉnh ngưỡng. KT-09 tiết kiệm tuyệt đối chỉ vài USD — làm ở giai đoạn 3, đừng phức tạp hóa pipeline sớm. KT-03 là kỹ thuật duy nhất **có thể lỗ** (phí lưu theo giờ + ngưỡng token tối thiểu có thể không đạt với prompt 200–250 token) — chỉ bật khi dashboard chứng minh có lãi; trong lúc chờ, hưởng implicit caching miễn phí bằng cách xếp phần bất biến lên đầu prompt.

### Ưu điểm của cả chiến lược 10 kỹ thuật

- **Tối ưu bằng kiến trúc, không bằng hy sinh model**: phần lớn khoản tiết kiệm đến từ *không gọi LLM* (KT-01/02) hoặc *gọi gọn hơn* (KT-05/06/07), không phải từ việc dùng model tệ hơn cho tác vụ cần model tốt (đã có đường escalate).
- **Nhiều lớp độc lập**: hỏng một kỹ thuật không sập chi phí — cache tắt vẫn còn metadata-first, mọi thứ tắt vẫn còn quota chặn đuôi.
- **Đo được**: `query_logs` + `token_quota` + dashboard làm mọi giả định (route ≥45% metadata, hit ≥35%, escalate <8%) thành con số kiểm chứng được hằng ngày.
- Hầu hết kỹ thuật **rẻ để thử, rẻ để bỏ** — trừ KT-08 (đổi ý = re-embed toàn bộ), không có cam kết một chiều nào.

### Nhược điểm & rủi ro của cả chiến lược

- **Đặt cược nặng vào một lời gọi Flash-Lite rẻ nhất** (router, đã tắt thinking): trích trường sai, viết lại sai là toàn chuỗi sai — mà từng khâu sau vẫn "xanh". Cần logging câu gốc + câu viết lại và eval multi-turn ngay từ giai đoạn 1.
- **Chất lượng có thể chết bởi nghìn vết cắt**: 10 kỹ thuật, mỗi cái ăn 1–2% chất lượng thì cộng lại là đáng kể. Phanh duy nhất là bộ eval 200 câu chạy sau mỗi thay đổi prompt/tham số — bộ eval không phải việc phụ, nó là điều kiện để dám dùng cả 10 kỹ thuật.
- **Nhiều bộ phận chuyển động cho một mục tiêu 15 USD**: cache 2 tầng + reranker local + batch job + quota là bốn hệ con phải vận hành đúng. Ở quy mô nhỏ hơn dự kiến (ví dụ 5.000 truy vấn/tháng), một nửa số kỹ thuật không bõ công — thứ tự triển khai bên dưới chính là cách phòng ngừa over-engineering.
- Các con số then chốt (45% metadata, 35% hit, 3% escalate, ngưỡng 0.05, quota 20k) đều là **giả định trước vận hành** — tháng đầu tiên phải coi là giai đoạn hiệu chỉnh, không phải nghiệm thu.

## 4. Thứ tự triển khai khuyến nghị (khớp lộ trình 4 giai đoạn)

| Giai đoạn | Kỹ thuật | Lý do xếp ở đây |
|---|---|---|
| 1 (tuần 1–3) | **KT-01**, **KT-04**, KT-07 (dạng tối thiểu), KT-08 (chốt số chiều) | Nền tảng: metadata-first cần có trước RAG; số chiều embedding phải chốt trước khi nạp; thinking=0 từ dòng code LLM đầu tiên |
| 2 (tuần 4–6) | **KT-06**, **KT-05**, KT-07 (hoàn chỉnh + eval multi-turn) | Nhánh RAG dựng đến đâu gắn van chi phí đến đó |
| 3 (tuần 7–8) | **KT-02** (hash trước, vector sau), **KT-10**, KT-09, KT-03 (chỉ nếu số liệu ủng hộ) | Đúng mục tiêu giai đoạn: "chi phí giảm về mức NFR-03"; lúc này đã có log thật để tinh chỉnh ngưỡng |
| 4 (tuần 9–10) | Hiệu chỉnh toàn bộ bằng bộ eval 200 câu + kiểm thử tải (throughput rerank, race quota) | Biến giả định thành số đo trước khi nghiệm thu |

## 5. Kết luận

Bộ 10 kỹ thuật **đáng dùng như một hệ thống, không phải như một danh sách chọn món** — sức mạnh nằm ở cách chúng móc vào nhau (cache chặn trước router, router nuôi SQL và cache, rerank mở đường cho trần output, quota đỡ tất cả). 3 kỹ thuật là bắt buộc vô điều kiện (KT-01, KT-04, KT-10), 4 kỹ thuật đáng dùng dưới sự giám sát của bộ eval (KT-05/06/07/08), 3 kỹ thuật cần số liệu vận hành thật trước khi cam kết (KT-02/09/03 — riêng KT-03 có khả năng lỗ). Điều kiện tiên quyết của cả chiến lược không phải là kỹ thuật nào trong 10 cái, mà là **hạ tầng đo lường** (`query_logs`, dashboard, bộ eval 200 câu): không đo được thì mọi "tối ưu" chỉ là niềm tin.
