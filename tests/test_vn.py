"""vn_norm là nền của mọi cột _norm — sai ở đây là sai toàn hệ thống."""

import pytest

from app.vn import ft_boolean_query, pipe_join, pipe_like, pipe_split, vn_norm


class TestVnNorm:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Giải tích 1", "giai tich 1"),
            ("ĐỀ THI", "de thi"),
            # v2: ký tự trang trí (– — • …) fold về khoảng trắng
            ("Xác suất – Thống kê", "xac suat thong ke"),
            # v2: token kỹ thuật được bảo toàn qua term-map
            ("lập trình C++ nâng cao", "lap trinh cplusplus nang cao"),
            # v2: số thập phân cô lập nối thành một token; chuỗi số dài giữ nguyên
            ("điểm 4,0 là đạt", "diem 4_0 la dat"),
            ("Số 2024.10.01.309", "so 2024.10.01.309"),
            # v2: ZWSP tách từ, soft-hyphen xóa hẳn
            ("học​kỳ", "hoc ky"),
            ("infor­mation", "information"),
            # v2: ligature + full-width qua NFKD
            ("ﬁle （PDF）", "file (pdf)"),
            ("đường Đại học", "duong dai hoc"),
            ("  nhiều   khoảng   trắng  ", "nhieu khoang trang"),
            ("MAT1093", "mat1093"),
            ("Nguyễn Văn Ưởng", "nguyen van uong"),
            ("LẬP TRÌNH HƯỚNG ĐỐI TƯỢNG", "lap trinh huong doi tuong"),
            ("", ""),
        ],
    )
    def test_normalizes(self, raw: str, expected: str):
        assert vn_norm(raw) == expected

    def test_none_is_empty(self):
        assert vn_norm(None) == ""

    def test_idempotent(self):
        once = vn_norm("Giải tích nâng cao – Chương 3")
        assert vn_norm(once) == once


class TestPipeColumns:
    def test_join_normalizes_and_dedupes(self):
        assert pipe_join(["GT1", "Giải tích 1", "gt1"]) == "|gt1|giai tich 1|"

    def test_empty_never_matches(self):
        # '' chứ không phải '|': LIKE '%|x|%' không được khớp chuỗi rỗng.
        assert pipe_join([]) == ""
        assert pipe_join(["", "  "]) == ""

    def test_roundtrip(self):
        values = ["XSTK", "xác suất"]
        assert pipe_split(pipe_join(values)) == ["xstk", "xac suat"]

    def test_like_exact_element(self):
        # 'gt1' không được khớp nhầm 'gt10'
        column = pipe_join(["GT10"])
        assert pipe_like("gt1").strip("%") not in column


class TestFtBooleanQuery:
    def test_and_mode_prefixes_plus(self):
        assert ft_boolean_query("Đề thi giữa kỳ") == "+de +thi +giua +ky"

    def test_strips_boolean_operators(self):
        # Ký tự cú pháp người dùng gõ vào phải bị loại; dấu + duy nhất còn lại
        # là tiền tố do chính hàm thêm ở đầu mỗi từ.
        result = ft_boolean_query('đề thi (giữa kỳ) +2026 -"nháp"')
        tokens = result.split()
        assert all(tok.startswith("+") for tok in tokens)
        assert not any(ch in tok[1:] for tok in tokens for ch in '+-()"~*<>@')

    def test_empty_input(self):
        assert ft_boolean_query("(((") == ""
        assert ft_boolean_query("") == ""
