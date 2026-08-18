"""Sinh PDF xem trước cho tài liệu Office (PPTX/DOCX) — kiểu Google Drive.

Trình duyệt chỉ render inline được PDF; slide PPTX hay DOCX muốn có popup xem
trước phải convert MỘT LẦN lúc nạp, lưu `files/previews/{document_id}.pdf`.
Trang PDF khớp 1:1 với slide (PPTX) nên nhảy trang theo trích dẫn vẫn đúng.

Chuỗi bộ chuyển, cái nào có thì dùng:
1. LibreOffice `soffice --headless` — đường CHUẨN trên máy chủ Linux
   (`apt install libreoffice-impress libreoffice-writer`), có cả bản Windows.
2. PowerPoint/Word COM qua PowerShell — máy dev Windows có sẵn Office thì dùng
   luôn, không cần cài thêm gì.

Không có bộ chuyển nào → bỏ qua êm (popup rơi về hiển thị đoạn trích + nút
tải). Convert hỏng KHÔNG được làm hỏng mẻ nạp — chỉ log.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

PREVIEWABLE_EXTENSIONS = {".pptx", ".docx"}
_TIMEOUT_SECONDS = 180

_POWERPOINT_PS = r"""
$src = '{src}'; $dst = '{dst}'
$pp = New-Object -ComObject PowerPoint.Application
try {{
  $pres = $pp.Presentations.Open($src, [Microsoft.Office.Core.MsoTriState]::msoTrue,
          [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoFalse)
  $pres.SaveAs($dst, 32)   # 32 = ppSaveAsPDF
  $pres.Close()
}} finally {{ $pp.Quit() }}
"""

_WORD_PS = r"""
$src = '{src}'; $dst = '{dst}'
$w = New-Object -ComObject Word.Application
$w.Visible = $false
try {{
  $doc = $w.Documents.Open($src, $false, $true)
  $doc.SaveAs2($dst, 17)   # 17 = wdFormatPDF
  $doc.Close(0)
}} finally {{ $w.Quit() }}
"""


def previews_dir(files_root: Path) -> Path:
    return files_root / "previews"


def preview_path(files_root: Path, document_id: int) -> Path:
    return previews_dir(files_root) / f"{document_id}.pdf"


# Số trang lấy thêm mỗi bên chỗ được trích. ±1 cho ba trang — đủ để đọc ngữ
# cảnh hai bên mà vẫn nhẹ. Đo trên tài liệu tuyển sinh: cả tệp 26,8 MB (83
# trang), lát ba trang 0,46 MB, lát năm trang 1,98 MB.
PREVIEW_SPAN = 1


def slice_path(files_root: Path, document_id: int, start: int, end: int) -> Path:
    return previews_dir(files_root) / f"{document_id}-p{start}-{end}.pdf"


def slice_range(page: int, page_count: int, span: int = PREVIEW_SPAN) -> tuple[int, int]:
    """Khoảng trang (1-based, đã kẹp trong tài liệu) quanh trang được trích."""
    start = max(1, page - span)
    end = min(page_count, page + span) if page_count else page + span
    return start, max(start, end)


def build_slice(source: Path, target: Path, start: int, end: int) -> bool:
    """Cắt các trang [start, end] của `source` thành một PDF nhỏ, có cache.

    Vì sao cần: popup trích dẫn nhúng cả tệp gốc vào iframe. Với tài liệu
    quảng bá 83 trang nặng 26,8 MB, mỗi lần người dùng bấm một trích dẫn là
    máy chủ đẩy đi 26,8 MB chỉ để hiện MỘT trang — trên máy nhỏ thì popup
    trắng vài giây rồi mới hiện, trông y như hỏng.
    """
    if target.is_file() and target.stat().st_size > 0:
        return True
    try:
        import pymupdf

        target.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(source) as document:
            if document.page_count == 0:
                return False
            first = max(0, min(document.page_count - 1, start - 1))
            last = max(first, min(document.page_count - 1, end - 1))
            with pymupdf.open() as sliced:
                sliced.insert_pdf(document, from_page=first, to_page=last)
                sliced.save(target, garbage=3, deflate=True)
        return target.is_file() and target.stat().st_size > 0
    except Exception:  # pragma: no cover - PDF dị dạng
        log.warning("Không cắt được lát xem trước cho %s", source.name)
        return False


def generate_preview(source: Path, document_id: int, files_root: Path) -> bool:
    """Convert source → previews/{id}.pdf. True = có file preview dùng được."""
    if source.suffix.lower() not in PREVIEWABLE_EXTENSIONS:
        return False
    target = preview_path(files_root, document_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        if _try_soffice(source, target):
            return True
        if sys.platform == "win32" and _try_office_com(source, target):
            return True
    except Exception as exc:  # convert hỏng không được làm hỏng mẻ nạp
        log.warning("Preview %s thất bại: %s", source.name, exc)
    if target.is_file() and target.stat().st_size > 0:
        return True
    log.info("Không có bộ chuyển PDF cho %s — popup sẽ rơi về đoạn trích.", source.name)
    return False


def _try_soffice(source: Path, target: Path) -> bool:
    soffice = shutil.which("soffice") or shutil.which("soffice.exe")
    if not soffice:
        return False
    # soffice tự đặt tên output theo tên file nguồn → convert vào thư mục tạm
    # rồi move về đúng tên {document_id}.pdf.
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", tmp, str(source)],
            capture_output=True, timeout=_TIMEOUT_SECONDS,
        )
        produced = Path(tmp) / (source.stem + ".pdf")
        if result.returncode == 0 and produced.is_file():
            shutil.move(str(produced), target)
            return True
    log.warning("soffice convert lỗi (%s): %s", source.name,
                result.stderr.decode(errors="replace")[:300])
    return False


def _try_office_com(source: Path, target: Path) -> bool:
    """PowerPoint/Word COM qua PowerShell — chỉ máy Windows có Office."""
    script_tpl = _POWERPOINT_PS if source.suffix.lower() == ".pptx" else _WORD_PS
    script = script_tpl.format(
        src=str(source.resolve()).replace("'", "''"),
        dst=str(target.resolve()).replace("'", "''"),
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=_TIMEOUT_SECONDS,
    )
    if result.returncode == 0 and target.is_file() and target.stat().st_size > 0:
        return True
    log.warning("Office COM convert lỗi (%s): %s", source.name,
                result.stderr.decode(errors="replace")[:300])
    return False


def remove_preview(files_root: Path, document_id: int) -> None:
    """Dọn file preview và MỌI lát đã cắt khi xóa tài liệu.

    Bỏ sót lát thì tài liệu mới trùng id sẽ dùng lại lát của tài liệu cũ —
    người dùng bấm trích dẫn và thấy nội dung của một tài liệu đã bị xóa.
    """
    try:
        preview_path(files_root, document_id).unlink(missing_ok=True)
        for stale in previews_dir(files_root).glob(f"{document_id}-p*.pdf"):
            stale.unlink(missing_ok=True)
    except OSError:
        pass
