"""Chuẩn hóa tiếng Việt — v2 (NFKD + term-map, theo thiết kế đại tu 14/08/2026).

MySQL không có `unaccent`, nên toàn bộ việc bỏ dấu làm ở tầng ứng dụng rồi
lưu vào các cột hậu tố `_norm`. Module này là NGUỒN DUY NHẤT của phép chuẩn hóa
đó — mọi cột `_norm` trong CSDL đều phải đi qua `vn_norm()`.

SỬA LUẬT TRONG FILE NÀY = PHẢI CHẠY `python -m ingest.cli renorm` trong cùng
đợt triển khai (dựng lại mọi cột _norm + cột dẫn xuất), nếu không index và
query lệch luật nhau — tìm kiếm âm thầm hỏng.

Khác v1: NFKD (phân rã tương thích — tự xử ligature ﬁ→fi, full-width （）→()),
loại ký tự Cf hai lớp (ZWSP/LRM/RLM → khoảng trắng; SHY/ZWJ/BOM → xóa hẳn),
fold ký tự trang trí [–—•·…] → khoảng trắng, TERM_MAP bảo toàn token kỹ thuật
(C++ → cplusplus) và nối số thập phân CÔ LẬP ('4,0' → '4_0' — underscore là
word-char của MySQL FULLTEXT nên thành MỘT token).

Hành vi chấp nhận đã đóng băng: superscript chú thích 'thi¹' → 'thi1' (NFKD
phân rã ¹→1) — chỉ xử lý thêm khi eval lộ ca thật.
"""

from __future__ import annotations

import re
import unicodedata

# đ/Đ không phải ký tự có dấu tổ hợp: NFD/NFKD không tách được, phải thay tay.
_DSTROKE = str.maketrans({"đ": "d", "Đ": "D"})

_WHITESPACE = re.compile(r"\s+")

# Ký tự có ý nghĩa trong cú pháp BOOLEAN MODE của MySQL FULLTEXT.
# Người dùng gõ "đề thi (giữa kỳ)" mà để nguyên là cú pháp hỏng.
_FT_SPECIAL = re.compile(r"[+\-><()~*\"@]")

# Cf hai lớp (phản biện yêu cầu): ký tự NGĂN CÁCH vô hình thay bằng khoảng
# trắng (âm tiết Việt là đơn vị từ — 'học​kỳ' phải ra 'hoc ky');
# ký tự NỐI/vô nghĩa xóa hẳn ('infor­mation' phải ra 'information').
_CF_TO_SPACE = {"​", "‎", "‏", " ", " "}
_CF_DROP_CATEGORY = "Cf"  # phần Cf còn lại (SHY, ZWJ/ZWNJ, BOM...) xóa hẳn

# Ký tự trang trí NFKD không phân rã được — fold về khoảng trắng để không
# dính vào token ('Xác suất – Thống kê' không được thành 'ke–thong').
_DECOR_TO_SPACE = str.maketrans({c: " " for c in "–—•·…‣▪©®™"})

# Token kỹ thuật bị bộ lọc cú pháp phá — ánh xạ về dạng chữ-liền TRƯỚC khi
# strip. Áp cho CẢ index lẫn query (cùng đi qua vn_norm) nên hai phía nhất
# quán by construction. Bảng dữ liệu — mở rộng tại đây, không rải rác.
_TERM_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"c\+\+", re.IGNORECASE), "cplusplus"),
    (re.compile(r"c#", re.IGNORECASE), "csharp"),
    (re.compile(r"\.net\b", re.IGNORECASE), "dotnet"),
    (re.compile(r"node\.js", re.IGNORECASE), "nodejs"),
]

# Số thập phân CÔ LẬP: đúng một dấu phân cách, không nằm trong chuỗi dài —
# '4,0'/'8.5' → '4_0'/'8_5' (một token FULLTEXT); '2024.10.01.309', '2.1.3'
# GIỮ NGUYÊN (phản biện: nối cả chuỗi dài gây regression khớp số hiệu văn bản).
_ISOLATED_DECIMAL = re.compile(r"(?<![\d.,])(\d{1,3})[.,](\d{1,3})(?![.,]?\d)")

# Phân tách phần tử trong các cột dẫn xuất: '|gt1|giai tich 1|'.
# Bao hai đầu để LIKE '%|gt1|%' khớp chính xác, không dính 'gt10'.
PIPE = "|"


