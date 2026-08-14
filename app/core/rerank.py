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


def rerank(
    question: str, chunks: list[dict[str, Any]], keep: int
) -> list[dict[str, Any]]:
    """Giữ `keep` chunk tốt nhất cho câu hỏi. Không bật/không nạp được → RRF."""
    if len(chunks) <= keep:
        return chunks

    if not get_settings().rerank_enabled:
        return chunks[:keep]

    model = _get_model()
    if model is None:
        return chunks[:keep]

    try:
        pairs = [(question, str(c["content"])) for c in chunks]
        scores = model.predict(pairs)
    except Exception as exc:  # pragma: no cover - phụ thuộc runtime model
        log.warning("Rerank lỗi lúc chấm điểm (%s) — dùng thứ tự RRF", exc)
        return chunks[:keep]

    ranked = sorted(zip(chunks, scores), key=lambda pair: pair[1], reverse=True)
    return [dict(c, rerank_score=float(s)) for c, s in ranked[:keep]]
