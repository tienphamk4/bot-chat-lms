"""Nhánh B đầy đủ (giai đoạn 2): embed câu hỏi → hybrid → rerank → sinh → kiểm trích dẫn.

Từng khâu hỏng đều rơi êm xuống bậc dưới:
  không embed được  → hybrid chạy mình kênh FULLTEXT
  không sinh được   → None, chat.py quay về chế độ trích nguyên văn (GĐ1)
  trích dẫn bịa     → citations.process cắt câu chứa nó
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import get_settings
from app.core import citations as citation_check
from app.core import rerank as reranker
from app.core import retrieval
from app.core.access import Identity
from app.db import fetch_value
from app.llm import embeddings, vertex
from app.llm.prompts import NOT_FOUND_SENTENCE, RAG_SYSTEM, rag_prompt
from app.schemas import Citation, RouterResult

log = logging.getLogger(__name__)

_infra_cache: dict = {"checked_at": 0.0, "ok": False}
_INFRA_TTL = 60.0


@dataclass
class RagResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    not_found: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_embed: int = 0
    model: str = ""
    finish_reason: str = ""


def infrastructure_ready() -> bool:
    """Có chunk nào đã embed chưa + LLM có bật không. Cache 60s — mỗi request
    không tốn thêm một câu COUNT."""
    import time

    now = time.monotonic()
    if now - _infra_cache["checked_at"] < _INFRA_TTL:
        return _infra_cache["ok"]

    ok = False
    if vertex.get_client() is not None:
        try:
            ok = bool(
                fetch_value(
                    "SELECT 1 FROM document_chunks"
                    " WHERE embedding IS NOT NULL LIMIT 1"
                )
            )
        except Exception:  # cột chưa tồn tại — chưa chạy migrate-vector
            ok = False

    _infra_cache.update(checked_at=now, ok=ok)
    return ok


def answer(
    identity: Identity,
    route: RouterResult,
    course: dict | None,
    keep: int,
    emit=None,
    trace=None,
    embed_future=None,
    scope_doc_type: str | None = None,
) -> RagResult | None:
    """Chạy trọn nhánh RAG. Trả None khi không sinh được — nơi gọi tự fallback.

    `emit(sentence)` (tùy chọn): nguồn token thật cho SSE. Chỉ câu ĐÃ TRỌN VẸN
    và ĐÃ QUA KIỂM TRÍCH DẪN mới được emit — streaming không được phép làm
    thủng quy tắc "[n] bịa thì cắt cả câu", nên đơn vị phát là câu, không phải
    token rời.
    """
    settings = get_settings()
    question = route.standalone_question

    tokens_embed = 0
    query_vector = None
    embedded = None
    parallel_embed = False

    # Embed đã khởi hành song song với router (chat.handle) — chỉ việc lấy về.
    if embed_future is not None:
        try:
            embedded = embed_future.result()
            parallel_embed = True
        except Exception:  # pragma: no cover
            embedded = None
    if embedded is None:
        embedded = embeddings.embed_query(question)

    if embedded is not None:
        query_vector, tokens_embed = embedded
    else:
        log.info("RAG: không embed được câu hỏi — hybrid chạy mình kênh FULLTEXT")
    if trace:
        if embedded is None:
            trace.mark("embed hỏng → bỏ qua")
        elif parallel_embed:
            trace.mark("embed (‖ với router, đã xong)")
        else:
            trace.mark("embed câu hỏi (Vertex)")

    # Ngữ cảnh cá nhân: không nêu môn thì ưu tiên các môn NGƯỜI NÀY đang học
    # (+ tài liệu cấp trường). Đây là TÍN HIỆU YẾU: kết quả trong phạm vi này
    # mà không cho ra câu trả lời CÓ NGUỒN thì thử lại toàn kho — sinh viên lớp
    # OOP vẫn hỏi được về đạo hàm.
    enrolled_scope = tuple(identity.course_ids) if identity.course_ids else None
    # Môn đến từ ngữ cảnh trang = tín hiệu mạnh nhưng MỀM: không ra thì được
    # bỏ. Môn người dùng nêu tường minh = ràng buộc cứng, không tự ý nới.
    course_soft = bool(course is not None and route.course_from_context)

    result = _generate_pass(
        identity, route, course, keep, emit, trace, query_vector, tokens_embed,
        enrolled_scope if course is None else None, scope_doc_type, settings,
    )

    if result is not None and result.not_found and course_soft:
        if trace:
            trace.mark("môn của trang không có câu trả lời — bỏ ràng buộc môn")
        result = _generate_pass(
            identity, route, None, keep, emit, trace, query_vector, tokens_embed,
            enrolled_scope, scope_doc_type, settings,
        )

    if (
        result is not None
        and result.not_found
        and enrolled_scope
        and (course is None or course_soft)
    ):
        if trace:
            trace.mark("phạm vi môn đang học không ra — thử toàn kho")
        result = _generate_pass(
            identity, route, None, keep, emit, trace, query_vector, tokens_embed,
            None, scope_doc_type, settings,
        )

    # Van cuối: khoanh theo LOẠI tài liệu cũng là scope mềm — đúng loại không
    # có câu trả lời thì mở mọi loại (vẫn đúng quyền).
    if result is not None and result.not_found and scope_doc_type:
        if trace:
            trace.mark("đúng loại tài liệu không ra — mở mọi loại")
        result = _generate_pass(
            identity, route, None, keep, emit, trace, query_vector, tokens_embed,
            None, None, settings,
        )
    return result


def _generate_pass(
    identity: Identity,
    route: RouterResult,
    course: dict | None,
    keep: int,
    emit,
    trace,
    query_vector,
    tokens_embed: int,
    scope,
    scope_doc_type: str | None,
    settings,
) -> RagResult | None:
    """Một lượt trọn vẹn: truy hồi → rerank → sinh → kiểm trích dẫn."""
    question = route.standalone_question

    candidates = retrieval.hybrid_search(
        identity,
        question,
        query_vector,
        route.keywords,
        course,
        per_channel=settings.rag_topk_per_channel,
        scope_course_ids=scope,
        scope_doc_type=scope_doc_type,
    )
    if not candidates and (scope or scope_doc_type):
        # Phạm vi ngữ cảnh trống trơn → nới ra toàn kho (vẫn đúng quyền).
        candidates = retrieval.hybrid_search(
            identity,
            question,
            query_vector,
            route.keywords,
            course,
            per_channel=settings.rag_topk_per_channel,
        )
    if trace:
        trace.mark("truy hồi hybrid ‖ (DB)")
    if not candidates:
        return RagResult(
            answer="", not_found=True, tokens_embed=tokens_embed
        )

    chunks = reranker.rerank(question, candidates, keep=keep)
    if trace:
        trace.mark("rerank")

    kept: list[str] = []
    cited: set[int] = set()
    # Câu KHÔNG gắn [n] bị GIỮ LẠI tới khi xuất hiện câu có nguồn — cả bài
    # không có nguồn nào là vi phạm "bắt buộc kèm trích dẫn": không được phát
    # ra một chữ nào (để còn thử lại phạm vi khác hoặc fallback).
    held_unmarked: list[str] = []
    buffer = ""
    final: vertex.TextResult | None = None
    not_found = False

    def _take(sentence: str) -> None:
        nonlocal not_found
        if not_found:
            return
        if NOT_FOUND_SENTENCE in sentence:
            not_found = True
            return
        ok, markers = citation_check.sentence_ok(sentence, len(chunks))
        if not ok:
            return
        if trace and not kept:
            trace.mark("sinh — câu đầu tiên (Vertex)")
        kept.append(sentence)
        if markers:
            cited.update(markers)
            if emit is not None:
                for held in held_unmarked:
                    emit(held)
                held_unmarked.clear()
                emit(sentence)
        elif emit is not None:
            if cited:
                emit(sentence)  # đã có nguồn phía trước — phát bình thường
            else:
                held_unmarked.append(sentence)

    for kind, payload in vertex.generate_text_stream(
        system_instruction=RAG_SYSTEM,
        prompt=rag_prompt(question, chunks),
        model=settings.gen_model,
        max_output_tokens=settings.gen_max_output_tokens,
        temperature=settings.gen_temperature,
    ):
        if kind == "delta":
            buffer += payload
            complete, buffer = citation_check.split_complete(buffer)
            for sentence in complete:
                _take(sentence)
        else:
            final = payload

    if buffer.strip():
        _take(buffer.strip())
    if trace:
        trace.mark("sinh — hoàn tất")

    if final is None and not kept:
        return None  # model hỏng trước khi ra được câu nào — fallback trích nguyên văn

    tokens_in = final.tokens_in if final else 0
    tokens_out = final.tokens_out if final else 0
    model_name = final.model if final else settings.gen_model
    finish = final.finish_reason if final else "STREAM_ABORTED"

    if finish and "MAX_TOKENS" in finish:
        # KT-05 dặn giám sát cắt cụt — đếm được qua grep log này.
        log.warning("RAG: câu trả lời chạm trần %d token", settings.gen_max_output_tokens)

    if not kept or not cited:
        if kept and not cited:
            # Có chữ nhưng không một nguồn nào — NFR "bắt buộc kèm trích dẫn"
            # cấm đưa ra ngoài (held_unmarked bảo đảm chưa chữ nào bị phát đi).
            log.warning("RAG: câu trả lời không có trích dẫn nào — loại toàn bộ")
        elif not kept and not not_found:
            # Mọi câu đều bị loại vì nguồn bịa — coi như không tìm thấy.
            log.warning("RAG: toàn bộ câu trả lời bị loại do trích dẫn không ánh xạ được")
        return RagResult(
            answer="",
            not_found=True,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_embed=tokens_embed,
            model=model_name,
        )

    return RagResult(
        # Mỗi câu một dòng — UI hiển thị thoáng như ChatGPT (.msg đã pre-wrap).
        answer="\n".join(kept),
        citations=citation_check.build_citations(cited, chunks),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_embed=tokens_embed,
        model=model_name,
        finish_reason=finish,
    )
