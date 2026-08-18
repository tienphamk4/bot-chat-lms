"""Router tầng 0 — nhận đúng khi chắc chắn, nhường LLM khi mơ hồ.

Đoán sai ở tầng này là loại lỗi đắt nhất của hệ thống (mọi tầng sau vẫn "xanh"
trên ý định sai), nên test khẳng định cả hai chiều: câu rõ phải bắt được, câu
mơ hồ phải trả confident=False.
"""

import pytest

from app.core.router_rules import route
from app.schemas import DocType, Intent

COURSES = [
    {
        "id": 1, "code": "MAT1093", "name": "Giải tích 1", "faculty_id": 2,
        "phrases": ["mat1093", "giai tich 1", "gt1"],
    },
    {
        "id": 2, "code": "MAT1101", "name": "Xác suất thống kê", "faculty_id": 2,
        "phrases": ["mat1101", "xac suat thong ke", "xstk"],
    },
]


@pytest.fixture(autouse=True)
def _catalog(course_catalog):
    course_catalog(COURSES)


class TestGreetingAndScope:
    def test_greeting(self):
        verdict = route("chào bạn")
        assert verdict.confident
        assert verdict.result.intent is Intent.greeting

    def test_thanks(self):
        verdict = route("cảm ơn nhé")
        assert verdict.confident
        assert verdict.result.intent is Intent.greeting

    @pytest.mark.parametrize(
        "message",
        [
            "giải giúp mình bài 3 trang 20",
            "viết hộ tiểu luận môn triết",
        ],
    )
    def test_out_of_scope(self, message: str):
        """Chính sách sản phẩm — chặn với mọi bot, không cần cấu hình gì."""
        verdict = route(message)
        assert verdict.confident
        assert verdict.result.intent is Intent.out_of_scope

    @pytest.mark.parametrize(
        "message", ["học phí kỳ này bao nhiêu", "thời khóa biểu tuần sau"]
    )
    def test_chu_de_chan_theo_bot(self, message: str):
        """Hai câu này CHỈ ngoài phạm vi khi bot khai chặn — xem
        TestBlockedTopicsTheoBot. Không khai thì router không được tự chặn."""
        assert route(message).result.intent is not Intent.out_of_scope
        blocked = ("hoc phi", "thoi khoa bieu")
        assert route(message, blocked).result.intent is Intent.out_of_scope


class TestConfidentLookup:
    def test_alias_plus_type(self):
        verdict = route("giáo trình gt1 có không")
        assert verdict.confident
        assert verdict.result.intent is Intent.lookup_doc
        assert verdict.result.course_code == "MAT1093"
        assert verdict.result.doc_type is DocType.giao_trinh

    def test_course_name_plus_type(self):
        verdict = route("cho mình xin slide xác suất thống kê")
        assert verdict.confident
        assert verdict.result.course_code == "MAT1101"
        assert verdict.result.doc_type is DocType.slide

    def test_code_plus_type_unknown_course(self):
        # Mã môn chưa có trong CSDL vẫn bắt được bằng regex
        verdict = route("đề thi INT9999 kỳ trước")
        assert verdict.confident
        assert verdict.result.course_code == "INT9999"
        assert verdict.result.doc_type is DocType.de_thi

    def test_generic_lookup_with_course(self):
        verdict = route("môn giải tích 1 có tài liệu gì")
        assert verdict.confident
        assert verdict.result.course_code == "MAT1093"

    def test_quy_che_without_course(self):
        verdict = route("cho xin quy chế đào tạo")
        assert verdict.confident
        assert verdict.result.doc_type is DocType.quy_che

    def test_semester_extraction(self):
        verdict = route("đề thi gt1 học kỳ 1 năm 2026")
        assert verdict.confident
        assert verdict.result.semester == "20261"

    def test_longest_phrase_wins(self):
        # "giải tích 1" phải thắng cụm ngắn hơn nếu có
        verdict = route("giáo trình giải tích 1")
        assert verdict.result.course_code == "MAT1093"


