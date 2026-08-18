"""GET /documents, /documents/{id}, /documents/{id}/download.

Quy tắc: tài liệu ngoài quyền trả 404, KHÔNG trả 403 — 403 xác nhận tài liệu
tồn tại, tự nó đã là một mẩu rò rỉ.

Tải file không bao giờ do Python bơm bytes: backend kiểm quyền rồi trả
`X-Accel-Redirect`, nginx phục vụ file từ internal location. Khi chạy dev không
có nginx, bật `DEV_SERVE_FILES=1` để FastAPI tự phục vụ (chỉ dùng cho demo).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.api.deps import CurrentIdentity
from app.config import get_settings
from app.core import retrieval
from app.core.access import can_read_document
from app.core.render import download_url
from app.schemas import DocType, DocumentListOut, DocumentOut

router = APIRouter(tags=["documents"])


@router.get("/documents", response_model=DocumentListOut)
async def list_documents(
    identity: CurrentIdentity,
    course: str | None = Query(None, description="Mã môn, ví dụ MAT1093"),
    type: DocType | None = Query(None),
    semester: str | None = Query(None, pattern=r"^20\d{2}[1-3]$"),
    lecturer: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=200, description="Tìm trong tiêu đề"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    rows, total = await run_in_threadpool(
        retrieval.list_documents,
        identity,
        course,
        type.value if type else None,
        semester,
        lecturer,
        q,
        page,
        page_size,
    )
    return DocumentListOut(
        items=[_to_out(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(document_id: int, identity: CurrentIdentity):
    row = await run_in_threadpool(retrieval.get_document, document_id)
    if not row or row.get("status") != "indexed" or not can_read_document(identity, row):
        raise HTTPException(status_code=404)
    return _to_out(row)


@router.get("/documents/{document_id}/preview")
async def preview_document(
    document_id: int,
    identity: CurrentIdentity,
    uid: int | None = Query(None, ge=1),
    page: int | None = Query(None, ge=1),
):
    """Xem trước inline cho popup trích dẫn — PDF render thẳng trong iframe,
    nhảy trang bằng fragment #page=N phía client.

    `uid`: iframe không gửi được header X-User-Id nên nhận danh tính qua query
    — CHỈ khi header vắng, cùng mức tin cậy demo với header (đều do client tự
    khai). Gắn SSO thật thì cookie phiên tự đi theo iframe, tham số này bỏ.
    """
    if identity.user_id is None and uid is not None:
        from app.core.access import load_identity

        identity = await run_in_threadpool(load_identity, uid)

    row = await run_in_threadpool(retrieval.get_document, document_id)
    if not row or row.get("status") != "indexed" or not can_read_document(identity, row):
        raise HTTPException(status_code=404)

    settings = get_settings()
    relative_path = str(row["file_path"]).replace("\\", "/").lstrip("/")

    # File Office không render inline được — dùng bản PDF đã convert lúc nạp
    # (files/previews/{id}.pdf, vẫn nằm trong FILES_ROOT nên đi chung đường
    # X-Accel-Redirect với file gốc).
    if not relative_path.lower().endswith(".pdf"):
        from ingest.preview import preview_path

        converted = preview_path(settings.files_root, document_id)
        if not converted.is_file():
            raise HTTPException(status_code=404)
        relative_path = f"previews/{document_id}.pdf"

    # Chỉ gửi VÀI TRANG quanh chỗ được trích, không gửi cả tệp.
    #
    # Popup trích dẫn nhúng PDF vào iframe. Tài liệu quảng bá 83 trang nặng
    # 26,8 MB, nên mỗi lần bấm một trích dẫn là đẩy đi 26,8 MB chỉ để hiện MỘT
    # trang — popup trắng vài giây rồi mới hiện, người dùng đọc là "lỗi".
    # Lát ba trang chỉ 0,46 MB. Lát được cache trên đĩa nên lần sau miễn phí.
    if page is not None:
        from ingest.preview import build_slice, slice_path, slice_range

        source = (settings.files_root / relative_path).resolve()
        # Đã xin lát thì LUÔN cắt, kể cả tệp nhỏ. Client tự tính số trang bên
        # trong lát; nếu máy chủ tùy nghi lúc cắt lúc không thì phép tính đó
        # sai mà client không có cách nào biết (iframe không đọc được header).
        if source.is_file():
            start, end = slice_range(page, int(row.get("page_count") or 0))
            target = slice_path(settings.files_root, document_id, start, end)
            if build_slice(source, target, start, end):
                relative_path = f"previews/{target.name}"

    absolute = (settings.files_root / relative_path).resolve()
    root = settings.files_root.resolve()
    if not str(absolute).startswith(str(root) + os.sep) and absolute != root:
        raise HTTPException(status_code=404)

    if settings.dev_serve_files:
        if not absolute.is_file():
            raise HTTPException(status_code=404)
        # KHÔNG đặt Content-Disposition attachment → trình duyệt render inline.
        return FileResponse(absolute, content_disposition_type="inline")

    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"{settings.xaccel_prefix}/{relative_path}",
            "Content-Disposition": "inline",
        },
    )


@router.get("/documents/{document_id}/download")
async def download_document(document_id: int, identity: CurrentIdentity):
    row = await run_in_threadpool(retrieval.get_document, document_id)
    if not row or row.get("status") != "indexed" or not can_read_document(identity, row):
        raise HTTPException(status_code=404)

    settings = get_settings()
    relative_path = str(row["file_path"]).replace("\\", "/").lstrip("/")

    # Phòng path traversal dù file_path do pipeline nạp sinh ra: đường dẫn
    # tuyệt đối sau khi ghép phải nằm trong FILES_ROOT.
    absolute = (settings.files_root / relative_path).resolve()
    root = settings.files_root.resolve()
    if not str(absolute).startswith(str(root) + os.sep) and absolute != root:
        raise HTTPException(status_code=404)

    filename = os.path.basename(relative_path)

    if settings.dev_serve_files:
        if not absolute.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(absolute, filename=filename)

    # Sản xuất: nginx internal location /protected-files/ phục vụ file.
    return Response(
        status_code=200,
        headers={
            "X-Accel-Redirect": f"{settings.xaccel_prefix}/{relative_path}",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


def _to_out(row: dict) -> DocumentOut:
    return DocumentOut(
        id=int(row["id"]),
        title=str(row["title"]),
        type=DocType(str(row["type"])),
        semester=row.get("semester"),
        lecturer=row.get("lecturer"),
        year=row.get("year"),
        course_code=row.get("course_code"),
        course_name=row.get("course_name"),
        page_count=row.get("page_count"),
        file_ext=str(row["file_ext"]),
        file_size=int(row["file_size"]),
        download_url=download_url(int(row["id"])),
    )
