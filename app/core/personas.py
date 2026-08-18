"""Trợ lý ảo đọc từ bảng `assistants` — mỗi bot là một bản ghi, không phải
một nhánh if trong mã.

Vì sao tách hẳn ra đây: một triển khai có thể chạy nhiều bot cạnh nhau (tra cứu
tài liệu, tư vấn tuyển sinh, hỗ trợ sinh viên…) trên cùng một backend. Chúng chỉ
khác nhau ở NỘI DUNG — tên, phạm vi tài liệu, câu chào, câu từ chối, đầu mối
liên hệ — chứ không khác ở luồng xử lý. Nhét những thứ đó vào mã là mỗi lần
thêm bot lại phải sửa Python, và sẽ đẻ ra đúng loại lỗi đã gặp: bot tư vấn học
vụ mời người hỏi "cho em biết cần tài liệu môn nào để tự ôn".

Không có bot nào khớp (chưa chạy migration, sai slug, CSDL chớp tắt) thì rơi về
`DEFAULT` — hệ thống vẫn trả lời được, chỉ là bằng giọng chung.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class Persona:
    slug: str = ""
    name: str = "Trợ lý tra cứu tài liệu"
    tagline: str = ""
    corpus_label: str = "tài liệu hiện có"
    offer: str = "Anh/chị cần tra cứu nội dung nào thì cho em biết, em hỗ trợ ngay ạ."
    doc_types: tuple[str, ...] = ()
    # Chủ đề bot này KHÔNG nhận, dạng chuỗi đã bỏ dấu ("hoc phi").
    blocked_topics: tuple[str, ...] = ()
    # Mức quyền tài liệu bot phục vụ. Rỗng = không giới hạn thêm.
    # Đây là bộ lọc CHỒNG LÊN quyền của người hỏi, chỉ thu hẹp chứ không nới.
    access_levels: tuple[str, ...] = ()
    default_doc_type: str | None = None

    # Tham số sinh trả lời. None = dùng mặc định hệ thống (.env), nhờ vậy bot
    # không khai gì thì hành vi y như trước.
    gen_temperature: float | None = None
    gen_max_tokens: int | None = None
    # Ngân sách suy luận. 0 = tắt (mặc định của cả dự án — KT-04).
    # Token suy luận tính vào OUTPUT nên đây là tham số đắt nhất trên trang
    # cấu hình; xem chú thích ở generation_config() bên dưới.
    gen_thinking_budget: int = 0

    def generation_config(self, settings) -> tuple[float, int, int]:
        """(temperature, max_output_tokens, thinking_budget) cho một lượt sinh.

        Trần token TRẢ LỜI và ngân sách SUY LUẬN được cộng lại thành
        max_output_tokens gửi cho model — đây là chỗ dễ sập nhất nếu để người
        dùng tự nhập một con số chung: token suy luận trừ thẳng vào
        max_output_tokens, nên bật suy luận 600 trên trần 800 là câu trả lời
        chỉ còn 200 token, cụt giữa chừng. Cụt thì hậu kiểm trích dẫn cắt câu
        cuối, mất dấu [n], và cả câu trả lời bị coi như không tìm thấy.

        Tách hai ô rồi tự cộng thì lỗi đó không xảy ra được nữa.
        """
        temperature = (
            float(self.gen_temperature)
            if self.gen_temperature is not None
            else settings.gen_temperature
        )
        answer_tokens = int(self.gen_max_tokens or settings.gen_max_output_tokens)
        thinking = max(0, int(self.gen_thinking_budget or 0))
        return temperature, answer_tokens + thinking, thinking
    screen: str | None = None
    replies: dict[str, str] = field(default_factory=dict)
    contact: dict[str, str] = field(default_factory=dict)
    welcome: dict[str, Any] = field(default_factory=dict)
    brand: dict[str, str] = field(default_factory=dict)

    def reply(self, key: str, fallback: str) -> str:
        """Câu tĩnh của bot, ghép thêm lời mời đi tiếp của chính nó.

        Lời mời để RIÊNG một cột chứ không viết chung vào từng câu: bốn lối rẽ
        tĩnh đều kết bằng nó, sửa một chỗ là cả bốn đổi theo, và không bao giờ
        có chuyện bot này mời việc của bot kia.
        """
        text = (self.replies.get(key) or "").strip() or fallback
        if self.offer and key != "abusive":
            return f"{text} {self.offer}".strip()
        if self.offer:
            # Câu từ chối ngôn từ xấu: mời quay lại ở câu riêng, nhẹ giọng hơn.
            return f"{text} Khi nào cần, anh/chị cứ hỏi em nhé."
        return text


DEFAULT = Persona()

_cache: dict[str, tuple[float, Persona | None]] = {}
_all_cache: tuple[float, list[Persona]] | None = None
_lock = threading.Lock()


def _json(value: Any, empty):
    if value is None:
        return empty
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return empty


def _row_to_persona(row: dict[str, Any]) -> Persona:
    doc_types = _json(row.get("doc_types"), [])
    return Persona(
        slug=str(row.get("slug") or ""),
        name=str(row.get("name") or DEFAULT.name),
        tagline=str(row.get("tagline") or ""),
        corpus_label=str(row.get("corpus_label") or DEFAULT.corpus_label),
        offer=str(row.get("offer") or ""),
        doc_types=tuple(str(t) for t in doc_types if t),
        blocked_topics=tuple(
            str(t).strip().lower() for t in _json(row.get("blocked_topics"), []) if t
        ),
        access_levels=tuple(
            str(a) for a in _json(row.get("access_levels"), []) if a
        ),
        default_doc_type=(row.get("default_doc_type") or None),
        gen_temperature=(
            float(row["gen_temperature"]) if row.get("gen_temperature") is not None else None
        ),
        gen_max_tokens=(
            int(row["gen_max_tokens"]) if row.get("gen_max_tokens") is not None else None
        ),
        gen_thinking_budget=int(row.get("gen_thinking_budget") or 0),
        screen=(row.get("screen") or None),
        replies={k: str(v) for k, v in _json(row.get("replies"), {}).items()},
        contact={k: str(v) for k, v in _json(row.get("contact"), {}).items()},
        welcome=_json(row.get("welcome"), {}),
        brand={k: str(v) for k, v in _json(row.get("brand"), {}).items()},
    )


_COLUMNS = """
    slug, name, tagline, corpus_label, offer, doc_types, blocked_topics,
    access_levels, default_doc_type, screen,
    gen_temperature, gen_max_tokens, gen_thinking_budget,
    replies, contact, welcome, brand
