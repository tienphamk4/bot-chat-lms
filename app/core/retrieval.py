"""Truy hồi — nhánh metadata (SQL), dự phòng FULLTEXT tiêu đề, và nhánh nội dung.

Ba đường, thử theo đúng thứ tự này:

1. `metadata_search` — SQL trên `documents`. Không sinh một token nào (KT-01).
   Phần lớn lưu lượng phải dừng ở đây; route metadata < 45% là dấu hiệu router
   đang bóc trường sai.
2. `title_search` — FULLTEXT trên `title_norm`, dùng khi nhánh 1 ra 0 kết quả.
3. `chunk_search` — FULLTEXT trên `document_chunks.content_norm`. Giai đoạn 1
   đây là toàn bộ nhánh nội dung; giai đoạn 2 nó thành một trong hai kênh của
   hybrid retrieval (kênh kia là vector), rồi RRF + rerank.

Mọi truy vấn ở đây đều mang mệnh đề quyền do `access_clause` sinh ra. Không có
đường nào đọc `documents` mà bỏ qua nó.
"""

from __future__ import annotations

from typing import Any

from app.core.access import Identity, access_clause
from app.core.courses import find_course
from app.db import fetch_all, fetch_one, where_and
from app.schemas import RouterResult
from app.vn import ft_boolean_query, ft_phrase_queries, vn_norm

# Thứ tự ưu tiên khi người hỏi không nêu loại tài liệu: cái học trước lên trước.
DOC_TYPE_PRIORITY = (
    "giao_trinh",
    "de_cuong",
    "slide",
    "bai_tap",
    "de_thi",
    "tai_lieu_tham_khao",
    "quy_che",
)

_TYPE_ORDER_SQL = "FIELD(d.type, " + ", ".join(f"'{t}'" for t in DOC_TYPE_PRIORITY) + ")"

_DOC_COLUMNS = """
    d.id, d.title, d.type, d.semester, d.lecturer, d.year,
    d.page_count, d.file_ext, d.file_size, d.course_id,
    c.code AS course_code, c.name AS course_name
"""


def resolve_course(route: RouterResult) -> dict[str, Any] | None:
    """Quy mã/tên gọi tắt trong câu hỏi về một dòng `courses` thật.

    Tầng luật khớp qua chỉ mục RAM thì course_id đã có sẵn — dựng dict từ RAM,
    0 lượt DB. Chỉ khi router LLM đưa về mã/tên chưa xác định mới phải tra DB.
    """
    if route.course_id:
        from app.core import course_index

        entry = course_index.get(route.course_id)
        if entry is not None:
            return {"id": entry.id, "code": entry.code, "name": entry.name}

    if not route.course_code and not route.course_hint:
        return None
    return find_course(code=route.course_code, hint=route.course_hint)


