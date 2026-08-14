"""Định dạng Server-Sent Events.

Client (trang chat, hoặc bất kỳ EventSource nào) đọc theo hợp đồng:

    event: route      — 1 lần, ngay sau router
    event: token      — n lần, mỗi lần một mẩu văn bản
    event: citations  — 1 lần, trước done
    event: done       — 1 lần, kết thúc
    event: error      — thay cho done khi hỏng

Giai đoạn 1 "giả streaming": câu trả lời template được cắt theo dòng và phát
dần. Giai đoạn 2, nhánh nội dung phát token thật từ LLM qua đúng các hàm này —
client không phân biệt được và không cần đổi gì.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

from app.core.chat import ChatOutcome
from app.schemas import DoneEvent, ErrorEvent, RouteEvent, TokenUsage

HEARTBEAT_SECONDS = 15.0

# Kích thước mẩu khi giả streaming. Cắt theo dòng trước, dòng quá dài cắt theo
# cụm ~60 ký tự để thanh chat có cảm giác chạy.
_CHUNK_CHARS = 60


def event(name: str, data: Any) -> str:
    """Một khung SSE hoàn chỉnh. `data` được JSON hóa một dòng."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n"


def heartbeat() -> str:
    """Dòng chú thích giữ kết nối — proxy/timeout không cắt được stream."""
    return ": ping\n\n"


def route_event(outcome: ChatOutcome) -> str:
    model = RouteEvent(
        intent=outcome.route.intent,
        branch=outcome.branch,
        router_source=outcome.route.source,
        course_code=outcome.route.course_code,
        doc_type=outcome.route.doc_type,
        semester=outcome.route.semester,
        lecturer=outcome.route.lecturer,
        standalone_question=outcome.route.standalone_question,
        cached=outcome.cached,
        degraded=outcome.degraded,
        log_id=outcome.log_id,
    )
    return event("route", model.model_dump(mode="json"))


def token_events(answer: str) -> Iterator[str]:
    for piece in _split(answer):
        yield event("token", {"text": piece})


def citations_event(outcome: ChatOutcome) -> str:
    return event(
        "citations",
        {"citations": [c.model_dump(mode="json") for c in outcome.citations]},
    )


def done_event(outcome: ChatOutcome) -> str:
    model = DoneEvent(
        log_id=outcome.log_id,
        latency_ms=outcome.latency_ms,
        route=outcome.branch,
        tokens=TokenUsage(
            **{"in": outcome.tokens_in, "out": outcome.tokens_out, "cached": 0}
        ),
        cost_usd=round(outcome.cost_usd, 8),
        degraded=outcome.degraded,
        trace=outcome.trace,
    )
    return event("done", model.model_dump(mode="json", by_alias=True))


def error_event(code: str, message: str, degraded_to: str | None = None) -> str:
    model = ErrorEvent(code=code, message=message, degraded_to=degraded_to)
    return event("error", model.model_dump(mode="json"))


# Nhịp phát khi giả-stream một câu trả lời đã tính xong. Không có nhịp thì mọi
# khung dồn một cục — người dùng không thấy "chữ chạy".
TOKEN_PACE_SECONDS = 0.024


async def token_frames_paced(
    answer: str, pace: float = TOKEN_PACE_SECONDS
) -> AsyncIterator[str]:
    import asyncio

    first = True
    for frame in token_events(answer):
        if not first:
            await asyncio.sleep(pace)
        first = False
        yield frame


async def stream_outcome(outcome: ChatOutcome) -> AsyncIterator[str]:
    """Toàn bộ luồng SSE cho một câu trả lời đã tính xong."""
    yield route_event(outcome)
    async for frame in token_frames_paced(outcome.answer):
        yield frame
    if outcome.citations:
        yield citations_event(outcome)
    yield done_event(outcome)


def _split(answer: str) -> Iterator[str]:
    for line in answer.splitlines(keepends=True):
        if len(line) <= _CHUNK_CHARS:
            if line:
                yield line
            continue
        words = line.split(" ")
        buffer = ""
        for word in words:
            candidate = f"{buffer} {word}" if buffer else word
            if len(candidate) > _CHUNK_CHARS and buffer:
                yield buffer + " "
                buffer = word
            else:
                buffer = candidate
        if buffer:
            yield buffer