"""


def get(slug: str | None) -> Persona:
    """Lấy persona theo slug. Không có thì trả DEFAULT — không bao giờ ném lỗi.

    Bot là thứ nằm trên đường đi của MỌI câu hỏi; để nó ném lỗi vì một dòng
    thiếu trong CSDL là đánh sập cả chat vì một chuyện trang trí.
    """
    if not slug:
        return DEFAULT

    now = time.monotonic()
    cached = _cache.get(slug)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1] or DEFAULT

    from app.db import fetch_one

    persona: Persona | None = None
    try:
        row = fetch_one(
            f"SELECT {_COLUMNS} FROM assistants WHERE slug = :slug AND is_active = 1",
            {"slug": slug},
        )
        if row:
            persona = _row_to_persona(row)
        else:
            log.warning("Không có trợ lý nào mang slug %r — dùng persona mặc định", slug)
    except Exception as exc:  # bảng chưa tạo, CSDL chớp tắt…
        log.warning("Không đọc được bảng assistants (%s) — dùng persona mặc định", exc)

    with _lock:
        _cache[slug] = (now, persona)
    return persona or DEFAULT


def list_active() -> list[Persona]:
    """Danh sách bot đang bật, để frontend dựng bong bóng."""
    global _all_cache

    now = time.monotonic()
    if _all_cache and now - _all_cache[0] < CACHE_TTL_SECONDS:
        return _all_cache[1]

    from app.db import fetch_all

    try:
        rows = fetch_all(
            f"SELECT {_COLUMNS} FROM assistants WHERE is_active = 1 "
            f"ORDER BY sort_order, id"
        )
        personas = [_row_to_persona(r) for r in rows]
    except Exception as exc:
        log.warning("Không liệt kê được assistants (%s)", exc)
        personas = []

    with _lock:
        _all_cache = (now, personas)
    return personas


def clear_cache() -> None:
    """Gọi sau khi sửa bảng assistants — nếu không phải chờ hết TTL 5 phút."""
    global _all_cache
    with _lock:
        _cache.clear()
        _all_cache = None
