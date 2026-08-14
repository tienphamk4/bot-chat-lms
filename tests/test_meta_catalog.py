"""Test 5 tầng gán metadata — trọng tâm là các tầng mới (catalog, nội dung, LLM).

Không cần CSDL: danh mục môn cài qua fixture course_catalog, LLM giả bằng
monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest import meta as metadata
from ingest.extract import Extraction, Page


def _ex(text: str) -> Extraction:
    lines = [l for l in text.splitlines() if l.strip()]
    return Extraction(pages=[Page(number=1, text=text)], page_count=1, front_matter=lines)

FILES_ROOT = Path("C:/data/files")

CATALOG = [
    {
        "id": 3,
        "code": "INT2204",
        "name": "Lập trình hướng đối tượng",
        "phrases": ["int2204", "lap trinh huong doi tuong", "oop", "lthdt"],
    },
    {
        "id": 1,
        "code": "MAT1093",
        "name": "Giải tích 1",
        "phrases": ["mat1093", "giai tich 1", "gt1"],
    },
]


class TestFilenameCatalog:
    def test_alias_trong_ten_file(self, course_catalog):
        # Chính ca của người dùng: oop_lthdt.pdf phải ra INT2204
        course_catalog(CATALOG)
        meta = metadata.resolve(FILES_ROOT / "oop_lthdt.pdf", FILES_ROOT)
        assert meta.course_code == "INT2204"
        assert "filename:catalog" in meta.sources

    def test_ten_mon_day_du_trong_ten_file(self, course_catalog):
        course_catalog(CATALOG)
        meta = metadata.resolve(
            FILES_ROOT / "de-cuong_lap-trinh-huong-doi-tuong.docx", FILES_ROOT
        )
        assert meta.course_code == "INT2204"
        assert meta.doc_type == "de_cuong"
        assert not meta.needs_review

    def test_ma_mon_tuong_minh_van_thang_catalog(self, course_catalog):
        course_catalog(CATALOG)
        meta = metadata.resolve(FILES_ROOT / "MAT1093_oop_bai-tap.pdf", FILES_ROOT)
        assert meta.course_code == "MAT1093"  # mã tường minh ưu tiên hơn alias

    def test_thu_muc_dat_theo_alias(self, course_catalog):
        course_catalog(CATALOG)
        meta = metadata.resolve(
            FILES_ROOT / "OOP" / "giao_trinh" / "chuong1.pdf", FILES_ROOT
        )
        assert meta.course_code == "INT2204"
        assert meta.doc_type == "giao_trinh"
        assert "dir:catalog" in meta.sources


class TestContentEnrich:
    def test_dien_mon_va_loai_tu_trang_bia(self, course_catalog):
        course_catalog(CATALOG)
        meta = metadata.DocMeta(title="x")
        metadata.enrich_from_content(
            meta,
            _ex("TRƯỜNG ĐẠI HỌC ABC\nGIÁO TRÌNH\nLập trình hướng đối tượng\nNXB 2026"),
        )
        assert meta.course_code == "INT2204"
        assert meta.doc_type == "giao_trinh"
        assert "content:catalog" in meta.sources
        assert "content:type" in meta.sources

    def test_khong_ghi_de_truong_da_co(self, course_catalog):
        course_catalog(CATALOG)
        meta = metadata.DocMeta(course_code="MAT1093", doc_type="de_thi")
        metadata.enrich_from_content(meta, _ex("GIÁO TRÌNH Lập trình hướng đối tượng"))
        assert meta.course_code == "MAT1093"
        assert meta.doc_type == "de_thi"

    def test_noi_dung_rong(self):
        meta = metadata.DocMeta()
        metadata.enrich_from_content(meta, _ex("   "))
        assert meta.course_code is None


class TestLlmTier:
    CANDIDATES = [
        {"code": "INT2204", "name": "Lập trình hướng đối tượng", "aliases": '["OOP"]'},
        {"code": "MAT1093", "name": "Giải tích 1", "aliases": "[]"},
    ]

    def _fake_response(self, monkeypatch, data):
        from app.llm import vertex

        result = vertex.LLMResult(
            data=data, tokens_in=50, tokens_out=20, tokens_cached=0, model="fake"
        )
        monkeypatch.setattr(vertex, "generate_json", lambda **kwargs: result)

    def test_nhan_ket_qua_tu_tin_cao(self, monkeypatch):
        self._fake_response(
            monkeypatch,
            {"course_code": "INT2204", "doc_type": "giao_trinh", "confidence": 0.9},
        )
        meta = metadata.classify_with_llm("x.pdf", "nội dung", self.CANDIDATES)
        assert meta is not None
        assert meta.course_code == "INT2204"
        assert meta.doc_type == "giao_trinh"

    def test_loai_khi_tu_tin_thap(self, monkeypatch):
        self._fake_response(
            monkeypatch,
            {"course_code": "INT2204", "doc_type": "giao_trinh", "confidence": 0.4},
        )
        assert metadata.classify_with_llm("x.pdf", "nd", self.CANDIDATES) is None

    def test_loai_ma_mon_bia(self, monkeypatch):
        # LLM bịa mã ngoài danh mục → trường đó bị bỏ, chỉ giữ doc_type
        self._fake_response(
            monkeypatch,
            {"course_code": "XYZ9999", "doc_type": "slide", "confidence": 0.95},
        )
        meta = metadata.classify_with_llm("x.pdf", "nd", self.CANDIDATES)
        assert meta is not None
        assert meta.course_code is None
        assert meta.doc_type == "slide"

    def test_llm_hong_tra_none(self, monkeypatch):
        from app.llm import vertex

        monkeypatch.setattr(vertex, "generate_json", lambda **kwargs: None)
        assert metadata.classify_with_llm("x.pdf", "nd", self.CANDIDATES) is None


class TestTitleEngine:
    def test_van_ban_hanh_chinh(self):
        title = metadata.title_from_content([
            "NGÂN HÀNG NHÀ NƯỚC VIỆT NAM",
            "HỌC VIỆN NGÂN HÀNG",
            "Số: 309/QĐ-HVNH",
            "QUYẾT ĐỊNH",
            "Về việc công nhận các chứng chỉ nghề nghiệp đủ điều kiện chuyển đổi",
            "Căn cứ Quyết định số 2786/QĐ-HVNH",
        ])
        assert title is not None
        assert title.startswith("Quyết Định Về việc công nhận")

    def test_giao_trinh_mo_dun(self):
        title = metadata.title_from_content([
            "ỦY BAN NHÂN DÂN TỈNH TIỀN GIANG",
            "TRƯỜNG CAO ĐẲNG TIỀN GIANG",
            "GIÁO TRÌNH",
            "MÔ ĐUN: LẬP TRÌNH WEB NÂNG CAO",
            "NGHỀ: CÔNG NGHỆ THÔNG TIN",
            "TRÌNH ĐỘ: TRUNG CẤP",
        ])
        assert title == "Giáo Trình Lập Trình Web Nâng Cao"

    def test_dong_hoa_co_dau_dai_nhat_khi_khong_mau(self):
        title = metadata.title_from_content([
            "TRƯỜNG ĐẠI HỌC BÁCH KHOA",
            "NHẬP MÔN TRÍ TUỆ NHÂN TẠO VÀ HỌC MÁY",
            "Nhà xuất bản Giáo dục 2026",
        ])
        assert title == "NHẬP MÔN TRÍ TUỆ NHÂN TẠO VÀ HỌC MÁY"

    def test_khong_co_ung_vien(self):
        assert metadata.title_from_content(["hello world", "plain text"]) is None

    def test_ten_file_bo_rac_upload(self):
        assert metadata._title_from_filename(
            Path("16_lap_trinh_web_nang_cao_0193.pdf")
        ) == "lap trinh web nang cao"
        assert "f3b0208d" not in metadata._title_from_filename(
            Path("f3b0208d_quyet_dinh_chuyen_doi.pdf")
        )
