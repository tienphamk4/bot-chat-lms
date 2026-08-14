"""Embedding qua gemini-embedding-001 — 768 chiều (Matryoshka, cắt từ 3072).

Ba quyết định cố định của dự án, đổi bất kỳ cái nào là RE-EMBED TOÀN BỘ:
model, số chiều (768), và phép chuẩn hóa L2 dưới đây. Google khuyến nghị
chuẩn hóa lại khi cắt Matryoshka xuống dưới 3072 chiều — không chuẩn hóa thì
khoảng cách cosine giữa các vector cắt bị lệch.

Vector đi vào Cloud SQL qua `string_to_vector('[...]')` — đúng đường đã probe.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.config import get_settings
from app.llm import vertex

log = logging.getLogger(__name__)

# Giới hạn của API embed theo lô; cũng là cỡ lô thiết kế chọn (mục 4 CLAUDE.md).
BATCH_SIZE = 100

TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"


@dataclass
class EmbedBatch:
    vectors: list[list[float]]
    tokens: int  # tổng token đầu vào — để ghi chi phí


def normalize(vector: list[float]) -> list[float]:
    """Chuẩn hóa L2 về độ dài 1."""
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


def to_sql_vector(vector: list[float]) -> str:
    """Định dạng cho string_to_vector(): '[0.012345,-0.67,...]'.

    6 chữ số thập phân là đủ cho float32 mà chuỗi ngắn được ~40%.
    """
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def embed_texts(
    texts: list[str],
    task_type: str = TASK_DOCUMENT,
    timeout_seconds: float | None = None,
    attempts: int = 2,
) -> EmbedBatch | None:
    """Embed một lô văn bản. Trả None khi LLM tắt/hỏng — nơi gọi tự xử.

    Lô vượt BATCH_SIZE là lỗi lập trình chứ không phải lỗi dữ liệu → assert.
    `timeout_seconds`/`attempts`: đường CHAT truyền trần ngắn (người dùng đang
    ngồi chờ, embed hỏng thì FULLTEXT gánh); đường nạp offline giữ mặc định.
    """
    assert len(texts) <= BATCH_SIZE, f"lô tối đa {BATCH_SIZE}, nhận {len(texts)}"
    if not texts:
        return EmbedBatch(vectors=[], tokens=0)

    settings = get_settings()
    # Client theo location riêng của embedding — xem chú thích trong config.
    client = vertex.get_client(location=settings.embed_location)
    if client is None:
        return None
    try:
        from google.genai import types

        from app.llm.vertex import _http_options

        response = client.models.embed_content(
            model=settings.embed_model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=settings.embed_dim,
                http_options=_http_options(
                    timeout_seconds or settings.llm_timeout_seconds, attempts=attempts
                ),
            ),
        )
    except Exception as exc:  # pragma: no cover - phụ thuộc mạng
        log.warning("Embed thất bại (%d văn bản): %s", len(texts), exc)
        return None

    vectors = [normalize(list(e.values)) for e in response.embeddings]

    # Vertex trả token_count trong statistics từng embedding; thiếu thì ước
    # lượng thô 4 ký tự/token — chỉ phục vụ ghi chi phí, không cần chính xác.
    tokens = 0
    for embedding, text in zip(response.embeddings, texts):
        stats = getattr(embedding, "statistics", None)
        count = getattr(stats, "token_count", None) if stats else None
        tokens += int(count) if count else max(1, len(text) // 4)

    return EmbedBatch(vectors=vectors, tokens=tokens)


# Cache embedding câu hỏi trong tiến trình — câu lặp lại khỏi tốn một lượt
# Vertex (~0,4-1s + quota). Khóa là bản chuẩn hóa; không cache thất bại.
_query_cache: dict[str, tuple[list[float], int]] = {}
_QUERY_CACHE_MAX = 512


def embed_query(question: str) -> tuple[list[float], int] | None:
    """Embed một câu hỏi (task_type khác với tài liệu — có ý nghĩa với model)."""
    from app.vn import vn_norm

    key = vn_norm(question)
    cached = _query_cache.get(key)
    if cached is not None:
        return cached[0], 0  # 0 token — không tốn lượt gọi nào

    # Trần NGẮN + 1 lần thử: quota embed đang bị lượt nạp tài liệu nền chiếm
    # thì thà bỏ kênh vector (FULLTEXT vẫn tìm tốt) còn hơn bắt người dùng chờ
    # backoff 8-16s — thủ phạm chính của những câu trả lời 28 giây.
    batch = embed_texts(
        [question], task_type=TASK_QUERY, timeout_seconds=4.0, attempts=1
    )
    if batch is None or not batch.vectors:
        return None

    if len(_query_cache) >= _QUERY_CACHE_MAX:
        _query_cache.clear()  # đơn giản hơn LRU thật, đủ cho mục đích này
    _query_cache[key] = (batch.vectors[0], batch.tokens)
    return batch.vectors[0], batch.tokens
