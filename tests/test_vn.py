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


class TestFtBacNoiDan:
    """Bốn bậc nới dần: and -> and_content -> or_content -> or."""

    def test_bo_hu_tu_cuu_cau_hoi_tu_nhien(self):
        # Bậc AND trượt chỉ vì chữ "gì" và "những" không có trong tài liệu.
        q = "ngành An toàn thông tin học những môn gì"
        assert ft_boolean_query(q) == "+nganh +an +toan +thong +tin +hoc +nhung +mon +gi"
        assert ft_boolean_query(q, mode="and_content") == "+nganh +an +toan +thong +tin +hoc +mon"

    def test_or_content_khong_con_hu_tu(self):
        got = ft_boolean_query("ngành nào của trường", mode="or_content")
        assert "nao" not in got.split() and "cua" not in got.split()

    def test_cau_qua_ngan_thi_khong_loc(self):
        # Lọc hết thì chẳng còn gì để tìm — thà giữ nguyên.
        assert ft_boolean_query("là gì", mode="and_content") == "+la +gi"

    def test_cum_hoi_bo_theo_cum(self):
        # "bao nhiêu" bỏ được, nhưng "báo cáo" phải giữ — hai cụm cùng dạng
        # bỏ dấu là "bao", nên chỉ bỏ theo CỤM mới an toàn.
        assert ft_boolean_query("học phí bao nhiêu", mode="and_content") == "+hoc +phi"
        assert "bao" in ft_boolean_query("báo cáo tuyển sinh", mode="and_content")


class TestBayTrungAmKhiBoDau:
    """Hư từ và từ có nghĩa đụng nhau sau khi bỏ dấu — bộ lọc không được ăn
    mất từ có nghĩa. Mỗi ca dưới đây là một chữ suýt bị loại nhầm."""

    def test_giu_tu_co_nghia_bi_trung_am_voi_hu_tu(self):
        cases = {
            "đề thi cuối kỳ": "thi",              # thi <-> thì
            "cơ hội nghề nghiệp": "co",           # cơ <-> có
            "tài liệu tham khảo": "tai",          # tài <-> tại
            "mã ngành tuyển sinh": "ma",          # mã <-> mà
            "chỉ tiêu tuyển sinh": "chi",         # chỉ <-> chi
            "chương trình tiếng Anh": "anh",      # Anh <-> anh (đại từ)
            "ngành trí tuệ nhân tạo AI": "ai",    # AI <-> ai
            "nền tảng kiến thức": "nen",          # nền <-> nên
            "kết quả học tập": "qua",             # quả <-> qua
            "trình độ đại học": "do",             # độ <-> do
            "ngoại ngữ bắt buộc": "ngoai",        # ngoại <-> ngoài
            "cơ khí động lực": "khi",             # khí <-> khi
            "nhu cầu nhân lực": "nhu",            # nhu <-> như
            "trọng tâm chương trình": "trong",    # trọng <-> trong
        }
        for question, must_keep in cases.items():
            got = ft_boolean_query(question, mode="and_content").split()
            assert f"+{must_keep}" in got, f"{question!r} mất chữ {must_keep!r}"


class TestFtPhraseQueries:
    """Bậc tìm theo CỤM — bù cho việc BOOLEAN MODE không có IDF."""

    def test_cum_nhieu_tu_dung_truoc(self):
        from app.vn import ft_phrase_queries

        # "chương trình" là cụm chung router hay kèm thêm; "Kinh tế số" mới là
        # cụm riêng. Chọn theo SỐ TỪ, không theo độ dài chuỗi — "chuong trinh"
        # dài hơn "kinh te so" về ký tự nhưng ít từ hơn.
        assert ft_phrase_queries(["chương trình", "Kinh tế số"]) == [
            '+"kinh te so"', '+"chuong trinh"'
        ]

    def test_bo_cum_mot_tu(self):
        from app.vn import ft_phrase_queries

        assert ft_phrase_queries(["gì", "học"]) == []
        assert ft_phrase_queries([]) == []
        assert ft_phrase_queries(None) == []

    def test_dau_hoi_khong_lot_vao_cum(self):
        from app.vn import ft_phrase_queries

        assert ft_phrase_queries(["Kinh tế số?"]) == ['+"kinh te so"']

    def test_khong_lap_cum_trung(self):
        from app.vn import ft_phrase_queries

        assert ft_phrase_queries(["Kinh tế số", "kinh tế số"]) == ['+"kinh te so"']


class TestDauCauKhongLotVaoTuKhoa:
    """Câu router viết lại gần như luôn kết thúc bằng "?" — token cuối phải là
    "gi" chứ không phải "gi?", nếu không hư từ không bị lọc và còn thành một từ
    khóa rác."""

    def test_bo_dau_hoi_cuoi_cau(self):
        assert ft_boolean_query("Kinh tế số học những gì?", mode="and_content") == (
            "+kinh +te +so +hoc"
        )

    def test_giu_nguyen_so_hieu_van_ban(self):
        assert "2024.10.01.309" in ft_boolean_query("Số: 2024.10.01.309")

    def test_giu_nguyen_diem_thap_phan(self):
        assert "+4_0" in ft_boolean_query("điểm 4,0 là đạt")
