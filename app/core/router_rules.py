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

# Phần còn lại sau khi lột lời chào ở đầu câu. Nếu vẫn còn chữ có nghĩa thì đây
# KHÔNG phải câu chào mà là câu hỏi có mở đầu lịch sự.
#
# Vì sao cần: bản cũ khớp lời chào ở ĐẦU câu rồi chốt luôn, nên
# "Chào bạn, cho mình hỏi slide kiểm thử phần mềm" chỉ nhận lại câu chào — phần
# hỏi thật bị vứt, không có bước tìm kiếm nào chạy. Bỏ chữ "chào bạn" đi thì
# đúng câu đó trả về 3 slide trong 0,85 giây.
_GREETING_STRIP = re.compile(
    r"^(xin chao|chao ban|chao cac ban|chao|hello|helo|hi|hey|alo"
    r"|good morning|good evening|cam on ban|cam on|thanks|thank you|tks)\b"
    r"[\s,.:;!-]*",
    re.IGNORECASE,
)
# Bản CÒN DẤU, dùng để cắt trên văn bản gốc. Không dùng lại mẫu trên được:
# nó khớp chuỗi đã bỏ dấu nên "Chào bạn" không khớp "chao ban", mà vn_norm còn
# đổi cả độ dài chuỗi (bỏ dấu tổ hợp) nên cũng không quy được vị trí cắt.
_GREETING_STRIP_RAW = re.compile(
    r"^(xin chào|chào bạn|chào các bạn|chào|xin chao|chao ban|chao"
    r"|hello|helo|hi|hey|alo|good morning|good evening"
    r"|cảm ơn bạn|cảm ơn|cam on ban|cam on|thanks|thank you|tks)\b"
    r"[\s,.:;!-]*",
    re.IGNORECASE,
)
# Hư từ còn sót sau lời chào — "chào bạn nhé ạ" vẫn chỉ là lời chào.
_GREETING_FILLER = {
    "a", "ban", "cac", "ban oi", "oi", "nhe", "nha", "voi", "em", "minh",
    "anh", "chi", "moi nguoi", "ad", "shop", "!", "?", ".",
}


def _strip_greeting(normalized: str) -> str:
    """Trả phần còn lại sau lời chào; rỗng nghĩa là cả câu chỉ là lời chào."""
    rest = _GREETING_STRIP.sub("", normalized).strip(" ,.!?;:-")
    if not rest:
        return ""
    tokens = [t for t in rest.split() if t not in _GREETING_FILLER]
    return " ".join(tokens)

# Ngôn từ xúc phạm. Hai danh sách vì bỏ dấu gây va chạm chết người:
# "tài liệu lớn" -> "tai lieu lon", "buổi học" -> "buoi hoc". Nhóm từ đơn dễ
# đụng hàng vì thế phải dò trên VĂN BẢN CÒN DẤU; nhóm viết tắt và cụm nhiều từ
# thì dò trên bản chuẩn hóa cho bắt được cả biến thể không dấu người dùng gõ.
_ABUSIVE_NORM = re.compile(
    # Viết tắt tiếng Việt
    r"\b(dm|dmm|dkm|dcm|vcl|vkl|clgt)\b"
    r"|\bdu (ma|me|cha|bo)\b"
    r"|\b(do|thang|con) (ngu|cho|kho|dien|cho de)\b"
    r"|\b(oc cho|mat day|khon nan|cut di|im mom|vai lon)\b"
    r"|\bngu nhu (cho|bo)\b"
    # Réo người thân — "mẹ mày béo" từng lọt qua vì không có nhóm này.
    r"|\b(me|bo|cha|ong noi|ba noi) may\b"
    # Tiếng Anh. Người dùng chửi bằng tiếng Anh cũng là chửi; bản đầu chỉ dò
    # tiếng Việt nên "fuck you" rơi xuống nhánh ngoài-phạm-vi và nhận lại câu
    # "em chưa có thông tin trong tài liệu tuyển sinh" — vô duyên.
    # f+u+c+k bắt luôn fuck / fuuuck / fuckk / fucking / fuckyou.
    r"|\bf+u+c+k"
    r"|\b(shit|bitch|asshole|bastard|cunt|dickhead|motherfucker|stfu)\b"
    r"|\b(idiot|moron|retard|jerk)\b"
    r"|\b(stupid|dumb) (bot|ai|assistant|machine)\b"
)
# Còn dấu thì các từ này không lẫn vào đâu được — bỏ dấu là đụng "cut" (cắt),
# "deo" (đeo), "lon" (lớn), nên nhóm này chỉ dò trên văn bản gốc.
_ABUSIVE_RAW = re.compile(
    r"\b(đụ|địt|lồn|buồi|cặc|cứt|đéo)\b", re.IGNORECASE
)

