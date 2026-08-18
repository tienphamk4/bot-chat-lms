"""Dựng câu trả lời bằng template — 0 token sinh.

Đây là chỗ kỹ thuật KT-01 biến thành tiền: mọi câu trả lời ở giai đoạn 1 đều
ghép từ dữ liệu thật trong CSDL, không có model nào sinh chữ. Giai đoạn 2 chỉ
nhánh nội dung mới chuyển sang LLM; nhánh metadata giữ nguyên template mãi mãi.

Quy tắc trích dẫn: mỗi dấu [n] trong câu trả lời PHẢI ứng với một phần tử trong
danh sách citations trả về. Hai thứ này luôn được dựng cùng lúc trong cùng một
hàm, để không bao giờ lệch nhau.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.core.access import Identity, access_clause
from app.core.personas import DEFAULT as DEFAULT_PERSONA
from app.core.personas import Persona
from ingest.context import clean_heading
from app.schemas import Citation, DocType, RouterResult

DOC_TYPE_LABEL: dict[str, str] = {
    "giao_trinh": "giáo trình",
    "slide": "slide bài giảng",
    "de_cuong": "đề cương",
    "de_thi": "đề thi",
    "bai_tap": "bài tập",
    "tai_lieu_tham_khao": "tài liệu tham khảo",
    # Nhãn hiển thị là "văn bản" (rộng hơn: quy chế, quy định, quyết định,
    # thông báo…). Giá trị trong CSDL vẫn là quy_che — đổi enum là phải
    # migration và nạp lại, không đáng cho một chuyện chữ nghĩa.
    "quy_che": "văn bản",
}

# Bốn câu dưới đây chỉ là ĐƯỜNG LUI khi câu hỏi không kèm slug bot, hoặc bảng
# `assistants` chưa có/không đọc được. Câu thật của từng bot nằm trong cột
# `replies` của bảng đó — xem app/core/personas.py.
#
# Cố ý KHÔNG liệt kê những việc bot không làm ("còn giải bài tập, tra điểm,
# học phí…"): danh sách ấy vừa dài vừa gắn chặt với một bot cụ thể, nhân bản
# sang bot tuyển sinh là sai ngay. Nói ngắn rồi mời đúng việc mình làm — phần
# mời do cột `offer` của từng bot cấp.
NOT_FOUND = "Em chưa tìm thấy thông tin này trong tài liệu hiện có ạ."

GREETING = "Em chào anh/chị."

OUT_OF_SCOPE = "Nội dung này nằm ngoài phạm vi hỗ trợ của em ạ."

ABUSIVE = "Em xin lỗi, em không thể tiếp tục cuộc trò chuyện với những ngôn từ như vậy ạ."

CLARIFY = (
    "Dạ, anh/chị đang muốn hỏi về nội dung cụ thể nào ạ? "
    "Anh/chị nói rõ hơn giúp em để em hỗ trợ chính xác nhất nhé."
)

EXCERPT_MAX_CHARS = 280

_WHITESPACE = re.compile(r"\s+")


def download_url(document_id: int) -> str:
    return f"/api/v1/documents/{document_id}/download"


def render_documents(
    rows: list[dict[str, Any]],
    route: RouterResult,
    course: dict[str, Any] | None = None,
    scope_doc_type: str | None = None,
    identity: Identity | None = None,
    persona: Persona | None = None,
) -> tuple[str, list[Citation]]:
    """Nhánh metadata: liệt kê tài liệu tìm được."""
    if not rows:
        return render_not_found(route, course, scope_doc_type, identity, persona), []

    citations: list[Citation] = []
    lines: list[str] = [_headline(rows)]

    for index, row in enumerate(rows, start=1):
        label = DOC_TYPE_LABEL.get(str(row["type"]), str(row["type"]))
        details = [label]
        if row.get("semester"):
            details.append(f"học kỳ {_semester_label(row['semester'])}")
        if row.get("lecturer"):
            details.append(str(row["lecturer"]))
        if row.get("page_count"):
            details.append(f"{row['page_count']} trang")

        lines.append(f"{index}. {row['title']} — {', '.join(details)} [{index}]")
        citations.append(
            Citation(
                n=index,
                document_id=int(row["id"]),
                title=str(row["title"]),
                type=DocType(str(row["type"])),
                semester=row.get("semester"),
                url=download_url(int(row["id"])),
            )
        )

    lines.append("Anh/chị bấm vào nguồn bên dưới để tải về ạ.")
    return "\n".join(lines), citations


def render_chunks(
    chunks: list[dict[str, Any]],
    route: RouterResult,
    course: dict[str, Any] | None = None,
    scope_doc_type: str | None = None,
    identity: Identity | None = None,
    persona: Persona | None = None,
) -> tuple[str, list[Citation]]:
    """Nhánh nội dung của giai đoạn 1: trích nguyên văn, không diễn giải.

    Giai đoạn 1 cố tình KHÔNG tóm tắt: không có model sinh thì mọi câu tóm tắt
    đều là bịa. Trả nguyên văn đoạn trích kèm nguồn vẫn trả lời được câu hỏi mà
    không có rủi ro sai lệch.
    """
    if not chunks:
        return render_not_found(route, course, scope_doc_type, identity, persona), []

    citations: list[Citation] = []
    lines: list[str] = ["Em tìm được mấy đoạn này trong tài liệu, anh/chị xem thử ạ:"]

    for index, chunk in enumerate(chunks, start=1):
        excerpt = _excerpt(str(chunk["content"]))
        # Gạch đầu dòng + dấu nguồn ở CUỐI dòng — UI render thành <li> với pill
        # trang nằm cuối, cùng bố cục với câu trả lời RAG.
        lines.append(f"- {excerpt} [{index}]")
        citations.append(
            Citation(
                n=index,
                document_id=int(chunk["document_id"]),
                title=str(chunk["title"]),
                type=DocType(str(chunk["type"])),
                semester=chunk.get("semester"),
                page=chunk.get("page_from"),
                page_to=chunk.get("page_to"),
                heading_path=clean_heading(chunk.get("heading_path")),
                url=download_url(int(chunk["document_id"])),
            )
        )

    return "\n".join(lines), citations


def render_not_found(
    route: RouterResult,
    course: dict[str, Any] | None = None,
    scope_doc_type: str | None = None,
    identity: Identity | None = None,
    persona: Persona | None = None,
) -> str:
    """Câu trả lời khi không tìm được gì — CHỈ nói chuyện của người dùng.

    Hai nhịp, hết: nhắc lại chủ đề đã hỏi trong phạm vi tài liệu của bot, rồi
    mời họ đi tiếp bằng đúng việc bot làm được. Kèm đầu mối liên hệ nếu bot có
    khai.

    Ba thứ CỐ Ý không nói, vì đều là chuyện nội bộ:

    * "kho chưa có tài liệu nào được nạp", "báo giúp quản trị viên bổ sung" —
      người hỏi không quản trị hệ thống này, nói với họ là đổ việc nhầm người.
    * tên môn/tài liệu đang có — bản cũ liệt kê từ TOÀN kho nên bot tuyển sinh
      chìa ra "Kiểm thử phần mềm", và với người ngoài quyền thì còn lộ cả sự
      tồn tại của tài liệu họ không được đọc.
    * loại tài liệu đang thiếu — cũng là thông tin vận hành.

    Người dùng chỉ cần biết: em chưa có thông tin này, và em giúp được gì.
    """
    persona = persona or DEFAULT_PERSONA
    topic = _topic_phrase(route.standalone_question)
    opening = (
        f"Về vấn đề “{topic}”, em chưa có thông tin trong {persona.corpus_label} ạ."
        if topic
        else f"Em chưa tìm thấy thông tin này trong {persona.corpus_label} ạ."
    )
    return _with_contact(f"{opening} {persona.offer}".strip(), persona)


# Đuôi nghi vấn cần lột để lấy CHỦ ĐỀ người ta hỏi: "kiểm thử là gì" -> "kiểm thử".
_TRAILING_QUESTION = re.compile(
    r"\s*(la gi|nghia la gi|nhu the nao|the nao|ra sao|la sao|ra lam sao)"
    r"\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_LEADING_FILLER = re.compile(
    r"^\s*(cho em hoi|cho minh hoi|em muon hoi|minh muon hoi|em hoi|ban oi)\s*[,:]?\s*",
    re.IGNORECASE,
)


def _topic_phrase(question: str) -> str:
    """Rút chủ đề từ câu hỏi để nhắc lại — cắt trên bản CÒN DẤU, dò trên bản
    chuẩn hóa. Cắt hụt thì trả rỗng, câu trả lời rơi về mẫu chung."""
    from app.vn import vn_norm

    text = (question or "").strip()
    if not text:
        return ""

    for pattern in (_LEADING_FILLER, _TRAILING_QUESTION):
        match = pattern.search(vn_norm(text))
        if match:
            if pattern is _LEADING_FILLER:
                text = text[match.end():].strip()
            else:
                text = text[: match.start()].strip()

    text = text.strip(" ,.?!:;“”\"'")
    if not text or len(text) > 80:
        return ""
    return text


def _indexed_count(scope_doc_type: str | None) -> int:
    import time

    from app.db import fetch_value

    key = scope_doc_type or "*"
    cached = _indexed_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _INDEXED_COUNT_TTL:
        return cached[1]
    try:
        if scope_doc_type:
            count = int(
                fetch_value(
                    "SELECT COUNT(*) FROM documents WHERE status='indexed' AND type = :t",
                    {"t": scope_doc_type},
                )
                or 0
            )
        else:
            count = int(
                fetch_value("SELECT COUNT(*) FROM documents WHERE status='indexed'") or 0
            )
    except Exception:  # đếm hỏng thì coi như có tài liệu — giữ thông điệp cũ
        return -1
    _indexed_cache[key] = (now, count)
    return count


def _headline(rows: list[dict[str, Any]]) -> str:
    """Mô tả thứ THẬT SỰ tìm được, không phải thứ người dùng hỏi.

    Ở nhánh metadata hai cái đó trùng nhau vì kết quả đã lọc đúng tiêu chí.
    Nhưng nhánh dự phòng FULLTEXT tìm theo tiêu đề, KHÔNG lọc loại và môn —
    dựng câu theo câu hỏi ở đó là nói sai: đã gặp ca "Em tìm thấy 1 đề thi của
    môn Xác suất thống kê" trong khi thứ chìa ra là slide của môn Kiểm thử
    phần mềm, ngay dưới một dòng vừa nói là không tìm thấy đề thi.
    """
    types = {str(row["type"]) for row in rows}
    what = (
        DOC_TYPE_LABEL.get(next(iter(types)), "tài liệu")
        if len(types) == 1
        else "tài liệu"
    )

    # Chỉ nói "của môn X" khi MỌI kết quả cùng thuộc đúng môn đó.
    where = ""
    course_ids = {row.get("course_id") for row in rows}
    if len(course_ids) == 1 and next(iter(course_ids)) is not None:
        first = rows[0]
        if first.get("course_name") and first.get("course_code"):
            where = f" của môn {first['course_name']} ({first['course_code']})"

    return f"Em tìm thấy {len(rows)} {what}{where}:"


def _semester_label(semester: str) -> str:
    """'20261' -> '1 năm 2026-2027'."""
    if len(semester) == 5 and semester.isdigit():
        year = int(semester[:4])
        return f"{semester[4]} năm {year}-{year + 1}"
    return semester


def _excerpt(content: str) -> str:
    text = _WHITESPACE.sub(" ", content).strip()
    if len(text) <= EXCERPT_MAX_CHARS:
        return text
    cut = text[:EXCERPT_MAX_CHARS]
    last_space = cut.rfind(" ")
    if last_space > EXCERPT_MAX_CHARS // 2:
        cut = cut[:last_space]
    return cut + "…"


def _with_contact(answer: str, persona: Persona | None = None) -> str:
    """Nối đường liên hệ vào câu không-tìm-thấy.

    Ưu tiên đầu mối RIÊNG của bot (bảng assistants), rồi mới tới .env dùng
    chung: bot tuyển sinh phải chỉ về Phòng Tuyển sinh chứ không phải về quản
    trị kho tài liệu. Không khai gì thì trả nguyên câu — thà không có lối liên
    hệ còn hơn chìa ra một số điện thoại bịa.
    """
    settings = get_settings()
    contact = (persona.contact if persona else None) or {}
    hotline = (contact.get("hotline") or settings.contact_hotline).strip()
    website = (contact.get("website") or settings.contact_website).strip()
    if not hotline and not website:
        return answer

    ways = []
    if hotline:
        ways.append(f"hotline {hotline}")
    if website:
        ways.append(f"website {website}")
    return (
        f"{answer}\n\nĐể được hỗ trợ chính xác nhất, anh/chị vui lòng liên hệ "
        f"{' hoặc '.join(ways)} ạ."
    )


# `_suggestions()` cũ đã bị XÓA, đừng dựng lại: nó lấy mã và tên môn từ chỉ mục
# trong RAM — không đi qua mệnh đề quyền — nên gợi ý được cả môn mà người hỏi
# không có quyền xem tài liệu. Bản thay thế `_accessible_topics()` cũng đã bỏ
# nốt: dù có lọc quyền, nó vẫn liệt kê môn từ TOÀN kho nên bot tuyển sinh chìa
# ra tên môn của bot tài liệu. Câu không-tìm-thấy giờ không gợi ý gì cả.
