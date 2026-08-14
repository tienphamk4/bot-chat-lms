"""Test hộ chiếu chất lượng văn bản + cổng residue — khóa hành vi đại tu 14/08."""

from __future__ import annotations

from app.vn import norm_residue_ratio, vn_norm
from ingest.quality import DocQuality, assess_page

# Mẫu thật từ giáo trình OOP hỏng font TCVN3 (doc 15) — rác TRỘN chữ hợp lệ.
MOJIBAKE_MIXED = (
    "Giáo trình môn Lұp trình hѭӟng đӕi tѭӧng Biên soҥn: Lê Thӏ Mỹ Hҥnh "
    "NӃu constructor lӟp Circle không bao gӗm constructor lӟp Point thì "
    "chѭѫng trình sӁ gӑi constructor mһc đӏnh cӫa lӟp Point khi tҥo đӕi tѭӧng "
) * 4

# Tiếng Việt bị vặt sạch dấu (lớp text OCR ẩu) — không chữ lạ.
STRIPPED_VN = (
    "sinh vien dang ky hoc phan theo quy dinh cua truong dai hoc "
    "diem hoc phan duoc tinh theo trong so quy dinh trong de cuong chi tiet "
    "quyet dinh nay co hieu luc ke tu ngay ky "
) * 5

CLEAN_VN = (
    "Sinh viên đăng ký học phần theo quy định của trường đại học. "
    "Điểm học phần được tính theo trọng số quy định trong đề cương chi tiết. "
) * 5

CLEAN_EN = (
    "Object oriented programming is a paradigm based on classes and objects. "
    "Inheritance allows a class to reuse behaviour from a parent class. "
) * 5

MATH_GREEK = (
    "Với mọi ε dương tồn tại δ dương sao cho |f(x) − L| < ε khi |x − a| < δ. "
    "Hàm số liên tục tại điểm α khi giới hạn tại α bằng giá trị hàm. "
) * 5


class TestAssessPage:
    def test_mojibake_tron_bi_bat(self):
        q = assess_page(1, MOJIBAKE_MIXED)
        assert q.alien_ratio > 0.02
        assert q.mojibake

    def test_viet_vat_dau_bi_bat(self):
        q = assess_page(1, STRIPPED_VN)
        assert q.stripped_vietnamese
        assert not q.mojibake

    def test_viet_sach_qua(self):
        q = assess_page(1, CLEAN_VN)
        assert not q.mojibake and not q.stripped_vietnamese

    def test_tieng_anh_khong_oan(self):
        q = assess_page(1, CLEAN_EN)
        assert not q.mojibake and not q.stripped_vietnamese

    def test_toan_hy_lap_khong_oan(self):
        q = assess_page(1, MATH_GREEK)
        assert q.alien_ratio < 0.02  # Greek nằm trong whitelist
        assert not q.mojibake

    def test_trang_ngan_khong_ket_luan(self):
        q = assess_page(1, "Mӝt vài chӳ ngắn")
        assert not q.mojibake


class TestDocVerdict:
    def _doc(self, pages):
        return DocQuality(pages=[assess_page(i, t) for i, t in enumerate(pages, 1)])

    def test_bia_sach_than_nat_van_bi_ket_an(self):
        # Kịch bản doc 15: 2 trang đầu sạch, thân mojibake
        doc = self._doc([CLEAN_VN, CLEAN_VN] + [MOJIBAKE_MIXED] * 8)
        assert doc.verdict_bad
        assert len(doc.bad_pages) == 8

    def test_mot_trang_hong_trong_sach_lon_khong_ket_an(self):
        doc = self._doc([CLEAN_VN] * 19 + [MOJIBAKE_MIXED])
        assert not doc.verdict_bad  # 5% < ngưỡng 10%

    def test_describe_liet_ke_trang(self):
        doc = self._doc([CLEAN_VN, MOJIBAKE_MIXED, MOJIBAKE_MIXED])
        assert "trang 2, 3" in doc.describe()


class TestNormResidue:
    def test_viet_sach_khong_residue(self):
        assert norm_residue_ratio(vn_norm(CLEAN_VN)) == 0.0

    def test_mojibake_con_residue(self):
        assert norm_residue_ratio(vn_norm(MOJIBAKE_MIXED)) > 0.01

    def test_hy_lap_duoc_whitelist(self):
        assert norm_residue_ratio(vn_norm(MATH_GREEK)) == 0.0
