"""Trích xuất văn bản từ PDF / DOCX / PPTX / TXT / MD — v2 layout-aware.

Kết quả thống nhất là `list[Page]` — mỗi phần tử một trang (PDF) hoặc một đơn
vị tương đương trang (slide PPTX; DOCX cả file là một "trang" vì docx không có
khái niệm trang trước khi render).

Khác v1 (đại tu 14/08/2026) — PDF đọc bằng `get_text("dict")` hai lượt:
- Đo cỡ chữ thân bài (mode theo trọng số ký tự) → dòng cỡ ≥1.15× hoặc in đậm,
  ≤120 ký tự là ỨNG VIÊN heading; văn phạm trong `chunk.classify_heading` mới
  là người quyết định CẤP — font chỉ đề cử, không phán quyết.
- Lột header/footer lặp (vân tay xuất hiện ≥30% số trang trong vùng mép trang)
  — hết cảnh 'TRƯỜNG ĐH X — Giáo trình Y — trang 5' chen giữa mọi chunk.
- Bỏ nguyên trang MỤC LỤC (mật độ dòng chấm-dẫn '..... 10' ≥30%) — nguồn số
  một của heading rác và chunk vô nghĩa.
- Gom `front_matter` (dòng thô 2 trang đầu + header/footer đại diện) cho Title
  Engine ở tầng metadata.
- Ngắt đoạn thật theo block PDF ('\n\n' giữa block) thay vì một dải '\n'.

Phát hiện scan giữ nguyên: trung bình < 50 ký tự/trang (đo trên văn bản THÔ,
trước khi lột trang mục lục) là bản quét ảnh → `is_scanned=True` để pipeline
đánh dấu `needs_ocr` thay vì nạp một đống chunk rỗng.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.vn import vn_norm

SCANNED_CHARS_PER_PAGE = 50

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}

_HEADING_SIZE_RATIO = 1.15
_MAX_HEADING_CHARS = 120
_HEADER_ZONE = 0.12   # 12% mép trên trang
_FOOTER_ZONE = 0.90   # 10% mép dưới trang
_FINGERPRINT_MIN_PAGES = 3
_FINGERPRINT_RATIO = 0.30
_TOC_LINE = re.compile(r"\.{4,}\s*\d{1,4}\s*$")
_TOC_DENSITY = 0.30
_BOLD_FLAG = 1 << 4  # PyMuPDF span flags bit 4


@dataclass
class Page:
    number: int  # 1-based
    text: str
    headings: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    pages: list[Page]
    page_count: int
    is_scanned: bool = False
    front_matter: list[str] = field(default_factory=list)  # dòng thô 2 trang đầu
    toc_pages: list[int] = field(default_factory=list)     # trang mục lục đã bỏ

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.pages)


class UnsupportedFormat(Exception):
    pass


def extract(path: Path) -> Extraction:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix == ".pptx":
        return _extract_pptx(path)
    if suffix in {".txt", ".md"}:
        return _extract_text(path)
    raise UnsupportedFormat(f"Không hỗ trợ định dạng {suffix}")


# ----------------------------------------------------------------------------
# PDF — hai lượt: thu thập dòng có layout → dựng lại trang đã làm sạch
# ----------------------------------------------------------------------------

@dataclass
class _Line:
    text: str
    size: float          # cỡ chữ lớn nhất trong dòng (theo span có nội dung)
    bold: bool
    y0: float
    y1: float
    height: float        # chiều cao trang chứa dòng
    block: int           # chỉ số block trong trang (để ngắt đoạn)
    x0: float = 0.0      # mép trái — dùng để nhận ra trang dàn nhiều cột
    x1: float = 0.0      # mép phải

    @property
    def in_margin(self) -> bool:
        if self.height <= 0:
            return False
        return self.y0 <= self.height * _HEADER_ZONE or self.y1 >= self.height * _FOOTER_ZONE


def _extract_pdf(path: Path) -> Extraction:
    import fitz  # PyMuPDF

    try:
        page_lines, raw_chars = _collect_pdf_lines(path)
    except Exception:
        # PDF dị dạng — rơi về đường cũ đọc phẳng, không chết pipeline.
        return _extract_pdf_plain(path)

    page_count = len(page_lines)
    is_scanned = page_count > 0 and (raw_chars / page_count) < SCANNED_CHARS_PER_PAGE

    body_size = _body_font_size(page_lines)
    strip_keys = _repeated_margin_keys(page_lines)
    toc_pages = _detect_toc_pages(page_lines)

    front_matter: list[str] = [
        line.text for lines in page_lines[:2] for line in lines if line.text.strip()
    ][:80]
    # Header/footer đại diện (mỗi vân tay một mẫu) — Title Engine đào giảng
    # viên/đơn vị từ đây thay vì mất trắng khi bị lột khỏi thân bài.
    seen_samples: set[str] = set()
    for lines in page_lines:
        for line in lines:
            key = _fingerprint(line.text)
            if line.in_margin and key in strip_keys and key not in seen_samples:
                seen_samples.add(key)
                front_matter.append(line.text)

    pages: list[Page] = []
    for index, lines in enumerate(page_lines, start=1):
        if index in toc_pages:
            pages.append(Page(number=index, text=""))
            continue
        pages.append(Page(number=index, text=_render_page(lines, body_size, strip_keys)))

    return Extraction(
        pages=pages,
        page_count=page_count,
        is_scanned=is_scanned,
        front_matter=front_matter,
        toc_pages=sorted(toc_pages),
    )


_SPACE_GAP_EM = 0.15


def _line_text(raw_line: dict) -> str:
    """Dựng chữ của một dòng, KHÔI PHỤC dấu cách bị mất.

    Slide PowerPoint xuất PDF hay đánh rơi ký tự space: 'Kiểm thử hệ thống'
    thành 'Kiểm thửhệthống' — chữ dính thì FULLTEXT trượt, embedding méo và
    model đọc ra rác (đo trên kho: chunk slide dính 5-10 từ mỗi trang).
    Chữ bị mất space vẫn để lại KHOẢNG TRỐNG HÌNH HỌC: đo thực tế trên slide
    hỏng, gap chỗ thiếu space là 0,27 em còn giữa các chữ trong cùng từ là
    0,00 em — ngưỡng 0,15 em tách bạch tuyệt đối, không có vùng xám.
    """
    parts: list[str] = []
    prev_x1: float | None = None
    prev_char = ""
    for span in raw_line.get("spans", []):
        size = float(span.get("size", 0.0)) or 12.0
        for char in span.get("chars", []):
            value = char.get("c", "")
            if not value:
                continue
            bbox = char.get("bbox", (0.0, 0.0, 0.0, 0.0))
            x0, x1 = float(bbox[0]), float(bbox[2])
            if (
                prev_x1 is not None
                and x0 - prev_x1 >= size * _SPACE_GAP_EM
                and not prev_char.isspace()
                and not value.isspace()
            ):
                parts.append(" ")
            parts.append(value)
            prev_x1, prev_char = x1, value
    return "".join(parts)


def _collect_pdf_lines(path: Path) -> tuple[list[list[_Line]], int]:
    import fitz

    page_lines: list[list[_Line]] = []
    raw_chars = 0
    with fitz.open(path) as document:
        for page in document:
            height = float(page.rect.height)
            lines: list[_Line] = []
            # rawdict cho bbox TỪNG KÝ TỰ — cần để khôi phục dấu cách bị mất.
            data = page.get_text("rawdict")
            for block_index, block in enumerate(data.get("blocks", [])):
                if block.get("type") != 0:  # bỏ block ảnh
                    continue
                for raw_line in block.get("lines", []):
                    spans = [
                        s for s in raw_line.get("spans", [])
                        if any(not c.get("c", " ").isspace() for c in s.get("chars", []))
                    ]
                    if not spans:
                        continue
                    text = _line_text(raw_line).strip()
                    if not text:
                        continue
                    raw_chars += len(text)
                    bbox = raw_line.get("bbox", (0, 0, 0, 0))
                    lines.append(
                        _Line(
                            text=text,
                            size=max(float(s.get("size", 0.0)) for s in spans),
                            bold=any(int(s.get("flags", 0)) & _BOLD_FLAG for s in spans),
                            y0=float(bbox[1]),
                            y1=float(bbox[3]),
                            x0=float(bbox[0]),
                            x1=float(bbox[2]),
                            height=height,
                            block=block_index,
                        )
                    )
            # Sắp lại theo cột NGAY tại đây: mọi bước sau (đo cỡ chữ thân
            # bài, lột header/footer, dựng trang, chia chunk) đều đọc từ danh
            # sách này, nên sửa một chỗ là cả đường ống đúng theo.
            page_lines.append(order_by_columns(lines, float(page.rect.width)))
    return page_lines, raw_chars


# --- Trang dàn nhiều cột ----------------------------------------------------
#
# Đọc tuyến tính (trên xuống dưới, trái sang phải) trộn các cột vào nhau, và
# hậu quả không phải là văn bản khó đọc mà là DỮ LIỆU SAI. Ca thật, trang 10
# tài liệu tuyển sinh — ba cột, mỗi cột một ngành:
#
#     cột 1 (x≈22)          cột 2 (x≈442)        cột 3 (x≈820)
#     HỆ THỐNG THÔNG TIN    KINH TẾ SỐ           HỆ THỐNG THÔNG TIN
#     QUẢN LÝ
#     Chỉ tiêu: 100         Mã ngành: 7310109    Chỉ tiêu: 50
#     Mã ngành: 7340405                          Mã ngành: 7480104
#
# Nối tuyến tính thì "Mã ngành: 7310109" (cột 2, Kinh tế số) đứng ngay cạnh
# "HỆ THỐNG THÔNG TIN QUẢN LÝ" (cột 1), và bot trả lời mã ngành của ngành này
# cho ngành kia — nghe rất chắc chắn, người hỏi không có cách nào nhận ra.
#
# Cách nhận cột: chiếu mọi dòng xuống trục ngang rồi tìm RÃNH TRỐNG chạy suốt
# chiều cao trang. Chiếu chứ không gom theo x0, vì gom theo mép trái thì một
# dòng thụt đầu dòng đã thành "cột" riêng.

_COL_BINS = 240             # độ phân giải khi chiếu xuống trục ngang
_MIN_GUTTER_RATIO = 0.025   # rãnh hẹp hơn 2,5% bề ngang trang là khoảng cách chữ
_MIN_LINES_PER_COL = 3      # ít hơn thì đó là chú thích lạc, không phải cột
_MIN_COLUMN_RATIO = 0.08    # cột hẹp hơn 8% bề ngang trang: bỏ qua


def _column_ranges(lines: list[_Line], width: float) -> list[tuple[float, float]]:
    """Các khoảng [x_trái, x_phải] của từng cột. Rỗng = trang một cột.

    Dòng nằm ở lề trên/dưới bị loại khỏi phép đo: header và footer thường chạy
    suốt bề ngang trang và sẽ bịt mất mọi rãnh, làm trang ba cột trông như một
    cột.
    """
    body = [ln for ln in lines if not ln.in_margin and ln.x1 > ln.x0]
    if len(body) < _MIN_LINES_PER_COL * 2 or width <= 0:
        return []

    step = width / _COL_BINS
    filled = bytearray(_COL_BINS)
    for line in body:
        start = max(0, min(_COL_BINS - 1, int(line.x0 / step)))
        end = max(0, min(_COL_BINS - 1, int(line.x1 / step)))
        for index in range(start, end + 1):
            filled[index] = 1

    # Gom các ô liền nhau CÓ chữ thành từng cột ứng viên.
    spans: list[tuple[int, int]] = []
    index = 0
    while index < _COL_BINS:
        if not filled[index]:
            index += 1
            continue
        start = index
        while index < _COL_BINS and filled[index]:
            index += 1
        spans.append((start, index - 1))

    min_gutter = max(1, int(_MIN_GUTTER_RATIO * _COL_BINS))
    merged: list[tuple[int, int]] = []
    for span in spans:
        if merged and span[0] - merged[-1][1] - 1 < min_gutter:
            merged[-1] = (merged[-1][0], span[1])  # rãnh quá hẹp: cùng một cột
        else:
            merged.append(span)

    if len(merged) < 2:
        return []

    ranges = [
        (start * step, (end + 1) * step)
        for start, end in merged
        if (end + 1 - start) * step >= _MIN_COLUMN_RATIO * width
    ]
    if len(ranges) < 2:
        return []

    # Cột nào quá ít dòng thì cả trang không đáng coi là nhiều cột — thà đọc
    # tuyến tính còn hơn cắt nhầm một trang văn xuôi bình thường.
    for left, right in ranges:
        count = sum(1 for ln in body if left <= (ln.x0 + ln.x1) / 2 < right)
        if count < _MIN_LINES_PER_COL:
            return []
    return ranges


def order_by_columns(lines: list[_Line], width: float) -> list[_Line]:
    """Sắp lại dòng theo CỘT: hết cột trái mới sang cột phải.

    Trang một cột trả về nguyên trạng, không đụng gì.
    """
    ranges = _column_ranges(lines, width)
    if not ranges:
        return lines

    # Xếp MỌI dòng vào cột theo tâm ngang, kể cả dòng nằm ở vùng lề. Dòng lề
    # bị loại khỏi phép ĐO rãnh (header/footer chạy ngang trang sẽ bịt rãnh),
    # nhưng không được loại khỏi phép XẾP: trong tài liệu quảng bá, tên ngành
    # in to nằm ngay đỉnh trang — tức trong vùng lề — và đẩy nó xuống cuối là
    # tách tên ngành khỏi đúng cột của nó, hỏng lại đúng thứ vừa sửa.
    # Header/footer thật vẫn bị lột sau đó theo vân tay lặp.
    buckets: list[list[_Line]] = [[] for _ in ranges]
    for line in lines:
        center = (line.x0 + line.x1) / 2
        slot = next(
            (i for i, (left, right) in enumerate(ranges) if left <= center < right),
            len(ranges) - 1,
        )
        buckets[slot].append(line)

    ordered: list[_Line] = []
    for column, bucket in enumerate(buckets):
        for line in sorted(bucket, key=lambda ln: (ln.y0, ln.x0)):
            # Ép mỗi cột thành một dải block riêng: chuyển cột luôn là ranh
            # giới đoạn, không bao giờ dính chữ của hai ngành vào một đoạn.
            ordered.append(replace(line, block=column * 100_000 + line.block))
    return ordered


def _body_font_size(page_lines: list[list[_Line]]) -> float:
    """Cỡ chữ thân bài = mode theo trọng số SỐ KÝ TỰ (làm tròn 0.5pt).

    Trọng số ký tự để một trang bìa chữ to không kéo lệch mode; thân bài luôn
    áp đảo về khối lượng ký tự.
    """
    weights: Counter[float] = Counter()
    for lines in page_lines:
        for line in lines:
            weights[round(line.size * 2) / 2] += len(line.text)
    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def _fingerprint(text: str) -> str:
    """Vân tay dòng lặp: bỏ dấu + bỏ chữ số (số trang thay đổi theo trang)."""
    return re.sub(r"\d+", "", vn_norm(text)).strip()


def _repeated_margin_keys(page_lines: list[list[_Line]]) -> set[str]:
    total_pages = len(page_lines)
    if total_pages < _FINGERPRINT_MIN_PAGES:
        return set()
    pages_with_key: dict[str, set[int]] = defaultdict(set)
    for page_index, lines in enumerate(page_lines):
        for line in lines:
            if not line.in_margin:
                continue
            key = _fingerprint(line.text)
            if key:
                pages_with_key[key].add(page_index)
    threshold = max(_FINGERPRINT_MIN_PAGES, int(total_pages * _FINGERPRINT_RATIO))
    return {key for key, pages in pages_with_key.items() if len(pages) >= threshold}


def _detect_toc_pages(page_lines: list[list[_Line]]) -> set[int]:
    """Trang mục lục: ≥30% dòng (và ≥5 dòng) mang đuôi chấm-dẫn '..... 10'."""
    toc: set[int] = set()
    for index, lines in enumerate(page_lines, start=1):
        nonempty = [line for line in lines if line.text.strip()]
        if len(nonempty) < 5:
            continue
        dotted = sum(1 for line in nonempty if _TOC_LINE.search(line.text))
        if dotted >= 5 and dotted / len(nonempty) >= _TOC_DENSITY:
            toc.add(index)
    return toc


def _render_page(lines: list[_Line], body_size: float, strip_keys: set[str]) -> str:
    """Dựng lại văn bản trang: lột header/footer, đề cử heading theo font.

    Đề cử = thêm tiền tố '### ' markdown cho dòng nổi bật về font mà văn phạm
    từ khóa KHÔNG tự nhận ra (tiêu đề dạng 'TỔNG QUAN VỀ CSDL'). Dòng văn phạm
    tự nhận ra ('Chương 3', 'Điều 5. ...') giữ nguyên — chunker tự xử.
    """
    from ingest.chunk import classify_heading

    parts: list[str] = []
    previous_block: int | None = None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if line.in_margin:
            key = _fingerprint(text)
            if key in strip_keys or not key:  # vân tay lặp hoặc số trang trần
                continue

        if previous_block is not None and line.block != previous_block:
            parts.append("")  # ranh giới block = ranh giới đoạn
        previous_block = line.block

        if (
            body_size > 0
            and len(text) <= _MAX_HEADING_CHARS
            and (line.size >= body_size * _HEADING_SIZE_RATIO or line.bold)
            and classify_heading(text) is None
            and _plausible_title_line(text)
        ):
            parts.append(f"### {text}")
        else:
            parts.append(text)

    return "\n".join(parts).strip()


def _plausible_title_line(text: str) -> bool:
    """Hình thái tối thiểu cho heading đề cử theo font (font chỉ ĐỀ CỬ)."""
    if _TOC_LINE.search(text):
        return False
    if text.endswith(("{", "}", ";", "=", ",", ".", ":")):
        return False
    letters = [ch for ch in text if ch.isalpha()]
    # Ô bảng một-từ-ngắn ('STT', 'Tổng', 'Kiểm') hay in đậm — không đề cử.
    if len(letters) < 6 and len(text.split()) < 2:
        return False
    if len(letters) < 3:
        return False
    return text[0].isupper() or text[0].isdigit()


def _extract_pdf_plain(path: Path) -> Extraction:
    """Đường lùi khi PDF dị dạng làm pass layout ném lỗi."""
    import fitz

    pages: list[Page] = []
    with fitz.open(path) as document:
        for index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            pages.append(Page(number=index, text=text.strip()))

    page_count = len(pages)
    total = sum(len(p.text) for p in pages)
    is_scanned = page_count > 0 and (total / page_count) < SCANNED_CHARS_PER_PAGE
    front = [
        line for p in pages[:2] for line in p.text.splitlines() if line.strip()
    ][:80]
    return Extraction(
        pages=pages, page_count=page_count, is_scanned=is_scanned, front_matter=front
    )


# ----------------------------------------------------------------------------
# DOCX / PPTX / TXT — như v1, bổ sung front_matter
# ----------------------------------------------------------------------------

def _extract_docx(path: Path) -> Extraction:
    from docx import Document

    document = Document(str(path))
    lines: list[str] = []
    headings: list[str] = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style else ""
        if style.startswith("heading"):
            # Đánh dấu heading bằng tiền tố '#' theo cấp — chunker nhận ra và
            # ưu tiên cắt tại đây.
            level = _heading_level(style)
            lines.append(f"{'#' * level} {text}")
            headings.append(text)
        else:
            lines.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))

    return Extraction(
        pages=[Page(number=1, text="\n".join(lines), headings=headings)],
        page_count=1,
        front_matter=lines[:80],
    )


def _extract_pptx(path: Path) -> Extraction:
    from pptx import Presentation

    presentation = Presentation(str(path))
    pages: list[Page] = []

    for index, slide in enumerate(presentation.slides, start=1):
        parts: list[str] = []
        title = ""
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue
            if shape == getattr(slide.shapes, "title", None):
                title = text
                parts.insert(0, f"# {text}")
            else:
                parts.append(text)
        if getattr(slide, "has_notes_slide", False) and slide.notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(f"[Ghi chú] {notes}")

        pages.append(
            Page(
                number=index,
                text="\n".join(parts),
                headings=[title] if title else [],
            )
        )

    front = [
        line for p in pages[:3] for line in p.text.splitlines() if line.strip()
    ][:80]
    return Extraction(pages=pages, page_count=len(pages), front_matter=front)


def _extract_text(path: Path) -> Extraction:
    content = path.read_text(encoding="utf-8", errors="replace")
    front = [line for line in content.splitlines() if line.strip()][:80]
    return Extraction(
        pages=[Page(number=1, text=content)], page_count=1, front_matter=front
    )


def _heading_level(style_name: str) -> int:
    digits = "".join(ch for ch in style_name if ch.isdigit())
    try:
        return max(1, min(3, int(digits)))
    except ValueError:
        return 1
