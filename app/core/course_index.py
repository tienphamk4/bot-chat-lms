"""Chỉ mục môn học trong tiến trình — thay cho việc gọi CSDL mỗi lượt hỏi.

Bảng `courses` chỉ cỡ vài trăm đến vài nghìn dòng, nạp hết vào RAM là rẻ. Đây là
"LRU trong tiến trình" mà thiết kế nhắc tới: không có Redis, cache nằm trong
tiến trình và trong MariaDB.

Nhận diện môn bằng cách dò cụm từ (n-gram) thay vì duyệt regex qua từng môn:
độ phức tạp theo độ dài câu hỏi, không theo số môn.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.db import fetch_all
from app.vn import pipe_split, vn_norm

# Alias dài nhất hợp lý ("nguyen ly he dieu hanh" = 5 từ). Dò tới 6 cho chắc.
MAX_PHRASE_WORDS = 6

TTL_SECONDS = 300.0


@dataclass(frozen=True)
class CourseEntry:
    id: int
    code: str
    name: str
    faculty_id: int | None


class _Index:
    def __init__(self) -> None:
        self.by_phrase: dict[str, CourseEntry] = {}
        self.by_id: dict[int, CourseEntry] = {}
        self.loaded_at: float = 0.0


_index = _Index()
_lock = threading.Lock()

# Cụm từ trỏ tới hai môn khác nhau bị đánh dấu bằng entry này rồi loại khỏi chỉ
# mục: khớp mơ hồ còn tệ hơn không khớp, cứ để router LLM xử lý.
_AMBIGUOUS = CourseEntry(id=-1, code="", name="", faculty_id=None)


def refresh() -> int:
    """Nạp lại chỉ mục từ CSDL. Trả về số cụm từ đã lập chỉ mục."""
    rows = fetch_all(
        """
        SELECT id, code, code_norm, name, name_norm, aliases_norm, faculty_id
        FROM courses
        WHERE active = 1
        """
    )

    by_phrase: dict[str, CourseEntry] = {}
    by_id: dict[int, CourseEntry] = {}

    for row in rows:
        entry = CourseEntry(
            id=int(row["id"]),
            code=row["code"],
            name=row["name"],
            faculty_id=int(row["faculty_id"]) if row["faculty_id"] is not None else None,
        )
        by_id[entry.id] = entry

        phrases = [row["code_norm"], row["name_norm"], *pipe_split(row["aliases_norm"])]
        for phrase in phrases:
            phrase = (phrase or "").strip()
            # Cụm 1 ký tự ("a") gây khớp nhầm nhiều hơn giúp ích.
            if len(phrase) < 2:
                continue
            # Cụm trùng giữa hai môn: giữ cụm đầu tiên và bỏ hẳn khỏi chỉ mục,
            # vì khớp mơ hồ còn tệ hơn không khớp — để router LLM xử lý.
            if phrase in by_phrase and by_phrase[phrase].id != entry.id:
                by_phrase[phrase] = _AMBIGUOUS
                continue

            by_phrase.setdefault(phrase, entry)

    resolved = {p: e for p, e in by_phrase.items() if e is not _AMBIGUOUS}

    with _lock:
        _index.by_phrase = resolved
        _index.by_id = by_id
        _index.loaded_at = time.monotonic()

    return len(resolved)


_refreshing = False


def _ensure_fresh() -> None:
    """Hết TTL thì làm mới Ở NỀN và vẫn phục vụ bản cũ — người dùng không bao
    giờ phải chờ lượt nạp lại danh mục. Chỉ khi chưa từng nạp (lần đầu sau
    boot mà warm-up chưa xong) mới nạp đồng bộ vì không có bản cũ để dùng."""
    global _refreshing

    age = time.monotonic() - _index.loaded_at
    if age <= TTL_SECONDS:
        return

    if not _index.by_phrase and not _index.by_id:
        refresh()  # chưa có gì trong tay — buộc phải chờ
        return

    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def _background() -> None:
        global _refreshing
        try:
            refresh()
        except Exception:  # pragma: no cover - DB chập chờn thì giữ bản cũ
            pass
        finally:
            with _lock:
                _refreshing = False

    import threading

    threading.Thread(target=_background, daemon=True).start()


def match(message: str) -> CourseEntry | None:
    """Tìm môn được nhắc tới trong câu hỏi. Khớp cụm DÀI NHẤT thắng.

    "giáo trình giải tích 1" khớp "giai tich 1" chứ không phải "giai tich".
    """
    _ensure_fresh()
    if not _index.by_phrase:
        return None

    words = vn_norm(message).split()
    best: tuple[int, CourseEntry] | None = None

    for start in range(len(words)):
        for length in range(min(MAX_PHRASE_WORDS, len(words) - start), 0, -1):
            phrase = " ".join(words[start : start + length])
            entry = _index.by_phrase.get(phrase)
            if entry is not None and (best is None or length > best[0]):
                best = (length, entry)
                break  # cụm dài nhất bắt đầu từ `start`, khỏi thử ngắn hơn

    return best[1] if best else None


def get(course_id: int) -> CourseEntry | None:
    _ensure_fresh()
    return _index.by_id.get(course_id)


def invalidate() -> None:
    """Gọi sau khi ghi bảng courses để lượt hỏi tiếp theo thấy dữ liệu mới."""
    with _lock:
        _index.loaded_at = 0.0