class TestDefersToLLM:
    @pytest.mark.parametrize(
        "message",
        [
            "môn nào học về đạo hàm",                   # không rõ môn
            "tài liệu hay cho người mới",               # không môn không loại
            "gt1",                                       # chỉ mỗi tên môn
            "đạo hàm là gì",                             # hỏi nội dung nhưng không manh mối phạm vi
        ],
    )
    def test_ambiguous_not_confident(self, message: str):
        assert not route(message).confident

    @pytest.mark.parametrize(
        "message",
        [
            "quy chế thi lại quy định thế nào",
            "chương 3 giáo trình nói gì về giới hạn",
            "điều kiện dự thi theo quy chế",
        ],
    )
    def test_content_question_confident_ask_content(self, message: str):
        # GĐ2: câu hỏi nội dung có manh mối phạm vi (loại/môn) → đi thẳng RAG,
        # không tốn router LLM, và tuyệt đối không được nhận nhầm là lookup.
        verdict = route(message)
        assert verdict.result.intent.value == "ask_content"
        assert verdict.confident

    def test_lecturer_not_misparsed_from_co(self):
        # "có" sau khi bỏ dấu trùng "cô" — không được đọc thành giảng viên
        verdict = route("môn giải tích 1 có tài liệu gì")
        assert verdict.result.lecturer is None

    def test_lecturer_extracted_from_original(self):
        verdict = route("slide gt1 của thầy Nguyễn Văn An")
        assert verdict.result.lecturer == "Nguyễn Văn An"


class TestAbusive:
    """Ngôn từ xúc phạm — nhánh riêng, trả câu từ chối, 0 token."""

    @pytest.mark.parametrize(
        "message",
        [
            "dm cái bot này",
            "đồ ngu",
            "thằng ngu",
            "óc chó",
            "vcl sao tìm không ra",
            "địt mẹ",
            "cút đi",
            "chào bạn, đồ ngu",  # chửi kèm lời chào vẫn là chửi
            "mẹ mày béo",        # réo người thân
            # Chửi bằng tiếng Anh cũng là chửi — bản đầu chỉ dò tiếng Việt nên
            # "fuck you" rơi xuống nhánh ngoài-phạm-vi rồi nhận lại câu
            # "em chưa có thông tin trong tài liệu tuyển sinh".
            "fuck you",
            "fuckyou",
            "what the fuck",
            "you are an idiot",
            "shit bot",
            "stupid bot",
            "ok mày là đồ ngu trường ăn cứt",
        ],
    )
    def test_abusive_detected(self, message: str):
        verdict = route(message)
        assert verdict.result.intent.value == "abusive"
        assert verdict.confident

    @pytest.mark.parametrize(
        "message",
        [
            # Bỏ dấu là va chạm: "lớn" -> "lon", "buổi" -> "buoi".
            "tài liệu nào có dung lượng lớn nhất",
            "slide buổi học hôm nay",
            "đề cương môn cơ sở dữ liệu lớn",
            "cho mình xin tài liệu buổi 3",
            "cắt giảm chỉ tiêu tuyển sinh",   # "cắt" -> "cat", không phải "cứt"
            "đeo thẻ sinh viên khi vào thi",  # "đeo" -> "deo", không phải "đéo"
        ],
    )
    def test_no_false_positive_on_diacritic_collision(self, message: str):
        verdict = route(message)
        assert verdict.result.intent.value != "abusive"


class TestClarify:
    """Câu cụt lủn — hỏi lại cho rõ, không đoán bừa."""

    @pytest.mark.parametrize(
        "message",
        ["là gì", "là gì?", "như thế nào", "cái đó là gì", "nó ra sao", "sao vậy", "gì"],
    )
    def test_vague_asks_back(self, message: str):
        verdict = route(message)
        assert verdict.result.intent.value == "clarify"
        assert verdict.confident

    @pytest.mark.parametrize(
        "message",
        [
            # Chỉ cần MỘT chữ có nghĩa là thoát khỏi luật hỏi-lại.
            "kiểm thử hồi quy là gì",
            "quy chế thi lại quy định thế nào",
            "INT3115 có tài liệu gì",
            "nội dung môn này là gì",
        ],
    )
    def test_question_with_subject_not_clarify(self, message: str):
        assert route(message).result.intent.value != "clarify"


class TestKiemCollision:
    """"kiếm" (tìm) và "kiểm" (kiểm thử) trùng nhau sau khi bỏ dấu."""

    @pytest.mark.parametrize(
        "message",
        ["kiểm thử hồi quy", "quy trình kiểm thử phần mềm", "kiểm tra chất lượng phần mềm"],
    )
    def test_kiem_thu_not_forced_to_lookup(self, message: str):
        # Từng bị chốt lookup_doc rồi trả về danh sách file thay vì trả lời nội
        # dung — vì "kiểm" khớp mẫu vốn dành cho "tìm kiếm".
        verdict = route(message)
        assert not (verdict.result.intent.value == "lookup_doc" and verdict.confident)

    def test_tim_kiem_still_lookup(self):
        verdict = route("tìm kiếm tài liệu INT3115")
        assert verdict.result.intent.value == "lookup_doc"
        assert verdict.confident


