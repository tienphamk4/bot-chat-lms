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

# Số lượt gọi embedding chạy song song.
#
# `gemini-embedding-2` trả về ĐÚNG MỘT vector dù gửi bao nhiêu văn bản trong
# `contents` — âm thầm bỏ phần còn lại, không lỗi, không cảnh báo. Gửi 100
# chunk nhận về 1 vector; chốt kiểm độ dài ở `ingest.loader.embed_pending` là
# thứ duy nhất chặn được việc gán nhầm vector cho 99 chunk kia.
#
# Nên mỗi lượt gọi chỉ một văn bản, và bù tốc độ bằng chạy song song. Hạn mức
# của model này là 6000 request/phút nên 12 luồng không chạm trần.
EMBED_CONCURRENCY = 12

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

    from concurrent.futures import ThreadPoolExecutor

    from google.genai import types

    from app.llm.vertex import _http_options

    config = types.EmbedContentConfig(
        task_type=task_type,
        output_dimensionality=settings.embed_dim,
        http_options=_http_options(
            timeout_seconds or settings.llm_timeout_seconds, attempts=attempts
        ),
    )

    def one(text: str):
        """Đúng MỘT văn bản mỗi lượt gọi — xem ghi chú EMBED_CONCURRENCY."""
        response = client.models.embed_content(
            model=settings.embed_model, contents=[text], config=config
        )
        embedding = response.embeddings[0]
        stats = getattr(embedding, "statistics", None)
        count = getattr(stats, "token_count", None) if stats else None
        return (
            normalize(list(embedding.values)),
            int(count) if count else max(1, len(text) // 4),
        )

    try:
        if len(texts) == 1:
            results = [one(texts[0])]
        else:
            with ThreadPoolExecutor(max_workers=EMBED_CONCURRENCY) as pool:
                results = list(pool.map(one, texts))
    except Exception as exc:  # pragma: no cover - phụ thuộc mạng
        log.warning("Embed thất bại (%d văn bản): %s", len(texts), exc)
        return None

    return EmbedBatch(
        vectors=[vector for vector, _ in results],
        tokens=sum(count for _, count in results),
    )


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