def vn_norm(text: str | None) -> str:
    """Bỏ dấu, chữ thường, gộp khoảng trắng — v2.

    NFKD → xử ký tự Cf hai lớp → bỏ Mn (dấu tổ hợp) → NFC → đ/Đ → d/D →
    lowercase → term-map → nối số thập phân cô lập → fold trang trí → gộp
    khoảng trắng.

    >>> vn_norm("Giải tích 1")
    'giai tich 1'
    >>> vn_norm("ĐỀ THI  giữa   kỳ")
    'de thi giua ky'
    >>> vn_norm("lập trình C++ và C#")
    'lap trinh cplusplus va csharp'
    >>> vn_norm("điểm 4,0 là đạt")
    'diem 4_0 la dat'
    >>> vn_norm("Số: 2024.10.01.309")
    'so: 2024.10.01.309'
    >>> vn_norm("học\\u200bkỳ")
    'hoc ky'
    >>> vn_norm("Xác suất – Thống kê")
    'xac suat thong ke'
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)

    kept: list[str] = []
    for ch in decomposed:
        if ch in _CF_TO_SPACE:
            kept.append(" ")
            continue
        category = unicodedata.category(ch)
        if category == "Mn" or category == _CF_DROP_CATEGORY:
            continue
        kept.append(ch)

    recomposed = unicodedata.normalize("NFC", "".join(kept))
    lowered = recomposed.translate(_DSTROKE).lower()

    for pattern, replacement in _TERM_MAP:
        lowered = pattern.sub(replacement, lowered)
    lowered = _ISOLATED_DECIMAL.sub(r"\1_\2", lowered)

    return _WHITESPACE.sub(" ", lowered.translate(_DECOR_TO_SPACE)).strip()


def pipe_join(values) -> str:
    """Dựng cột dẫn xuất dạng '|a|b|c|' từ một dãy giá trị.

    Đã chuẩn hóa, bỏ rỗng, bỏ trùng nhưng giữ nguyên thứ tự xuất hiện.
    Dãy rỗng trả về '' (không phải '|') để `LIKE '%|x|%'` không bao giờ khớp.

    >>> pipe_join(["GT1", "Giải tích 1", "GT1"])
    '|gt1|giai tich 1|'
    """
    seen: list[str] = []
    for value in values:
        normalized = vn_norm(value)
        if normalized and normalized not in seen:
            seen.append(normalized)
    if not seen:
        return ""
    return PIPE + PIPE.join(seen) + PIPE


def pipe_split(column: str | None) -> list[str]:
    """Nghịch đảo của `pipe_join`."""
    if not column:
        return []
    return [part for part in column.split(PIPE) if part]


def pipe_like(value: str) -> str:
    """Mẫu LIKE khớp chính xác một phần tử trong cột dẫn xuất.

    >>> pipe_like("GT1")
    '%|gt1|%'
    """
    return f"%{PIPE}{vn_norm(value)}{PIPE}%"


def norm_residue_ratio(norm_text: str) -> float:
    """Tỉ lệ CHỮ CÁI đáng ngờ còn sót sau vn_norm — cổng chặn mojibake.

    Tiếng Việt sạch qua vn_norm phải ra chữ cái ASCII thuần. Còn sót chữ cái
    non-ASCII là dấu hiệu mojibake (Cyrillic từ font TCVN3 hỏng: ӝ/ҩ/ѭ...).
    Whitelist ký hiệu khoa học hợp lệ: Hy Lạp (α, ε, δ trong giáo trình toán)
    và letterlike (µ, Ω, ℓ) — phản biện yêu cầu để không oan tài liệu toán.
    """
    letters = [ch for ch in norm_text if ch.isalpha()]
    if not letters:
        return 0.0
    suspicious = 0
    for ch in letters:
        if ord(ch) < 128:
            continue
        code = ord(ch)
        if 0x0370 <= code <= 0x03FF:  # Greek
            continue
        if ch in "µΩℓ°":
            continue
        suspicious += 1
    return suspicious / len(letters)


def ft_boolean_query(text: str, mode: str = "and") -> str:
    """Chuyển câu hỏi của người dùng thành biểu thức BOOLEAN MODE an toàn.

    Bỏ hết ký tự cú pháp (người dùng không định gõ toán tử), rồi gắn tiền tố
    `+` cho từng từ nếu muốn AND. Trả về '' khi không còn từ nào — nơi gọi phải
    kiểm tra và bỏ qua truy vấn FULLTEXT thay vì gửi chuỗi rỗng.

    Từ ASCII 1 ký tự bị loại khi câu còn từ khác: min_token_size=1 làm chữ cái
    đơn ('c', 'a') khớp cả trăm chunk, kéo nhiễu đè lên từ có nghĩa.

    >>> ft_boolean_query("Đề thi giữa kỳ (2026)")
    '+de +thi +giua +ky +2026'
    >>> ft_boolean_query("giải tích", mode="or")
    'giai tich'
    >>> ft_boolean_query("lập trình C")
    '+lap +trinh'
    """
    cleaned = _FT_SPECIAL.sub(" ", vn_norm(text))
    words = [w for w in cleaned.split() if w]
    if len(words) > 1:
        multi = [w for w in words if not (len(w) == 1 and w.isascii() and w.isalpha())]
        if multi:
            words = multi
    if not words:
        return ""
    if mode == "and":
        return " ".join(f"+{w}" for w in words)
    return " ".join(words)
