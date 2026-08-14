"""Semantic cache — giai đoạn 1 chỉ có tầng 0 (khớp hash).

Tầng vector (`VEC_DISTANCE_COSINE < 0.05`) để giai đoạn 3, khi đã có embedding
và có log thật để tinh chỉnh ngưỡng.

## Vì sao khóa cache có phạm vi quyền

Cache là chỗ dễ làm rò rỉ tài liệu nhất: một câu trả lời dựng từ tài liệu nội bộ
mà đem trả cho người ngoài là vi phạm NFR "rò rỉ = 0", và lỗi này im lặng —
không có ngoại lệ nào được ném ra.

Cách chặn: chỉ ghi cache cho những phạm vi quyền ĐỒNG NHẤT giữa mọi người dùng
cùng phạm vi.

- Kết quả toàn tài liệu `public`   → ai cũng dùng lại được.
- Có tài liệu `faculty` của khoa X → chỉ người khoa X dùng lại.
- Có tài liệu `staff`              → chỉ cán bộ dùng lại.
- Có tài liệu `course`             → KHÔNG ghi cache. Hai sinh viên cùng khoa
  vẫn khác danh sách lớp học phần, nên không có phạm vi chung nào an toàn.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import get_settings
from app.core.access import Identity
from app.db import connection, fetch_one
from app.vn import vn_norm
from sqlalchemy import text

# Phạm vi không gắn với khoa nào dùng 0 chứ không dùng NULL: NULL trong UNIQUE
# KEY được coi là khác nhau, để NULL là thủng khóa cache.
NO_FACULTY = 0


def question_hash(question: str, context_key: str = "") -> str:
    """Khóa cache = câu hỏi chuẩn hóa + ngữ cảnh trang (nếu có).

    Cùng câu "kế thừa là gì" hỏi từ trang bài học OOP và từ trang chủ là hai
    câu trả lời khác nhau — không được dùng chung cache.
    """
    payload = vn_norm(question)
    if context_key:
        payload += "\x1f" + context_key
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def scope_for(
    documents: list[dict[str, Any]], identity: Identity
) -> tuple[str, int] | None:
    """Phạm vi an toàn để ghi cache, hoặc None nếu không được ghi."""
    levels = {str(doc.get("access", "public")) for doc in documents}

    if "course" in levels:
        return None
    if "staff" in levels:
        return "staff", NO_FACULTY
    if "faculty" in levels:
        if identity.faculty_id is None:
            return None
        faculties = {
            doc.get("faculty_id")
            for doc in documents
            if doc.get("access") == "faculty"
        }
        if faculties != {identity.faculty_id}:
            return None
        return "faculty", identity.faculty_id
    return "public", NO_FACULTY


def lookup(
    question: str, identity: Identity, context_key: str = ""
) -> dict[str, Any] | None:
    """Tìm cache khớp cả câu hỏi lẫn phạm vi quyền. Phạm vi hẹp nhất thắng."""
    row = fetch_one(
        """
        SELECT id, answer, citations, route, access_scope, faculty_scope
        FROM query_cache
        WHERE question_hash = :h
          AND expires_at > UTC_TIMESTAMP()
          AND (
                (access_scope = 'public'  AND faculty_scope = 0)
             OR (access_scope = 'faculty' AND faculty_scope = :fid AND :fid <> 0)
             OR (access_scope = 'staff'   AND :is_staff = 1)
          )
        ORDER BY FIELD(access_scope, 'staff', 'faculty', 'public')
        LIMIT 1
        """,
        {
            "h": question_hash(question, context_key),
            "fid": identity.faculty_id or NO_FACULTY,
            "is_staff": 1 if identity.is_staff else 0,
        },
    )
    if not row:
        return None

    # Đếm hit ở luồng nền — số liệu thống kê không được phép làm chậm câu trả
    # lời (bản cũ ghi đồng bộ = cache hit cũng tốn một lượt ghi + commit).
    import threading

    hit_id = int(row["id"])

    def _count_hit() -> None:
        try:
            with connection() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE query_cache
                        SET hit_count = hit_count + 1, last_hit_at = UTC_TIMESTAMP()
                        WHERE id = :id
                        """
                    ),
                    {"id": hit_id},
                )
        except Exception:  # pragma: no cover
            pass

    threading.Thread(target=_count_hit, daemon=True).start()

    try:
        citations = json.loads(row["citations"] or "[]")
    except ValueError:
        citations = []

    return {"answer": row["answer"], "citations": citations, "route": row["route"]}


def clear_all() -> int:
    """Xóa toàn bộ cache. Gọi sau khi tài liệu bị nạp lại: document_id đổi nên
    link tải trong các câu trả lời cũ đã chết — giữ cache là phục vụ đồ hỏng."""
    with connection() as conn:
        result = conn.execute(text("DELETE FROM query_cache"))
        return int(result.rowcount or 0)


def store(
    question: str,
    answer: str,
    citations: list[dict[str, Any]],
    route: str,
    documents: list[dict[str, Any]],
    identity: Identity,
    context_key: str = "",
) -> bool:
    """Ghi cache nếu tìm được phạm vi an toàn. Trả về đã ghi hay chưa."""
    scope = scope_for(documents, identity)
    if scope is None:
        return False

    access_scope, faculty_scope = scope
    ttl = get_settings().cache_ttl_seconds

    with connection() as conn:
        conn.execute(
            text(
                """
                INSERT INTO query_cache
                    (question_hash, question_norm, answer, citations, route,
                     access_scope, faculty_scope, expires_at)
                VALUES
                    (:h, :qn, :answer, :citations, :route,
                     :access_scope, :faculty_scope,
                     DATE_ADD(UTC_TIMESTAMP(), INTERVAL :ttl SECOND))
                ON DUPLICATE KEY UPDATE
                    answer     = VALUES(answer),
                    citations  = VALUES(citations),
                    route      = VALUES(route),
                    expires_at = VALUES(expires_at)
                """
            ),
            {
                "h": question_hash(question, context_key),
                "qn": vn_norm(question),
                "answer": answer,
                "citations": json.dumps(citations, ensure_ascii=False),
                "route": route,
                "access_scope": access_scope,
                "faculty_scope": faculty_scope,
                "ttl": ttl,
            },
        )
    return True


def purge_expired() -> int:
    with connection() as conn:
        result = conn.execute(
            text("DELETE FROM query_cache WHERE expires_at <= UTC_TIMESTAMP()")
        )
        return int(result.rowcount or 0)
