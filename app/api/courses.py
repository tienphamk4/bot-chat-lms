"""GET /courses — gợi ý môn học cho ô tìm kiếm."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentIdentity
from app.core.access import Identity, access_clause
from app.db import fetch_all
from app.schemas import CourseOut, Offering
from app.vn import ft_boolean_query, pipe_like, vn_norm

router = APIRouter(tags=["courses"])


@router.get("/courses", response_model=list[CourseOut])
async def suggest_courses(
    identity: CurrentIdentity,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(10, ge=1, le=100),
):
    """Có `q`: gợi ý theo từ khóa. Không `q`: toàn danh mục (cho ô chọn ngữ cảnh)."""
    if not q:
        return await run_in_threadpool(_list_all, identity, limit)
    return await run_in_threadpool(_search, identity, q, limit)


def _list_all(identity: Identity, limit: int) -> list[CourseOut]:
    rows = fetch_all(
        "SELECT * FROM courses WHERE active = 1 ORDER BY code LIMIT :limit",
        {"limit": limit},
    )
    counts = _document_counts(identity, [int(r["id"]) for r in rows])
    return [_to_out(row, counts.get(int(row["id"]), 0)) for row in rows]


def _search(identity: Identity, q: str, limit: int) -> list[CourseOut]:
    q_norm = vn_norm(q)

    # Khớp chắc trước: tiền tố mã môn, tên môn, alias.
    rows = fetch_all(
        """
        SELECT * FROM courses
        WHERE active = 1
          AND (code_norm LIKE :prefix OR name_norm LIKE :contains
               OR aliases_norm LIKE :alias)
        ORDER BY CASE
            WHEN code_norm = :exact THEN 0
            WHEN code_norm LIKE :prefix THEN 1
            ELSE 2 END, name
        LIMIT :limit
        """,
        {
            "prefix": f"{q_norm}%",
            "contains": f"%{q_norm}%",
            "alias": pipe_like(q),
            "exact": q_norm,
            "limit": limit,
        },
    )

    # Chưa đủ thì bù bằng FULLTEXT (gõ lệch thứ tự từ vẫn ra).
    if len(rows) < limit:
        expression = ft_boolean_query(q, mode="or")
        if expression:
            seen = {row["id"] for row in rows}
            extra = fetch_all(
                """
                SELECT *, MATCH(search_norm) AGAINST (:q IN BOOLEAN MODE) AS score
                FROM courses
                WHERE active = 1 AND MATCH(search_norm) AGAINST (:q IN BOOLEAN MODE)
                ORDER BY score DESC
                LIMIT :limit
                """,
                {"q": expression, "limit": limit},
            )
            rows.extend(r for r in extra if r["id"] not in seen)
            rows = rows[:limit]

    counts = _document_counts(identity, [int(r["id"]) for r in rows])
    return [_to_out(row, counts.get(int(row["id"]), 0)) for row in rows]


def _document_counts(identity: Identity, course_ids: list[int]) -> dict[int, int]:
    """Số tài liệu MÀ NGƯỜI HỎI THẤY ĐƯỢC — cũng phải qua mệnh đề quyền, nếu
    không thì con số này tự nó rò rỉ sự tồn tại của tài liệu nội bộ."""
    if not course_ids:
        return {}
    from app.db import in_clause

    placeholder, params = in_clause("cid", course_ids)
    acl_sql, acl_params = access_clause(identity, "d")
    params.update(acl_params)

    rows = fetch_all(
        f"""
        SELECT d.course_id, COUNT(*) AS n
        FROM documents d
        WHERE d.status = 'indexed' AND d.course_id IN ({placeholder}) AND {acl_sql}
        GROUP BY d.course_id
        """,
        params,
    )
    return {int(r["course_id"]): int(r["n"]) for r in rows}


def _to_out(row: dict, document_count: int) -> CourseOut:
    def parse(raw) -> list:
        if isinstance(raw, list):
            return raw
        try:
            data = json.loads(raw or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    return CourseOut(
        id=int(row["id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        faculty_id=row.get("faculty_id"),
        credits=row.get("credits"),
        aliases=[a for a in parse(row.get("aliases")) if isinstance(a, str)],
        offerings=[
            Offering(**o)
            for o in parse(row.get("offerings"))
            if isinstance(o, dict) and o.get("semester")
        ],
        document_count=document_count,
    )
