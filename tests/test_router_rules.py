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
            "học phí kỳ này bao nhiêu",
            "thời khóa biểu tuần sau",
        ],
    )
    def test_out_of_scope(self, message: str):
        verdict = route(message)
        assert verdict.confident
        assert verdict.result.intent is Intent.out_of_scope


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
