"""Định tuyến hai tầng: luật trước, LLM sau.

Tầng luật (`router_rules`) bắt các câu có cấu trúc rõ ràng và không tốn token.
Chỉ phần còn lại mới đi tới Vertex Flash-Lite.

Khi Vertex hỏng, hết quota, hoặc bị tắt bằng cấu hình, hàm ở đây trả về kết quả
tốt nhất mà tầng luật đoán được — hệ thống hạ cấp mượt chứ không dừng phục vụ
(KT-10).
"""

from __future__ import annotations

import logging

from app.core import router_rules
from app.llm import vertex
from app.llm.prompts import router_prompt, router_system
from app.schemas import DocType, Intent, RouterResult

log = logging.getLogger(__name__)

ROUTER_MAX_OUTPUT_TOKENS = 200

# Trường không xác định được thì trả chuỗi rỗng thay vì null: schema đơn giản
# hơn, model ít trả sai hơn, và việc quy đổi "" -> None làm ở _coerce.
ROUTER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["lookup_doc", "ask_content", "out_of_scope", "greeting"],
        },
        "course_code": {"type": "string"},
        "course_hint": {"type": "string"},
        # Không có "" trong enum — Gemini 3.5 từ chối enum chứa chuỗi rỗng
        # (400 INVALID_ARGUMENT). Không xác định được thì model BỎ TRƯỜNG NÀY
        # (không nằm trong required); _doc_type coi thiếu là None.
        "doc_type": {
            "type": "string",
            "enum": [
                "giao_trinh",
                "slide",
                "de_cuong",
                "de_thi",
                "bai_tap",
                "tai_lieu_tham_khao",
                "quy_che",
            ],
        },
        "semester": {"type": "string"},
        "lecturer": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "standalone_question": {"type": "string"},
    },
    "required": ["intent", "standalone_question"],
    "propertyOrdering": [
        "intent",
        "course_code",
        "course_hint",
        "doc_type",
        "semester",
        "lecturer",
        "keywords",
        "standalone_question",
    ],
}

def resolve(
    message: str,
    allow_llm: bool = True,
    verdict: router_rules.RuleVerdict | None = None,
    blocked_topics: tuple[str, ...] = (),
) -> RouterResult:
    """`verdict` truyền sẵn khi nơi gọi đã chạy tầng luật (để quyết việc khác,
    ví dụ có nên embed song song không) — tránh chạy luật hai lần."""
    if verdict is None:
        verdict = router_rules.route(message, blocked_topics)
    if verdict.confident or not allow_llm:
        log.info(
            "Router [rule] intent=%s course=%s type=%s (LLM không cần gọi%s)",
            verdict.result.intent.value,
            verdict.result.course_code,
            verdict.result.doc_type.value if verdict.result.doc_type else None,
            "" if verdict.confident else " — quota chặn",
        )
        return verdict.result

    response = vertex.generate_json(
        system_instruction=router_system(blocked_topics),
        prompt=router_prompt(message),
        response_schema=ROUTER_SCHEMA,
        max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
    )
    if response is None:
        # Vertex hỏng hoặc tắt: dùng kết quả đoán của tầng luật.
        log.info("Router [rule-fallback] LLM không khả dụng — dùng kết quả tầng luật")
        verdict.result.llm_failed = True
        return verdict.result

    merged = _merge(verdict.result, response)

    # Tầng luật đã thấy dấu hiệu hỏi nội dung TƯỜNG MINH thì model không được
    # lật ngược thành câu đi tìm file.
    #
    # Ca thật: "lộ trình ngành An toàn thông tin của trường là gì" — luật phán
    # đúng ask_content nhờ chữ "là gì", nhưng model đổi thành lookup_doc và tự
    # đoán thêm loại "đề cương". Hệ quả: tìm đề cương → không có → nhánh dự
    # phòng khớp tiêu đề → trả về "Em không tìm thấy đề cương như anh/chị cần"
    # kèm một danh sách file, trong khi tài liệu có đủ nội dung để trả lời.
    #
    # Luật ở đây chỉ nhận cụm rõ ràng nên gần như không dương tính giả; còn
    # model thì đang SUY DIỄN. Suy diễn không được thắng bằng chứng.
    if (
        verdict.result.intent is Intent.ask_content
        and merged.intent is not Intent.ask_content
    ):
        log.info(
            "Router: giữ ask_content của tầng luật, bỏ qua %s do model đề xuất",
            merged.intent.value,
        )
        merged.intent = Intent.ask_content
        # Bỏ luôn loại tài liệu model đoán kèm, TRỪ KHI tầng luật cũng thấy.
        # Model vừa đọc sai kiểu câu hỏi thì phán đoán khác của nó trên cùng
        # câu đó cũng không đáng tin — mà loại tài liệu bịa ra thì siết truy
        # hồi vào đúng chỗ không có gì ("đề cương" trong kho chưa có đề cương).
        if merged.doc_type is not None and verdict.result.doc_type is None:
            log.info("Router: bỏ doc_type=%s model đoán kèm", merged.doc_type.value)
            merged.doc_type = None
    log.info(
        "Router [llm] model=%s intent=%s course=%s tokens=%d/%d — GEMINI ĐÃ ĐƯỢC GỌI",
        response.model,
        merged.intent.value,
        merged.course_code,
        response.tokens_in,
        response.tokens_out,
    )
    return merged


def _merge(rule_result: RouterResult, response: vertex.LLMResult) -> RouterResult:
    data = response.data

    merged = RouterResult(
        intent=_intent(data.get("intent")),
        course_id=rule_result.course_id,
        course_code=_text(data.get("course_code")) or rule_result.course_code,
        course_hint=_text(data.get("course_hint")) or rule_result.course_hint,
        doc_type=_doc_type(data.get("doc_type")) or rule_result.doc_type,
        semester=_semester(data.get("semester")) or rule_result.semester,
        lecturer=_text(data.get("lecturer")) or rule_result.lecturer,
        keywords=[k for k in (data.get("keywords") or []) if isinstance(k, str) and k.strip()][:5],
        standalone_question=_text(data.get("standalone_question")) or rule_result.standalone_question,
        source="llm",
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        model=response.model,
    )
    return merged


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _intent(value) -> Intent:
    try:
        return Intent(value)
    except ValueError:
        log.warning("Router trả intent lạ: %r", value)
        return Intent.lookup_doc


def _doc_type(value) -> DocType | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return DocType(text)
    except ValueError:
        return None


def _semester(value) -> str | None:
    """Chỉ nhận đúng dạng YYYYK — model hay bịa '2026' hoặc 'HK1'."""
    text = _text(value)
    if text and len(text) == 5 and text.isdigit() and text[4] in "123":
        return text
    return None