class TestBlockedTopicsTheoBot:
    """"Ngoài phạm vi" là dữ liệu của từng bot, không phải hằng số trong mã.

    "Học phí" ngoài việc của bot tra cứu tài liệu, nhưng là câu hỏi trung tâm
    của bot tư vấn tuyển sinh. Chặn cứng trong mã thì nhân bản bot mới lại phải
    sửa Python — đúng thứ thiết kế này tránh.
    """

    DOCS = ("hoc phi", "thoi khoa bieu", "dang ky hoc phan")

    def test_bot_tai_lieu_chan_hoc_phi(self):
        assert route("học phí ngành CNTT bao nhiêu", self.DOCS).result.intent.value == "out_of_scope"

    def test_bot_tuyen_sinh_nhan_hoc_phi(self):
        assert route("học phí ngành CNTT bao nhiêu", ()).result.intent.value != "out_of_scope"

    @pytest.mark.parametrize(
        "message",
        ["giải giúp mình bài 3 trang 20", "xem bảng điểm của tôi", "viết giúp tiểu luận"],
    )
    def test_chinh_sach_san_pham_chan_voi_moi_bot(self, message: str):
        # Không bot nào được phép nhận: làm bài hộ, điểm cá nhân.
        assert route(message, ()).result.intent.value == "out_of_scope"


class TestBaLuatChacQuaSom:
    """Ba luật từng tự nhận "chắc chắn" quá sớm, cắt đường trước cả bước tìm kiếm.

    Cả ba đều đã gặp thật khi chạy tay, không phải giả định.
    """

    # --- (1) Lời chào ở đầu câu nuốt luôn phần hỏi -------------------------
    @pytest.mark.parametrize(
        "message",
        ["chào bạn", "chào bạn nhé", "xin chào", "cảm ơn bạn", "hello", "chào ạ"],
    )
    def test_chi_la_loi_chao_thi_van_la_chao(self, message: str):
        verdict = route(message)
        assert verdict.result.intent is Intent.greeting
        assert verdict.confident

    @pytest.mark.parametrize(
        "message",
        [
            "chào bạn, cho mình hỏi slide kiểm thử phần mềm",
            "xin chào, trường có những ngành nào",
            "hello mình cần giáo trình giải tích 1",
        ],
    )
    def test_chao_kem_cau_hoi_thi_khong_phai_chao(self, message: str):
        verdict = route(message)
        assert verdict.result.intent is not Intent.greeting

    def test_lot_loi_chao_de_lam_cau_hoi_doc_lap(self):
        """Phần sau lời chào trở thành câu hỏi cho các bước tiếp — nếu không,
        bước truy hồi vẫn mang theo chữ "chào bạn" làm nhiễu từ khóa."""
        verdict = route("chào bạn, cho mình hỏi slide kiểm thử phần mềm")
        assert "chào" not in verdict.result.standalone_question.lower()
        assert "slide" in verdict.result.standalone_question.lower()

    # --- (2) Ngoài phạm vi khớp bất kỳ đâu trong câu -----------------------
    def test_cau_xin_tai_lieu_ve_chu_de_bi_chan_thi_khong_chot_cung(self):
        """Có dấu hiệu đang xin tài liệu thì nhường router LLM phán, thà tốn
        một lượt gọi model còn hơn từ chối oan."""
        verdict = route("có tài liệu nào nói về hạch toán học phí không", ("hoc phi",))
        assert not verdict.confident

    def test_hoi_thang_chu_de_bi_chan_thi_van_chot(self):
        verdict = route("học phí kỳ này bao nhiêu", ("hoc phi",))
        assert verdict.result.intent is Intent.out_of_scope
        assert verdict.confident

    # --- (3) Chữ "quy định" đủ để chốt loại văn bản ------------------------
    @pytest.mark.parametrize(
        "message",
        ["quy định đặt tên biến trong lập trình", "quy định về kiểu dữ liệu"],
    )
    def test_quy_dinh_chuyen_mon_thi_khong_chot(self, message: str):
        assert not route(message).confident

    @pytest.mark.parametrize(
        "message",
        [
            "cho xin quy chế đào tạo",
            "nội quy ký túc xá",
            "điều lệ trường",
            "quy định của trường về thi lại",
            "quy định về học bổng",
        ],
    )
    def test_van_ban_cap_truong_thi_van_chot(self, message: str):
        verdict = route(message)
        assert verdict.result.doc_type is DocType.quy_che
        assert verdict.confident
