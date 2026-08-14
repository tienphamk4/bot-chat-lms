"""Ma trận quyền — NFR cứng: rò rỉ tài liệu ngoài quyền = 0.

`can_read_document` là hàm thuần nên ma trận đầy đủ chạy không cần CSDL:
4 mức access × 5 danh tính (ẩn danh, SV đúng khoa, SV khác khoa, SV có lớp,
cán bộ). `access_clause` kiểm cấu trúc SQL + tham số sinh ra.
"""

import pytest

from app.core.access import ANONYMOUS, Identity, access_clause, can_read_document
from app.db import in_clause

# --- Danh tính mẫu -----------------------------------------------------------

SV_TOAN = Identity(user_id=1, role="student", faculty_id=2, course_ids=(10, 11))
SV_CNTT = Identity(user_id=2, role="student", faculty_id=1, course_ids=(20,))
GV_TOAN = Identity(user_id=3, role="lecturer", faculty_id=2, course_ids=())
ADMIN = Identity(user_id=4, role="admin", faculty_id=None, course_ids=())


def doc(access: str, faculty_id: int | None = None, course_id: int | None = None) -> dict:
    return {"access": access, "faculty_id": faculty_id, "course_id": course_id}


class TestMatrix:
    """Từng ô của ma trận. True = được xem."""

    MATRIX = [
        # (tài liệu, danh tính, được xem?)
        (doc("public"), ANONYMOUS, True),
        (doc("public"), SV_TOAN, True),
        (doc("public"), ADMIN, True),

        (doc("faculty", faculty_id=2), ANONYMOUS, False),
        (doc("faculty", faculty_id=2), SV_TOAN, True),    # đúng khoa
        (doc("faculty", faculty_id=2), SV_CNTT, False),   # khác khoa
        (doc("faculty", faculty_id=2), GV_TOAN, True),    # GV cùng khoa
        (doc("faculty", faculty_id=2), ADMIN, True),      # admin quản cả kho: xem được mọi khoa

        (doc("course", course_id=10), ANONYMOUS, False),
        (doc("course", course_id=10), SV_TOAN, True),     # có trong lớp
        (doc("course", course_id=12), SV_TOAN, False),    # cùng khoa nhưng không có lớp
        (doc("course", course_id=10), SV_CNTT, False),
        (doc("course", course_id=10), GV_TOAN, False),    # GV cũng phải có lớp

        (doc("staff"), ANONYMOUS, False),
        (doc("staff"), SV_TOAN, False),
        (doc("staff"), GV_TOAN, True),
        (doc("staff"), ADMIN, True),
    ]

    @pytest.mark.parametrize(("document", "identity", "allowed"), MATRIX)
    def test_cell(self, document, identity, allowed):
        assert can_read_document(identity, document) is allowed

    def test_unknown_access_denied(self):
        # Giá trị access lạ (dữ liệu hỏng) phải chặn, không mở — kiểm bằng SV
        # (admin có đường mở riêng, xem test_admin_xem_duoc_moi_tai_lieu)
        assert can_read_document(SV_TOAN, doc("something_new")) is False
        assert can_read_document(ANONYMOUS, doc("something_new")) is False

    def test_admin_xem_duoc_moi_tai_lieu(self):
        # Trang quản trị phải mở/xem trước được tài liệu giới hạn theo khoa —
        # tài khoản admin thường không thuộc khoa nào nên luật khoa từng chặn
        # oan chính người đang quản trị kho.
        assert can_read_document(ADMIN, doc("faculty", faculty_id=99)) is True
        assert can_read_document(ADMIN, doc("course", course_id=77)) is True

    def test_truy_hoi_van_loc_quyen_cho_sinh_vien(self):
        # Luật mở cho admin KHÔNG được nới cho vai trò khác
        assert can_read_document(GV_TOAN, doc("faculty", faculty_id=99)) is False
        assert can_read_document(SV_CNTT, doc("course", course_id=77)) is False


class TestAccessClause:
    def test_anonymous_params_close_everything(self):
        sql, params = access_clause(ANONYMOUS)
        assert params["acc_faculty_id"] is None
        assert params["acc_is_staff"] == 0
        # danh sách môn rỗng → IN (NULL): không bao giờ đúng
        assert "IN (NULL)" in sql

    def test_student_params(self):
        sql, params = access_clause(SV_TOAN)
        assert params["acc_faculty_id"] == 2
        assert params["acc_is_staff"] == 0
        assert params["acc_course_0"] == 10
        assert params["acc_course_1"] == 11
        assert ":acc_course_0, :acc_course_1" in sql

    def test_all_four_levels_present(self):
        sql, _ = access_clause(SV_TOAN)
        for level in ("'public'", "'faculty'", "'course'", "'staff'"):
            assert level in sql

    def test_no_string_interpolation_of_values(self):
        # Giá trị không bao giờ được nằm trong SQL — chỉ placeholder
        sql, _ = access_clause(SV_TOAN)
        assert "10" not in sql
        assert "11" not in sql

    def test_alias_applied(self):
        sql, _ = access_clause(SV_TOAN, alias="x")
        assert "x.access" in sql
        assert "d.access" not in sql


class TestInClause:
    def test_generates_exact_placeholders(self):
        sql, params = in_clause("doc", [4, 7, 9])
        assert sql == ":doc_0, :doc_1, :doc_2"
        assert params == {"doc_0": 4, "doc_1": 7, "doc_2": 9}

    def test_empty_fails_closed(self):
        sql, params = in_clause("doc", [])
        assert sql == "NULL"
        assert params == {}
