"""POST /api/v1/chat — đầu não của toàn bộ hệ thống.

Hai chế độ trên cùng một logic:
- `stream=true`  → SSE: route → token* → citations → done (hoặc error).
- `stream=false` → JSON một cục. Bộ eval 200 câu dùng đường này.

Việc tính câu trả lời là I/O CSDL + (đôi khi) một lời gọi Vertex — chạy trong
threadpool qua `run_in_threadpool` để không chặn event loop giữa các kết nối
SSE đồng thời.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import CurrentIdentity
from app.core import chat as chat_core
from app.core import sse
from app.schemas import ChatRequest, ChatResponse, DoneEvent, RouteEvent, TokenUsage

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# nginx cần thêm `proxy_buffering off` cho location /api/ — X-Accel-Buffering
# là lớp nhắc thứ hai, nginx đọc header này và tắt buffer cho riêng response
# này kể cả khi quên sửa config. Quên cả hai = chat đứng im rồi hiện hết một lúc.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/chat")
async def chat_endpoint(body: ChatRequest, identity: CurrentIdentity):
    if body.stream:
        return StreamingResponse(
            _stream(body, identity),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    try:
        outcome = await run_in_threadpool(
            chat_core.handle, body.session_id, body.message, identity,
            None, body.context,
        )
    except Exception:
        log.exception("chat handle thất bại")
        return JSONResponse(
            status_code=500,
            content={"code": "internal", "message": "Lỗi hệ thống, thử lại sau."},
        )

    response = ChatResponse(
        answer=outcome.answer,
        route=RouteEvent(
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
        ),
        citations=outcome.citations,
        done=DoneEvent(
            log_id=outcome.log_id,
            latency_ms=outcome.latency_ms,
            route=outcome.branch,
            tokens=TokenUsage(
                **{"in": outcome.tokens_in, "out": outcome.tokens_out, "cached": 0}
            ),
            cost_usd=round(outcome.cost_usd, 8),
            degraded=outcome.degraded,
            trace=outcome.trace,
        ),
    )
    return JSONResponse(content=response.model_dump(mode="json", by_alias=True))


async def _stream(body: ChatRequest, identity) -> AsyncIterator[str]:
    """Chạy handle trong threadpool; nhánh RAG đẩy token THẬT qua queue trong
    lúc chạy, các nhánh còn lại giả-stream có nhịp sau khi tính xong.

    Worker (thread) và generator (event loop) nói chuyện qua queue.Queue —
    sink được gọi từ thread nên không đụng asyncio. Lỗi ở bất kỳ đâu thành
    event error — stream luôn kết thúc tử tế."""
    import queue as queue_module
    import time as time_module

    frames: queue_module.Queue[str] = queue_module.Queue()
    sent = {"route": False, "token": False}

    def sink(kind: str, payload) -> None:
        # Chạy trong worker thread — chỉ dựng khung SSE (thuần CPU) rồi bỏ queue.
        if kind == "route":
            sent["route"] = True
            frames.put(sse.route_event(payload))
        elif kind == "token":
            sent["token"] = True
            frames.put(sse.event("token", {"text": payload}))

    task = asyncio.create_task(
        run_in_threadpool(
            chat_core.handle, body.session_id, body.message, identity, sink,
            body.context,
        )
    )

    last_sent = time_module.monotonic()
    try:
        while True:
            try:
                frame = frames.get_nowait()
                yield frame
                last_sent = time_module.monotonic()
                continue
            except queue_module.Empty:
                pass
            if task.done():
                break
            await asyncio.sleep(0.05)
            if time_module.monotonic() - last_sent >= sse.HEARTBEAT_SECONDS:
                yield sse.heartbeat()
                last_sent = time_module.monotonic()

        # Task xong — vét nốt khung còn trong queue rồi mới lấy kết quả.
        while True:
            try:
                yield frames.get_nowait()
            except queue_module.Empty:
                break
        outcome = task.result()
    except Exception:
        log.exception("chat stream thất bại")
        yield sse.error_event("internal", "Lỗi hệ thống, thử lại sau.")
        return

    if not sent["route"]:
        yield sse.route_event(outcome)
    if not sent["token"]:
        async for frame in sse.token_frames_paced(outcome.answer):
            yield frame
    if outcome.citations:
        yield sse.citations_event(outcome)
    yield sse.done_event(outcome)
