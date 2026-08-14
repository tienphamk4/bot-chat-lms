"""Hộ chiếu chất lượng văn bản — đo TỪNG TRANG, kết án theo tỉ lệ trang hỏng.

Thay bộ dò boolean một-mẫu-gộp cũ (chỉ nhìn 5 trang đầu, chỉ đo sự VẮNG dấu)
bằng scorecard đa tín hiệu. Bài học xương máu từ kiểm toán: giáo trình OOP 348
chunk lọt vào kho với 89% chunk mojibake TCVN3 ('Mӝt cҩu trúc') vì 5 trang đầu
(bìa + mục lục) sạch — trong khi thân sách nát.

Bốn tín hiệu mỗi trang:
- diacritic_ratio — tỉ lệ chữ mang dấu tiếng Việt (văn bản Việt sạch: 12-30%).
- alien_ratio — tỉ lệ CHỮ CÁI ngoài bảng chữ Việt + whitelist khoa học
  (Cyrillic ӝ/ҩ/ѭ từ font TCVN3 hỏng, PUA...). > ~1% là mojibake gần chắc chắn
  (doc hỏng thật đo được 11-15%, biên rất rộng).
- skeleton_hits — từ-xương tiếng Việt mất dấu ('hoc', 'quyet'...): phân biệt
  "tiếng Việt bị vặt dấu" với "tài liệu tiếng Anh thật".
- Kết án mojibake BẮT BUỘC có chứng cứ kèm (alien + skeleton/vắng dấu) — giữ
  vai trò chống oan cho tài liệu tiếng Anh/toán (phản biện yêu cầu).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

# Bảng chữ tiếng Việt đầy đủ (thường — so sánh sau lower).
_VN_LETTERS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ"
)
_VN_DIACRITICS = set(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"
)
_SKELETONS = {
    "hoc", "dinh", "cua", "truong", "sinh", "vien", "quyet",
    "nganh", "diem", "chuong", "trinh", "nghi", "khoan", "mon",
}


def _is_whitelisted(ch: str) -> bool:
    """Ký hiệu khoa học hợp lệ — không tính là 'chữ lạ'."""
    code = ord(ch)
    if 0x0370 <= code <= 0x03FF:  # Greek (α, ε, δ trong giáo trình toán)
        return True
    return ch in "µΩℓ"


@dataclass
class PageQuality:
    number: int
    letters: int = 0
    diacritic_ratio: float = 0.0
    alien_ratio: float = 0.0
    skeleton_hits: int = 0

    @property
    def mojibake(self) -> bool:
        """Trang mojibake — chữ lạ đáng kể KÈM chứng cứ đây-là-tiếng-Việt.

        Ca phổ biến nhất (TCVN3 hỏng một phần) là rác TRỘN chữ hợp lệ:
        'Giáo trình môn Lұp trình hѭӟng' — dấu thật vẫn còn, từ-xương gần như
        không có. Chứng cứ tiếng-Việt do đó là BẤT KỲ: có dấu thật (EN không
        thể có), có từ-xương (vặt dấu toàn phần), hoặc chữ lạ áp đảo (≥10%).
        """
        if self.letters < 200:
            return False  # quá ngắn để kết luận
        if self.alien_ratio < 0.02:
            return False
        return (
            self.diacritic_ratio >= 0.02
            or self.skeleton_hits >= 2
            or self.alien_ratio >= 0.10
        )

    @property
    def stripped_vietnamese(self) -> bool:
        """Trang tiếng Việt bị VẶT SẠCH dấu (khác mojibake: không chữ lạ)."""
        return (
            self.letters >= 400
            and self.alien_ratio < 0.01
            and self.diacritic_ratio < 0.03
            and self.skeleton_hits >= 3
        )


@dataclass
class DocQuality:
    pages: list[PageQuality] = field(default_factory=list)

    @property
    def bad_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.mojibake or p.stripped_vietnamese]

    @property
    def assessable_pages(self) -> int:
        return sum(1 for p in self.pages if p.letters >= 200)

    @property
    def verdict_bad(self) -> bool:
        """Tài liệu hỏng khi > 10% số trang đánh-giá-được là trang hỏng."""
        assessable = self.assessable_pages
        if assessable == 0:
            return False
        return len(self.bad_pages) / assessable > 0.10

    def describe(self) -> str:
        bad = self.bad_pages
        preview = ", ".join(str(n) for n in bad[:12])
        more = f"… (+{len(bad) - 12})" if len(bad) > 12 else ""
        return f"{len(bad)}/{self.assessable_pages} trang hỏng chữ (trang {preview}{more})"


def assess_page(number: int, text: str) -> PageQuality:
    # NFKC trước để ligature/width không bị đếm oan thành chữ lạ.
    lowered = unicodedata.normalize("NFKC", text).lower()
    letters = [ch for ch in lowered if ch.isalpha()]
    quality = PageQuality(number=number, letters=len(letters))
    if not letters:
        return quality

    alien = sum(
        1 for ch in letters if ch not in _VN_LETTERS and not _is_whitelisted(ch)
    )
    diacritic = sum(1 for ch in letters if ch in _VN_DIACRITICS)
    quality.alien_ratio = alien / len(letters)
    quality.diacritic_ratio = diacritic / len(letters)

    words = set()
    current: list[str] = []
    for ch in lowered:
        if "a" <= ch <= "z":
            current.append(ch)
        elif current:
            words.add("".join(current))
            current = []
    if current:
        words.add("".join(current))
    quality.skeleton_hits = len(_SKELETONS & words)
    return quality


def assess(pages) -> DocQuality:
    """Đo mọi trang (đếm ký tự thuần Python — 165 trang không đáng kể)."""
    return DocQuality(pages=[assess_page(p.number, p.text) for p in pages])
