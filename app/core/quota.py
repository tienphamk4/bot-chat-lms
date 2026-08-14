"""Quota token & ngân sách ngày (KT-10) — lưới an toàn cuối cùng.

Mọi kỹ thuật tiết kiệm phía trên có thể hỏng cùng lúc; bảng `token_quota` và
guard này bảo đảm hóa đơn ngày vẫn có cận trên.

Chạm trần KHÔNG dừng phục vụ. Hệ thống "hạ cấp mượt": tắt router LLM, mọi câu
hỏi đi qua đường rule-based + SQL (0 token) — người dùng vẫn tra cứu được tài
liệu, chỉ mất phần hiểu câu hỏi mơ hồ.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.core.access import Identity
from app.db import connection, fetch_one
from sqlalchemy import text


@dataclass(frozen=True)
class QuotaStatus:
    allow_llm: bool
    degraded: bool
    reason: str | None = None


# Cache 15s theo user — guard là lưới an toàn, không cần chính xác từng giây;
# chạm trần muộn nhất 15s vẫn chặn được hóa đơn (add_usage vẫn đếm đủ từng câu).
_check_cache: dict[int, tuple[float, "QuotaStatus"]] = {}
_CHECK_TTL = 15.0


def check(identity: Identity) -> QuotaStatus:
    """Còn được gọi LLM không? Kiểm cả trần cá nhân lẫn ngân sách toàn hệ thống.

    MỘT lượt khứ hồi DB duy nhất (hai phép kiểm gộp bằng subquery) — bản cũ hai
    truy vấn nối tiếp từng ăn 3,4s khi mạng tới DB chậm. Khách ẩn danh không có
    dòng quota riêng — họ dùng chung ngân sách ngày.
    """
    import time

    settings = get_settings()
    cache_key = identity.user_id or 0

    cached = _check_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < _CHECK_TTL:
        return cached[1]

    row = fetch_one(
        """
        SELECT
            COALESCE((SELECT tokens_total FROM token_quota
                      WHERE user_id = :uid AND usage_date = UTC_DATE()), 0) AS user_used,
            (SELECT COALESCE(SUM(cost_usd), 0) FROM query_logs
             WHERE created_at >= UTC_DATE()) AS day_spent
        """,
        {"uid": identity.user_id or 0},
    )

    status = QuotaStatus(allow_llm=True, degraded=False)
    if row:
        if (
            identity.user_id is not None
            and int(row["user_used"]) >= settings.quota_daily_tokens
        ):
            status = QuotaStatus(allow_llm=False, degraded=True, reason="user_quota")
        elif float(row["day_spent"]) >= settings.daily_budget_usd:
            status = QuotaStatus(allow_llm=False, degraded=True, reason="daily_budget")

    if len(_check_cache) > 10_000:
        _check_cache.clear()
    _check_cache[cache_key] = (time.monotonic(), status)
    return status


def add_usage(identity: Identity, tokens_in: int, tokens_out: int) -> None:
    """Cộng dồn một câu duy nhất — INSERT ... ON DUPLICATE KEY UPDATE là nguyên
    tử phía server, nhiều worker uvicorn cộng cùng lúc không mất lượt."""
    if identity.user_id is None or (tokens_in == 0 and tokens_out == 0):
        return

    with connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO token_quota
                    (user_id, usage_date, tokens_in, tokens_out, tokens_total, request_count)
                VALUES
                    (:uid, UTC_DATE(), :ti, :to, :ti + :to, 1)
                ON DUPLICATE KEY UPDATE
                    tokens_in     = tokens_in     + VALUES(tokens_in),
                    tokens_out    = tokens_out    + VALUES(tokens_out),
                    tokens_total  = tokens_total  + VALUES(tokens_total),
                    request_count = request_count + 1
                """
            ),
            {"uid": identity.user_id, "ti": tokens_in, "to": tokens_out},
        )