# Ngoài phạm vi với MỌI bot — đây là chính sách sản phẩm, không phải cấu hình:
# hệ thống không làm bài hộ và không đụng tới điểm cá nhân, bất kể bot nào.
#
# Những chủ đề chỉ SAI VỚI MỘT SỐ BOT — học phí, thời khóa biểu, đăng ký học
# phần — đã chuyển sang cột `blocked_topics` của từng bot: học phí là ngoài
# việc của bot tra cứu tài liệu, nhưng lại là câu hỏi trung tâm của bot tuyển
# sinh. Chặn cứng ở đây là nhân bản bot mới lại phải sửa Python.
_OUT_OF_SCOPE = [
    re.compile(r"\bgiai (giup|ho|dum|gium)\b"),
    re.compile(r"\b(lam|giai) (bai|de) (nay|tren|sau)\b"),
    re.compile(r"\blam (giup|ho|dum|gium)\b"),
    re.compile(r"\bviet (giup|ho|dum|gium)?\s*(tieu luan|bai luan|essay|bao cao)\b"),
    re.compile(r"\bdap an (bai|cau) (nay|tren)\b"),
    re.compile(r"\b(tra cuu |xem |bao nhieu )?diem (thi|so|tong ket|trung binh) cua\b"),
    re.compile(r"\bbang diem\b"),
]

# Câu đang đi TÌM TÀI LIỆU, dù có chứa từ khóa thuộc nhóm ngoài phạm vi.
# "có tài liệu nào nói về hạch toán học phí không" là câu xin tài liệu, không
# phải câu đòi tra học phí cá nhân — bản cũ chặn thẳng vì thấy chữ "học phí".
_DOC_SEEKING = re.compile(
    r"\b(tai lieu|giao trinh|slide|bai giang|de cuong|de thi|bai tap|quy che"
    r"|quy dinh|van ban|tham khao|danh sach|liet ke|tim kiem"
    r"|co .{0,20}khong|nao noi ve|noi ve)\b"
)

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

# "quy chế / nội quy / điều lệ" chỉ có một nghĩa: văn bản của trường.
# "quy định" thì KHÔNG — nó vừa là tên loại văn bản, vừa là từ thường ngày
# ("quy định đặt tên biến trong lập trình"). Chốt cứng theo nó là đẩy người hỏi
# đi lục văn bản hành chính trong khi họ đang hỏi chuyên môn.
_QUY_CHE_MANH = re.compile(r"\b(quy che|noi quy|dieu le)\b")
# "quy định" chỉ đủ chắc khi đi kèm dấu hiệu văn bản cấp trường.
_QUY_CHE_BOI_CANH = re.compile(
    r"\b(cua truong|nha truong|dao tao|hoc vu|thi cu|thi lai|tot nghiep|hoc bong"
    r"|ky luat|tuyen sinh|van ban|ban hanh|quyet dinh|sinh vien)\b"
)

# Câu kiểu "môn X có tài liệu gì" — không nêu loại nhưng ý định tra cứu rất rõ.
# "kiem" ĐÃ BỊ BỎ khỏi danh sách: bỏ dấu thì "kiếm" (tìm kiếm) trùng "kiểm"
# (kiểm thử, kiểm tra), nên "kiểm thử hồi quy" bị nhận là câu đi tìm file và
# trả về danh sách slide thay vì trả lời nội dung. Bắt ý tìm kiếm thì dùng cả cụm.
_GENERIC_LOOKUP = re.compile(
    r"\b(tai lieu|tai lieu gi|co gi|nhung gi|danh sach|liet ke|tim kiem|tim|co khong)\b"
)

# Dấu hiệu hỏi NỘI DUNG bên trong tài liệu — "quy chế thi lại quy định thế nào"
# là ask_content, không phải xin danh sách file quy chế. Chỉ nhận cụm rõ ràng;
# "gì" trần trụi không tính (dính "có tài liệu gì").
_CONTENT_QUESTION = re.compile(
    r"\b(la gi|nghia la gi|the nao|nhu the nao|ra sao|noi gi|noi ve gi"
    r"|quy dinh gi|dieu kien|trinh bay gi|noi dung|tom tat|giai thich"
    # Nhóm "kể cho tôi nghe về X" — người dùng thật hay hỏi kiểu này, mà bản
    # đầu không có nên câu "Tôi muốn biết về các ngành học trong khoa CNTT" bị
    # xếp thành câu đi tìm file. Nhánh dự phòng khớp được chữ "ngành" trong
    # tiêu đề rồi dừng, nên RAG không bao giờ chạy và người hỏi nhận về một
    # danh sách hai file thay vì câu trả lời.
    r"|muon biet|cho biet|cho toi biet|cho em biet|gioi thieu"
    r"|thong tin ve|tim hieu ve|noi ve|hoc nhung gi|hoc gi|gom nhung gi"
    r"|bao gom|gom nhung|liet ke cac"
    # Hỏi về chương trình học
    r"|hoc nhung mon|hoc mon|lo trinh|chuong trinh hoc|mon hoc nao"
    # Câu hỏi nối tiếp: "vậy còn ngành kỹ thuật phần mềm thì sao"
    r"|thi sao|vay con|con .{0,30} thi sao)\b"
)

