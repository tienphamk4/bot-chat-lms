"""Ghép toàn bộ luồng truy vấn giai đoạn 1 thành một hàm.

    cache hash → guard quota → router (luật → LLM) → nhánh theo intent
    → dựng câu trả lời (0 token sinh) → ghi log/cache/quota

Trả về `ChatOutcome` thuần dữ liệu; tầng API quyết định phát SSE hay JSON. Nhờ
vậy luồng này test được không cần HTTP, và bộ eval 200 câu gọi thẳng được.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.core import cache, personas, quota, retrieval
from app.core.access import Identity
from app.core.render import (
    ABUSIVE,
    CLARIFY,
    GREETING,
    OUT_OF_SCOPE,
    render_chunks,
    render_documents,
    render_not_found,
)
from app.core.router import resolve
from app.db import execute
from app.schemas import Citation, DocType, Intent, RouterResult
from app.vn import vn_norm

log = logging.getLogger(__name__)

# Pool nhỏ dùng chung cho các việc chạy kèm request (embed song song với router).
# Không dùng context manager per-request vì shutdown sẽ chặn chờ.
from concurrent.futures import ThreadPoolExecutor as _TPE

_bg_pool = _TPE(max_workers=4, thread_name_prefix="chat-bg")


class _Trace:
    """Đồng hồ bấm giờ từng bước — ms tính TỪ BƯỚC TRƯỚC, cộng lại = tổng.

    Kết quả đi ra ngoài qua done.trace để UI vẽ timeline: nhìn một phát biết
    câu này chậm ở đâu (DB? router? embed? sinh?)."""

    __slots__ = ("last", "steps")

    def __init__(self) -> None:
        self.last = time.perf_counter()
        self.steps: list[dict] = []

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.steps.append({"step": name, "ms": int((now - self.last) * 1000)})
        self.last = now


@dataclass
class ChatOutcome:
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    route: RouterResult = field(default_factory=RouterResult)
    branch: str = "static"  # cache | metadata | fulltext | content | rag | static
    cached: bool = False
    degraded: bool = False
    log_id: int | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0
    raw_question: str = ""  # câu gốc — để cache thêm một khóa cho lượt lặp y hệt

    # GĐ2 — token của bước sinh và bước embed câu hỏi (0 khi đi nhánh khác)
    gen_tokens_in: int = 0
    gen_tokens_out: int = 0
    embed_tokens: int = 0
    gen_model: str | None = None

    # Timeline từng bước cho UI — [{'step': str, 'ms': int}, ...]
    trace: list = field(default_factory=list)

    # Khóa ngữ cảnh trang (lesson:INT2204 / advisory / "") — tách khóa cache.
    context_key: str = ""

    @property
    def tokens_in(self) -> int:
        return self.route.tokens_in + self.gen_tokens_in

    @property
    def tokens_out(self) -> int:
        return self.route.tokens_out + self.gen_tokens_out


def handle(
    session_id: str, message: str, identity: Identity, sink=None, context=None
) -> ChatOutcome:
    """`sink(kind, payload)` (tùy chọn) — kênh đẩy sự kiện cho SSE khi nhánh RAG
    stream thật: kind='route' (payload=ChatOutcome, phát trước khi sinh) và
    kind='token' (payload=str). Không có sink → hành vi cũ, trả một cục.

    `context` (ChatContext, tùy chọn): người dùng đang đứng ở trang nào trong
    LMS — bài học môn X thì câu hỏi không nêu môn ưu tiên tài liệu môn đó,
    trang tư vấn thì thiên về quy chế. Cache tách khóa theo ngữ cảnh."""
    started = time.perf_counter()
    settings = get_settings()
    trace = _Trace()

    # Bot nào đang trả lời — quyết định câu chữ, phạm vi tài liệu và đầu mối
    # liên hệ. Đọc từ bảng `assistants`, hỏng thì rơi về persona mặc định.
    persona = personas.get(context.assistant if context is not None else None)

    context_key = ""
    if context is not None and (context.screen or context.course_code):
        context_key = f"{context.screen or ''}|{vn_norm(context.course_code or '')}"
    # Hai bot khác nhau KHÔNG dùng chung ô cache: cùng một câu hỏi, câu trả lời
    # và cả cách từ chối đều khác nhau theo persona.
    if persona.slug:
        context_key = f"{persona.slug}#{context_key}"

    # --- Tầng 0: cache hash (song song với guard quota — hai lượt DB độc lập,
    # từ máy xa Cloud SQL mỗi lượt tuần tự là ~1s) -----------------------------
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        hit_future = pool.submit(cache.lookup, message, identity, context_key)
        quota_future = pool.submit(quota.check, identity)
        hit = hit_future.result()
        quota_status = quota_future.result()
    trace.mark("cache + quota (DB ‖)")

    if hit is not None:
        outcome = ChatOutcome(
            answer=hit["answer"],
            citations=[Citation(**c) for c in hit["citations"]],
            route=RouterResult(source="cache", standalone_question=message.strip()),
            branch="cache",
            cached=True,
        )
        return _finalize(outcome, session_id, message, identity, started, trace)

    # --- Router 2 tầng, embed chạy SONG SONG khi có khả năng cần RAG ---------
    # Router LLM và embed câu hỏi độc lập nhau mà trước đây chạy nối tiếp —
    # cho embed khởi hành cùng lúc thì thời gian embed bị router "che" hết.
    # Chỉ kích hoạt khi câu có khả năng đi nhánh nội dung (luật không chắc,
    # hoặc luật đã chốt ask_content) — greeting/tra cứu không tốn lượt embed.
    from app.core import router_rules
    from app.llm import embeddings

    verdict = router_rules.route(message, persona.blocked_topics)
    embed_future = None
    if quota_status.allow_llm and (
        not verdict.confident or verdict.result.intent is Intent.ask_content
    ):
        embed_future = _bg_pool.submit(embeddings.embed_query, message)

    route = resolve(
        message, allow_llm=quota_status.allow_llm, verdict=verdict,
        blocked_topics=persona.blocked_topics,
    )
    if route.llm_failed:
        # Đừng đổ oan cho tầng luật: thời gian ở đây là cú gọi LLM bị nghẽn
        # quota/timeout rồi mới rơi về luật.
        trace.mark("router (LLM nghẽn → dùng luật)")
    else:
        trace.mark(f"router ({route.source})")

    # Embed chạy trước dùng câu GỐC — chỉ hợp lệ khi router không viết lại câu.
    if embed_future is not None and vn_norm(route.standalone_question) != vn_norm(message):
        embed_future = None

    # --- Ngữ cảnh trang (LMS truyền vào) -------------------------------------
    # Trang bài học môn X: câu hỏi không nêu môn → lấy môn của trang đang mở.
    # Trang tư vấn: thiên về văn bản quy chế. Chỉ ĐIỀN CHỖ TRỐNG — người dùng
    # nêu môn/loại tường minh trong câu hỏi thì lời họ nói thắng.
    # Khoanh vùng loại tài liệu theo persona (cột doc_types). Bot chỉ đọc quy
    # chế thì khai ["quy_che"] trong CSDL, không cần nhánh if nào ở đây.
    scope_doc_type = persona.doc_types[0] if persona.doc_types else None
    if context is not None:
        if (
            context.course_code
            and not (route.course_id or route.course_code or route.course_hint)
            # Câu hỏi về QUY CHẾ là tài liệu cấp trường — đứng ở trang bài học
            # hỏi quy chế thì không được nhét môn của trang vào.
            and route.doc_type is not DocType.quy_che
        ):
            route.course_code = context.course_code.upper()
            route.course_from_context = True
        if context.screen == "advisory":
            scope_doc_type = "quy_che"
            if route.doc_type is None and route.intent is Intent.lookup_doc:
                route.doc_type = DocType.quy_che

    # --- Tầng 0b: cache theo câu viết lại (KT-07) ----------------------------
    # Đây là chỗ KT-07 sinh lãi cho cache: hai người diễn đạt khác nhau nhưng
    # router viết lại thành cùng một câu → dùng chung một kết quả.
    if vn_norm(route.standalone_question) != vn_norm(message):
        hit = cache.lookup(route.standalone_question, identity, context_key)
        trace.mark("cache câu viết lại (DB)")
        if hit is not None:
            outcome = ChatOutcome(
                answer=hit["answer"],
                citations=[Citation(**c) for c in hit["citations"]],
                route=route,
                branch="cache",
                cached=True,
                degraded=quota_status.degraded,
                cost_usd=settings.cost_usd(route.tokens_in, route.tokens_out),
            )
            return _finalize(outcome, session_id, message, identity, started, trace)

    outcome = ChatOutcome(
        route=route, degraded=quota_status.degraded, raw_question=message,
        context_key=context_key,
    )

    # --- Nhánh theo intent ---------------------------------------------------
    if route.intent is Intent.greeting:
        outcome.answer = persona.reply("greeting", GREETING)
    elif route.intent is Intent.abusive:
        outcome.answer = persona.reply("abusive", ABUSIVE)
    elif route.intent is Intent.clarify:
        outcome.answer = persona.reply("clarify", CLARIFY)
    elif route.intent is Intent.out_of_scope:
        outcome.answer = persona.reply("out_of_scope", OUT_OF_SCOPE)
    elif route.intent is Intent.ask_content:
        _run_content(
            outcome, identity, settings.content_limit, quota_status.allow_llm,
            sink=sink, trace=trace, embed_future=embed_future,
            scope_doc_type=scope_doc_type, persona=persona,
        )
    else:
        _run_lookup(
            outcome, identity, settings.metadata_limit, settings.content_limit,
            quota_status.allow_llm, sink=sink, trace=trace,
            embed_future=embed_future, scope_doc_type=scope_doc_type,
            persona=persona,
        )

    outcome.cost_usd = settings.cost_usd(
        outcome.tokens_in, outcome.tokens_out, outcome.embed_tokens
    )
    return _finalize(outcome, session_id, message, identity, started, trace)


def _run_lookup(
    outcome: ChatOutcome,
    identity: Identity,
    limit: int,
    content_limit: int,
    allow_llm: bool,
    sink=None,
    trace: _Trace | None = None,
    embed_future=None,
    scope_doc_type: str | None = None,
    persona=None,
) -> None:
    """Nhánh A, với hai bậc dự phòng đúng thiết kế:
    metadata → FULLTEXT tiêu đề → nhánh nội dung (GĐ2: RAG)."""
    route = outcome.route
    course = retrieval.resolve_course(route)

    rows = retrieval.metadata_search(
        identity, route, course, limit=limit,
        assistant_slug=(persona.slug or None) if persona else None,
    )
    if trace:
        trace.mark("SQL metadata (DB)")
    if rows:
        outcome.branch = "metadata"
        outcome.answer, outcome.citations = render_documents(rows, route, course, identity=identity, persona=persona)
        # Người dùng nêu một môn/chủ đề KHÔNG có trong danh mục ("quy chế môn
        # lập trình di động") → danh sách trả về chỉ khớp LOẠI, không khớp chủ
        # đề — phải nói thật thay vì tỏ ra "tìm thấy".
        if route.course_hint and course is None:
            outcome.answer = (
                f"Em chưa thấy tài liệu nào riêng cho \"{route.course_hint}\" — "
                f"dưới đây là những gì gần nhất hiện có ạ:\n" + outcome.answer
            )
        _store_cache(outcome, rows, identity)
        return

    question = route.standalone_question

    # Nhánh dự phòng khớp TIÊU ĐỀ vốn để cứu câu tra cứu diễn đạt lạ. Nhưng khi
    # câu hỏi thực chất là HỎI NỘI DUNG, một cú trùng chữ trong tiêu đề lại giả
    # vờ thành công và chặn mất bước quan trọng nhất — người hỏi nhận về danh
    # sách file trong khi tài liệu có đủ nội dung để trả lời.
    #
    # Ca thật: "lộ trình ngành An toàn thông tin của trường là gì" → kho không
    # có đề cương nào, nhưng tiêu đề "GT Ngành và CTĐT" trùng chữ nên thang
    # dừng lại ở đây và RAG không bao giờ chạy.
    #
    # Đây là lớp chặn theo CƠ CHẾ, khác với việc bổ sung từng lối nói vào bộ
    # nhận dạng: lối nói mới sẽ còn xuất hiện, nhưng đã là câu hỏi nội dung thì
    # không bao giờ được kết thúc bằng một danh sách file.
    from app.core.router_rules import looks_like_content_question

    if looks_like_content_question(question):
        if trace:
            trace.mark("câu hỏi nội dung — bỏ qua khớp tiêu đề")
        _run_content(
            outcome, identity, content_limit, allow_llm, course,
            sink=sink, trace=trace, embed_future=embed_future,
            scope_doc_type=scope_doc_type, persona=persona,
        )
        return

    rows = retrieval.title_search(
        identity, question, limit=limit,
        assistant_slug=(persona.slug or None) if persona else None,
    )
    if trace:
        trace.mark("FULLTEXT tiêu đề (DB)")
    if rows:
        outcome.branch = "fulltext"
        outcome.answer, outcome.citations = render_documents(rows, route, course, identity=identity, persona=persona)
        # Fallback trả về loại KHÁC loại người dùng hỏi ("slide GT1" nhưng chỉ
        # có giáo trình) → phải nói thật là không có đúng thứ họ cần.
        if route.doc_type and not any(
            str(r["type"]) == route.doc_type.value for r in rows
        ):
            from app.core.render import DOC_TYPE_LABEL

            label = DOC_TYPE_LABEL.get(route.doc_type.value, "loại tài liệu này")
            outcome.answer = (
                f"Em không tìm thấy {label} như anh/chị cần — "
                f"nhưng có tài liệu liên quan, anh/chị xem tạm ạ:\n" + outcome.answer
            )
        _store_cache(outcome, rows, identity)
        return

    _run_content(
        outcome, identity, content_limit, allow_llm, course,
        sink=sink, trace=trace, embed_future=embed_future,
        scope_doc_type=scope_doc_type, persona=persona,
    )


def _run_content(
    outcome: ChatOutcome,
    identity: Identity,
    limit: int,
    allow_llm: bool,
    course: dict | None = None,
    sink=None,
    trace: _Trace | None = None,
    embed_future=None,
    scope_doc_type: str | None = None,
    persona=None,
) -> None:
    """Nhánh B. GĐ2: RAG đầy đủ (hybrid → rerank → sinh có trích dẫn).

    Thang hạ cấp, mỗi bậc rơi êm xuống bậc dưới:
      RAG (cần LLM + đã có embedding) → trích nguyên văn FULLTEXT (GĐ1) → not_found.
    Quota chạm trần (allow_llm=False) vào thẳng bậc giữa — đúng KT-10.
    """
    from app.core import rag

    route = outcome.route
    if course is None:
        course = retrieval.resolve_course(route)

    # Người dùng NÊU RÕ loại tài liệu ("quy chế...", "giáo trình nói gì...")
    # → khoanh truy hồi vào đúng loại đó. Scope mềm: không ra thì rag tự nới.
    if scope_doc_type is None and route.doc_type is not None:
        scope_doc_type = route.doc_type.value

    if allow_llm and rag.infrastructure_ready():
        emit = None
        if sink is not None:
            # SSE: route đi trước token đầu tiên. branch chốt 'rag' tại đây;
            # trường hợp hiếm sinh thất bại giữa chừng thì nội dung fallback vẫn
            # về đúng client, chỉ nhãn route đã phát là 'rag' — chấp nhận được.
            outcome.branch = "rag"
            sink("route", outcome)
            emit = lambda sentence: sink("token", sentence + "\n")

        result = rag.answer(
            identity, route, course, keep=limit, emit=emit, trace=trace,
            embed_future=embed_future, scope_doc_type=scope_doc_type,
            assistant_slug=(persona.slug or None) if persona else None,
            persona=persona,
        )
        if result is not None:
            outcome.gen_tokens_in = result.tokens_in
            outcome.gen_tokens_out = result.tokens_out
            outcome.embed_tokens = result.tokens_embed
            outcome.gen_model = result.model or None
            if result.not_found:
                outcome.branch = "rag"
                outcome.answer = render_not_found(route, course, scope_doc_type, identity, persona)
                return
            outcome.branch = "rag"
            outcome.answer = result.answer
            outcome.citations = result.citations
            _store_cache(
                outcome,
                [c.model_dump() for c in result.citations],
                identity,
                from_chunks=True,
            )
            return
        # result None = sinh thất bại → rơi xuống chế độ trích nguyên văn.

    chunks = retrieval.chunk_search(
        identity, route.standalone_question, route.keywords, course, limit=limit,
        scope_doc_type=scope_doc_type,
        assistant_slug=(persona.slug or None) if persona else None,
    )
    if trace:
        trace.mark("FULLTEXT chunk (DB)")
    outcome.branch = "content"
    outcome.answer, outcome.citations = render_chunks(
        chunks, route, course, scope_doc_type, identity, persona
    )
    if chunks:
        _store_cache(outcome, chunks, identity, from_chunks=True)


def _store_cache(
    outcome: ChatOutcome,
    rows: list[dict[str, Any]],
    identity: Identity,
    from_chunks: bool = False,
) -> None:
    """Lên lịch ghi cache ở luồng nền — người hỏi không phải chờ 2-3 lượt DB
    của việc sổ sách. Giá trị cần thiết được chốt TRƯỚC khi sang thread (answer/
    citations đã final tại thời điểm gọi; _finalize chỉ đụng log_id/latency)."""
    import threading

    threading.Thread(
        target=_store_cache_sync,
        args=(
            outcome.route.standalone_question,
            outcome.raw_question,
            outcome.answer,
            [c.model_dump() for c in outcome.citations],
            outcome.branch,
            rows,
            identity,
            from_chunks,
            outcome.context_key,
        ),
        daemon=True,
    ).start()


def _store_cache_sync(
    standalone_question: str,
    raw_question: str,
    answer: str,
    citations: list[dict[str, Any]],
    branch: str,
    rows: list[dict[str, Any]],
    identity: Identity,
    from_chunks: bool,
    context_key: str = "",
) -> None:
    """Thân thật của việc ghi cache — chạy trong thread nền."""
    try:
        from app.db import fetch_all, in_clause

        ids = list({int(r["document_id" if from_chunks else "id"]) for r in rows})
        placeholder, params = in_clause("doc", ids)
        docs = fetch_all(
            f"SELECT id, access, faculty_id FROM documents WHERE id IN ({placeholder})",
            params,
        )

        # Khóa chính: câu viết lại độc lập (KT-07) — hit chéo giữa các cách
        # diễn đạt. Khóa phụ: câu gốc — lượt lặp y hệt hit ngay tầng 0, khỏi
        # tốn router.
        keys = [standalone_question]
        from app.vn import vn_norm

        if raw_question and vn_norm(raw_question) != vn_norm(keys[0]):
            keys.append(raw_question)

        for key in keys:
            cache.store(
                question=key,
                answer=answer,
                citations=citations,
                route=branch,
                documents=docs,
                identity=identity,
                context_key=context_key,
            )
    except Exception:  # pragma: no cover
        log.exception("Ghi cache thất bại — bỏ qua, không chặn câu trả lời")


def _finalize(
    outcome: ChatOutcome,
    session_id: str,
    message: str,
    identity: Identity,
    started: float,
    trace: _Trace | None = None,
) -> ChatOutcome:
    outcome.latency_ms = int((time.perf_counter() - started) * 1000)

    # Ghi query_logs ở luồng nền — người hỏi không chờ lượt DB của việc sổ
    # sách. Đổi lại done.log_id = null; nút feedback (chưa có trên UI) khi làm
    # sẽ chuyển sang UUID sinh trước thay vì AUTO_INCREMENT.
    log_params = {
        "session_id": session_id,
        "user_id": identity.user_id,
        "question": message,
        "standalone": outcome.route.standalone_question,
        "intent": outcome.route.intent.value,
        "route": outcome.branch,
        "router_source": outcome.route.source,
        "model": outcome.gen_model or outcome.route.model,
        "tin": outcome.tokens_in,
        "tout": outcome.tokens_out,
        "cost": outcome.cost_usd,
        "latency": outcome.latency_ms,
        "results": len(outcome.citations),
        "degraded": 1 if outcome.degraded else 0,
    }

    def _write_log() -> None:
        try:
            execute(
                """
                INSERT INTO query_logs
                    (session_id, user_id, question, standalone_question, intent,
                     route, router_source, model, tokens_in, tokens_out,
                     cost_usd, latency_ms, result_count, degraded)
                VALUES
                    (:session_id, :user_id, :question, :standalone, :intent,
                     :route, :router_source, :model, :tin, :tout,
                     :cost, :latency, :results, :degraded)
                """,
                log_params,
            )
        except Exception:  # pragma: no cover
            log.exception("Ghi query_logs thất bại")

    _bg_pool.submit(_write_log)

    if trace is not None:
        trace.mark("ghi log (nền)")
        outcome.trace = trace.steps

    # Cộng quota ở luồng nền — thêm một lượt DB mà người hỏi không cần chờ.
    # Token embed tính vào quota như input: chi phí thật thì tính cả.
    import threading

    tokens_in_total = outcome.tokens_in + outcome.embed_tokens
    tokens_out_total = outcome.tokens_out

    def _add_usage() -> None:
        try:
            quota.add_usage(identity, tokens_in_total, tokens_out_total)
        except Exception:  # pragma: no cover
            log.exception("Cộng token_quota thất bại")

    threading.Thread(target=_add_usage, daemon=True).start()

    return outcome
