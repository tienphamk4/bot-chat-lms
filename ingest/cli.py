"""CLI nạp tài liệu — chạy tay hoặc qua systemd timer lúc 2h sáng.

    python -m ingest.cli scan                 # quét toàn bộ FILES_ROOT
    python -m ingest.cli scan --dir files/    # quét thư mục chỉ định
    python -m ingest.cli one files/x.pdf      # nạp một file
    python -m ingest.cli reindex 42           # nạp lại tài liệu id=42
    python -m ingest.cli resync-derived       # dựng lại cột dẫn xuất courses
    python -m ingest.cli review               # liệt kê file chờ xử lý

Không Celery, không hàng đợi — tác vụ theo lô, systemd timer là đủ (thiết kế
mục 2). Exit code khác 0 khi có file failed để timer báo lỗi qua systemd.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings

log = logging.getLogger("ingest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="quét thư mục và nạp mọi file mới")
    scan.add_argument("--dir", type=Path, default=None, help="mặc định FILES_ROOT")

    one = subparsers.add_parser("one", help="nạp một file")
    one.add_argument("path", type=Path)

    reindex = subparsers.add_parser("reindex", help="nạp lại một tài liệu theo id")
    reindex.add_argument("document_id", type=int)

    subparsers.add_parser("resync-derived", help="dựng lại cột dẫn xuất của courses")
    subparsers.add_parser("review", help="liệt kê tài liệu pending_review/needs_ocr/failed")

    # --- Giai đoạn 2 ---
    subparsers.add_parser("embed", help="lấp embedding cho chunk chưa có (GĐ2)")
    subparsers.add_parser("migrate-vector", help="thêm cột embedding nếu chưa có (003)")
    subparsers.add_parser(
        "build-vector-index",
        help="tạo index ScaNN sau khi embed xong toàn bộ (corpus lớn mới cần)",
    )
    ocr = subparsers.add_parser("ocr", help="OCR một tài liệu needs_ocr bằng Gemini vision")
    ocr.add_argument("document_id", type=int)
    ocr.add_argument("--max-pages", type=int, default=None,
                     help="nâng trần số trang OCR (mặc định 30)")

    # --- Đại tu 14/08 ---
    renorm = subparsers.add_parser(
        "renorm",
        help="tính lại mọi cột *_norm bằng vn_norm bản mới — chỉ UPDATE dòng đổi",
    )
    renorm.add_argument("--dry-run", action="store_true", help="chỉ đếm, không ghi")

    backfill = subparsers.add_parser(
        "backfill-context",
        help="gắn bối cảnh cho chunk đã nạp (009) — không phải nạp lại file",
    )
    backfill.add_argument("--doc-id", type=int, default=None, help="chỉ một tài liệu")
    backfill.add_argument(
        "--no-llm", action="store_true",
        help="chỉ dùng bối cảnh cấu trúc (0 token), bỏ tầng model",
    )
    backfill.add_argument("--dry-run", action="store_true", help="chỉ in, không ghi")

    reindex_all = subparsers.add_parser(
        "reindex-all",
        help="nạp lại TOÀN BỘ tài liệu qua pipeline mới (chunk + heading + tiêu đề)",
    )
    reindex_all.add_argument(
        "--status", default="indexed,pending_review",
        help="các status cần nạp lại, phân tách bằng phẩy",
    )

    import_courses = subparsers.add_parser(
        "import-courses",
        help="nhập/cập nhật môn học từ CSV — tự tính mọi cột dẫn xuất, không cần SQL",
    )
    import_courses.add_argument("csv_path", type=Path, help="xem scripts/courses-example.csv")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Cùng bộ giảm ồn với app/main.py — embed theo lô in rất nhiều dòng httpx/AFC.
    for noisy in ("google_genai", "google_genai.models", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.command == "scan":
        return _scan(args.dir)
    if args.command == "one":
        return _one(args.path)
    if args.command == "reindex":
        return _reindex(args.document_id)
    if args.command == "resync-derived":
        return _resync()
    if args.command == "review":
        return _review()
    if args.command == "embed":
        return _embed()
    if args.command == "migrate-vector":
        return _migrate_vector()
    if args.command == "build-vector-index":
        return _build_vector_index()
    if args.command == "ocr":
        from ingest.ocr import ocr_document

        result = ocr_document(args.document_id, max_pages=args.max_pages)
        log.info("OCR: %s — %s", result.status, result.message)
        if result.status == "indexed":
            from ingest.loader import embed_pending

            log.info("Embed: %s", embed_pending())
        return 0 if result.status == "indexed" else 1
    if args.command == "renorm":
        return _renorm(dry_run=args.dry_run)
    if args.command == "backfill-context":
        return _backfill_context(
            doc_id=args.doc_id, use_llm=not args.no_llm, dry_run=args.dry_run
        )
    if args.command == "reindex-all":
        return _reindex_all(args.status)
    if args.command == "import-courses":
        return _import_courses(args.csv_path)
    return 2


def _files_root(override: Path | None = None) -> Path:
    root = (override or get_settings().files_root).resolve()
    if not root.is_dir():
        log.error("Thư mục không tồn tại: %s", root)
        raise SystemExit(2)
    return root


def _scan(directory: Path | None) -> int:
    from ingest.loader import embed_pending, scan_directory

    root = _files_root(directory)
    counts: Counter[str] = Counter()

    for path, result in scan_directory(root):
        counts[result.status] += 1
        symbol = {"indexed": "+", "skipped": "="}.get(result.status, "!")
        log.info("%s %-14s %s (%s)", symbol, result.status,
                 path.relative_to(root), result.message)

    log.info("Xong: %s", dict(counts))

    # GĐ2: lấp embedding cho chunk mới. LLM tắt thì bỏ qua êm — FULLTEXT vẫn chạy.
    if _has_vector_column():
        log.info("Embed: %s", embed_pending())

    _refresh_index()
    return 1 if counts.get("failed") else 0


def _embed() -> int:
    from ingest.loader import embed_pending

    if not _has_vector_column():
        log.error("Chưa có cột embedding — chạy trước: python -m ingest.cli migrate-vector")
        return 2
    result = embed_pending()
    log.info("Embed: %s", result)
    return 1 if result.get("failed_batches") else 0


def _has_vector_column() -> bool:
    from app.db import fetch_value

    try:
        return bool(
            fetch_value(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = 'document_chunks' AND column_name = 'embedding'
                """
            )
        )
    except Exception:  # pragma: no cover
        return False