# Câu cụt lủn — "là gì", "như thế nào", "cái đó ra sao". Toàn hư từ, không có
# lấy một chủ thể nào để đi tìm. Đoán bừa ở đây chắc chắn trả sai, nên hỏi lại.
#
# Điều kiện phải NGẶT: mọi âm tiết đều nằm trong danh sách rỗng nghĩa VÀ có ít
# nhất một từ để hỏi. Chỉ cần một chữ có nghĩa ("kiểm thử hồi quy là gì") là
# thoát khỏi luật này ngay.
_CONTENTLESS = {
    "la", "gi", "cai", "do", "no", "the", "nao", "sao", "nhu", "ra", "vay",
    "a", "ay", "day", "kia", "o", "voi", "thi", "ma", "co", "khong", "biet",
    "em", "minh", "ban", "anh", "chi", "toi", "hoi", "ne", "nhi", "ha", "u",
}
_INTERROGATIVE = {"gi", "nao", "sao", "the"}
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def looks_like_content_question(text: str) -> bool:
    """Câu có dấu hiệu HỎI NỘI DUNG tường minh ("là gì", "muốn biết về"…).

    Độ chính xác cao vì chỉ nhận cụm rõ ràng, nên dùng được làm tín hiệu chốt:
    tầng nào phía sau cũng không được phép lật ngược nó thành câu đi tìm file.
    """
    return _CONTENT_QUESTION.search(vn_norm(text)) is not None


def _is_vague(normalized: str) -> bool:
    tokens = _PUNCT.sub(" ", normalized).split()
    if not tokens:
        return False
    if not all(token in _CONTENTLESS for token in tokens):
        return False
    return any(token in _INTERROGATIVE for token in tokens)


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


def route(message: str, blocked_topics=()) -> RuleVerdict:
    """`blocked_topics` do persona của bot đang trả lời cấp (chuỗi đã bỏ dấu).

    Nhờ nó, "học phí" là ngoài phạm vi với bot tra cứu tài liệu nhưng vẫn được
    nhận bình thường ở bot tư vấn tuyển sinh — cùng một hàm, khác một cột dữ
    liệu.
    """
    normalized = vn_norm(message)

    result = RouterResult(source="rule", standalone_question=message.strip())

    # Xúc phạm xét TRƯỚC mọi thứ: câu chửi có kèm lời chào hay từ khóa tra cứu
    # thì vẫn là câu chửi, không phải lời chào hay yêu cầu tìm tài liệu.
    if _ABUSIVE_NORM.search(normalized) or _ABUSIVE_RAW.search(message):
        result.intent = Intent.abusive
        return RuleVerdict(result, confident=True)

    # (1) Chào hỏi: chỉ chốt khi bỏ lời chào ra là KHÔNG CÒN GÌ. Còn chữ thì
    # đây là câu hỏi mở đầu lịch sự — định tuyến tiếp phần còn lại, và dùng
    # chính phần đó làm câu hỏi độc lập cho các bước sau.
    if _GREETING.search(normalized) or _THANKS.search(normalized):
        rest = _strip_greeting(normalized)
        if not rest:
            result.intent = Intent.greeting
            return RuleVerdict(result, confident=True)
        normalized = rest
        stripped = _GREETING_STRIP_RAW.sub("", message).strip(" ,.!?;:-")
        if stripped:
            result.standalone_question = stripped

    # (2) Ngoài phạm vi: câu CÓ dấu hiệu đang xin tài liệu thì không chốt cứng,
    # đẩy sang router LLM phán. Thà tốn một lượt gọi model còn hơn từ chối oan
    # một câu hỏi tài liệu hợp lệ.
    hit_scope = any(pattern.search(normalized) for pattern in _OUT_OF_SCOPE) or any(
        topic and topic in normalized for topic in blocked_topics
    )
    if hit_scope:
        result.intent = Intent.out_of_scope
        return RuleVerdict(result, confident=not _DOC_SEEKING.search(normalized))

    # Câu cụt: hỏi lại cho rõ thay vì đoán rồi trả sai.
    if _is_vague(normalized):
        result.intent = Intent.clarify
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

    # Văn bản quy chế là tài liệu CẤP TRƯỜNG, không gắn môn nào. Vì thế chỉ
    # chắc chắn khi câu không nhắc tới môn nào: "quy định đặt tên biến trong
    # lập trình" có chữ "quy định" nhưng là câu hỏi chuyên môn, chốt cứng ở đây
    # là đẩy người hỏi đi lục văn bản hành chính của trường.
    if doc_type is DocType.quy_che:
        chac = course is None and (
            _QUY_CHE_MANH.search(normalized) is not None
            or _QUY_CHE_BOI_CANH.search(normalized) is not None
        )
        return RuleVerdict(result, confident=chac)

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
