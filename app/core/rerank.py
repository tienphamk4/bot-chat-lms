"""Rerank local bằng bge-reranker-v2-m3 (KT-06) — TÙY CHỌN.

Miễn phí token (chạy CPU local, ~200ms) và là van giảm input: 20 ứng viên sau
RRF chỉ giữ 4 chunk vào prompt (~65% input token nhánh RAG).

Tắt mặc định (RERANK_ENABLED=0): model nặng ~2GB + torch, không bắt máy dev
tải. Bật trên server: `pip install -r requirements-rerank.txt` rồi đặt
RERANK_ENABLED=1. Mọi sự cố (thiếu thư viện, model tải hỏng) đều rơi êm về
thứ tự RRF — rerank là tối ưu, không phải điểm hỏng đơn.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_model = None
_model_failed = False
_lock = threading.Lock()


def _get_model():
    """Nạp CrossEncoder lần đầu được gọi; hỏng một lần thì thôi luôn."""
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model

    with _lock:
        if _model is not None or _model_failed:
            return _model
        try:
            from sentence_transformers import CrossEncoder

            settings = get_settings()
            log.info("Đang nạp reranker %s (lần đầu hơi lâu)...", settings.rerank_model)
            _model = CrossEncoder(settings.rerank_model, max_length=1024)
        except Exception as exc:
            _model_failed = True
            log.warning(
                "Không nạp được reranker (%s) — dùng thứ tự RRF. "
                "Bật lại: pip install -r requirements-rerank.txt", exc,
            )
    return _model


def _context_words(chunk: dict[str, Any]) -> frozenset[str]:
    from app.vn import vn_norm

    context = chunk.get("context_text")
    return frozenset(vn_norm(str(context)).split()) if context else frozenset()


def by_context(
    question: str,
    chunks: list[dict[str, Any]],
    phrases: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Xếp lại theo mức khớp BỐI CẢNH, giữ nguyên thứ hạng RRF khi hòa.

    Vì sao cần: các trang giới thiệu ngành dùng chung y hệt phần đại cương
    ("Triết học Mác - Lê Nin, Kinh tế chính trị..."), nên vector của chúng gần
    như trùng nhau và câu "ngành An toàn thông tin học những môn gì" kéo về
    trang của bốn ngành khác nhau, xếp gần như ngẫu nhiên. Thứ DUY NHẤT phân
    biệt được chúng là dòng bối cảnh — mà một dòng thì quá nhẹ để lay chuyển
    vector của cả chunk. Ở đây nó được cân riêng.

    Mỗi từ được CÂN THEO ĐỘ HIẾM ngay trong tập ứng viên: từ xuất hiện ở hầu
    hết ứng viên thì không phân biệt được gì, nên gần như không tính điểm. Đếm
    đều nhau là hỏng — "ngành Luật kinh tế ra trường làm gì" có "kinh" và "te"
    khớp mọi chương trình kinh tế của trường, dìm mất chữ "luat" vốn là từ duy
    nhất phân biệt; kết quả trả về Kinh tế quốc tế, Kinh tế phát triển. Lấy độ
    hiếm ngay trong tập ứng viên thì không cần quét kho, cũng không cần bảng
    thống kê nào phải bảo trì.

    Đây là bản thay thế miễn phí cho cross-encoder: không token, không tải
    model 2GB, và chạy được ngay cả khi RERANK_ENABLED=0. Bật reranker thật
    thì bước này thành thừa và được bỏ qua.
    """
    import math

    from app.vn import content_terms, vn_norm

    terms = frozenset(w for w in content_terms(question) if len(w) > 1)
    if not terms:
        return chunks

    words = [_context_words(c) for c in chunks]
    total = len(chunks)
    weight = {
        term: math.log(1.0 + total / (1 + sum(term in w for w in words)))
        for term in terms
    }

    # CỤM nguyên vẹn thắng đứt các từ rời. Tiếng Việt ghép từ bằng nhiều âm
    # tiết, nên đếm theo âm tiết lẻ thì "kinh tế số" tan thành "kinh" + "tế" +
    # "số" — mà "số" lại đụng "Tổng số tín chỉ" có mặt ở gần hết kho, thành ra
    # câu hỏi về Kinh tế số trả về Kinh tế học tài chính. Cụm ở đây lấy từ
    # `keywords` của router (đã là cụm khóa do model tách), không phải n-gram
    # cắt máy móc từ câu hỏi.
    contexts = [vn_norm(str(c.get("context_text") or "")) for c in chunks]

    # Cụm cũng phải cân theo độ hiếm của CHÍNH NÓ, không phải tổng độ hiếm các
    # âm tiết. Router hay kèm những cụm vô thưởng vô phạt ("chương trình",
    # "học gì") bên cạnh cụm thật ("Kinh tế số"); cụm chung có mặt ở nửa số
    # ứng viên nên gần như không được cộng gì, còn cụm thật thì cộng đậm.
    key_phrases: list[tuple[str, float]] = []
    for phrase in phrases:
        normalized = vn_norm(str(phrase or ""))
        size = len(normalized.split())  # chỉ để lọc cụm một từ
        if size < 2:
            continue
        found = sum(normalized in ctx for ctx in contexts)
        # CỔNG ĐỘ ĐẶC TRƯNG: cụm có mặt ở hơn một phần năm số ứng viên thì
        # không phân biệt được gì giữa chúng, dù nó dài bao nhiêu từ. Không có
        # cổng này thì "chương trình đào tạo" (4 từ, có mặt khắp nơi) được
        # thưởng đậm hơn "ngành Bảo hiểm" (3 từ, chỉ đúng hai chunk), và câu
        # hỏi về ngành Bảo hiểm trả về chương trình của sáu ngành khác.
        if found and found <= max(1, total // 5):
            key_phrases.append((normalized, size * math.log(1.0 + total / (1 + found))))

    def _score(index: int) -> float:
        total_score = sum(weight[t] for t in terms & words[index])
        for phrase, bonus in key_phrases:
            if phrase in contexts[index]:
                total_score += bonus
        return total_score

    scored = [(_score(rank), rank, c) for rank, c in enumerate(chunks)]
    if len({round(s, 6) for s, _, _ in scored}) <= 1:
        return chunks  # không phân biệt được thì đừng xáo trộn
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [dict(c, context_score=round(score, 6)) for score, _, c in scored]


def rerank(
    question: str,
    chunks: list[dict[str, Any]],
    keep: int,
    phrases: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Giữ `keep` chunk tốt nhất cho câu hỏi. Không bật/không nạp được → RRF."""
    if len(chunks) <= keep:
        return chunks

    if not get_settings().rerank_enabled:
        return by_context(question, chunks, phrases)[:keep]

    model = _get_model()
    if model is None:
        return by_context(question, chunks, phrases)[:keep]

    try:
        pairs = [(question, str(c["content"])) for c in chunks]
        scores = model.predict(pairs)
    except Exception as exc:  # pragma: no cover - phụ thuộc runtime model
        log.warning("Rerank lỗi lúc chấm điểm (%s) — dùng thứ tự RRF", exc)
        return chunks[:keep]

    ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
    return [dict(c, rerank_score=float(s)) for c, s in ranked[:keep]]
