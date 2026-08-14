"""Ghi và tra bảng `courses` — bảng gộp aliases + offerings.

`courses` giữ hai cột JSON là nguồn sự thật (`aliases`, `offerings`) và bốn cột
dẫn xuất phục vụ tìm kiếm (`aliases_norm`, `lecturers_norm`, `semesters`,
`search_norm`). MariaDB không cho FULLTEXT trên generated column ảo, nên các cột
dẫn xuất phải do ứng dụng ghi.

`save_course()` là ĐƯỜNG GHI DUY NHẤT. Sửa JSON bằng tay (qua SQL client, qua
đợt đồng bộ từ hệ thống của trường…) sẽ làm lệch cột dẫn xuất — chữa bằng
`python -m ingest.cli resync-derived`.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from app.db import connection, fetch_all, fetch_one
from app.vn import pipe_join, pipe_like, vn_norm
from sqlalchemy import text


def derive_columns(
    code: str,
    name: str,
    aliases: Iterable[str],
    offerings: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Tính bốn cột dẫn xuất từ nguồn sự thật.

    Hàm thuần, không chạm CSDL — để test được và để `resync-derived` dùng lại.
    """
    alias_list = [a for a in aliases if a and a.strip()]
    lecturers = [o.get("lecturer") for o in offerings if o.get("lecturer")]
    semesters = [o.get("semester") for o in offerings if o.get("semester")]

    # search_norm là thứ duy nhất đi vào FULLTEXT: mã môn + tên môn + mọi alias.
    # Không đưa tên giảng viên vào đây — hỏi "tài liệu của thầy A" không nên
    # kéo về mọi môn thầy A từng dạy.
    search_parts = [vn_norm(code), vn_norm(name)] + [vn_norm(a) for a in alias_list]

    return {
        "aliases_norm": pipe_join(alias_list),
        "lecturers_norm": pipe_join(lecturers),
        "semesters": pipe_join(semesters),
        "search_norm": " ".join(p for p in search_parts if p),
    }


def save_course(
    code: str,
    name: str,
    faculty_id: int | None = None,
    credits: int | None = None,
    aliases: Iterable[str] | None = None,
    offerings: Iterable[dict[str, Any]] | None = None,
) -> int:
    """Chèn hoặc cập nhật một môn, luôn ghi kèm cột dẫn xuất. Trả về course_id."""
    alias_list = list(aliases or [])
    offering_list = list(offerings or [])
    derived = derive_columns(code, name, alias_list, offering_list)

    params = {
        "code": code,
        "code_norm": vn_norm(code),
        "name": name,
        "name_norm": vn_norm(name),
        "faculty_id": faculty_id,
        "credits": credits,
        "aliases": json.dumps(alias_list, ensure_ascii=False),
        "offerings": json.dumps(offering_list, ensure_ascii=False),
        **derived,
    }

    with connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO courses
                    (faculty_id, code, code_norm, name, name_norm, credits,
                     aliases, offerings, aliases_norm, lecturers_norm, semesters, search_norm)
                VALUES
                    (:faculty_id, :code, :code_norm, :name, :name_norm, :credits,
                     :aliases, :offerings, :aliases_norm, :lecturers_norm, :semesters, :search_norm)
                ON DUPLICATE KEY UPDATE
                    faculty_id     = VALUES(faculty_id),
                    code           = VALUES(code),
                    name           = VALUES(name),
                    name_norm      = VALUES(name_norm),
                    credits        = VALUES(credits),
                    aliases        = VALUES(aliases),
                    offerings      = VALUES(offerings),
                    aliases_norm   = VALUES(aliases_norm),
                    lecturers_norm = VALUES(lecturers_norm),
                    semesters      = VALUES(semesters),
                    search_norm    = VALUES(search_norm)
                """
            ),
            params,
        )
        row = conn.execute(
            text("SELECT id FROM courses WHERE code_norm = :code_norm"),
            {"code_norm": params["code_norm"]},
        ).first()
    return int(row[0])


def resync_derived() -> int:
    """Dựng lại toàn bộ cột dẫn xuất từ hai cột JSON. Trả về số dòng đã sửa."""
    rows = fetch_all("SELECT id, code, name, aliases, offerings FROM courses")
    updated = 0
    with connection() as conn:
        for row in rows:
            derived = derive_columns(
                row["code"],
                row["name"],
                _json_list(row["aliases"]),
                _json_list(row["offerings"]),
            )
            conn.execute(
                text(
                    """
                    UPDATE courses SET
                        aliases_norm   = :aliases_norm,
                        lecturers_norm = :lecturers_norm,
                        semesters      = :semesters,
                        search_norm    = :search_norm
                    WHERE id = :id
                    """
                ),
                {**derived, "id": row["id"]},
            )
            updated += 1
    return updated


def find_course(
    code: str | None = None,
    hint: str | None = None,
) -> dict[str, Any] | None:
    """Tìm môn theo mã trước, rồi tới alias/tên, cuối cùng mới FULLTEXT.

    Thứ tự này có chủ đích: mã môn là khớp chắc chắn, alias là khớp do người
    dùng tự đặt, FULLTEXT là đoán. Đoán chỉ dùng khi hai cái chắc đã trượt.
    """
    if code:
        row = fetch_one(
            "SELECT * FROM courses WHERE code_norm = :c AND active = 1",
            {"c": vn_norm(code)},
        )
        if row:
            return row

    if not hint:
        return None

    hint_norm = vn_norm(hint)

    row = fetch_one(
        """
        SELECT * FROM courses
        WHERE active = 1 AND (name_norm = :h OR aliases_norm LIKE :like)
        ORDER BY CASE WHEN name_norm = :h THEN 0 ELSE 1 END
        LIMIT 1
        """,
        {"h": hint_norm, "like": pipe_like(hint)},
    )
    if row:
        return row

    from app.vn import ft_boolean_query

    expression = ft_boolean_query(hint)
    if not expression:
        return None

    return fetch_one(
        """
        SELECT *, MATCH(search_norm) AGAINST (:q IN BOOLEAN MODE) AS score
        FROM courses
        WHERE active = 1 AND MATCH(search_norm) AGAINST (:q IN BOOLEAN MODE)
        ORDER BY score DESC
        LIMIT 1
        """,
        {"q": expression},
    )


def _json_list(raw: Any) -> list:
    if not raw:
        return []
    if isinstance(raw, (list, dict)):
        return raw if isinstance(raw, list) else [raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
