"""Ghi tài liệu vào CSDL — khâu cuối của pipeline nạp.

Nguyên tắc:
- Checksum SHA-256 quyết định danh tính file. Trùng checksum → bỏ qua.
  Cùng đường dẫn nhưng checksum đổi → xóa chunk cũ, nạp lại đúng tài liệu đó.
- documents + document_chunks ghi trong MỘT transaction: không bao giờ có tài
  liệu 'indexed' thiếu chunk hay chunk mồ côi.
- File lạ/hỏng đánh dấu failed/pending_review/needs_ocr và đi tiếp — một file
  không làm hỏng cả mẻ.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.core import course_index
from app.db import connection, fetch_all, fetch_one
from app.vn import vn_norm
from ingest import chunk as chunker
from ingest import extract as extractor
from ingest import meta as metadata

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    document_id: int | None
    status: str  # indexed | needs_ocr | pending_review | failed | skipped
    message: str = ""


def embed_pending(batch_size: int = 100) -> dict:
    """Lấp embedding cho mọi chunk chưa có (GĐ2) — chạy sau scan, idempotent.

    Chunk hỏng ở lô nào thì lô đó để lại NULL, lần chạy sau thử tiếp — không có
    trạng thái kẹt. Trả về {'embedded': n, 'tokens': n, 'failed_batches': n}.
    LLM tắt/không có credential → trả ngay {'embedded': 0, 'skipped': True}.
    """
    from app.llm import embeddings

    embedded = 0
    tokens = 0
    failed_batches = 0

    while True:
        rows = fetch_all_pending(batch_size)
        if not rows:
            break

        # Embed văn bản ĐÃ kèm bối cảnh, đúng thứ đã đưa vào FULLTEXT. Vector
        # của một danh sách môn học trần trụi không mang chủ đề nào, nên kênh
        # vector cũng trượt y như kênh từ khóa nếu bỏ bước này.
        from ingest.context import indexed_text

        batch = embeddings.embed_texts(
            [indexed_text(r.get("context_text"), r["content"]) for r in rows]
        )
        if batch is None:
            # Không phân biệt được "tắt" với "hỏng tạm" ở đây — cả hai đều
            # dừng vòng lặp, lần scan sau thử lại.
            if embedded == 0 and failed_batches == 0:
                return {"embedded": 0, "skipped": True}
            failed_batches += 1
            break

        pairs = list(zip(rows, batch.vectors))
        with connection() as conn:
            for row, vector in pairs:
                conn.execute(
                    text(
                        "UPDATE document_chunks"
                        " SET embedding = string_to_vector(:v) WHERE id = :id"
                    ),
                    {"v": embeddings.to_sql_vector(vector), "id": row["id"]},
                )
        embedded += len(pairs)
        tokens += batch.tokens

        # API trả thiếu vector so với số văn bản gửi đi = bất thường; dừng để
        # không lặp vô hạn trên cùng một lô — phần thiếu vẫn NULL, lần sau thử lại.
        if len(batch.vectors) != len(rows):
            log.warning(
                "Embed trả %d vector cho %d văn bản — dừng lô",
                len(batch.vectors), len(rows),
            )
            failed_batches += 1
            break

    result = {"embedded": embedded, "tokens": tokens}
    if failed_batches:
        result["failed_batches"] = failed_batches
    if embedded:
        log.info("Đã embed %d chunk (~%d token đầu vào)", embedded, tokens)
    return result


def _course_name(course_id: int | None) -> str | None:
    """Tên môn học của tài liệu — một mảnh bối cảnh miễn phí."""
    if not course_id:
        return None
    row = fetch_one("SELECT name FROM courses WHERE id = :id", {"id": course_id})
    return (row or {}).get("name")


def fetch_all_pending(batch_size: int):
    from app.db import fetch_all

    return fetch_all(
        """
        SELECT ch.id, ch.content, ch.context_text
        FROM document_chunks ch
        JOIN documents d ON d.id = ch.document_id
        WHERE ch.embedding IS NULL AND d.status = 'indexed'
        ORDER BY ch.id
        LIMIT :n
        """,
        {"n": batch_size},
    )


def scan_directory(root: Path):
    """Quét đệ quy FILES_ROOT, nạp từng file, yield (đường dẫn, kết quả).

    Đây là vòng lặp quét DUY NHẤT — CLI `ingest scan`, endpoint /admin/scan và
    timer 2h sáng đều đi qua đây, để luật bỏ qua manifest.csv nằm ở một chỗ.
    """
    from ingest.meta import MANIFEST_NAME

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        yield path, ingest_file(path, root)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_file(
    path: Path,
    files_root: Path,
    extraction_override: "extractor.Extraction | None" = None,
) -> IngestResult:
    """`extraction_override`: dùng văn bản đã OCR thay cho trích xuất từ file —
    khi đó bỏ qua cổng kiểm scan/nát-dấu (người gọi chịu trách nhiệm chất lượng)."""
    if path.suffix.lower() not in extractor.SUPPORTED_EXTENSIONS:
        return IngestResult(None, "skipped", f"bỏ qua định dạng {path.suffix}")

    checksum = sha256_file(path)
    relative = str(path.relative_to(files_root)).replace("\\", "/")

    existing = fetch_one(
        "SELECT id, status, file_path FROM documents WHERE checksum = :c",
        {"c": checksum},
    )
    stale = None
    if existing:
        if existing["status"] in ("pending", "pending_review", "needs_ocr", "failed"):
            # File từng nạp DỞ (hoặc là stub 'pending' do upload async vừa tạo
            # để theo dõi tiến độ) — không bỏ qua: chạy trọn pipeline với trạng
            # thái hiện tại. Bản ghi cũ bị thay bằng kết quả mới.
            stale = {"id": existing["id"]}
        else:
            return IngestResult(
                int(existing["id"]), "skipped", "trùng checksum, đã nạp trước đó"
            )

    if stale is None:
        # Cùng đường dẫn, nội dung đổi → nạp lại: xóa bản ghi cũ (chunk cascade).
        stale = fetch_one(
            "SELECT id FROM documents WHERE file_path = :p", {"p": relative}
        )

    doc_meta = metadata.resolve(path, files_root)
    course_id, faculty_id = _resolve_course(doc_meta.course_code)

    if extraction_override is not None:
        extraction = extraction_override
    else:
        try:
            extraction = extractor.extract(path)
        except Exception as exc:
            log.exception("Trích xuất thất bại: %s", path)
            return _write_stub(
                relative, path, checksum, doc_meta, course_id, faculty_id,
                status="failed", error=f"trích xuất thất bại: {exc}", stale_id=stale,
            )

    if extraction_override is None and extraction.is_scanned:
        return _write_stub(
            relative, path, checksum, doc_meta, course_id, faculty_id,
            status="needs_ocr", error="bản quét ảnh, chờ OCR",
            page_count=extraction.page_count, stale_id=stale,
        )

    # Hộ chiếu chất lượng ĐO MỌI TRANG (không lấy mẫu 5 trang đầu — bài học
    # giáo trình OOP: bìa sạch, thân 89% mojibake vẫn lọt): mojibake TCVN3
    # (chữ Cyrillic lạ) hoặc tiếng Việt vặt-sạch-dấu → cách ly chờ OCR, kèm
    # danh sách trang hỏng để người quản trị nhìn được tận nơi.
    if extraction_override is None:
        from ingest import quality

        doc_quality = quality.assess(extraction.pages)
        if doc_quality.verdict_bad:
            return _write_stub(
                relative, path, checksum, doc_meta, course_id, faculty_id,
                status="needs_ocr",
                error=(
                    f"chữ trích từ file hỏng ({doc_quality.describe()}) — "
                    "thường do PDF scan hoặc font TCVN3/VNI lỗi. Bấm 'OCR bằng AI' "
                    "để đọc lại; nạp ở tình trạng này sẽ đầu độc kết quả tìm kiếm"
                ),
                page_count=extraction.page_count, stale_id=stale,
            )

    # --- Tầng 4: nội dung file — LUÔN chạy -----------------------------------
    # Tiêu đề thật nằm ở bìa chứ không phải tên file ('16 lap trinh web nang
    # cao 0193' → 'Giáo Trình Lập Trình Web Nâng Cao'); môn/loại chỉ điền chỗ
    # trống, không ghi đè tên file/thư mục/manifest.
    metadata.enrich_from_content(doc_meta, extraction)
    if doc_meta.course_code and course_id is None:
        course_id, faculty_id = _resolve_course(doc_meta.course_code)

    # --- Tầng 5: LLM — chỉ khi 4 tầng máy móc vẫn thiếu trường ---------------
    if doc_meta.needs_review:
        head_text = " ".join(p.text for p in extraction.pages[:3])
        if get_settings().ingest_llm_meta:
            llm_meta = metadata.classify_with_llm(path.name, head_text)
            if llm_meta is not None:
                if doc_meta.course_code is None and llm_meta.course_code:
                    doc_meta.course_code = llm_meta.course_code
                if doc_meta.doc_type is None and llm_meta.doc_type:
                    doc_meta.doc_type = llm_meta.doc_type
                doc_meta.sources.extend(llm_meta.sources)

        course_id, faculty_id = _resolve_course(doc_meta.course_code)

    # Mã môn bắt được nhưng KHÔNG có trong danh mục courses → dừng lại hỏi
    # người, không lặng lẽ nạp với course_id NULL (tài liệu mồ côi: lọc theo
    # môn không bao giờ thấy nó).
    if doc_meta.course_code and course_id is None:
        return _write_stub(
            relative, path, checksum, doc_meta, course_id, faculty_id,
            status="pending_review",
            error=(
                f"mã môn '{doc_meta.course_code}' không có trong danh mục môn học. "
                f"Hoặc mã bị gõ nhầm (sửa tên file/manifest rồi bấm 'Nạp tài liệu' lại), "
                f"hoặc môn chưa được thêm vào bảng courses (thêm môn trước rồi nạp lại)."
            ),
            page_count=extraction.page_count, stale_id=stale,
        )

    if doc_meta.needs_review:
        missing = []
        if doc_meta.doc_type is None:
            missing.append("loại tài liệu")
        if doc_meta.doc_type != "quy_che" and doc_meta.course_code is None:
            missing.append("mã môn")
        tried = ", ".join(doc_meta.sources) or "tên file/thư mục/manifest đều không có manh mối"
        return _write_stub(
            relative, path, checksum, doc_meta, course_id, faculty_id,
            status="pending_review",
            error=(
                f"chưa xác định được {' và '.join(missing)}. Sửa một trong ba cách rồi "
                f"bấm 'Nạp tài liệu' lần nữa: (1) đổi tên file kèm mã môn + loại, "
                f"vd 'MAT1093_giao-trinh_{path.stem[:30]}{path.suffix}'; "
                f"(2) chuyển file vào thư mục dạng MAT1093/giao_trinh/; "
                f"(3) thêm dòng cho file này vào manifest.csv. (đã dò: {tried})"
            ),
            page_count=extraction.page_count, stale_id=stale,
        )

    chunks = chunker.split(extraction)
    if not chunks:
        return _write_stub(
            relative, path, checksum, doc_meta, course_id, faculty_id,
            status="failed", error="không trích được nội dung nào",
            page_count=extraction.page_count, stale_id=stale,
        )

    # CỔNG CUỐI trước INSERT — tuyến phòng thủ độc lập với hộ chiếu trang:
    # chunk nào chuẩn hóa xong vẫn sót chữ lạ (>1% chữ cái) là mojibake. Quá 5%
    # số chunk như vậy → cách ly cả tài liệu (KHÔNG drop lẻ chunk — mất dữ liệu
    # âm thầm là anti-pattern; theo phán quyết phản biện).
    from app.vn import norm_residue_ratio

    residue_chunks = [
        piece.index for piece in chunks
        if norm_residue_ratio(vn_norm(piece.content)) > 0.01
    ]
    if extraction_override is None and len(residue_chunks) / len(chunks) > 0.05:
        preview = ", ".join(str(i) for i in residue_chunks[:10])
        return _write_stub(
            relative, path, checksum, doc_meta, course_id, faculty_id,
            status="needs_ocr",
            error=(
                f"{len(residue_chunks)}/{len(chunks)} đoạn còn ký tự lạ sau chuẩn hóa "
                f"(đoạn {preview}…) — mojibake lọt qua tầng trên. Bấm 'OCR bằng AI' để đọc lại"
            ),
            page_count=extraction.page_count, stale_id=stale,
        )

    # BỐI CẢNH cho từng chunk — chạy TRƯỚC khi ghi, vì content_norm (FULLTEXT)
    # phải chứa nó. Tầng cấu trúc miễn phí luôn chạy; tầng model chỉ chạy khi
    # bật, và hỏng thì rơi êm chứ không chặn việc nạp.
    from ingest import context as chunk_context

    course_name = _course_name(course_id)
    chunk_dicts = [
        {
            "content": piece.content,
            "heading_path": piece.heading_path,
            "page_from": piece.page_from,
        }
        for piece in chunks
    ]
    contexts = chunk_context.build(
        chunk_dicts,
        doc_title=doc_meta.title,
        course_name=course_name,
        use_llm=get_settings().chunk_context_llm,
    )

    with connection() as conn:
        if stale:
            conn.execute(
                text("DELETE FROM documents WHERE id = :id"), {"id": stale["id"]}
            )

        result = conn.execute(
            text(
                """
                INSERT INTO documents
                    (course_id, faculty_id, title, title_norm, type, semester,
                     lecturer, lecturer_norm, year, access, status,
                     file_path, file_ext, file_size, page_count, checksum,
                     indexed_at)
                VALUES
                    (:course_id, :faculty_id, :title, :title_norm, :type, :semester,
                     :lecturer, :lecturer_norm, :year, :access, 'indexed',
                     :file_path, :file_ext, :file_size, :page_count, :checksum,
                     UTC_TIMESTAMP())
                """
            ),
            {
                "course_id": course_id,
                "faculty_id": faculty_id,
                "title": doc_meta.title,
                "title_norm": vn_norm(doc_meta.title),
                "type": doc_meta.doc_type,
                "semester": doc_meta.semester,
                "lecturer": doc_meta.lecturer,
                "lecturer_norm": vn_norm(doc_meta.lecturer) if doc_meta.lecturer else None,
                "year": doc_meta.year,
                "access": doc_meta.access,
                "file_path": relative,
                "file_ext": path.suffix.lower().lstrip("."),
                "file_size": path.stat().st_size,
                "page_count": extraction.page_count,
                "checksum": checksum,
            },
        )
        document_id = int(result.lastrowid)

        conn.execute(
            text(
                """
                INSERT INTO document_chunks
                    (document_id, chunk_index, content, content_norm,
                     context_text, is_fragment,
                     heading_path, page_from, page_to, token_count)
                VALUES
                    (:document_id, :chunk_index, :content, :content_norm,
                     :context_text, :is_fragment,
                     :heading_path, :page_from, :page_to, :token_count)
                """
            ),
            [
                {
                    "document_id": document_id,
                    "chunk_index": piece.index,
                    # Cắt chuỗi số mồ côi ngay khi ghi: bảng vỡ định dạng thì
                    # con số đã mất hàng/cột, giữ lại chỉ mời model bịa.
                    "content": chunk_context.strip_fragments(piece.content),
                    # FULLTEXT khớp trên văn bản ĐÃ kèm bối cảnh — đó là điểm
                    # mấu chốt: chunk "HỌC PHẦN CHUNG: Lập trình Python…" giờ
                    # khớp được câu hỏi có chữ "An toàn thông tin".
                    "content_norm": vn_norm(
                        chunk_context.indexed_text(ctx, chunk_context.strip_fragments(piece.content))
                    ),
                    "context_text": ctx or None,
                    "is_fragment": int(chunk_context.is_fragmented(piece.content)),
                    "heading_path": piece.heading_path,
                    "page_from": piece.page_from,
                    "page_to": piece.page_to,
                    "token_count": piece.token_count,
                }
                for piece, ctx in zip(chunks, contexts)
            ],
        )

    # PDF xem trước cho file Office (popup trích dẫn mở như Google Drive).
    # Convert hỏng/không có bộ chuyển → bỏ qua êm, đã log bên trong.
    try:
        from ingest import preview

        preview.generate_preview(path, int(document_id), files_root)
    except Exception:  # pragma: no cover - tùy môi trường có Office/soffice
        log.warning("Sinh preview cho %s thất bại (bỏ qua)", path.name)

    return IngestResult(document_id, "indexed", f"{len(chunks)} chunk")


def reindex_document(document_id: int, files_root: Path) -> IngestResult:
    row = fetch_one(
        "SELECT file_path FROM documents WHERE id = :id", {"id": document_id}
    )
    if not row:
        return IngestResult(None, "failed", "không tìm thấy tài liệu")

    path = files_root / str(row["file_path"])
    if not path.is_file():
        return IngestResult(document_id, "failed", f"file không còn: {row['file_path']}")

    return replace_document(document_id, path, files_root)


def replace_document(
    document_id: int,
    path: Path,
    files_root: Path,
    extraction_override: "extractor.Extraction | None" = None,
) -> IngestResult:
    """Xóa bản ghi cũ rồi nạp lại file — GIỮ danh sách trợ lý được gán.

    Cả nạp lại lẫn OCR đều đi qua đây. Xóa-rồi-tạo-mới nghĩa là document_id
    đổi, và các dòng `assistant_documents` bị cascade xóa theo. Hậu quả im
    lặng: bot mất sạch tài liệu được gán, nhưng quy tắc "chưa gán thì đọc toàn
    kho" lại làm nó vẫn trả lời được — chỉ là trả lời từ kho của bot khác.
    Không có lỗi nào báo, và ranh giới giữa các trợ lý biến mất.
    """
    assistants = [
        int(r["assistant_id"])
        for r in (
            fetch_all(
                "SELECT assistant_id FROM assistant_documents WHERE document_id = :id",
                {"id": document_id},
            )
            or []
        )
    ]

    with connection() as conn:
        conn.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})

    result = ingest_file(path, files_root, extraction_override=extraction_override)

    if assistants and result.document_id:
        with connection() as conn:
            conn.execute(
                text(
                    "INSERT IGNORE INTO assistant_documents (assistant_id, document_id)"
                    " VALUES (:a, :d)"
                ),
                [{"a": a, "d": result.document_id} for a in assistants],
            )
    return result


def _resolve_course(course_code: str | None) -> tuple[int | None, int | None]:
    if not course_code:
        return None, None
    row = fetch_one(
        "SELECT id, faculty_id FROM courses WHERE code_norm = :c",
        {"c": vn_norm(course_code)},
    )
    if not row:
        return None, None
    return int(row["id"]), row.get("faculty_id")


def _write_stub(
    relative: str,
    path: Path,
    checksum: str,
    doc_meta: metadata.DocMeta,
    course_id: int | None,
    faculty_id: int | None,
    status: str,
    error: str,
    page_count: int | None = None,
    stale_id: dict | None = None,
) -> IngestResult:
    """Ghi bản ghi documents không có chunk — giữ dấu vết để admin xử lý tiếp."""
    with connection() as conn:
        if stale_id:
            conn.execute(
                text("DELETE FROM documents WHERE id = :id"), {"id": stale_id["id"]}
            )
        result = conn.execute(
            text(
                """
                INSERT INTO documents
                    (course_id, faculty_id, title, title_norm, type, semester,
                     lecturer, lecturer_norm, year, access, status,
                     file_path, file_ext, file_size, page_count, checksum,
                     error_message)
                VALUES
                    (:course_id, :faculty_id, :title, :title_norm, :type, :semester,
                     :lecturer, :lecturer_norm, :year, :access, :status,
                     :file_path, :file_ext, :file_size, :page_count, :checksum,
                     :error)
                """
            ),
            {
                "course_id": course_id,
                "faculty_id": faculty_id,
                "title": doc_meta.title,
                "title_norm": vn_norm(doc_meta.title),
                # ENUM type NOT NULL: chưa biết loại thì tạm 'tai_lieu_tham_khao',
                # bản ghi đang ở trạng thái chờ xử lý nên không lộ ra tìm kiếm
                # (mọi truy vấn đều lọc status='indexed').
                "type": doc_meta.doc_type or "tai_lieu_tham_khao",
                "semester": doc_meta.semester,
                "lecturer": doc_meta.lecturer,
                "lecturer_norm": vn_norm(doc_meta.lecturer) if doc_meta.lecturer else None,
                "year": doc_meta.year,
                "access": doc_meta.access,
                "status": status,
                "file_path": relative,
                "file_ext": path.suffix.lower().lstrip("."),
                "file_size": path.stat().st_size,
                "page_count": page_count,
                "checksum": checksum,
                "error": error,
            },
        )
        document_id = int(result.lastrowid)

    course_index.invalidate()
    return IngestResult(document_id, status, error)
