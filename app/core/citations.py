"""Hậu xử lý câu trả lời RAG: kiểm từng dấu [n] trước khi cho ra ngoài.

Quy tắc cứng của dự án (mục 5 CLAUDE.md): dấu [n] không ánh xạ được về chunk
thật → LOẠI BỎ CẢ CÂU chứa nó. Model bịa nguồn thì thà mất câu đó còn hơn đưa
người dùng một trích dẫn sai — NFR "trích dẫn đúng ≥ 95%".
"""

from __future__ import annotations

import re
from typing import Any

from app.core.render import download_url
from app.schemas import Citation, DocType

# Bắt cả [1] lẫn [1, 6] — model thỉnh thoảng gộp nhiều nguồn một ngoặc dù
# prompt dặn tách riêng; kiểm chứng phải hiểu được cả hai dạng.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _marker_numbers(text: str) -> list[int]:
    numbers: list[int] = []
    for group in _MARKER.findall(text):
        numbers.extend(int(part) for part in re.split(r"\s*,\s*", group))
    return numbers

# Tách câu đủ tốt cho hậu kiểm: kết thúc bằng . ! ? … theo sau là khoảng
# trắng/hết chuỗi. Không cần hoàn hảo — sai lệch chỉ làm câu bị loại dài hơn.
# Không cắt sau SỐ + chấm ('Mục 4. Phương pháp…', 'Chương 3. Đạo hàm') — đó là
# số hiệu đề mục, cắt ở đây làm câu gợi ý đọc thêm gãy đôi trên UI.
_SENTENCE_END = re.compile(r"(?<=[.!?…])(?<!\d\.)\s+")


def sentence_ok(sentence: str, n_chunks: int) -> tuple[bool, set[int]]:
    """Một câu qua được kiểm không, và nó trích những nguồn nào."""
    markers = set(_marker_numbers(sentence))
    ok = all(1 <= n <= n_chunks for n in markers)
    return ok, markers if ok else set()


def split_complete(buffer: str) -> tuple[list[str], str]:
    """Tách các câu ĐÃ TRỌN VẸN khỏi đầu buffer, giữ phần dở lại.

    Dùng cho đường streaming: chỉ câu trọn vẹn mới được kiểm và phát đi.
    """
    parts = _SENTENCE_END.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def process(
    answer: str, chunks: list[dict[str, Any]]
) -> tuple[str, list[Citation]]:
    """Lọc câu chứa [n] không hợp lệ, dựng danh sách citations từ [n] còn lại.

    Trả về (câu trả lời đã lọc, citations). Câu trả lời rỗng sau lọc → chuỗi
    rỗng, nơi gọi tự thay bằng câu "không tìm thấy" chuẩn.
    """
    kept_sentences: list[str] = []
    cited: set[int] = set()

    for sentence in _SENTENCE_END.split(answer.strip()):
        if not sentence:
            continue
        ok, markers = sentence_ok(sentence, len(chunks))
        if not ok:
            continue  # câu có nguồn bịa — bỏ cả câu
        kept_sentences.append(sentence.strip())
        cited.update(markers)

    citations = build_citations(cited, chunks)
    return " ".join(kept_sentences), citations


def build_citations(
    cited: set[int], chunks: list[dict[str, Any]]
) -> list[Citation]:
    return [_to_citation(n, chunks[n - 1]) for n in sorted(cited)]


_SNIPPET_MAX_CHARS = 360


def _snippet(content: str | None) -> str | None:
    """Đoạn trích nguyên văn cho popup xem trước — cắt gọn tại ranh giới từ."""
    if not content:
        return None
    text = " ".join(str(content).split())
    if len(text) <= _SNIPPET_MAX_CHARS:
        return text
    cut = text[:_SNIPPET_MAX_CHARS]
    last_space = cut.rfind(" ")
    if last_space > _SNIPPET_MAX_CHARS // 2:
        cut = cut[:last_space]
    return cut + "…"


def _to_citation(n: int, chunk: dict[str, Any]) -> Citation:
    document_id = int(chunk["document_id"])
    file_path = str(chunk.get("file_path") or "")
    # PDF render thẳng; PPTX/DOCX dùng bản PDF đã convert lúc nạp (nếu có) —
    # trang PDF khớp 1:1 slide nên nhảy trang theo trích dẫn vẫn đúng.
    preview_url = None
    if file_path.lower().endswith(".pdf"):
        preview_url = f"/api/v1/documents/{document_id}/preview"
    else:
        from app.config import get_settings
        from ingest.preview import preview_path

        if preview_path(get_settings().files_root, document_id).is_file():
            preview_url = f"/api/v1/documents/{document_id}/preview"
    return Citation(
        n=n,
        document_id=document_id,
        title=str(chunk["title"]),
        type=DocType(str(chunk["type"])),
        semester=chunk.get("semester"),
        page=chunk.get("page_from"),
        page_to=chunk.get("page_to"),
        heading_path=chunk.get("heading_path"),
        url=download_url(document_id),
        snippet=_snippet(chunk.get("content")),
        preview_url=preview_url,
    )
