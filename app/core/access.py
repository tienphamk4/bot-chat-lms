"""Lọc quyền — chạy TRƯỚC truy hồi, không bao giờ sau khi sinh.

Nguyên tắc cứng của dự án: LLM không được nhìn thấy chunk ngoài quyền. Cách duy
nhất bảo đảm điều đó là mệnh đề quyền nằm ngay trong câu SQL truy hồi, chứ
không lọc lại kết quả ở tầng trên.

Mọi truy vấn chạm bảng `documents` đều phải gọi `access_clause()`. Không có
đường nào khác.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db import fetch_all, fetch_one, in_clause

STAFF_ROLES = frozenset({"lecturer", "staff", "admin"})


@dataclass(frozen=True)
class Identity:
    """Người hỏi. `user_id=None` là khách ẩn danh — chỉ thấy tài liệu public."""

    user_id: int | None = None
    role: str = "anonymous"
    faculty_id: int | None = None
    course_ids: tuple[int, ...] = field(default_factory=tuple)
    full_name: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def is_anonymous(self) -> bool:
        return self.user_id is None


ANONYMOUS = Identity()

# Cache danh tính trong tiến trình — load_identity vốn tốn 2 lượt DB cho MỖI
# request (users + user_courses), đứng ngoài mọi timeline nên không ai thấy.
# TTL ngắn: đổi vai trò/ghi danh có hiệu lực trễ tối đa 60s, chấp nhận được.
_identity_cache: dict[int, tuple[float, Identity]] = {}
_IDENTITY_TTL = 60.0


def load_identity(user_id: int | None) -> Identity:
    """Đọc danh tính từ CSDL (cache 60s). ID không tồn tại/bị khóa → ẩn danh."""
    if not user_id:
        return ANONYMOUS

    import time

    cached = _identity_cache.get(user_id)
    if cached is not None and time.monotonic() - cached[0] < _IDENTITY_TTL:
        return cached[1]

    identity = _load_identity_db(user_id)
    _identity_cache[user_id] = (time.monotonic(), identity)
    if len(_identity_cache) > 10_000:  # chặn phình bộ nhớ
        _identity_cache.clear()
    return identity


def _load_identity_db(user_id: int) -> Identity:
    row = fetch_one(
        """
        SELECT id, role, faculty_id, full_name
        FROM users
        WHERE id = :uid AND active = 1
        """,
        {"uid": user_id},
    )
    if not row:
        return ANONYMOUS

    enrolled = fetch_all(
        "SELECT course_id FROM user_courses WHERE user_id = :uid",
        {"uid": user_id},
    )
    return Identity(
        user_id=int(row["id"]),
        role=str(row["role"]),
        faculty_id=int(row["faculty_id"]) if row["faculty_id"] is not None else None,
        course_ids=tuple(int(r["course_id"]) for r in enrolled),
        full_name=row["full_name"],
    )


def access_clause(identity: Identity, alias: str = "d") -> tuple[str, dict]:
    """Mệnh đề SQL giới hạn tài liệu người này được phép thấy.

    Trả về `(sql, params)` để nơi gọi nối vào WHERE. Tất cả giá trị đều đi qua
    tham số ràng buộc; danh sách môn học sinh đúng số placeholder.

    Ẩn danh → chỉ còn nhánh `public`, vì `faculty_id = NULL` không bao giờ đúng
    và danh sách môn rỗng thành `IN (NULL)`.
    """
    course_sql, course_params = in_clause("acc_course", identity.course_ids)

    sql = (
        f"({alias}.access = 'public'"
        f" OR ({alias}.access = 'faculty' AND {alias}.faculty_id = :acc_faculty_id)"
        f" OR ({alias}.access = 'course' AND {alias}.course_id IN ({course_sql}))"
        f" OR ({alias}.access = 'staff' AND :acc_is_staff = 1))"
    )
    params: dict = {
        "acc_faculty_id": identity.faculty_id,
        "acc_is_staff": 1 if identity.is_staff else 0,
    }
    params.update(course_params)
    return sql, params


def can_read_document(identity: Identity, document: dict) -> bool:
    """Kiểm tra lại một tài liệu đã lấy được — dùng cho endpoint tải file.

    Trùng logic với `access_clause` là cố ý: đường tải file không đi qua truy
    hồi nên phải tự kiểm, và hai chỗ kiểm độc lập thì một chỗ sai vẫn còn chỗ
    kia chặn.
    """
    access = document.get("access")
    if access == "public":
        return True
    # Quản trị viên quản lý cả kho: phải mở/xem trước được mọi tài liệu, kể cả
    # tài liệu giới hạn theo khoa (tài khoản admin thường không thuộc khoa nào
    # nên luật khoa sẽ chặn oan chính người đang quản trị nó).
    if identity.role == "admin":
        return True
    if access == "faculty":
        return (
            identity.faculty_id is not None
            and document.get("faculty_id") == identity.faculty_id
        )
    if access == "course":
        course_id = document.get("course_id")
        return course_id is not None and course_id in identity.course_ids
    if access == "staff":
        return identity.is_staff
    return False
