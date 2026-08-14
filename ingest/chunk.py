"""Chia chunk: target 500 token, overlap 80, min 120 — v2 (đại tu 14/08/2026).

Ưu tiên điểm cắt theo đúng thiết kế: heading cấp 1-4 → ngắt trang → đoạn văn →
câu. Chunk ngắn hơn min thì gộp vào chunk kế.

Khác v1 (theo kiểm toán + phản biện):
- `classify_heading` có CẤP theo ngữ cảnh từ khóa (Phần > Chương/Bài > Mục/Điều
  > n.m > n.m.k) thay cho regex một khối; 'Điều 15' sau 'Chương II' thành
  'Chương II > Điều 15', quy chế phẳng thì 'Điều 15' đứng một mình.
- Stack sibling-replace chuẩn cây tài liệu (pop mọi cấp ≥ cấp mới) — hết cảnh
  heading đầu tiên của tài liệu bị ghim vĩnh viễn làm gốc rác.
- Loại dòng mục lục (đuôi chấm-dẫn '..... 10') và dòng hình thái xấu (kết thúc
  '{' ';' — dòng code lọt từ giáo trình lập trình).
- Nối heading bị wrap dòng ('Bài 8: Tạo cơ sở dữ liệu với' + 'MySQL').
- Cắt câu bảo vệ viết tắt tiếng Việt (TS. GS. PGS. ThS. tr. vd.).
- heading_path gắn theo BLOCK tại thời điểm block ra đời, không dùng biến trạng
  thái chung — chunk nhận path của block đầu tiên thuộc về nó.

Đếm token không gọi API (ước lượng ~2.3 ký tự/token tiếng Việt).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ingest.extract import Extraction

TARGET_TOKENS = 500
OVERLAP_TOKENS = 80
MIN_TOKENS = 120

_CHARS_PER_TOKEN = 2.3
MAX_HEADING_CHARS = 120
MAX_PATH_CHARS = 500

# Đuôi chấm-dẫn của dòng mục lục: 'BÀI 1: TỔNG QUAN ......... 10'
_TOC_TAIL = re.compile(r"\.{4,}\s*\d{1,4}\s*$")

# Hình thái KHÔNG THỂ là heading: kết thúc bằng ký tự code/liệt kê dở.
_BAD_ENDINGS = ("{", "}", ";", "=", ",", "(", "\\", "/")

# Bảng luật (regex, cấp). Cấp: 1=Phần, 2=Chương/Bài/Phụ lục/La Mã, 3=Mục/Điều
# và 'n. Tiêu đề', 4='n.m', 5='n.m.k' — số cấp chỉ để so sánh pop/push.
_KEYWORD_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^phần\s+[0-9ivxlcdm]+\b.*$", re.IGNORECASE), 1),
    (re.compile(r"^(?:chương|bài|phụ\s*lục)\s+[0-9ivxlcdm]+\b.*$", re.IGNORECASE), 2),
    (re.compile(r"^[IVXLCDM]{1,7}[.)]\s+\S.*$"), 2),
    (re.compile(r"^(?:mục|điều|khoản)\s+\d+\b.*$", re.IGNORECASE), 3),
]
_NUMERIC_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,2}[.)]?\s+\S.*$"), 5),
    (re.compile(r"^\d{1,2}\.\d{1,2}[.)]?\s+\S.*$"), 4),
    (re.compile(r"^\d{1,2}[.)]\s+\S.*$"), 3),
]

# Viết tắt tiếng Việt có dấu chấm — KHÔNG được cắt câu tại đây.
_ABBREVIATIONS = {
    "ts", "gs", "pgs", "ths", "tr", "vd", "stt", "st", "tp", "q", "p",
    "mr", "mrs", "dr", "no", "vs", "etc",
}
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

PAGE_BREAK = "\f"  # đánh dấu ngắt trang trong dòng văn bản ghép


@dataclass
class Chunk:
    index: int
    content: str
    heading_path: str | None
    page_from: int | None
    page_to: int | None
    token_count: int


@dataclass
class _Block:
    """Một đoạn không chia nhỏ hơn nữa (đoạn văn hoặc heading)."""

    text: str
    page: int
    heading: str | None = None   # khác None nếu block này LÀ heading
    level: int = 0               # cấp heading (0 = không phải heading)
    path: str = ""               # heading_path hiệu lực tại thời điểm block ra đời


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


def classify_heading(line: str) -> tuple[int, str] | None:
    """Dòng này có phải heading không, và ở CẤP nào.

    Trả về (cấp, văn bản heading) hoặc None. Luật hình thái áp trước:
    ≤120 ký tự, không đuôi chấm-dẫn mục lục, không kết thúc bằng ký tự code.
    """
    text = line.strip()
    if not text or len(text) > MAX_HEADING_CHARS:
        return None
    if _TOC_TAIL.search(text):
        return None  # dòng MỤC LỤC — không bao giờ là heading nội dung
    if text.endswith(_BAD_ENDINGS):
        return None

    if text.startswith("#"):
        level = min(len(text) - len(text.lstrip("#")), 3)
        stripped = text.lstrip("#").strip()
        if stripped:
            return (level, stripped)
        return None

    for pattern, level in _KEYWORD_RULES:
        if pattern.match(text):
            return (level, text)

    for pattern, level in _NUMERIC_RULES:
        if pattern.match(text):
            # Từ đầu sau số phải mang chữ (không phải dòng toàn số của bảng)
            rest = re.sub(r"^[\d.)\s]+", "", text)
            if rest and any(ch.isalpha() for ch in rest[:24]) and rest[0].isupper():
                return (level, text)
            return None

    return None


def split_sentences(text: str) -> list[str]:
    """Cắt câu có bảo vệ viết tắt: 'TS. Nguyễn Văn A' không bị đứt tại 'TS.'."""
    raw = _SENTENCE_END.split(text)
    merged: list[str] = []
    for part in raw:
        if merged:
            prev_word = merged[-1].rstrip().rsplit(" ", 1)[-1].rstrip(".").lower()
            if merged[-1].rstrip().endswith(".") and prev_word in _ABBREVIATIONS:
                merged[-1] = merged[-1] + " " + part
                continue
        merged.append(part)
    return merged


def split(extraction: Extraction) -> list[Chunk]:
    blocks = _to_blocks(extraction)
    if not blocks:
        return []

    chunks: list[Chunk] = []
    current: list[_Block] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        text = "\n".join(b.text for b in current).strip()
        if not text:
            current, current_tokens = [], 0
            return
        # Path của chunk = path block thường ĐẦU TIÊN có path; chunk mở đầu
        # bằng rác không-path (bảng đã hạ cấp) thì lấy heading cuối trong chunk.
        body_paths = [b.path for b in current if b.heading is None and b.path]
        heading_paths = [b.path for b in current if b.heading is not None]
        path = (body_paths[0] if body_paths else (heading_paths[-1] if heading_paths else "")) or None
        if path and len(path) > MAX_PATH_CHARS:
            path = "…" + path[-(MAX_PATH_CHARS - 1):]
        chunks.append(
            Chunk(
                index=len(chunks),
                content=text,
                heading_path=path,
                page_from=current[0].page,
                page_to=current[-1].page,
                token_count=estimate_tokens(text),
            )
        )
        # Overlap: giữ đuôi ~OVERLAP_TOKENS làm đầu chunk sau, để câu trả lời
        # không bị cắt đứt ngữ cảnh đúng tại ranh giới chunk.
        tail: list[_Block] = []
        tail_tokens = 0
        for block in reversed(current):
            block_tokens = estimate_tokens(block.text)
            if tail_tokens + block_tokens > OVERLAP_TOKENS:
                break
            tail.insert(0, block)
            tail_tokens += block_tokens
        current = list(tail)
        current_tokens = tail_tokens

    for block in blocks:
        if block.heading is not None:
            # Heading là điểm cắt ưu tiên cao nhất: chốt chunk hiện tại (nếu đủ
            # dài) và KHÔNG giữ overlap cũ — mục mới bắt đầu sạch.
            if current_tokens >= MIN_TOKENS:
                flush()
                current, current_tokens = [], 0
            current.append(block)
            current_tokens += estimate_tokens(block.text)
            continue

        block_tokens = estimate_tokens(block.text)

        if current_tokens + block_tokens > TARGET_TOKENS and current_tokens >= MIN_TOKENS:
            flush()

        if block_tokens > TARGET_TOKENS:
            # Đoạn văn quá dài: xuống cấp cắt theo câu.
            for piece in _split_long_block(block):
                piece_tokens = estimate_tokens(piece.text)
                if current_tokens + piece_tokens > TARGET_TOKENS and current_tokens >= MIN_TOKENS:
                    flush()
                current.append(piece)
                current_tokens += piece_tokens
            continue

        current.append(block)
        current_tokens += block_tokens

    flush()

    # Chunk cuối ngắn hơn min: gộp vào chunk trước cho khỏi lửng lơ.
    if len(chunks) >= 2 and chunks[-1].token_count < MIN_TOKENS:
        last = chunks.pop()
        previous = chunks[-1]
        merged = previous.content + "\n" + last.content
        chunks[-1] = Chunk(
            index=previous.index,
            content=merged,
            heading_path=previous.heading_path,
            page_from=previous.page_from,
            page_to=last.page_to,
            token_count=estimate_tokens(merged),
        )

    return chunks


_MAX_HEADING_RUN = 4  # ≥5 heading liền nhau không nội dung = bảng đề cương/mục lục

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _heading_number(text: str) -> int | None:
    """Số thứ tự của heading ('Bài 5' → 5, 'CHƯƠNG II' → 2, '2.1 …' → 2)."""
    match = re.search(r"\d+", text[:24])
    if match:
        return int(match.group())
    match = re.match(r"^\S+\s+([ivxlcdm]{1,7})\b", text.lower())
    if match:
        total, prev = 0, 0
        for ch in reversed(match.group(1)):
            value = _ROMAN_VALUES[ch]
            total = total - value if value < prev else total + value
            prev = max(prev, value)
        return total
    return None


def _to_blocks(extraction: Extraction) -> list[_Block]:
    """Dựng dãy block role-tagged qua 3 bước: phân loại → hạ cấp run heading
    → gắn stack. Heading cập nhật stack NGAY khi ra đời và mọi block sau đó
    mang path hiện hành — không còn biến trạng thái ở split()."""
    # Bước 1: phân loại phẳng toàn tài liệu.
    raw: list[tuple[int | None, str, int]] = []
    for page in extraction.pages:
        for paragraph in re.split(r"\n\s*\n", page.text):
            for kind, text in _split_headings(paragraph):
                if text.strip():
                    raw.append((kind, text, page.number))

    # Bước 2: hạ cấp run heading dài — tài liệu thật không bao giờ có ≥5
    # heading liền nhau không chữ nào xen giữa; bảng 'Nội dung tổng quát'
    # (mỗi ô 'Bài N: ...') và mục lục không chấm-dẫn thì có. Hạ cả run xuống
    # văn bản thường: nội dung vẫn tìm kiếm được, cây heading sạch.
    demoted: set[int] = set()
    run: list[int] = []
    last_number: int | None = None
    for index, (kind, text, _page) in enumerate(raw):
        if kind is not None:
            number = _heading_number(text)
            # Số thứ tự KHỞI ĐỘNG LẠI sau run dài (…Bài 15, Bài 16, rồi
            # 'BÀI 1' thật mở chương): chốt demote bảng, heading mới thoát nạn.
            if (
                len(run) > _MAX_HEADING_RUN
                and number == 1
                and last_number is not None
                and last_number > 1
            ):
                demoted.update(run)
                run = []
            run.append(index)
            if number is not None:
                last_number = number
        elif len(text.strip()) < 25:
            continue  # ô đệm ngắn của bảng ('8', '4 4') — không phá run
        else:
            if len(run) > _MAX_HEADING_RUN:
                demoted.update(run)
            run = []
            last_number = None
    if len(run) > _MAX_HEADING_RUN:
        demoted.update(run)

    # Bước 3: dựng block + stack sibling-replace.
    blocks: list[_Block] = []
    stack: list[tuple[int, str]] = []

    def current_path() -> str:
        return " > ".join(text for _, text in stack)

    for index, (kind, text, page_number) in enumerate(raw):
        if kind is not None and index not in demoted:
            level = kind
            # Sibling-replace: pop mọi cấp sâu-bằng-hoặc-hơn rồi push.
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            blocks.append(
                _Block(
                    text=text, page=page_number,
                    heading=text, level=level, path=current_path(),
                )
            )
        else:
            blocks.append(
                _Block(text=text.strip(), page=page_number, path=current_path())
            )
    return blocks


def _split_headings(paragraph: str) -> list[tuple[int | None, str]]:
    """Tách heading khỏi đoạn, kèm nối heading bị wrap dòng.

    Trả về dãy (level|None, text): level != None nghĩa là heading cấp đó.
    Luật nối wrap (phản biện): heading chưa kết thúc bằng dấu câu + dòng sau
    NGẮN (≤60 ký tự), không tự là heading, tổng sau nối ≤120 ký tự.
    """
    lines = paragraph.splitlines()
    groups: list[tuple[int | None, str]] = []
    buffer: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        classified = classify_heading(line)
        if classified is None:
            buffer.append(lines[index])
            index += 1
            continue

        if buffer:
            groups.append((None, "\n".join(buffer)))
            buffer = []

        level, text = classified
        # Nối wrap: dòng kế tiếp là phần đuôi của heading?
        if (
            index + 1 < len(lines)
            and not text.endswith((".", ":", "?", "!", "…"))
        ):
            nxt = lines[index + 1].strip()
            if (
                nxt
                and len(nxt) <= 60
                and classify_heading(nxt) is None
                and not nxt.endswith((".", ";", ":"))
                and len(text) + 1 + len(nxt) <= MAX_HEADING_CHARS
            ):
                text = f"{text} {nxt}"
                index += 1
        groups.append((level, text))
        index += 1

    if buffer:
        groups.append((None, "\n".join(buffer)))
    return groups


def _split_long_block(block: _Block) -> list[_Block]:
    sentences = split_sentences(block.text)
    pieces: list[_Block] = []
    buffer = ""

    def push(text: str) -> None:
        pieces.append(_Block(text=text, page=block.page, path=block.path))

    for sentence in sentences:
        # Một CÂU đơn lẻ đã vượt target (bảng dài, văn bản không chấm câu):
        # bậc cắt cuối cùng là theo từ.
        if estimate_tokens(sentence) > TARGET_TOKENS:
            if buffer:
                push(buffer)
                buffer = ""
            for part in _split_by_words(sentence):
                push(part)
            continue

        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if estimate_tokens(candidate) > TARGET_TOKENS and buffer:
            push(buffer)
            buffer = sentence
        else:
            buffer = candidate
    if buffer:
        push(buffer)
    return pieces


def _split_by_words(sentence: str) -> list[str]:
    max_chars = int(TARGET_TOKENS * _CHARS_PER_TOKEN)
    words = sentence.split()
    parts: list[str] = []
    buffer = ""
    for word in words:
        candidate = f"{buffer} {word}" if buffer else word
        if len(candidate) > max_chars and buffer:
            parts.append(buffer)
            buffer = word
        else:
            buffer = candidate
    if buffer:
        parts.append(buffer)
    return parts