def _migrate_vector() -> int:
    """Bản idempotent của migrations/003_vector_cloudsql.sql."""
    from app.db import execute

    if _has_vector_column():
        log.info("Cột embedding đã tồn tại — không làm gì.")
        return 0
    execute(
        "ALTER TABLE document_chunks"
        " ADD COLUMN embedding VECTOR(768) USING VARBINARY NULL"
    )
    log.info("Đã thêm cột embedding VECTOR(768). Tiếp theo: python -m ingest.cli embed")
    return 0


def _build_vector_index() -> int:
    """Tạo index ScaNN của Cloud SQL — chỉ cần khi corpus lớn (exact KNN chậm).

    Yêu cầu: toàn bộ chunk đã có embedding (còn NULL sẽ lỗi), flag
    cloudsql_vector=on. BRUTE_FORCE cho <10k dòng, TREE_SQ cho >=10k.
    """
    from app.db import execute, fetch_value

    pending = fetch_value(
        "SELECT COUNT(*) FROM document_chunks WHERE embedding IS NULL"
    )
    if pending:
        log.error("Còn %s chunk chưa embed — chạy `embed` trước.", pending)
        return 1

    total = int(fetch_value("SELECT COUNT(*) FROM document_chunks") or 0)
    index_type = "TREE_SQ" if total >= 10_000 else "BRUTE_FORCE"
    execute(
        "CALL mysql.create_vector_index('vi_chunk',"
        " 'edu_docbot.document_chunks', 'embedding',"
        f" 'index_type={index_type}, distance_measure=COSINE')"
    )
    log.info("Đã tạo vector index (%s) trên %d chunk.", index_type, total)
    return 0


