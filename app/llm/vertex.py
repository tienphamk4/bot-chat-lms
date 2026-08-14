"""Cầu nối Vertex AI (google-genai).

Cả dự án chỉ gọi LLM qua module này, nên ba tham số chống vỡ ngân sách được ép
ở một chỗ duy nhất và không thể quên:

- `thinking_budget=0` — token suy luận tính vào output. Đây là nguyên nhân vỡ
  ngân sách phổ biến nhất (KT-04).
- `max_output_tokens` chặn trên — output đắt gấp 4-6 lần input (KT-05).
- `temperature` thấp — router cần ổn định, không cần sáng tạo.

Lỗi gọi LLM không bao giờ được làm hỏng request: hàm ở đây trả về `None` và để
nơi gọi hạ cấp về đường rule-based.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# Client theo location — sinh/router ở google_cloud_location, embedding có thể
# ở location khác (xem chú thích embed_location trong config).
_clients: dict[str, Any] = {}
_client_lock = threading.Lock()


@dataclass
class LLMResult:
    data: dict[str, Any]
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    model: str


# Trần thời gian cho lời gọi sinh (dài hơn router vì stream 400 token).
GEN_TIMEOUT_SECONDS = 25.0


def _http_options(timeout_seconds: float, attempts: int = 2):
    """Trần thời gian + bớt retry cho một lời gọi.

    Mặc định SDK gặp 429 sẽ tự retry nhiều lần với backoff — người dùng ngồi
    chờ 20-40s cho một câu trả lời mà đằng nào cũng có đường hạ cấp. Chặn ở
    đây: quá hạn/quá 2 lần thử → trả lỗi ngay → tầng trên rơi về luật/trích
    nguyên văn trong vài giây.
    """
    from google.genai import types

    kwargs: dict[str, Any] = {"timeout": int(timeout_seconds * 1000)}
    retry_cls = getattr(types, "HttpRetryOptions", None)
    if retry_cls is not None:
        try:
            kwargs["retry_options"] = retry_cls(attempts=attempts)
        except Exception:  # pragma: no cover - khác biệt phiên bản SDK
            pass
    try:
        return types.HttpOptions(**kwargs)
    except TypeError:  # pragma: no cover - SDK cũ không có retry_options
        return types.HttpOptions(timeout=int(timeout_seconds * 1000))


def get_client(location: str | None = None) -> Any:
    """Client dùng chung theo location. Trả None nếu chưa cấu hình/chưa cài SDK.

    `location=None` = location sinh/router (google_cloud_location). Embedding
    truyền embed_location riêng. Với Gemini Developer API (api key) thì location
    không có ý nghĩa — một client cho tất cả.
    """
    settings = get_settings()
    if not settings.use_llm_router:
        return None
    if not settings.gemini_api_key and not settings.google_cloud_project:
        return None

    key = "apikey" if settings.gemini_api_key else (
        location or settings.google_cloud_location
    )
    client = _clients.get(key)
    if client is not None:
        return client

    with _client_lock:
        if key not in _clients:
            try:
                from google import genai

                if settings.gemini_api_key:
                    # Gemini Developer API — vertexai=False tường minh để thắng
                    # biến môi trường GOOGLE_GENAI_USE_VERTEXAI nếu có.
                    _clients[key] = genai.Client(
                        vertexai=False, api_key=settings.gemini_api_key
                    )
                else:
                    # Vertex AI — xác thực qua ADC hoặc GOOGLE_APPLICATION_CREDENTIALS.
                    _clients[key] = genai.Client(
                        vertexai=True,
                        project=settings.google_cloud_project,
                        location=key,
                    )
            except Exception as exc:  # pragma: no cover - phụ thuộc môi trường
                log.warning("Không khởi tạo được LLM client (%s): %s", key, exc)
                return None
    return _clients.get(key)


def generate_json(
    system_instruction: str,
    prompt: str,
    response_schema: dict[str, Any],
    max_output_tokens: int = 256,
    temperature: float = 0.0,
) -> LLMResult | None:
    """Gọi model, ép trả JSON đúng schema. Trả None nếu hỏng ở bất kỳ khâu nào."""
    client = get_client()
    if client is None:
        return None

    settings = get_settings()

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=settings.router_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                response_schema=response_schema,
                # KT-04: bắt buộc, không phải tùy chọn.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                # Router chỉ thử MỘT lần: tầng luật là fallback tử tế, thử lại
                # dưới quota nghẽn chỉ kéo dài cái chắc chắn thất bại (7-10s).
                http_options=_http_options(settings.router_timeout_seconds, attempts=1),
            ),
        )
    except Exception as exc:  # pragma: no cover - phụ thuộc mạng
        log.warning("Gọi Vertex thất bại: %s", exc)
        return None

    usage = getattr(response, "usage_metadata", None)
    tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
    tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
    tokens_cached = int(getattr(usage, "cached_content_token_count", 0) or 0)

    raw = (getattr(response, "text", None) or "").strip()
    if not raw:
        log.warning("Vertex trả về rỗng (có thể do chạm max_output_tokens)")
        return None

    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("Vertex trả JSON không hợp lệ: %.200s", raw)
        return None

    if not isinstance(data, dict):
        return None

    return LLMResult(
        data=data,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=tokens_cached,
        model=settings.router_model,
    )


@dataclass
class TextResult:
    text: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    model: str
    finish_reason: str


def generate_text_stream(
    system_instruction: str,
    prompt: str,
    model: str | None = None,
    max_output_tokens: int = 400,
    temperature: float = 0.2,
):
    """Bản streaming của generate_text — nguồn token thật cho SSE (GĐ2).

    Generator giao thức hai loại phần tử:
        ("delta", str)              — mẩu văn bản mới, 0..n lần
        ("done", TextResult | None) — đúng một lần, cuối cùng. None = hỏng.
    Cùng ba van chi phí với generate_text; usage lấy từ chunk cuối.
    """
    client = get_client()
    if client is None:
        yield ("done", None)
        return

    settings = get_settings()
    target_model = model or settings.gen_model

    # Chuỗi model thử lần lượt: model chính → model dự phòng (hàng đợi quota
    # RIÊNG theo base model — 3.5 nghẽn thì 2.5 thường vẫn thoáng). Chỉ chuyển
    # làn khi model trước CHƯA nhả ra chữ nào — đã phát chữ rồi mà đổi model là
    # nối hai giọng văn khác nhau.
    lineup = [target_model]
    fallback = settings.gen_fallback_model
    if fallback and fallback != target_model:
        lineup.append(fallback)

    from google.genai import types

    for attempt_model in lineup:
        parts: list[str] = []
        usage = None
        finish = ""
        try:
            stream = client.models.generate_content_stream(
                model=attempt_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),  # KT-04
                    http_options=_http_options(GEN_TIMEOUT_SECONDS),
                ),
            )
            for chunk in stream:
                piece = getattr(chunk, "text", None)
                chunk_usage = getattr(chunk, "usage_metadata", None)
                if chunk_usage is not None:
                    usage = chunk_usage
                candidates = getattr(chunk, "candidates", None) or []
                if candidates and getattr(candidates[0], "finish_reason", None):
                    finish = str(candidates[0].finish_reason)
                if piece:
                    parts.append(piece)
                    yield ("delta", piece)
        except Exception as exc:  # pragma: no cover - phụ thuộc mạng
            if parts:
                # Đã phát chữ rồi mới chết — không chuyển làn được nữa.
                log.warning("Stream đứt giữa chừng (%s): %s", attempt_model, exc)
                yield ("done", None)
                return
            log.warning(
                "Model %s không phản hồi (%s)%s", attempt_model, exc,
                " — chuyển model dự phòng" if attempt_model != lineup[-1] else "",
            )
            continue

        text_out = "".join(parts).strip()
        if not text_out:
            log.warning("Model %s trả stream rỗng", attempt_model)
            continue

        yield (
            "done",
            TextResult(
                text=text_out,
                tokens_in=int(getattr(usage, "prompt_token_count", 0) or 0),
                tokens_out=int(getattr(usage, "candidates_token_count", 0) or 0),
                tokens_cached=int(getattr(usage, "cached_content_token_count", 0) or 0),
                model=attempt_model,
                finish_reason=finish,
            ),
        )
        return

    yield ("done", None)


def generate_text(
    system_instruction: str,
    prompt: str,
    model: str | None = None,
    max_output_tokens: int = 400,
    temperature: float = 0.2,
) -> TextResult | None:
    """Sinh văn bản tự do — dùng cho câu trả lời RAG (giai đoạn 2).

    Giữ nguyên ba van chi phí như generate_json. finish_reason được trả ra để
    tầng trên theo dõi tình trạng cắt cụt do trần output (KT-05 dặn giám sát).
    """
    client = get_client()
    if client is None:
        return None

    settings = get_settings()
    target_model = model or settings.gen_model

    try:
        from google.genai import types

        response = client.models.generate_content(
            model=target_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),  # KT-04
            ),
        )
    except Exception as exc:  # pragma: no cover - phụ thuộc mạng
        log.warning("Sinh trả lời thất bại: %s", exc)
        return None

    usage = getattr(response, "usage_metadata", None)
    text_out = (getattr(response, "text", None) or "").strip()
    if not text_out:
        log.warning("Model sinh rỗng (model=%s)", target_model)
        return None

    finish = ""
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        finish = str(getattr(candidates[0], "finish_reason", "") or "")

    return TextResult(
        text=text_out,
        tokens_in=int(getattr(usage, "prompt_token_count", 0) or 0),
        tokens_out=int(getattr(usage, "candidates_token_count", 0) or 0),
        tokens_cached=int(getattr(usage, "cached_content_token_count", 0) or 0),
        model=target_model,
        finish_reason=finish,
    )
