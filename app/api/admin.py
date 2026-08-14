"""Các endpoint /admin — upload, reindex, metrics.

Tất cả nằm sau `require_admin` (role='admin' trong bảng users, sai thì 404).
"""

from __future__ import annotations

import re
import shutil
import tempfile
import threading
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import AdminIdentity
from app.config import get_settings
from app.db import fetch_all, fetch_one
from app.schemas import MetricsOut
from app.vn import vn_norm
from ingest.extract import SUPPORTED_EXTENSIONS

router = APIRouter(prefix="/admin", tags=["admin"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.post("/documents", status_code=201)
async def upload_document(
    identity: AdminIdentity,
    file: UploadFile,
    course_code: str | None = Query(None, max_length=32),
    doc_type: str | None = Query(None),
    semester: str | None = Query(None, pattern=r"^20\d{2}[1-3]$"),
    access: str = Query("public", pattern=r"^(public|faculty|course|staff)$"),
):
    """Nhận file, lưu vào FILES_ROOT/uploads/, nạp ngay.

    Metadata truyền qua query sẽ được ghi vào manifest.csv để tầng 3 của
    trình gán metadata áp dụng — cùng một đường với nạp từ thư mục.
    """
    settings = get_settings()

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Chỉ nhận: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    safe_name = _SAFE_NAME.sub("_", Path(file.filename or "upload").name)
    uploads_dir = settings.files_root / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    destination = uploads_dir / safe_name

    # Ghi qua file tạm rồi move — không bao giờ có file viết dở trong FILES_ROOT
    # cho timer 2h sáng quét nhầm.
    size = 0
    with tempfile.NamedTemporaryFile(dir=uploads_dir, delete=False) as buffer:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                Path(buffer.name).unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File quá 100 MB")
            buffer.write(chunk)
        temp_path = Path(buffer.name)
    shutil.move(temp_path, destination)

    if course_code or doc_type or semester or access != "public":
        _append_manifest(
            settings.files_root, destination, course_code, doc_type, semester, access
        )

    # --- Nạp BẤT ĐỒNG BỘ: trả về ngay, pipeline chạy thread nền -------------
    # File to trích xuất + chunk mất nhiều giây; bắt trình duyệt treo chờ là
    # trải nghiệm tồi. Trả checksum để client poll /admin/ingest-status/{ck}
    # (poll theo CHECKSUM chứ không theo id — ingest thay bản ghi stub bằng
    # bản ghi mới nên id đổi giữa chừng, checksum là khóa UNIQUE bất biến).
    from ingest.loader import sha256_file

    checksum = await run_in_threadpool(sha256_file, destination)
    existing = await run_in_threadpool(
        fetch_one,
        "SELECT id, status FROM documents WHERE checksum = :c",
        {"c": checksum},
    )

    # File ĐÃ nạp xong mà upload lại kèm metadata → ý người dùng là "áp
    # metadata này vào tài liệu đó", không phải "bỏ qua" — làm ngay (nhanh).
    if existing and existing["status"] == "indexed":
        if course_code or doc_type or semester or access != "public":
            return await run_in_threadpool(
                _apply_metadata_to_existing,
                settings.files_root, int(existing["id"]), destination,
                course_code, doc_type, semester, access,
            )
        return {
            "document_id": existing["id"], "status": "skipped",
            "checksum": checksum, "message": "trùng checksum, đã nạp trước đó",
        }

    stub_id = await run_in_threadpool(
        _create_processing_stub, destination, settings.files_root,
        doc_type, access, checksum, size,
    )
    threading.Thread(
        target=_ingest_in_background,
        args=(destination, settings.files_root, checksum),
        daemon=True,
    ).start()
    return {
        "document_id": stub_id, "status": "processing",
        "checksum": checksum, "message": "đang xử lý ở nền",
    }


def _create_processing_stub(
    destination: Path, files_root: Path, doc_type: str | None,
    access: str, checksum: str, size: int,
) -> int:
    """Bản ghi 'pending' hiện diện NGAY để client theo dõi tiến độ; pipeline nền
    sẽ thay nó bằng kết quả thật (ingest_file coi 'pending' là stub được thay)."""
    from app.db import execute

    relative = str(destination.relative_to(files_root)).replace("\\", "/")
    title = destination.stem.replace("_", " ")[:500]
    execute(
        """
        INSERT INTO documents
            (title, title_norm, type, access, status, file_path, file_ext,
             file_size, checksum, error_message)
        VALUES
            (:title, :title_norm, :type, :access, 'pending', :fp, :ext,
             :size, :ck, 'đang xử lý ở nền…')
        ON DUPLICATE KEY UPDATE
            status = 'pending', error_message = 'đang xử lý ở nền…',
            file_path = VALUES(file_path)
        """,
        {
            "title": title, "title_norm": vn_norm(title)[:500],
            # type tạm để thỏa NOT NULL — pipeline sẽ ghi loại thật.
            "type": doc_type or "tai_lieu_tham_khao",
            "access": access, "fp": relative,
            "ext": destination.suffix.lower().lstrip("."),
            "size": size, "ck": checksum,
        },
    )
    row = fetch_one("SELECT id FROM documents WHERE checksum = :c", {"c": checksum})
    return int(row["id"]) if row else 0


def _ingest_in_background(destination: Path, files_root: Path, checksum: str) -> None:
    from ingest.loader import ingest_file

    try:
        result = ingest_file(destination, files_root)
        if result.status == "indexed":
            _schedule_embed()  # chunk mới embed ở nền
    except Exception as exc:  # pipeline chết thì stub phải nói thật, không treo
        from app.db import execute

        try:
            execute(
                "UPDATE documents SET status='failed', error_message=:e WHERE checksum=:c",
                {"e": f"lỗi xử lý: {exc}"[:2000], "c": checksum},
            )
        except Exception:
            pass


@router.get("/ingest-status/{checksum}")
async def ingest_status(checksum: str, identity: AdminIdentity):
    """Client poll tiến độ nạp theo checksum (khóa bất biến qua mọi lần thay
    bản ghi). 'pending' = đang xử lý; kèm số chunk/embedded khi đã indexed."""
    row = await run_in_threadpool(
        fetch_one,
        """
        SELECT d.id, d.status, d.error_message,
               (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunks,
               (SELECT COUNT(*) FROM document_chunks c
                 WHERE c.document_id = d.id AND c.embedding IS NOT NULL) AS embedded
        FROM documents d WHERE d.checksum = :c
        """,
        {"c": checksum},
    )
    if not row:
        # Khe hở tí xíu giữa DELETE stub và INSERT bản ghi mới — vẫn đang xử lý.
        return {"status": "pending", "message": "đang xử lý"}
    return {
        "document_id": row["id"], "status": row["status"],
        "message": row["error_message"] or "",
        "chunks": row["chunks"], "embedded": row["embedded"],
    }


def _apply_metadata_to_existing(
    files_root: Path,
    document_id: int,
    uploaded_copy: Path,
    course_code: str | None,
    doc_type: str | None,
    semester: str | None,
    access: str,
) -> dict:
    """Cập nhật môn/loại/kỳ/quyền cho tài liệu đã nạp + ghi manifest để lần
    reindex sau giữ nguyên. Bản copy vừa upload trùng lặp thì dọn đi."""
    from app.db import execute, fetch_one
    from app.vn import vn_norm
    from ingest.meta import VALID_TYPES

    doc = fetch_one(
        "SELECT id, file_path FROM documents WHERE id = :id", {"id": document_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Không có tài liệu này")

    sets = ["access = :access"]
    params: dict = {"id": document_id, "access": access}

    if course_code:
        course = fetch_one(
            "SELECT id, faculty_id FROM courses WHERE code_norm = :c AND active = 1",
            {"c": vn_norm(course_code)},
        )
        if not course:
            raise HTTPException(
                status_code=422, detail=f"Mã môn '{course_code}' không có trong danh mục"
            )
        sets += ["course_id = :course_id", "faculty_id = :faculty_id"]
        params["course_id"] = int(course["id"])
        params["faculty_id"] = course["faculty_id"]

    if doc_type:
        if doc_type not in VALID_TYPES:
            raise HTTPException(status_code=422, detail=f"doc_type phải thuộc {sorted(VALID_TYPES)}")
        sets.append("type = :type")
        params["type"] = doc_type

    if semester:
        sets.append("semester = :semester")
        params["semester"] = semester

    execute(f"UPDATE documents SET {', '.join(sets)} WHERE id = :id", params)

    # Manifest ghi theo đường dẫn của BẢN GỐC đang indexed — nguồn sự thật lâu dài.
    _upsert_manifest(files_root, doc["file_path"], course_code, doc_type, semester, access)

    # Bản copy vừa upload là file thừa (trùng nội dung) — dọn, trừ khi nó chính
    # là file gốc (re-upload đúng chỗ cũ).
    try:
        original = (files_root / doc["file_path"]).resolve()
        if uploaded_copy.resolve() != original:
            uploaded_copy.unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "document_id": document_id,
        "status": "updated",
        "message": "tài liệu đã có sẵn — cập nhật môn/loại/học kỳ/quyền theo lựa chọn mới",
    }


# Một lượt quét tại một thời điểm — bấm nút hai lần không tạo hai lượt chạy đè
# nhau (checksum vốn chống nạp trùng, nhưng chạy đè nhau vẫn phí I/O và khó đọc log).
_scan_lock = threading.Lock()

# Embed chạy NỀN sau các thao tác admin — HTTP trả về ngay, không bắt người
# quản trị nhìn spinner vài phút với kho lớn. Lock chống hai lượt embed đè nhau
# (đè không hỏng dữ liệu nhưng phí token gọi trùng).
_embed_lock = threading.Lock()


def _schedule_embed() -> None:
    def _run() -> None:
        if not _embed_lock.acquire(blocking=False):
            return  # đã có lượt embed đang chạy — lượt sau tự vét phần còn lại
        try:
            from ingest.loader import embed_pending

            embed_pending()
        except Exception:  # pragma: no cover
            pass
        finally:
            _embed_lock.release()

    threading.Thread(target=_run, daemon=True).start()


@router.post("/scan")
async def scan_files(identity: AdminIdentity):
    """Quét FILES_ROOT và nạp mọi file mới — bản API của `ingest.cli scan`.

    Chạy ĐỒNG BỘ trong threadpool: hợp cho vài chục file khi demo/vận hành tay.
    Nạp định kỳ khối lượng lớn vẫn là việc của systemd timer 2h sáng.
    """
    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Đang có một lượt nạp chạy — chờ xong rồi bấm lại.",
        )
    try:
        return await run_in_threadpool(_run_scan, get_settings().files_root)
    finally:
        _scan_lock.release()


def _run_scan(root: Path) -> dict:
    from app.core import course_index
    from ingest.loader import embed_pending, scan_directory

    if not root.is_dir():
        raise HTTPException(
            status_code=422, detail=f"FILES_ROOT không tồn tại: {root}"
        )

    counts: Counter[str] = Counter()
    files: list[dict] = []
    for path, result in scan_directory(root):
        counts[result.status] += 1
        # skipped (đã nạp từ trước, checksum trùng) không cần kể lại từng dòng.
        if result.status != "skipped":
            files.append(
                {
                    "file": str(path.relative_to(root)).replace("\\", "/"),
                    "status": result.status,
                    "document_id": result.document_id,
                    "message": result.message,
                }
            )

    # GĐ2: embed phần thiếu chạy NỀN — response trả ngay, RAG tự dùng khi xong.
    summary = dict(counts)
    if counts.get("indexed"):
        _schedule_embed()
        summary["embed"] = "đang chạy nền"

    course_index.refresh()
    return {"summary": summary, "files": files}


@router.post("/reprocess-all")
async def reprocess_all(identity: AdminIdentity):
    """Xử lý lại TOÀN BỘ theo bảng documents: đọc từng path, chạy lại trọn
    pipeline (trích xuất → 5 tầng metadata → chunk → embed). Offline, chậm cũng
    được — dùng khi đổi luật metadata/chunker hoặc muốn làm sạch toàn kho."""
    if not _scan_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Đang có một lượt nạp chạy — chờ xong rồi bấm lại.",
        )
    try:
        return await run_in_threadpool(_run_reprocess_all, get_settings().files_root)
    finally:
        _scan_lock.release()


def _run_reprocess_all(root: Path) -> dict:
    from app.core import course_index
    from ingest.loader import embed_pending, reindex_document

    docs = fetch_all("SELECT id, file_path FROM documents ORDER BY id")
    counts: Counter[str] = Counter()
    files: list[dict] = []
    for doc in docs:
        result = reindex_document(int(doc["id"]), root)
        counts[result.status] += 1
        files.append(
            {
                "file": doc["file_path"],
                "status": result.status,
                "document_id": result.document_id,
                "message": result.message,
            }
        )

    summary = dict(counts)
    if counts.get("indexed"):
        _schedule_embed()
        summary["embed"] = "đang chạy nền"

    # document_id đã đổi hàng loạt → cache cũ trỏ link chết, dọn sạch.
    from app.core import cache

    summary["cache_cleared"] = cache.clear_all()
    course_index.refresh()
    return {"summary": summary, "files": files}


@router.post("/ocr/{document_id}")
async def ocr_document_endpoint(document_id: int, identity: AdminIdentity):
    """OCR một tài liệu nát chữ bằng Gemini vision rồi nạp lại. Đồng bộ —
    văn bản hành chính vài trang mất ~10-30s; giáo trình dày dùng CLI."""
    from ingest.ocr import ocr_document

    result = await run_in_threadpool(ocr_document, document_id)
    if result.status == "indexed":
        _schedule_embed()
    return {
        "document_id": result.document_id,
        "status": result.status,
        "message": result.message,
    }


@router.post("/reindex/{document_id}")
async def reindex(document_id: int, identity: AdminIdentity):
    from ingest.loader import reindex_document

    settings = get_settings()
    result = await run_in_threadpool(
        reindex_document, document_id, settings.files_root
    )
    if result.status == "failed" and result.document_id is None:
        raise HTTPException(status_code=404, detail=result.message)

    if result.status == "indexed":
        _schedule_embed()
        # id tài liệu đã đổi → câu trả lời cũ trong cache trỏ link chết.
        from app.core import cache

        await run_in_threadpool(cache.clear_all)

    return {
        "document_id": result.document_id,
        "status": result.status,
        "message": result.message,
    }


@router.get("/documents")
async def list_all_documents(identity: AdminIdentity):
    """Toàn bộ kho tài liệu cho bảng quản trị (kèm tickbox chọn xử lý lại)."""
    return await run_in_threadpool(
        fetch_all,
        """
        SELECT d.id, d.title, d.file_path, d.type, d.status, d.access,
               d.semester, d.page_count, c.code AS course_code,
               (SELECT COUNT(*) FROM document_chunks ch
                WHERE ch.document_id = d.id) AS chunks,
               (SELECT COUNT(*) FROM document_chunks ch
                WHERE ch.document_id = d.id AND ch.embedding IS NOT NULL) AS embedded
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        ORDER BY d.id DESC
        LIMIT 500
        """,
    )


class DeleteIn(BaseModel):
    document_ids: list[int] = Field(min_length=1, max_length=200)


@router.post("/documents/delete")
async def delete_documents(body: DeleteIn, identity: AdminIdentity):
    """Xóa vĩnh viễn các tài liệu đã tick: bản ghi DB (chunk cascade), FILE trên
    đĩa và dòng manifest — thiếu một trong ba là lượt quét sau nạp lại y nguyên."""
    return await run_in_threadpool(
        _delete_documents, get_settings().files_root, body.document_ids
    )


def _delete_documents(root: Path, ids: list[int]) -> dict:
    from app.core import cache
    from app.db import execute, in_clause

    placeholder, params = in_clause("d", ids)
    docs = fetch_all(
        f"SELECT id, file_path, title FROM documents WHERE id IN ({placeholder})",
        params,
    )
    if not docs:
        raise HTTPException(status_code=404, detail="Không có tài liệu nào khớp")

    removed_files = 0
    for doc in docs:
        try:
            (root / doc["file_path"]).unlink(missing_ok=True)
            removed_files += 1
        except OSError:  # file đang bị khóa/không quyền — vẫn xóa bản ghi
            pass

    execute(f"DELETE FROM documents WHERE id IN ({placeholder})", params)
    _remove_manifest_rows(root, [d["file_path"] for d in docs])
    cache.clear_all()  # câu trả lời cũ trỏ tài liệu đã xóa = link chết

    return {
        "deleted": [{"id": d["id"], "title": d["title"]} for d in docs],
        "count": len(docs),
        "files_removed": removed_files,
    }


def _remove_manifest_rows(files_root: Path, paths: list[str]) -> None:
    import csv

    from ingest.meta import MANIFEST_NAME

    manifest = files_root / MANIFEST_NAME
    if not manifest.exists():
        return
    normalized = {p.replace("\\", "/") for p in paths}
    with open(manifest, encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle)]
    kept = [
        r for r in rows
        if (r.get("file_path") or "").strip().replace("\\", "/") not in normalized
    ]
    if len(kept) == len(rows):
        return
    with open(manifest, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in kept:
            writer.writerow({col: row.get(col, "") for col in _MANIFEST_COLUMNS})


@router.get("/pending")
async def pending_documents(identity: AdminIdentity):
    """Danh sách file nạp dở — nguồn dữ liệu cho bảng 'Chờ xử lý' trên UI."""
    return await run_in_threadpool(
        fetch_all,
        """
        SELECT id, title, file_path, status, error_message, created_at
        FROM documents
        WHERE status IN ('pending_review', 'needs_ocr', 'failed')
        ORDER BY id DESC
        LIMIT 200
        """,
    )


@router.get("/courses")
async def all_courses(identity: AdminIdentity):
    """Toàn bộ danh mục môn cho dropdown gán metadata (admin, không phân trang)."""
    return await run_in_threadpool(
        fetch_all,
        "SELECT code, name FROM courses WHERE active = 1 ORDER BY code LIMIT 500",
    )


class AssignIn(BaseModel):
    document_id: int
    course_code: str | None = Field(None, max_length=32)
    doc_type: str
    semester: str | None = Field(None, pattern=r"^20\d{2}[1-3]$")
    access: str = Field("public", pattern=r"^(public|faculty|course|staff)$")
    title: str | None = Field(None, max_length=500)


@router.post("/assign")
async def assign_metadata(body: AssignIn, identity: AdminIdentity):
    """Gán môn + loại cho một file rồi nạp lại — thao tác 'chọn loại tương ứng'
    trên UI. Ghi qua manifest (tầng thắng tất cả) rồi chạy lại pipeline."""
    from ingest.loader import ingest_file, reindex_document
    from ingest.meta import VALID_TYPES

    if body.doc_type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"doc_type phải thuộc {sorted(VALID_TYPES)}")
    if body.doc_type != "quy_che" and not body.course_code:
        raise HTTPException(status_code=422, detail="Loại này bắt buộc chọn môn")

    settings = get_settings()
    doc = await run_in_threadpool(
        fetch_one,
        "SELECT id, file_path, status FROM documents WHERE id = :id",
        {"id": body.document_id},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Không có tài liệu này")

    def _do() -> dict:
        _upsert_manifest(
            settings.files_root, doc["file_path"], body.course_code,
            body.doc_type, body.semester, body.access, body.title,
        )
        absolute = settings.files_root / doc["file_path"]
        if absolute.is_file():
            result = ingest_file(absolute, settings.files_root)
        else:
            # File gốc không còn trên đĩa → thử nạp lại theo bản ghi.
            result = reindex_document(int(doc["id"]), settings.files_root)

        if result.status == "indexed":
            from app.core import cache

            cache.clear_all()  # id có thể đã đổi sau khi nạp lại
        return {
            "document_id": result.document_id,
            "status": result.status,
            "message": result.message,
        }

    result = await run_in_threadpool(_do)
    if result.get("status") == "indexed":
        _schedule_embed()
    return result


@router.get("/metrics", response_model=MetricsOut)
async def metrics(identity: AdminIdentity, days: int = Query(7, ge=1, le=90)):
    return await run_in_threadpool(_collect_metrics, days)


def _collect_metrics(days: int) -> MetricsOut:
    """Dashboard chi phí — hạ tầng đo lường của cả chiến lược tối ưu.

    Các con số đối chiếu với giả định thiết kế: metadata >= 45%, cache hit
    >= 35% sau 1 tháng, RAG <= 30%.
    """
    params = {"days": days}

    cost_by_day = fetch_all(
        """
        SELECT DATE(created_at) AS day,
               COUNT(*)                          AS queries,
               ROUND(SUM(cost_usd), 6)           AS cost_usd,
               SUM(tokens_in)                    AS tokens_in,
               SUM(tokens_out)                   AS tokens_out,
               ROUND(AVG(latency_ms))            AS avg_latency_ms
        FROM query_logs
        WHERE created_at >= DATE_SUB(UTC_DATE(), INTERVAL :days DAY)
        GROUP BY DATE(created_at)
        ORDER BY day DESC
        """,
        params,
    )

    route_distribution = fetch_all(
        """
        SELECT route, COUNT(*) AS n,
               ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS percent
        FROM query_logs
        WHERE created_at >= DATE_SUB(UTC_DATE(), INTERVAL :days DAY)
        GROUP BY route
        ORDER BY n DESC
        """,
        params,
    )

    totals = fetch_one(
        """
        SELECT COUNT(*) AS total,
               COALESCE(SUM(cost_usd), 0) AS cost,
               SUM(route = 'cache') AS cache_hits
        FROM query_logs
        WHERE created_at >= DATE_SUB(UTC_DATE(), INTERVAL :days DAY)
        """,
        params,
    )

    documents_by_status = fetch_all(
        "SELECT status, COUNT(*) AS n FROM documents GROUP BY status ORDER BY n DESC"
    )

    total_queries = int(totals["total"]) if totals else 0
    cache_hits = int(totals["cache_hits"] or 0) if totals else 0

    return MetricsOut(
        days=days,
        cost_by_day=[dict(r, day=str(r["day"])) for r in cost_by_day],
        route_distribution=[dict(r) for r in route_distribution],
        cache_hit_rate=round(cache_hits / total_queries, 4) if total_queries else 0.0,
        documents_by_status=[dict(r) for r in documents_by_status],
        total_cost_usd=round(float(totals["cost"]), 6) if totals else 0.0,
        total_queries=total_queries,
    )


_MANIFEST_COLUMNS = [
    "file_path", "title", "course_code", "doc_type", "semester", "lecturer", "access",
]


def _upsert_manifest(
    files_root: Path,
    relative: str,
    course_code: str | None,
    doc_type: str | None,
    semester: str | None,
    access: str,
    title: str | None = None,
) -> None:
    """Ghi/cập nhật MỘT dòng manifest cho file. Manifest là tầng thắng tất cả,
    nên gán metadata qua UI = ghi vào đây rồi nạp lại — cùng một đường với sửa
    tay, không có trạng thái riêng nào phải đồng bộ."""
    import csv

    from ingest.meta import MANIFEST_NAME, VALID_TYPES

    if doc_type and doc_type not in VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"doc_type phải thuộc {sorted(VALID_TYPES)}")

    manifest = files_root / MANIFEST_NAME
    rows: list[dict] = []
    if manifest.exists():
        with open(manifest, encoding="utf-8-sig", newline="") as handle:
            rows = [row for row in csv.DictReader(handle)]

    normalized = relative.replace("\\", "/")
    rows = [
        r for r in rows
        if (r.get("file_path") or "").strip().replace("\\", "/") != normalized
    ]
    rows.append(
        {
            "file_path": normalized,
            "title": title or "",
            "course_code": course_code or "",
            "doc_type": doc_type or "",
            "semester": semester or "",
            "lecturer": "",
            "access": access,
        }
    )

    with open(manifest, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_MANIFEST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in _MANIFEST_COLUMNS})


def _append_manifest(
    files_root: Path,
    file_path: Path,
    course_code: str | None,
    doc_type: str | None,
    semester: str | None,
    access: str,
) -> None:
    relative = str(file_path.relative_to(files_root)).replace("\\", "/")
    _upsert_manifest(files_root, relative, course_code, doc_type, semester, access)