def _one(path: Path) -> int:
    from ingest.loader import ingest_file

    root = _files_root()
    target = path.resolve()
    if not target.is_file():
        log.error("File không tồn tại: %s", target)
        return 2
    if not target.is_relative_to(root):
        log.error("File phải nằm trong FILES_ROOT (%s)", root)
        return 2

    result = ingest_file(target, root)
    log.info("%s: %s (%s)", result.status, target.name, result.message)
    _refresh_index()
    return 0 if result.status in {"indexed", "skipped"} else 1


def _reindex(document_id: int) -> int:
    from ingest.loader import reindex_document

    result = reindex_document(document_id, _files_root())
    log.info("%s: doc %s (%s)", result.status, result.document_id, result.message)
    _refresh_index()
    return 0 if result.status == "indexed" else 1


def _resync() -> int:
    from app.core.courses import resync_derived

    updated = resync_derived()
    log.info("Đã dựng lại cột dẫn xuất cho %d môn", updated)
    _refresh_index()
    return 0


def _review() -> int:
    from app.db import fetch_all

    rows = fetch_all(
        """
        SELECT id, status, file_path, error_message
        FROM documents
        WHERE status IN ('pending_review', 'needs_ocr', 'failed')
        ORDER BY status, id
        """
    )
    if not rows:
        log.info("Không có tài liệu nào chờ xử lý.")
        return 0
    for row in rows:
        log.info("#%-5s %-15s %s — %s", row["id"], row["status"],
                 row["file_path"], row["error_message"] or "")
    log.info("Tổng: %d. Bổ sung metadata vào manifest.csv rồi chạy reindex.", len(rows))
    return 0


def _renorm(dry_run: bool = False) -> int:
    """Tính lại *_norm bằng vn_norm hiện hành, CHỈ UPDATE dòng có khác biệt.

    Dùng sau mỗi lần nâng cấp vn_norm (C++→cplusplus, 4,0→4_0, Cf→space...):
    dữ liệu norm cũ trong DB lệch với norm truy vấn mới → FULLTEXT trượt.
    Đọc theo lô id, so sánh tại app, UPDATE từng dòng khác — bảng nhỏ hơn
    100k dòng thì vài chục giây.
    """
    from app.db import execute, fetch_all
    from app.vn import vn_norm
    from app.core.courses import resync_derived

    changed_total = 0

    # 1) documents: title_norm + lecturer_norm
    rows = fetch_all("SELECT id, title, lecturer, title_norm, lecturer_norm FROM documents")
    doc_changed = 0
    for row in rows:
        new_title = vn_norm(row["title"] or "")[:500]
        new_lect = vn_norm(row["lecturer"]) if row["lecturer"] else None
        if new_title != (row["title_norm"] or "") or new_lect != row["lecturer_norm"]:
            doc_changed += 1
            if not dry_run:
                execute(
                    "UPDATE documents SET title_norm = :t, lecturer_norm = :l WHERE id = :id",
                    {"t": new_title, "l": new_lect, "id": row["id"]},
                )
    log.info("documents: %d/%d dòng đổi", doc_changed, len(rows))
    changed_total += doc_changed

    # 2) document_chunks: content_norm — theo lô 200 dòng cho nhẹ RAM/RTT
    last_id, chunk_changed, chunk_seen = 0, 0, 0
    while True:
        batch = fetch_all(
            """
            SELECT id, content, content_norm FROM document_chunks
            WHERE id > :last ORDER BY id LIMIT 200
            """,
            {"last": last_id},
        )
        if not batch:
            break
        for row in batch:
            chunk_seen += 1
            new_norm = vn_norm(row["content"] or "")
            if new_norm != (row["content_norm"] or ""):
                chunk_changed += 1
                if not dry_run:
                    execute(
                        "UPDATE document_chunks SET content_norm = :n WHERE id = :id",
                        {"n": new_norm, "id": row["id"]},
                    )
        last_id = batch[-1]["id"]
    log.info("document_chunks: %d/%d dòng đổi", chunk_changed, chunk_seen)
    changed_total += chunk_changed

    # 3) faculties.name_norm
    rows = fetch_all("SELECT id, name, name_norm FROM faculties")
    fac_changed = 0
    for row in rows:
        new_norm = vn_norm(row["name"] or "")[:255]
        if new_norm != (row["name_norm"] or ""):
            fac_changed += 1
            if not dry_run:
                execute(
                    "UPDATE faculties SET name_norm = :n WHERE id = :id",
                    {"n": new_norm, "id": row["id"]},
                )
    log.info("faculties: %d/%d dòng đổi", fac_changed, len(rows))
    changed_total += fac_changed

    # 4) courses: mọi cột dẫn xuất đi qua resync_derived (một nguồn sự thật)
    if dry_run:
        log.info("courses: bỏ qua ở dry-run (resync_derived luôn ghi lại)")
    else:
        updated = resync_derived()
        log.info("courses: resync_derived %d môn", updated)
        _refresh_index()

    log.info("%s: tổng %d dòng lệch chuẩn norm mới.",
             "DRY-RUN — chưa ghi gì" if dry_run else "Đã cập nhật", changed_total)
    return 0


