"""OCR bằng Gemini vision — cứu các tài liệu `needs_ocr`.

Thiết kế gốc xếp Gemini vision là phương án OCR cuối vì đắt nhất; nhưng với
văn bản hành chính vài trang thì chi phí là vài phần nghìn USD — rẻ hơn nhiều
so với việc cài đặt Tesseract riêng, và chất lượng tiếng Việt tốt hơn hẳn.
Đây là tác vụ OFFLINE có chủ đích của quản trị viên (bấm nút từng tài liệu),
không chạy tự động hàng loạt.

    python -m ingest.cli ocr <doc_id>
    POST /api/v1/admin/ocr/{doc_id}
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings
from app.db import fetch_one
from ingest.extract import Extraction, Page
from ingest.loader import IngestResult

log = logging.getLogger(__name__)

# Trần số trang một lần OCR — chống bấm nhầm vào giáo trình 300 trang
# (30 trang ≈ 0,01-0,02 USD; giáo trình dày thì cân nhắc chạy CLI có chủ đích).
MAX_PAGES = 30

_OCR_PROMPT = (
    "Đây là ảnh một trang văn bản hành chính/học thuật tiếng Việt. "
    "Hãy chép lại TOÀN BỘ văn bản trong ảnh, đúng chính tả và đầy đủ dấu "
    "tiếng Việt. Giữ cấu trúc dòng/đoạn; bảng biểu thì chép từng dòng dạng "
    "'cột 1 | cột 2 | ...'. Chỉ trả về văn bản, không bình luận."
)


def ocr_document(
    document_id: int,
    files_root: Path | None = None,
    max_pages: int | None = None,
) -> IngestResult:
    """OCR từng trang PDF bằng Gemini vision rồi nạp lại tài liệu bằng văn bản
    đã OCR. Metadata giữ nguyên đường cũ (manifest/tên file vẫn thắng).

    `max_pages` nâng trần cho giáo trình dày — chỉ CLI dùng có chủ đích
    (`ingest.cli ocr <id> --max-pages 200`), nút web vẫn giữ trần mặc định.
    """
    from app.llm import vertex

    settings = get_settings()
    root = files_root or settings.files_root
    page_cap = max_pages or MAX_PAGES

    doc = fetch_one(
        "SELECT id, file_path, status FROM documents WHERE id = :id",
        {"id": document_id},
    )
    if not doc:
        return IngestResult(None, "failed", "không tìm thấy tài liệu")

    path = root / str(doc["file_path"])
    if not path.is_file():
        return IngestResult(document_id, "failed", f"file không còn: {doc['file_path']}")
    if path.suffix.lower() != ".pdf":
        return IngestResult(document_id, "failed", "OCR hiện chỉ hỗ trợ PDF")

    client = vertex.get_client()
    if client is None:
        return IngestResult(document_id, "failed", "LLM đang tắt — bật USE_LLM_ROUTER và credential")

    import fitz  # PyMuPDF

    from google.genai import types

    pdf = fitz.open(path)
    if pdf.page_count > page_cap:
        return IngestResult(
            document_id, "failed",
            f"{pdf.page_count} trang vượt trần {page_cap} trang/lần OCR — "
            f"chạy CLI: python -m ingest.cli ocr {document_id} --max-pages {pdf.page_count}",
        )

    pages: list[Page] = []
    for index in range(pdf.page_count):
        pixmap = pdf[index].get_pixmap(dpi=150)
        png = pixmap.tobytes("png")
        try:
            response = client.models.generate_content(
                model=settings.gen_model,
                contents=[
                    _OCR_PROMPT,
                    types.Part.from_bytes(data=png, mime_type="image/png"),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=4000,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    http_options=vertex._http_options(60.0),
                ),
            )
            text = (getattr(response, "text", None) or "").strip()
        except Exception as exc:  # pragma: no cover - phụ thuộc mạng
            log.warning("OCR trang %d thất bại: %s", index + 1, exc)
            text = ""
        if text:
            pages.append(Page(number=index + 1, text=text))
        log.info("OCR trang %d/%d: %d ký tự", index + 1, pdf.page_count, len(text))
    pdf.close()

    if not pages:
        return IngestResult(document_id, "failed", "OCR không thu được chữ nào")

    extraction = Extraction(pages=pages, page_count=len(pages))

    # Xóa bản ghi cũ rồi nạp lại với văn bản OCR — cùng đường với reindex,
    # kể cả việc giữ lại danh sách trợ lý được gán.
    from ingest.loader import replace_document

    result = replace_document(document_id, path, root, extraction_override=extraction)

    from app.core import cache

    cache.clear_all()
    return result
