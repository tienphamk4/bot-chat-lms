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
    """Dọn file preview khi xóa tài liệu — thiếu cũng không sao."""
    try:
        preview_path(files_root, document_id).unlink(missing_ok=True)
    except OSError:
        pass