def _reindex_all(status_csv: str) -> int:
    """Nạp lại từng tài liệu theo id qua pipeline hiện hành rồi embed phần thiếu."""
    from app.db import fetch_all, in_clause
    from ingest.loader import embed_pending, reindex_document

    statuses = [s.strip() for s in status_csv.split(",") if s.strip()]
    clause, params = in_clause("st", statuses)
    rows = fetch_all(
        f"SELECT id, file_path, status FROM documents WHERE status IN ({clause}) ORDER BY id",
        params,
    )
    if not rows:
        log.info("Không có tài liệu nào ở status %s.", statuses)
        return 0

    root = _files_root()
    counts: Counter[str] = Counter()
    for row in rows:
        result = reindex_document(row["id"], root)
        counts[result.status] += 1
        symbol = "+" if result.status == "indexed" else "!"
        log.info("%s doc %-4s %-14s %s", symbol, row["id"], result.status, result.message)

    log.info("Nạp lại xong: %s", dict(counts))
    if _has_vector_column():
        log.info("Embed: %s", embed_pending())
    _refresh_index()

    # Cache trả lời cũ dựng trên chunk cũ — dọn để khỏi trả nội dung lỗi thời.
    try:
        from app.core import cache as chat_cache

        chat_cache.clear_all()
        log.info("Đã xóa semantic cache.")
    except Exception:  # pragma: no cover
        log.warning("Không xóa được cache (bỏ qua).")
    return 1 if counts.get("failed") else 0


