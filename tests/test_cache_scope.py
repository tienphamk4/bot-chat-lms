"""Chọn phạm vi cache — chỗ dễ rò rỉ nhất vì lỗi ở đây im lặng."""

from app.core.access import Identity
from app.core.cache import NO_FACULTY, scope_for

SV_TOAN = Identity(user_id=1, role="student", faculty_id=2, course_ids=(10,))
GV = Identity(user_id=3, role="lecturer", faculty_id=2)
ANON = Identity()


def docs(*specs: tuple[str, int | None]) -> list[dict]:
    return [{"access": access, "faculty_id": fid} for access, fid in specs]


class TestScopeFor:
    def test_all_public_is_shared(self):
        assert scope_for(docs(("public", None)), ANON) == ("public", NO_FACULTY)

    def test_faculty_docs_scoped_to_faculty(self):
        result = scope_for(docs(("public", None), ("faculty", 2)), SV_TOAN)
        assert result == ("faculty", 2)

    def test_faculty_mismatch_not_cached(self):
        # Kết quả lẫn tài liệu của khoa khác (không nên xảy ra, nhưng nếu xảy
        # ra) thì tuyệt đối không cache
        assert scope_for(docs(("faculty", 2), ("faculty", 3)), SV_TOAN) is None

    def test_anonymous_with_faculty_docs_not_cached(self):
        assert scope_for(docs(("faculty", 2)), ANON) is None

    def test_course_docs_never_cached(self):
        # Hai SV cùng khoa vẫn khác danh sách lớp — không có phạm vi chung an toàn
        result = scope_for(docs(("public", None), ("course", None)), SV_TOAN)
        assert result is None

    def test_staff_docs_scoped_to_staff(self):
        assert scope_for(docs(("staff", None)), GV) == ("staff", NO_FACULTY)

    def test_course_beats_staff(self):
        # Có cả course lẫn staff: course thắng (nghĩa là không cache)
        assert scope_for(docs(("staff", None), ("course", None)), GV) is None

    def test_empty_result_is_public(self):
        # Không có tài liệu nào (câu "không tìm thấy") cache theo public
        assert scope_for([], SV_TOAN) == ("public", NO_FACULTY)
