"""Đầu dò sức khỏe cho systemd/nginx/uptime monitor."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool

from app.db import ping

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    """Tiến trình còn sống. Không chạm CSDL — dùng cho watchdog systemd."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(response: Response):
    """Sẵn sàng nhận lưu lượng: CSDL phải trả lời."""
    ok = await run_in_threadpool(ping)
    if not ok:
        response.status_code = 503
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