def metadata_search(
    identity: Identity,
    route: RouterResult,
    course: dict[str, Any] | None,
    limit: int = 10,
    assistant_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Nhánh A. Trả về [] khi câu hỏi không có tiêu chí lọc nào.

    Không có tiêu chí mà vẫn chạy thì câu SQL sẽ trả về "mọi tài liệu trong
    trường" — vô nghĩa với người hỏi và tốn công với CSDL.
    """
    conditions = ["d.status = 'indexed'"]
    params: dict[str, Any] = {"limit": limit}

    acl_sql, acl_params = access_clause(identity, "d")
    conditions.append(acl_sql)
    params.update(acl_params)

    assistant_sql = _assistant_condition(assistant_slug, params)
    if assistant_sql:
        conditions.append(assistant_sql.removeprefix("AND "))

    has_filter = False

    if course:
        conditions.append("d.course_id = :course_id")
        params["course_id"] = course["id"]
        has_filter = True

    if route.doc_type:
        conditions.append("d.type = :doc_type")
        params["doc_type"] = route.doc_type.value
        has_filter = True

    if route.semester:
        conditions.append("d.semester = :semester")
        params["semester"] = route.semester
        has_filter = True

    if route.lecturer:
        conditions.append("d.lecturer_norm LIKE :lecturer")
        params["lecturer"] = f"%{vn_norm(route.lecturer)}%"
        has_filter = True

    if not has_filter:
        return []

    return fetch_all(
        f"""
        SELECT {_DOC_COLUMNS}
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE {where_and(conditions)}
        ORDER BY {_TYPE_ORDER_SQL}, d.semester DESC, d.title
        LIMIT :limit
        """,
        params,
    )


def title_search(
    identity: Identity, question: str, limit: int = 10,
    assistant_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Dự phòng khi nhánh A ra 0 kết quả: khớp toàn văn trên tiêu đề."""
    expression = ft_boolean_query(question, mode="or")
    if not expression:
        return []

    acl_sql, params = access_clause(identity, "d")
    params.update({"q": expression, "limit": limit})
    assistant_sql = _assistant_condition(assistant_slug, params)

    return fetch_all(
        f"""
        SELECT {_DOC_COLUMNS},
               MATCH(d.title_norm) AGAINST (:q IN BOOLEAN MODE) AS score
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.status = 'indexed'
          AND {acl_sql}
          {assistant_sql}
          AND MATCH(d.title_norm) AGAINST (:q IN BOOLEAN MODE)
        ORDER BY score DESC, {_TYPE_ORDER_SQL}
        LIMIT :limit
        """,
        params,
    )


# --- Khoanh vùng theo TRỢ LÝ ------------------------------------------------
#
# Bảng `assistant_documents` nói tài liệu nào thuộc bot nào. Quy tắc cố ý mềm:
#
#   bot ĐÃ gán tài liệu  -> chỉ đọc đúng những tài liệu đó
#   bot CHƯA gán gì      -> đọc toàn kho (theo doc_types nếu có khai)
#
# Nhờ vế thứ hai, bật tính năng này không làm chết bot đang chạy: chưa gán thì
# hành vi y như trước, gán tới đâu siết tới đó.
#
# Cache 60 giây để mỗi câu hỏi khỏi tốn thêm một lượt DB chỉ để hỏi "bot này có
# gán tài liệu nào chưa".
_ASSISTANT_SCOPE_TTL = 60.0
# slug -> (thời điểm, có tài liệu gán riêng?, các mức quyền bot phục vụ)
_assistant_scope_cache: dict[str, tuple[float, bool, tuple[str, ...]]] = {}


def _assistant_scope(slug: str) -> tuple[bool, tuple[str, ...]]:
    """Hai thứ quyết định phạm vi của bot, lấy trong một lượt DB và cache chung."""
    import json
    import time

    from app.db import fetch_one

    cached = _assistant_scope_cache.get(slug)
    now = time.monotonic()
    if cached and now - cached[0] < _ASSISTANT_SCOPE_TTL:
        return cached[1], cached[2]

    has_docs, levels = False, ()
    try:
        row = fetch_one(
            """
            SELECT a.access_levels AS levels,
                   (SELECT COUNT(*) FROM assistant_documents ad
                     WHERE ad.assistant_id = a.id) AS n
            FROM assistants a WHERE a.slug = :slug
            """,
            {"slug": slug},
        )
        if row:
            has_docs = int(row["n"] or 0) > 0
            raw = row["levels"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            levels = tuple(str(x) for x in (raw or []) if x)
    except Exception:
        # Bảng chưa tạo / DB chớp tắt: coi như không khoanh vùng gì -> không
        # siết oan, bot vẫn trả lời được.
        pass

    _assistant_scope_cache[slug] = (now, has_docs, levels)
    return has_docs, levels


def _assistant_has_documents(slug: str) -> bool:
    return _assistant_scope(slug)[0]


def clear_assistant_scope_cache() -> None:
    _assistant_scope_cache.clear()


def _assistant_condition(
    assistant_slug: str | None, params: dict[str, Any], alias: str = "d"
) -> str:
    """Hai mệnh đề khoanh vùng theo bot, cùng nối bằng AND.

    Cả hai chỉ THU HẸP. Mệnh đề quyền của người hỏi (access_clause) vẫn áp
    riêng và không bao giờ bị nới ra bởi cấu hình bot — bot khai phục vụ mức
    'staff' không làm sinh viên đọc được tài liệu staff.
    """
    if not assistant_slug:
        return ""

    has_docs, levels = _assistant_scope(assistant_slug)
    parts = []

    if has_docs:
        params["assistant_slug"] = assistant_slug
        parts.append(
            f"AND EXISTS (SELECT 1 FROM assistant_documents ad "
            f"JOIN assistants a ON a.id = ad.assistant_id "
            f"WHERE ad.document_id = {alias}.id AND a.slug = :assistant_slug)"
        )

    if levels:
        from app.db import in_clause

        sql_in, level_params = in_clause("bot_access", list(levels))
        params.update(level_params)
        parts.append(f"AND {alias}.access IN ({sql_in})")

    return " ".join(parts)


def _scope_condition(
    scope_course_ids, params: dict[str, Any], alias: str = "d"
) -> str:
    """Khoanh vùng theo các môn đang học + tài liệu cấp trường (course_id NULL).

    Đây là lớp "ngữ cảnh cá nhân": sinh viên hỏi "kế thừa là gì" không nêu môn
    thì ưu tiên tìm trong môn họ đang học, thay vì cả kho. KHÔNG thay thế lọc
    quyền — access_clause vẫn áp riêng.
    """
    if not scope_course_ids:
        return ""
    from app.db import in_clause

    sql_in, scoped_params = in_clause("scope_course", list(scope_course_ids))
    params.update(scoped_params)
    return f"AND ({alias}.course_id IN ({sql_in}) OR {alias}.course_id IS NULL)"


def _interleave(
    results: list[list[dict[str, Any]]], limit: int
) -> list[dict[str, Any]]:
    """Trộn nhiều danh sách đã xếp hạng: hạng 1 của mỗi danh sách trước, rồi
    hạng 2... Điểm MATCH của hai truy vấn khác nhau không cùng thang đo nên
    không gộp điểm được; xen kẽ theo THỨ HẠNG thì danh sách nào cũng có phần.
    """
    merged: list[dict[str, Any]] = []
    seen: set = set()
    for rank in range(max((len(r) for r in results), default=0)):
        for ranked in results:
            if rank >= len(ranked) or len(merged) >= limit:
                continue
            row = ranked[rank]
            if row["chunk_id"] in seen:
                continue
            seen.add(row["chunk_id"])
            merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def chunk_search(
    identity: Identity,
    question: str,
    keywords: list[str] | None = None,
    course: dict[str, Any] | None = None,
    limit: int = 4,
    scope_course_ids=None,
    scope_doc_type: str | None = None,
    assistant_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Nhánh nội dung rút gọn của giai đoạn 1.

    Giữ đúng `limit` chunk (mặc định 4) — bằng số chunk mà reranker sẽ giữ ở
    giai đoạn 2, để định dạng câu trả lời và trích dẫn không phải đổi khi thay
    ruột.
    """
    source = " ".join(keywords) if keywords else question

    acl_sql, params = access_clause(identity, "d")
    params["limit"] = limit

    course_condition = ""
    if course:
        course_condition = "AND d.course_id = :course_id"
        params["course_id"] = course["id"]
    else:
        course_condition = _scope_condition(scope_course_ids, params)

    if scope_doc_type:
        course_condition += " AND d.type = :scope_type"
        params["scope_type"] = scope_doc_type

    # Khoanh vùng theo bot — nối vào cùng chỗ với các điều kiện phạm vi khác.
    course_condition += " " + _assistant_condition(assistant_slug, params)

    # `is_fragment = 0`: bỏ các chunk là bảng số liệu đã vỡ định dạng khi trích
    # xuất ("Mã ngành: 7480104 | Chỉ tiêu: 50 | 2 | 0 | 2 | 5 | 26,38 …"). Con
    # số còn đó nhưng hàng/cột đã mất, nên gán số cho ngành nào cũng là bịa.
    # Thà nói chưa có thông tin — xem ingest/context.py.
    sql = f"""
        SELECT ch.id AS chunk_id, ch.content, ch.context_text, ch.heading_path,
               ch.page_from, ch.page_to,
               d.id AS document_id, d.title, d.type, d.semester, d.lecturer,
               d.file_path,
               c.code AS course_code, c.name AS course_name,
               MATCH(ch.content_norm) AGAINST (:q IN BOOLEAN MODE) AS score
        FROM document_chunks ch
        JOIN documents d ON d.id = ch.document_id
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.status = 'indexed'
          AND ch.is_fragment = 0
          AND {acl_sql}
          {course_condition}
          AND MATCH(ch.content_norm) AGAINST (:q IN BOOLEAN MODE)
        ORDER BY score DESC
        LIMIT :limit
        """

    # Nới dần theo bậc, dừng ở bậc đầu tiên có kết quả.
    #
    # Bậc CỤM đứng đầu: đòi nguyên cụm khóa ("kinh te so"). Chính xác nhất, và
    # là bậc duy nhất chống được điểm yếu cốt lõi của BOOLEAN MODE — xếp hạng
    # theo SỐ từ khớp, không có IDF, nên âm tiết phổ thông nặng ngang âm tiết
    # hiếm. Đây cũng là bậc gánh chính khi kênh vector chết vì nghẽn quota.
    #
    # Rồi tới AND (chính xác cao: "quy chế cntt" phải chứa CẢ HAI). Hai bậc
    # *_content bỏ hư từ trước khi so: câu "ngành An toàn thông tin học những
    # môn gì" trượt bậc AND chỉ vì chữ "gì" không có trong tài liệu, rồi rơi
    # thẳng xuống OR — nơi "những/gì" khớp cả kho.
    # Trong bậc CỤM thì chạy HẾT các cụm rồi trộn, chứ không dừng ở cụm đầu
    # tiên có kết quả: "nhiều từ hơn" không có nghĩa là "đặc trưng hơn". Câu
    # "ngành Bảo hiểm đào tạo ra sao" cho ra hai cụm — "chuong trinh dao tao"
    # (4 từ) và "nganh bao hiem" (3 từ). Dừng ở cụm đầu là lấy nhầm cụm nói
    # chung chung, kéo về chương trình của sáu ngành khác. Trộn cả hai rồi để
    # tầng xếp hạng lại quyết định thì không phải đoán cụm nào đặc trưng.
    phrases = ft_phrase_queries(keywords or ())
    if phrases:
        results = [fetch_all(sql, {**params, "q": p}) for p in phrases]
        merged = _interleave(results, limit)
        if merged:
            return merged

    for mode in ("and", "and_content", "or_content", "or"):
        expression = ft_boolean_query(source, mode=mode)
        if not expression:
            break
        rows = fetch_all(sql, {**params, "q": expression})
        if rows:
            return rows
    return []


def vector_search(
    identity: Identity,
    query_vector: list[float],
    course: dict[str, Any] | None = None,
    limit: int = 20,
    scope_course_ids=None,
    scope_doc_type: str | None = None,
    assistant_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Kênh vector của hybrid retrieval — cú pháp Cloud SQL for MySQL.

    Exact KNN bằng cosine_distance(): đúng tuyệt đối, không cần index, đủ nhanh
    dưới ~10k chunk (bảng demo vài chục). Corpus lớn thì tạo index ScaNN bằng
    `ingest.cli build-vector-index` — nhưng NEAREST() của Cloud SQL không đi kèm
    WHERE lọc quyền tùy ý, nên đường mặc định của dự án vẫn là exact KNN có ACL
    ngay trong câu SQL; index là tối ưu tương lai khi dữ liệu buộc phải đổi.
    """
    from app.llm.embeddings import to_sql_vector

    acl_sql, params = access_clause(identity, "d")
    params.update({"qv": to_sql_vector(query_vector), "limit": limit})

    course_condition = ""
    if course:
        course_condition = "AND d.course_id = :course_id"
        params["course_id"] = course["id"]
    else:
        course_condition = _scope_condition(scope_course_ids, params)

    if scope_doc_type:
        course_condition += " AND d.type = :scope_type"
        params["scope_type"] = scope_doc_type

    # Khoanh vùng theo bot — nối vào cùng chỗ với các điều kiện phạm vi khác.
    course_condition += " " + _assistant_condition(assistant_slug, params)

    return fetch_all(
        f"""
        SELECT ch.id AS chunk_id, ch.content, ch.context_text, ch.heading_path,
               ch.page_from, ch.page_to,
               d.id AS document_id, d.title, d.type, d.semester, d.lecturer,
               d.file_path,
               c.code AS course_code, c.name AS course_name,
               cosine_distance(ch.embedding, string_to_vector(:qv)) AS distance
        FROM document_chunks ch
        JOIN documents d ON d.id = ch.document_id
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.status = 'indexed'
          AND ch.embedding IS NOT NULL
          AND ch.is_fragment = 0
          AND {acl_sql}
          {course_condition}
        ORDER BY distance ASC
        LIMIT :limit
        """,
        params,
    )


def hybrid_search(
    identity: Identity,
    question: str,
    query_vector: list[float] | None,
    keywords: list[str] | None = None,
    course: dict[str, Any] | None = None,
    per_channel: int = 20,
    scope_course_ids=None,
    scope_doc_type: str | None = None,
    assistant_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Hai kênh song song (FULLTEXT + vector) → RRF. Chưa cắt top-K cuối —
    việc của rerank ở tầng trên."""
    from concurrent.futures import ThreadPoolExecutor

    from app.core.fusion import rrf_merge

    if query_vector is not None:
        # Hai kênh độc lập — chạy song song, tiết kiệm một lượt khứ hồi DB
        # (đáng kể khi app và DB không cùng mạng, như máy dev nối Cloud SQL).
        with ThreadPoolExecutor(max_workers=2) as pool:
            # Truyền theo TÊN chứ không theo vị trí: hai hàm này có tham số
            # cuối khác nhau, và một lần thêm tham số mới mà quên sửa chỗ này
            # là ranh giới bot rơi mất mà không ai báo lỗi.
            fulltext_future = pool.submit(
                chunk_search, identity, question, keywords, course,
                limit=per_channel, scope_course_ids=scope_course_ids,
                scope_doc_type=scope_doc_type, assistant_slug=assistant_slug,
            )
            vector_future = pool.submit(
                vector_search, identity, query_vector, course,
                limit=per_channel, scope_course_ids=scope_course_ids,
                scope_doc_type=scope_doc_type, assistant_slug=assistant_slug,
            )
            fulltext = fulltext_future.result()
            vectors = vector_future.result()
    else:
        fulltext = chunk_search(
            identity, question, keywords, course,
            limit=per_channel, scope_course_ids=scope_course_ids,
            scope_doc_type=scope_doc_type, assistant_slug=assistant_slug,
        )
        vectors = []

    channels = [ch for ch in (fulltext, vectors) if ch]
    if not channels:
        return []
    return rrf_merge(channels)


def list_documents(
    identity: Identity,
    course_code: str | None = None,
    doc_type: str | None = None,
    semester: str | None = None,
    lecturer: str | None = None,
    query: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """Dùng cho `GET /documents`. Trả về (danh sách, tổng số)."""
    conditions = ["d.status = 'indexed'"]
    params: dict[str, Any] = {}

    acl_sql, acl_params = access_clause(identity, "d")
    conditions.append(acl_sql)
    params.update(acl_params)

    if course_code:
        conditions.append("c.code_norm = :course_code")
        params["course_code"] = vn_norm(course_code)
    if doc_type:
        conditions.append("d.type = :doc_type")
        params["doc_type"] = doc_type
    if semester:
        conditions.append("d.semester = :semester")
        params["semester"] = semester
    if lecturer:
        conditions.append("d.lecturer_norm LIKE :lecturer")
        params["lecturer"] = f"%{vn_norm(lecturer)}%"
    if query:
        expression = ft_boolean_query(query, mode="or")
        if expression:
            conditions.append("MATCH(d.title_norm) AGAINST (:q IN BOOLEAN MODE)")
            params["q"] = expression

    where = where_and(conditions)

    total = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE {where}
        """,
        params,
    )

    params["limit"] = page_size
    params["offset"] = max(0, (page - 1) * page_size)

    rows = fetch_all(
        f"""
        SELECT {_DOC_COLUMNS}
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE {where}
        ORDER BY {_TYPE_ORDER_SQL}, d.semester DESC, d.title
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    return rows, int(total["total"]) if total else 0


def get_document(document_id: int) -> dict[str, Any] | None:
    """Đọc thô một tài liệu, CHƯA lọc quyền.

    Nơi gọi bắt buộc kiểm bằng `access.can_read_document` trước khi trả ra
    ngoài. Tách như vậy để endpoint tải file kiểm quyền tường minh, thấy được
    trong code review.
    """
    return fetch_one(
        """
        SELECT d.*, c.code AS course_code, c.name AS course_name
        FROM documents d
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.id = :id
        """,
        {"id": document_id},
    )