def _import_courses(csv_path: Path) -> int:
    """Nhập môn từ CSV qua save_course — đường ghi chuẩn duy nhất của courses.

    Cột: code,name,faculty_id,credits,aliases  (aliases phân tách bằng '|').
    Trùng code → CẬP NHẬT (upsert). Mọi cột dẫn xuất (*_norm, search_norm)
    tự tính, chỉ mục tên môn trong RAM tự làm mới — không cần resync-derived.
    """
    import csv as csv_module

    from app.core.courses import save_course

    if not csv_path.is_file():
        log.error("Không thấy file: %s", csv_path)
        return 2

    imported, failed = 0, 0
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for line_no, row in enumerate(csv_module.DictReader(handle), start=2):
            code = (row.get("code") or "").strip().upper()
            name = (row.get("name") or "").strip()
            if not code or not name:
                log.warning("Dòng %d thiếu code/name — bỏ qua", line_no)
                failed += 1
                continue
            aliases = [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
            try:
                course_id = save_course(
                    code=code,
                    name=name,
                    faculty_id=int(row["faculty_id"]) if (row.get("faculty_id") or "").strip() else None,
                    credits=int(row["credits"]) if (row.get("credits") or "").strip() else None,
                    aliases=aliases,
                )
                imported += 1
                log.info("+ %s — %s (id=%d, %d tên gọi tắt)", code, name, course_id, len(aliases))
            except Exception as exc:
                failed += 1
                log.error("! dòng %d (%s): %s", line_no, code, exc)

    _refresh_index()
    log.info("Xong: %d môn nhập/cập nhật, %d lỗi. Bot nhận tên môn mới ngay.", imported, failed)
    return 1 if failed else 0


def _refresh_index() -> None:
    from app.core import course_index

    try:
        course_index.refresh()
    except Exception:  # pragma: no cover
        log.debug("Không làm mới được course_index (không sao khi chạy CLI)")


def _backfill_context(
    doc_id: int | None = None, use_llm: bool = True, dry_run: bool = False
) -> int:
    """Gắn bối cảnh cho chunk ĐÃ nằm trong CSDL, không đụng tới file gốc.

    Ba cột được ghi lại: `context_text`, `is_fragment`, và `content_norm` (vì
    FULLTEXT phải khớp trên văn bản đã kèm bối cảnh). Cột `embedding` bị xóa
    để lượt `embed` sau nhúng lại theo văn bản mới — bỏ bước đó thì kênh vector
    vẫn giữ vector cũ và nửa cái vá không có tác dụng.

    Nạp lại cả file cũng ra kết quả này, nhưng tốn OCR/trích xuất lại và làm
    đổi id chunk; ở đây chỉ cần một lượt ghi.
    """
    from sqlalchemy import text

    from app.db import connection, fetch_all
    from app.vn import vn_norm
    from ingest import context as chunk_context

    where = "WHERE d.id = :doc_id" if doc_id else ""
    params = {"doc_id": doc_id} if doc_id else {}
    docs = fetch_all(
        f"""
        SELECT d.id, d.title, c.name AS course_name
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        {where}
        ORDER BY d.id
        """,
        params,
    )
    if not docs:
        log.error("Không có tài liệu nào khớp")
        return 1

    total = fragments = 0
    for doc in docs:
        rows = fetch_all(
            """
            SELECT id, content, heading_path, page_from
            FROM document_chunks WHERE document_id = :d ORDER BY chunk_index
            """,
            {"d": doc["id"]},
        )
        if not rows:
            continue
        log.info("[%s] %s — %d chunk", doc["id"], doc["title"], len(rows))
        contexts = chunk_context.build(
            [dict(r) for r in rows],
            doc_title=doc["title"],
            course_name=doc.get("course_name"),
            use_llm=use_llm,
        )
        payload = []
        for row, ctx in zip(rows, contexts):
            flag = int(chunk_context.is_fragmented(row["content"]))
            fragments += flag
            payload.append(
                {
                    "id": row["id"],
                    "ctx": ctx or None,
                    "flag": flag,
                    "norm": vn_norm(chunk_context.indexed_text(ctx, row["content"])),
                }
            )
        total += len(payload)
        if dry_run:
            for item, row in list(zip(payload, rows))[:5]:
                log.info("  tr%s -> %s", row["page_from"], item["ctx"])
            continue
        with connection() as conn:
            conn.execute(
                text(
                    """
                    UPDATE document_chunks
                       SET context_text = :ctx, is_fragment = :flag,
                           content_norm = :norm, embedding = NULL
                     WHERE id = :id
                    """
                ),
                payload,
            )

    log.info(
        "Xong: %d chunk%s, %d chunk là bảng vỡ định dạng (bị loại khỏi RAG). "
        "Chạy `embed` để nhúng lại.",
        total,
        " (dry-run)" if dry_run else "",
        fragments,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
