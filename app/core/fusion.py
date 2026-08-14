"""Reciprocal Rank Fusion — trộn kết quả hai kênh truy hồi ở tầng ứng dụng.

RRF không cần chuẩn hóa điểm giữa hai kênh (điểm FULLTEXT và khoảng cách cosine
không cùng thang đo): chỉ dùng THỨ HẠNG. score(d) = Σ 1/(k + rank_i(d)).
k=60 theo thiết kế — giá trị kinh điển từ bài báo gốc, đủ phẳng để một kênh
không lấn át kênh kia.
"""

from __future__ import annotations

from typing import Any

RRF_K = 60


def rrf_merge(
    ranked_lists: list[list[dict[str, Any]]],
    key: str = "chunk_id",
    k: int = RRF_K,
) -> list[dict[str, Any]]:
    """Trộn nhiều danh sách đã xếp hạng thành một, theo điểm RRF giảm dần.

    Mỗi phần tử là dict có trường `key` định danh. Phần tử xuất hiện ở nhiều
    danh sách giữ bản đầu tiên gặp (nội dung như nhau, chỉ điểm khác) và cộng
    dồn điểm — đó chính là ưu thế của tài liệu được cả hai kênh tìm thấy.
    """
    scores: dict[Any, float] = {}
    first_seen: dict[Any, dict[str, Any]] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            identity = item[key]
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(identity, item)

    merged = []
    for identity in sorted(scores, key=lambda i: scores[i], reverse=True):
        item = dict(first_seen[identity])
        item["rrf_score"] = scores[identity]
        merged.append(item)
    return merged
