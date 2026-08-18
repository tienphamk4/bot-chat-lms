"""Kiểu dữ liệu dùng chung: hợp đồng API và kết quả router.

Hợp đồng ở đây được cố định từ giai đoạn 1 và KHÔNG đổi ở giai đoạn 2. Giai
đoạn 2 chỉ thay ruột nhánh nội dung (FULLTEXT → hybrid + rerank + sinh), còn
client vẫn đọc đúng các sự kiện `route` / `token` / `citations` / `done`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(str, Enum):
    lookup_doc = "lookup_doc"
    ask_content = "ask_content"
    out_of_scope = "out_of_scope"
    greeting = "greeting"
    # CHỈ tầng luật sinh ra hai giá trị này; enum của router LLM cố ý không có.
    # Model gặp câu chửi sẽ phán out_of_scope, gặp câu cụt sẽ đoán bừa một ý
    # định — cả hai đều là mức hạ cấp chấp nhận được.
    abusive = "abusive"
    clarify = "clarify"


class DocType(str, Enum):
    giao_trinh = "giao_trinh"
    slide = "slide"
    de_cuong = "de_cuong"
    de_thi = "de_thi"
    bai_tap = "bai_tap"
    tai_lieu_tham_khao = "tai_lieu_tham_khao"
    quy_che = "quy_che"


class Access(str, Enum):
    public = "public"
    faculty = "faculty"
    course = "course"
    staff = "staff"


class DocStatus(str, Enum):
    pending = "pending"
    pending_review = "pending_review"
    needs_ocr = "needs_ocr"
    indexed = "indexed"
    failed = "failed"


# --- Router -----------------------------------------------------------------


class RouterResult(BaseModel):
    """Kết quả bóc tách ý định.

    `standalone_question` là trường then chốt (KT-07): câu hỏi được viết lại độc
    lập ngữ cảnh, nhờ đó các bước sau không phải gửi lịch sử hội thoại — chặn
    phình input theo số lượt, và làm khóa cache dùng chung được giữa các user.
    """

    intent: Intent = Intent.lookup_doc
    course_code: str | None = None
    # id trong bảng courses khi tầng luật đã khớp qua chỉ mục RAM — giúp bước
    # sau khỏi tra DB lại. None = chưa xác định (kể cả khi có course_code).
    course_id: int | None = None
    course_hint: str | None = None
    doc_type: DocType | None = None
    semester: str | None = None
    lecturer: str | None = None
    keywords: list[str] = Field(default_factory=list)
    standalone_question: str = ""

    # Không gửi cho LLM, do tầng ứng dụng điền.
    source: Literal["rule", "llm", "cache"] = "rule"
    # True = đã thử gọi router LLM nhưng hỏng (429/timeout) → rơi về luật.
    # Trace dùng cờ này để không đổ oan "rule chậm" cho cú gọi thất bại.
    llm_failed: bool = False
    # True = course đến từ NGỮ CẢNH TRANG chứ không phải người dùng nêu —
    # tín hiệu mạnh nhưng mềm: không có kết quả thì được phép bỏ ràng buộc,
    # khác với môn người dùng nêu tường minh (ràng buộc cứng).
    course_from_context: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None


# --- Chat -------------------------------------------------------------------


class ChatContext(BaseModel):
    """Người dùng đang đứng ở đâu trong LMS khi hỏi — do app nhúng truyền vào.

    screen='lesson' + course_code: đang mở bài học một môn → câu hỏi không nêu
    môn sẽ ưu tiên tài liệu môn đó. screen='advisory': trang tư vấn quy chế →
    ưu tiên văn bản quy chế. Không có context = trang chủ, hành vi như cũ.
    """

    screen: Literal["lesson", "advisory"] | None = None
    course_code: str | None = Field(None, max_length=32)
    # Bot nào đang trả lời. Quyết định câu chào, câu từ chối, phạm vi tài liệu
    # và đầu mối liên hệ — tất cả đọc từ bảng `assistants`, không nằm trong mã.
    assistant: str | None = Field(None, max_length=64)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    message: str = Field(min_length=1, max_length=2000)
    stream: bool = True
    context: ChatContext | None = None


class Citation(BaseModel):
    n: int
    document_id: int
    title: str
    type: DocType
    semester: str | None = None
    page: int | None = None      # trang bắt đầu của đoạn trích
    page_to: int | None = None   # trang kết thúc — chunk thường trải 2 trang
    heading_path: str | None = None
    url: str
    # Popup xem trước trên UI: đoạn trích nguyên văn + link xem inline.
    # preview_url chỉ có với PDF (trình duyệt render được, nhảy trang #page=N);
    # định dạng khác để None — popup rơi về hiển thị snippet.
    snippet: str | None = None
    preview_url: str | None = None


class RouteEvent(BaseModel):
    intent: Intent
    branch: str  # cache | metadata | fulltext | content | static
    router_source: str = "rule"  # rule | llm | cache — llm nghĩa là Gemini đã được gọi
    course_code: str | None = None
    doc_type: DocType | None = None
    semester: str | None = None
    lecturer: str | None = None
    standalone_question: str = ""
    cached: bool = False
    degraded: bool = False
    log_id: int | None = None


class TokenUsage(BaseModel):
    in_: int = Field(0, alias="in")
    out: int = 0
    cached: int = 0

    model_config = {"populate_by_name": True}


class DoneEvent(BaseModel):
    log_id: int | None = None
    latency_ms: int = 0
    route: str = ""
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    degraded: bool = False
    # Timeline từng bước [{'step','ms'}] — UI vẽ để thấy chậm ở đâu.
    trace: list[dict[str, Any]] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    code: Literal[
        "quota_exceeded", "upstream_error", "invalid_request", "internal"
    ]
    message: str
    degraded_to: str | None = None


class ChatResponse(BaseModel):
    """Thân trả về khi `stream=false` — gộp đúng nội dung của luồng SSE.

    Bộ eval 200 câu dùng đường này để khỏi phải phân tích SSE.
    """

    answer: str
    route: RouteEvent
    citations: list[Citation] = Field(default_factory=list)
    done: DoneEvent


# --- Tài liệu & môn học -----------------------------------------------------


class DocumentOut(BaseModel):
    id: int
    title: str
    type: DocType
    semester: str | None = None
    lecturer: str | None = None
    year: int | None = None
    course_code: str | None = None
    course_name: str | None = None
    page_count: int | None = None
    file_ext: str
    file_size: int
    download_url: str


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    page_size: int


class Offering(BaseModel):
    semester: str
    lecturer: str | None = None
    class_code: str | None = None


class CourseOut(BaseModel):
    id: int
    code: str
    name: str
    faculty_id: int | None = None
    credits: int | None = None
    aliases: list[str] = Field(default_factory=list)
    offerings: list[Offering] = Field(default_factory=list)
    document_count: int = 0


class AssistantOut(BaseModel):
    """Một trợ lý ảo — frontend đọc để tự dựng bong bóng và popup."""

    slug: str
    name: str
    tagline: str = ""
    placeholder: str = ""
    welcome: dict[str, Any] = Field(default_factory=dict)
    brand: dict[str, str] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    log_id: int
    helpful: bool
    comment: str | None = Field(default=None, max_length=1000)


# --- Admin ------------------------------------------------------------------


class MetricsOut(BaseModel):
    days: int
    cost_by_day: list[dict[str, Any]]
    route_distribution: list[dict[str, Any]]
    cache_hit_rate: float
    documents_by_status: list[dict[str, Any]]
    total_cost_usd: float
    total_queries: int
