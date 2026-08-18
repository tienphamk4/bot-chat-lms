"""Danh sách trợ lý ảo đang bật — frontend đọc để tự dựng bong bóng.

Đây là mấu chốt của việc nhân bản: thêm một bot = INSERT một dòng vào bảng
`assistants`, không sửa HTML, không sửa Python, không deploy lại. Trang chat gọi
endpoint này rồi vẽ đúng số bong bóng với đúng màu, đúng tên, đúng lời chào.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.core import personas
from app.schemas import AssistantOut

router = APIRouter(tags=["assistants"])


@router.get("/assistants", response_model=list[AssistantOut])
async def list_assistants() -> list[AssistantOut]:
    rows = await run_in_threadpool(personas.list_active)
    return [
        AssistantOut(
            slug=p.slug,
            name=p.name,
            tagline=p.tagline,
            placeholder=p.welcome.get("placeholder")
            or "Nhập nội dung anh/chị muốn hỏi…",
            welcome={
                "title": p.welcome.get("title") or p.name,
                "text": p.welcome.get("text") or p.tagline,
                "icon": p.welcome.get("icon") or "💬",
                "chips": p.welcome.get("chips") or [],
            },
            brand=p.brand,
        )
        for p in rows
        if p.slug
    ]


# ---------------------------------------------------------------------------
# Quản trị — trang cấu hình chatbot gọi vào đây.
#
# Nhân bản một bot là POST một bản ghi; đổi giọng là PUT một bản ghi. Không có
# đường nào phải sửa mã hay deploy lại.
# ---------------------------------------------------------------------------

from fastapi import Body, HTTPException

from app.api.deps import AdminIdentity
from app.db import execute, fetch_all, fetch_one

admin_router = APIRouter(prefix="/admin", tags=["admin"])

# blocked_topics, access_levels và default_doc_type TỪNG BỊ THIẾU ở đây: trang
# cấu hình gửi lên đủ nhưng API lặng lẽ bỏ qua, nên sửa xong bấm lưu là mất.
_EDITABLE = (
    "name", "tagline", "corpus_label", "offer", "doc_types", "blocked_topics",
    "access_levels", "default_doc_type", "screen",
    "gen_temperature", "gen_max_tokens", "gen_thinking_budget",
    "replies", "contact", "welcome", "brand", "sort_order", "is_active",
)
_JSON_FIELDS = {
    "doc_types", "blocked_topics", "access_levels",
    "replies", "contact", "welcome", "brand",
}
_LIST_FIELDS = {"doc_types", "blocked_topics", "access_levels"}


def _row_out(row: dict) -> dict:
    import json

    out = dict(row)
    for key in _JSON_FIELDS:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
            except ValueError:
                out[key] = [] if key in _LIST_FIELDS else {}
    return out


@admin_router.get("/assistants")
async def admin_list(identity: AdminIdentity):
    rows = await run_in_threadpool(
        fetch_all,
        """
        SELECT id, slug, name, tagline, corpus_label, offer, doc_types,
               blocked_topics, access_levels, default_doc_type, screen,
               gen_temperature, gen_max_tokens, gen_thinking_budget,
               replies, contact, welcome, brand, sort_order, is_active
        FROM assistants ORDER BY sort_order, id
        """,
    )
    return [_row_out(r) for r in rows]


@admin_router.post("/assistants", status_code=201)
async def admin_create(identity: AdminIdentity, body: dict = Body(...)):
    import json

    slug = str(body.get("slug") or "").strip().lower()
    if not slug or not all(ch.isalnum() or ch in "-_" for ch in slug):
        raise HTTPException(400, "slug chỉ gồm chữ, số, gạch ngang hoặc gạch dưới")

    exists = await run_in_threadpool(
        fetch_one, "SELECT id FROM assistants WHERE slug = :s", {"s": slug}
    )
    if exists:
        raise HTTPException(409, f"Đã có trợ lý mang slug '{slug}'")

    params = {"slug": slug}
    for key in _EDITABLE:
        value = body.get(key)
        if key in _JSON_FIELDS:
            params[key] = json.dumps(
                value if value is not None else ([] if key in _LIST_FIELDS else {}),
                ensure_ascii=False,
            )
        elif key == "is_active":
            params[key] = 1 if value in (None, True, 1, "1", "true") else 0
        elif key == "sort_order":
            params[key] = int(value or 0)
        elif key == "gen_thinking_budget":
            params[key] = max(0, min(int(value or 0), 8192))
        elif key in ("gen_temperature", "gen_max_tokens"):
            # Rỗng = dùng mặc định hệ thống, KHÔNG phải 0.
            params[key] = None if value in (None, "", "null") else (
                float(value) if key == "gen_temperature" else int(value)
            )
        elif key in ("screen", "default_doc_type"):
            params[key] = (str(value).strip() or None) if value else None
        else:
            params[key] = str(value or "")

    if not params["name"]:
        raise HTTPException(400, "Thiếu tên trợ lý")

    columns = ", ".join(["slug", *_EDITABLE])
    values = ", ".join(f":{c}" for c in ["slug", *_EDITABLE])
    await run_in_threadpool(
        execute, f"INSERT INTO assistants ({columns}) VALUES ({values})", params
    )
    await run_in_threadpool(personas.clear_cache)
    return {"slug": slug}


@admin_router.put("/assistants/{slug}")
async def admin_update(identity: AdminIdentity, slug: str, body: dict = Body(...)):
    import json

    row = await run_in_threadpool(
        fetch_one, "SELECT id FROM assistants WHERE slug = :s", {"s": slug}
    )
    if not row:
        raise HTTPException(404, "Không có trợ lý này")

    sets, params = [], {"slug": slug}
    for key in _EDITABLE:
        if key not in body:
            continue
        value = body[key]
        if key in _JSON_FIELDS:
            params[key] = json.dumps(value, ensure_ascii=False)
        elif key == "is_active":
            params[key] = 1 if value in (True, 1, "1", "true") else 0
        elif key == "sort_order":
            params[key] = int(value or 0)
        elif key == "gen_thinking_budget":
            params[key] = max(0, min(int(value or 0), 8192))
        elif key in ("gen_temperature", "gen_max_tokens"):
            params[key] = None if value in (None, "", "null") else (
                float(value) if key == "gen_temperature" else int(value)
            )
        elif key in ("screen", "default_doc_type"):
            params[key] = (str(value).strip() or None) if value else None
        else:
            params[key] = str(value or "")
        sets.append(f"{key} = :{key}")

    if not sets:
        return {"updated": 0}

    await run_in_threadpool(
        execute, f"UPDATE assistants SET {', '.join(sets)} WHERE slug = :slug", params
    )
    await run_in_threadpool(personas.clear_cache)
    return {"updated": 1}


@admin_router.delete("/assistants/{slug}")
async def admin_delete(identity: AdminIdentity, slug: str):
    n = await run_in_threadpool(
        execute, "DELETE FROM assistants WHERE slug = :s", {"s": slug}
    )
    await run_in_threadpool(personas.clear_cache)
    if not n:
        raise HTTPException(404, "Không có trợ lý này")
    return {"deleted": n}


@admin_router.get("/assistants/{slug}/documents")
async def admin_list_documents(identity: AdminIdentity, slug: str):
    """Id các tài liệu đang gán cho trợ lý này."""
    rows = await run_in_threadpool(
        fetch_all,
        """
        SELECT ad.document_id AS id
        FROM assistant_documents ad
        JOIN assistants a ON a.id = ad.assistant_id
        WHERE a.slug = :slug
        """,
        {"slug": slug},
    )
    return [int(r["id"]) for r in rows]


@admin_router.put("/assistants/{slug}/documents")
async def admin_set_documents(
    identity: AdminIdentity, slug: str, body: dict = Body(...)
):
    """Đặt LẠI danh sách tài liệu của trợ lý này (thay thế, không cộng dồn).

    Đây là chiều ngược của POST /admin/documents/assistants: bên kia đứng từ
    tài liệu chọn bot, bên này đứng từ bot chọn tài liệu. Màn cấu hình bot dùng
    chiều này vì người quản trị đang nghĩ theo mạch "bot này đọc những gì".
    """
    from app.core.retrieval import clear_assistant_scope_cache
    from app.db import in_clause

    ids = [int(x) for x in (body.get("document_ids") or [])][:1000]

    def _apply() -> int:
        row = fetch_one("SELECT id FROM assistants WHERE slug = :s", {"s": slug})
        if not row:
            return -1
        execute(
            "DELETE FROM assistant_documents WHERE assistant_id = :a",
            {"a": row["id"]},
        )
        if not ids:
            return 0
        # Chỉ nhận id có thật, tránh dựng liên kết trỏ vào hư không.
        doc_sql, doc_params = in_clause("doc", ids)
        real = fetch_all(
            f"SELECT id FROM documents WHERE id IN ({doc_sql})", doc_params
        )
        for d in real:
            execute(
                "INSERT IGNORE INTO assistant_documents (assistant_id, document_id) "
                "VALUES (:a, :d)",
                {"a": row["id"], "d": d["id"]},
            )
        return len(real)

    n = await run_in_threadpool(_apply)
    if n < 0:
        raise HTTPException(404, "Không có trợ lý này")
    await run_in_threadpool(clear_assistant_scope_cache)
    await run_in_threadpool(personas.clear_cache)
    return {"linked": n}
