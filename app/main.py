"""Điểm vào FastAPI.

Chạy dev:
    uvicorn app.main:app --reload --port 8080

Sản xuất (systemd): uvicorn 127.0.0.1:8080, 2 workers, sau nginx.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    admin, assistants, chat, courses, documents, feedback, health,
)
from app.config import get_settings
from app.db import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Bớt ồn từ thư viện bên thứ ba: "AFC is enabled..." (google-genai in mỗi
    # lời gọi dù ta không dùng function calling) và mỗi HTTP request của httpx.
    # Log của app vẫn theo LOG_LEVEL ở trên.
    for noisy in ("google_genai", "google_genai.models", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Nạp chỉ mục môn học vào RAM. CSDL chưa sẵn sàng thì vẫn khởi động —
    # /readyz sẽ báo degraded, chỉ mục tự nạp lại ở lượt hỏi đầu tiên.
    from app.core import course_index

    try:
        count = course_index.refresh()
        logging.getLogger("app").info("Chỉ mục môn học: %d cụm từ", count)
    except Exception:
        logging.getLogger("app").warning(
            "Chưa nạp được chỉ mục môn học — CSDL chưa sẵn sàng?"
        )

    # Làm nóng Vertex ở nền: lời gọi ĐẦU TIÊN của một tiến trình tốn ~10-13s
    # (bắt tay ADC + mở kênh), các lần sau <1s. Trả trước chi phí đó lúc boot
    # để người dùng đầu tiên không phải chịu. Thread riêng — không chặn startup.
    import threading

    def _warm_db() -> None:
        try:
            from app.db import warm_pool

            opened = warm_pool()
            logging.getLogger("app").info("Pool DB đã ấm: %d kết nối", opened)
        except Exception:
            pass

    threading.Thread(target=_warm_db, daemon=True).start()

    def _warm_vertex() -> None:
        try:
            from app.llm import embeddings, vertex

            warmed = embeddings.embed_query("khởi động") is not None

            # Kênh sinh (location global) ấm riêng — một lời gọi 1 token.
            gen = vertex.generate_text(
                system_instruction="Trả lời đúng một ký tự.",
                prompt="1",
                max_output_tokens=5,
            )
            if warmed or gen is not None:
                logging.getLogger("app").info("Vertex đã ấm — sẵn sàng phục vụ")
        except Exception:
            pass  # LLM tắt/thiếu credential — hệ thống vẫn chạy nhánh luật

    threading.Thread(target=_warm_vertex, daemon=True).start()

    yield
    dispose_engine()


app = FastAPI(
    title="EDU-DOCBOT",
    version="0.1.0",
    description="Chatbot tra cứu tài liệu học tập — giai đoạn 1 (metadata-first)",
    lifespan=lifespan,
)

_cors = get_settings().cors_origin_list
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-User-Id"],
    )

PREFIX = "/api/v1"
app.include_router(chat.router, prefix=PREFIX)
app.include_router(assistants.router, prefix=PREFIX)
app.include_router(assistants.admin_router, prefix=PREFIX)
app.include_router(documents.router, prefix=PREFIX)
app.include_router(courses.router, prefix=PREFIX)
app.include_router(feedback.router, prefix=PREFIX)
app.include_router(admin.router, prefix=PREFIX)
app.include_router(health.router)

# Trang chat demo. Sản xuất: nginx phục vụ thẳng thư mục web/, mount này chỉ
# chạy khi thư mục tồn tại cạnh mã nguồn (tiện cho dev, vô hại khi có nginx).
_web_dir = Path(__file__).resolve().parent.parent / "web"
if _web_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
