"""Router tầng 0 — bằng luật, 0 token.

Chặn trước LLM là kỹ thuật tiết kiệm mạnh nhất của dự án (KT-01). Tầng này bắt
những câu có cấu trúc rõ ràng — "giáo trình giải tích 1", "đề thi XSTK kỳ 1
2026", "chào bạn" — và trả kết quả mà không gọi model nào. Ước tính nuốt 50-60%
lưu lượng tra cứu.

Luật quyết định: chỉ nhận khi CHẮC CHẮN. Không chắc thì trả `confident=False`
để router LLM xử lý. Đoán sai ở đây là loại lỗi đắt nhất — mọi tầng sau vẫn
chạy "xanh" trên một ý định sai.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core import course_index
from app.schemas import DocType, Intent, RouterResult
from app.vn import vn_norm

# --- Mẫu nhận dạng ----------------------------------------------------------
# Tất cả mẫu dưới đây khớp trên chuỗi ĐÃ chuẩn hóa (bỏ dấu, chữ thường), trừ
# mẫu tên giảng viên — xem chú thích ở _extract_lecturer.

_GREETING = re.compile(
    r"^(xin chao|chao ban|chao|hello|helo|hi|hey|alo|good morning|good evening)\b"
)
_THANKS = re.compile(r"^(cam on|cam on ban|thanks|thank you|tks|tam biet|bye)\b")

# Ngoài phạm vi theo đúng mục "Ngoài phạm vi" của tài liệu thiết kế.
_OUT_OF_SCOPE = [
    re.compile(r"\bgiai (giup|ho|dum|gium)\b"),
    re.compile(r"\b(lam|giai) (bai|de) (nay|tren|sau)\b"),
    re.compile(r"\blam (giup|ho|dum|gium)\b"),
    re.compile(r"\bviet (giup|ho|dum|gium)?\s*(tieu luan|bai luan|essay|bao cao)\b"),
    re.compile(r"\bdap an (bai|cau) (nay|tren)\b"),
    re.compile(r"\b(tra cuu |xem |bao nhieu )?diem (thi|so|tong ket|trung binh) cua\b"),
    re.compile(r"\bbang diem\b"),
    re.compile(r"\bhoc phi\b"),
    re.compile(r"\bthoi khoa bieu\b"),
    re.compile(r"\bdang ky hoc phan\b"),
]

# Thứ tự có ý nghĩa: cụm dài, cụ thể đứng trước cụm ngắn, chung chung.
_DOC_TYPES: list[tuple[re.Pattern[str], DocType]] = [
    (re.compile(r"\b(tai lieu tham khao|sach tham khao|tham khao them)\b"), DocType.tai_lieu_tham_khao),
    (re.compile(r"\b(de cuong|syllabus)\b"), DocType.de_cuong),
    (re.compile(r"\b(giao trinh|textbook|sach giao khoa)\b"), DocType.giao_trinh),
    (re.compile(r"\b(slide|bai giang|powerpoint|ppt)\b"), DocType.slide),
    (re.compile(r"\b(de thi|de kiem tra|de giua ky|de cuoi ky|de thi thu|de mau)\b"), DocType.de_thi),
    (re.compile(r"\b(bai tap|bai giai|assignment|exercise)\b"), DocType.bai_tap),
    (re.compile(r"\b(quy che|quy dinh|noi quy|dieu le)\b"), DocType.quy_che),
]

# Câu kiểu "môn X có tài liệu gì" — không nêu loại nhưng ý định tra cứu rất rõ.
_GENERIC_LOOKUP = re.compile(
    r"\b(tai lieu|tai lieu gi|co gi|nhung gi|danh sach|liet ke|tim|kiem|co khong)\b"
)

# Dấu hiệu hỏi NỘI DUNG bên trong tài liệu — "quy chế thi lại quy định thế nào"
# là ask_content, không phải xin danh sách file quy chế. Chỉ nhận cụm rõ ràng;
# "gì" trần trụi không tính (dính "có tài liệu gì").
_CONTENT_QUESTION = re.compile(
    r"\b(la gi|nghia la gi|the nao|nhu the nao|ra sao|noi gi|noi ve gi"
    r"|quy dinh gi|dieu kien|trinh bay gi|noi dung|tom tat|giai thich)\b"
)

# Chữ dính liền số. KHÔNG cho phép khoảng trắng giữa chữ và số — nới ra là
# "đề thi giữa kỳ 2026" bị đọc thành mã môn "KY2026".
_COURSE_CODE = re.compile(r"\b([A-Za-z]{2,4})(\d{3,4})\b")

# '20261' = HK1 năm học 2026-2027.
_SEMESTER_CODE = re.compile(r"\b(20\d{2})([1-3])\b")
_SEMESTER_PARTS = re.compile(r"\b(?:hoc ky|hoc ki|hk|ky|ki)\s*([1-3])\b")
_YEAR = re.compile(r"\b(20\d{2})\b")

# Tên giảng viên dò trên VĂN BẢN GỐC còn dấu, không dò trên bản chuẩn hóa:
# bỏ dấu xong "cô" và "có" đều thành "co", nên "môn này có tài liệu gì" sẽ bị
# đọc thành giảng viên tên "tài liệu gì".
_LECTURER = re.compile(
    r"(?:thầy|cô|giảng viên|giáo viên)\s+([^\n,.;?!]{2,60})", re.IGNORECASE
)
_LECTURER_STOP = {"dạy", "có", "phụ", "trách", "của", "môn", "lớp", "gửi", "soạn"}


@dataclass
class RuleVerdict:
    """Kết quả tầng luật. `confident=False` nghĩa là hãy hỏi router LLM."""

    result: RouterResult
    confident: bool


def route(message: str) -> RuleVerdict:
    normalized = vn_norm(message)

    result = RouterResult(source="rule", standalone_question=message.strip())

    if _GREETING.search(normalized) or _THANKS.search(normalized):
        result.intent = Intent.greeting
        return RuleVerdict(result, confident=True)

    if any(pattern.search(normalized) for pattern in _OUT_OF_SCOPE):
        result.intent = Intent.out_of_scope
        return RuleVerdict(result, confident=True)

    doc_type = _extract_doc_type(normalized)
    course = course_index.match(message) or _course_from_code(normalized)
    semester = _extract_semester(normalized)
    lecturer = _extract_lecturer(message)

    result.doc_type = doc_type
    result.semester = semester
    result.lecturer = lecturer
    if course is not None:
        result.course_code = course.code
        result.course_hint = course.name
        result.course_id = course.id or None  # id=0 = mã bắt bằng regex, chưa chắc có trong DB

    # Hỏi nội dung thắng tra cứu: "đề cương INT2204 gồm những nội dung gì" cần
    # đọc bên trong tài liệu, không phải liệt kê file. Có manh mối phạm vi
    # (môn hoặc loại) thì đủ chắc để đi thẳng RAG, khỏi tốn router LLM.
    if _CONTENT_QUESTION.search(normalized):
        result.intent = Intent.ask_content
        return RuleVerdict(result, confident=course is not None or doc_type is not None)

    result.intent = Intent.lookup_doc

    # Quy chế/quy định là tài liệu cấp trường, không gắn môn nào — có loại tài
    # liệu là đủ chắc.
    if doc_type is DocType.quy_che:
        return RuleVerdict(result, confident=True)

    if course is not None and doc_type is not None:
        return RuleVerdict(result, confident=True)

    if course is not None and _GENERIC_LOOKUP.search(normalized):
        return RuleVerdict(result, confident=True)

    # Còn lại: có thể là hỏi nội dung, có thể là tra cứu diễn đạt lạ. Không đoán.
    return RuleVerdict(result, confident=False)


def _extract_doc_type(normalized: str) -> DocType | None:
    for pattern, doc_type in _DOC_TYPES:
        if pattern.search(normalized):
            return doc_type
    return None


def _course_from_code(normalized: str):
    """Bắt mã môn dạng MAT1093 ngay cả khi môn đó chưa có trong chỉ mục."""
    found = _COURSE_CODE.search(normalized)
    if not found:
        return None
    code = f"{found.group(1)}{found.group(2)}".upper()
    return course_index.CourseEntry(id=0, code=code, name="", faculty_id=None)


def _extract_semester(normalized: str) -> str | None:
    direct = _SEMESTER_CODE.search(normalized)
    if direct:
        return f"{direct.group(1)}{direct.group(2)}"

    part = _SEMESTER_PARTS.search(normalized)
    year = _YEAR.search(normalized)
    if part and year:
        return f"{year.group(1)}{part.group(1)}"
    return None


def _extract_lecturer(message: str) -> str | None:
    found = _LECTURER.search(message)
    if not found:
        return None

    # Tên riêng tiếng Việt viết hoa từng chữ. Lấy đúng dãy từ viết hoa liền sau
    # danh xưng; gặp từ thường đầu tiên là dừng. "cô có tài liệu" dừng ngay.
    words: list[str] = []
    for word in found.group(1).split():
        cleaned = word.strip("\"'()")
        if not cleaned or not cleaned[0].isupper() or cleaned.lower() in _LECTURER_STOP:
            break
        words.append(cleaned)
        if len(words) == 4:
            break

    return " ".join(words) if words else None
