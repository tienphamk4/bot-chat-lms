"""POST /feedback — gắn đánh giá của người dùng vào query_logs.

Dữ liệu này nuôi bộ eval: câu bị đánh "không hữu ích" là ứng viên đưa vào bộ
200 câu kiểm thử.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentIdentity
from app.db import execute, fetch_one
from app.schemas import FeedbackIn

router = APIRouter(tags=["feedback"])


@router.post("/feedback", status_code=201)
async def create_feedback(body: FeedbackIn, identity: CurrentIdentity):
    exists = await run_in_threadpool(
        fetch_one, "SELECT id FROM query_logs WHERE id = :id", {"id": body.log_id}
    )
    if not exists:
        raise HTTPException(status_code=404, detail="log_id không tồn tại")

    feedback_id = await run_in_threadpool(
        execute,
        """
        INSERT INTO feedback (log_id, helpful, comment)
        VALUES (:log_id, :helpful, :comment)
        """,
        {
            "log_id": body.log_id,
            "helpful": 1 if body.helpful else 0,
            "comment": body.comment,
        },
    )
    return {"id": feedback_id}
