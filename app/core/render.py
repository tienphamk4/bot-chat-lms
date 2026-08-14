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

from app.schemas import Citation, DocType, RouterResult

DOC_TYPE_LABEL: dict[str, str] = {
    "giao_trinh": "giáo trình",
    "slide": "slide bài giảng",
    "de_cuong": "đề cương",
    "de_thi": "đề thi",
    "bai_tap": "bài tập",
    "tai_lieu_tham_khao": "tài liệu tham khảo",
    "quy_che": "quy chế",
}

NOT_FOUND = "Mình không tìm thấy thông tin này trong tài liệu hiện có."

GREETING = (
    "Chào bạn 👋 Mình là trợ lý tra cứu tài liệu học tập — giáo trình, slide, "
    "đề cương, đề thi, bài tập, quy chế đào tạo đều hỏi mình được. "
    "Bạn đang cần tài liệu môn nào, cứ gõ tự nhiên nhé (ví dụ: \"giáo trình giải tích 1\")."
)

OUT_OF_SCOPE = (
    "Chỗ này nằm ngoài phạm vi của mình mất rồi 😅 Mình chỉ giúp được phần "
    "tra cứu tài liệu học tập và quy chế đào tạo thôi — giải bài tập, viết bài "
    "hộ hay tra điểm, học phí, thời khóa biểu thì mình không làm được. "
    "Nếu bạn cần tài liệu của môn nào để tự ôn thì mình tìm giúp ngay!"
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
) -> tuple[str, list[Citation]]:
    """Nhánh metadata: liệt kê tài liệu tìm được."""
    if not rows:
        return render_not_found(route, course, scope_doc_type), []

    citations: list[Citation] = []
    lines: list[str] = [_headline(len(rows), route, course)]

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

    lines.append("Bạn bấm vào nguồn bên dưới để tải về nhé.")
    return "\n".join(lines), citations


def render_chunks(
    chunks: list[dict[str, Any]],
    route: RouterResult,
    course: dict[str, Any] | None = None,
    scope_doc_type: str | None = None,
) -> tuple[str, list[Citation]]:
    """Nhánh nội dung của giai đoạn 1: trích nguyên văn, không diễn giải.

    Giai đoạn 1 cố tình KHÔNG tóm tắt: không có model sinh thì mọi câu tóm tắt
    đều là bịa. Trả nguyên văn đoạn trích kèm nguồn vẫn trả lời được câu hỏi mà
    không có rủi ro sai lệch.
    """
    if not chunks:
        return render_not_found(route, course, scope_doc_type), []

    citations: list[Citation] = []
    lines: list[str] = ["Mình tìm được mấy đoạn này trong tài liệu, bạn xem thử nhé:"]

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
                heading_path=chunk.get("heading_path"),
                url=download_url(int(chunk["document_id"])),
            )
        )

    return "\n".join(lines), citations


def render_not_found(
    route: RouterResult,
    course: dict[str, Any] | None = None,
    scope_doc_type: str | None = None,
) -> str:
    """Câu trả lời khi rỗng — đúng câu chuẩn của dự án, kèm 2 gợi ý từ khóa.

    Trước khi gợi ý "thử từ khóa khác", kiểm xem PHẠM VI đang tìm có tài liệu
    nào không: kho trống mà vẫn xúi người dùng đổi từ khóa là vô lý — tìm kiểu
    gì cũng không ra, phải nói thẳng là chưa có tài liệu.
    """
    indexed = _indexed_count(scope_doc_type)
    if indexed == 0:
        if scope_doc_type == "quy_che":
            return (
                "Kho hiện chưa có văn bản quy chế, quy định nào được nạp nên mình "
                "chưa tư vấn được. Bạn báo quản trị viên bổ sung, rồi quay lại hỏi nhé!"
            )
        if scope_doc_type:
            label = DOC_TYPE_LABEL.get(scope_doc_type, "tài liệu")
            return f"Kho hiện chưa có {label} nào được nạp. Bạn báo quản trị viên bổ sung nhé!"
        return (
            "Kho tài liệu hiện đang trống — quản trị viên chưa nạp tài liệu nào, "
            "nên mình chưa có gì để tra cứu giúp bạn. Bạn quay lại sau nhé!"
        )

    suggestions = _suggestions(route, course)
    if not suggestions:
        return f"{NOT_FOUND} Bạn thử diễn đạt lại, hoặc hỏi mình xem môn đó có những tài liệu gì nhé."
    return (
        f"{NOT_FOUND} Bạn thử tìm lại với từ khóa {' hoặc '.join(suggestions)} xem sao nhé."
    )


# Đếm tài liệu indexed theo phạm vi, cache 60 giây trong tiến trình — chỉ để
# quyết định thông điệp không-tìm-thấy, trễ một phút không sao.
_INDEXED_COUNT_TTL = 60.0
_indexed_cache: dict[str, tuple[float, int]] = {}


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


def _headline(
    count: int, route: RouterResult, course: dict[str, Any] | None
) -> str:
    what = DOC_TYPE_LABEL.get(route.doc_type.value, "tài liệu") if route.doc_type else "tài liệu"
    where = ""
    # CHỈ nói "của môn X" khi X là môn THẬT đã khớp danh mục. course_hint chưa
    # khớp mà vẫn gắn vào là khẳng định sai sự thật ("giáo trình của môn hệ
    # quản trị CSDL" nhưng chìa ra Giải tích 1).
    if course:
        where = f" của môn {course['name']} ({course['code']})"
    return f"Mình tìm thấy {count} {what}{where}:"


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


def _suggestions(route: RouterResult, course: dict[str, Any] | None) -> list[str]:
    candidates: list[str] = []
    if course:
        candidates.append(str(course["code"]))
        candidates.append(str(course["name"]))
    elif route.course_hint:
        candidates.append(route.course_hint)
    if route.doc_type:
        candidates.append(DOC_TYPE_LABEL.get(route.doc_type.value, route.doc_type.value))
    candidates.extend(route.keywords)

    unique: list[str] = []
    for candidate in candidates:
        cleaned = (candidate or "").strip()
        if cleaned and cleaned not in unique:
            unique.append(f'"{cleaned}"')
    return unique[:2]
