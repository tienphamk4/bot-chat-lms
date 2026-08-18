"""Bối cảnh chunk + nhận diện bảng vỡ định dạng (ingest/context.py).

Các ca ở đây lấy nguyên văn từ kho tuyển sinh thật — chỗ đã làm hệ thống trả
lời sai ngành và suýt bịa điểm chuẩn.
"""

from __future__ import annotations

from ingest import context as C


class TestBangVoDinhDang:
    """Bảng bị bẹp thành dòng chữ thì không còn suy ra được số của ai."""

    def test_bang_diem_chuan_that_bi_bat(self):
        # Nguyên văn chunk 1966, trang 10: "2 0 2 5" vốn là năm 2025, "26,38"
        # là điểm chuẩn — nhưng đã mất hàng/cột nên không biết của ngành nào.
        raw = (
            "Mã ngành: 7480104 Chỉ tiêu tuyển sinh: 50 "
            "2 0 2 5 26,38 2 0 2 4 * 35,94 Điểm chuẩn THPTQG"
        )
        assert C.is_fragmented(raw)

    def test_bang_tong_hop_chi_tieu_bi_bat(self):
        raw = "Tổng chỉ tiêu: 1900 MÃ XÉT TUYỂN CHỈ TIÊU 55 25,25 26,50 26,42 55 55 55 160 160 105"
        assert C.is_fragmented(raw)

    def test_danh_sach_mon_hoc_danh_so_khong_bi_bat(self):
        # Liệt kê có đánh số là văn bản BÌNH THƯỜNG — bắt nhầm thì mất sạch
        # phần đáng giá nhất của kho.
        raw = (
            "Các học phần chính: 1. Cơ sở lập trình 2. Cơ sở dữ liệu "
            "3. Kiến trúc máy tính 4. Quản trị mạng 5. Phân tích thiết kế hệ thống"
        )
        assert not C.is_fragmented(raw)

    def test_van_ban_thuong_khong_bi_bat(self):
        raw = "Ngành An toàn thông tin đào tạo các chuyên gia có trình độ cao về bảo mật."
        assert not C.is_fragmented(raw)

    def test_nam_hoc_va_tin_chi_roi_rac_khong_bi_bat(self):
        raw = "Tổng số tín chỉ: 130 áp dụng từ năm 2025 đến năm 2029 cho khóa 67"
        assert not C.is_fragmented(raw)


class TestLocHeadingRac:
    """heading_path chỉ đáng đưa vào bối cảnh khi nó thật là tiêu đề."""

    def test_giu_tieu_de_that(self):
        assert C.clean_heading("HỌC PHẦN CHUNG") == "HỌC PHẦN CHUNG"
        assert C.clean_heading("CÔNG NGHỆ THÔNG TIN (ĐHCQ)") == "CÔNG NGHỆ THÔNG TIN (ĐHCQ)"

    def test_bo_cau_van_bi_cat_giua_chung(self):
        # Bộ nhận diện heading gặp tài liệu dàn trang đồ họa hay bắt nhầm kiểu này.
        assert C.clean_heading(
            "Chuyên gia phát triển phần mềm: Quản lý, phân tích thiết kế và phát"
        ) is None

    def test_bo_cau_qua_dai(self):
        assert C.clean_heading(
            "Chương trình đào tạo trình độ đại học ngành Hệ thống thông tin quản lý "
            "hướng tới mục tiêu đào tạo cử nhân có năng lực"
        ) is None

    def test_rong_thi_bo(self):
        assert C.clean_heading(None) is None
        assert C.clean_heading("   ") is None


class TestBoiCanhCauTruc:
    def test_ghep_du_ba_manh(self):
        out = C.structural_context("Giáo trình Giải tích 1", "Giải tích 1", "Chương 3")
        # Trùng lặp bị gộp: tên môn đã nằm trong tiêu đề thì không nhắc hai lần.
        assert "Giáo trình Giải tích 1" in out and "Chương 3" in out

    def test_khong_lap_lai_manh_trung(self):
        out = C.structural_context("Giải tích 1", "Giải tích 1", None)
        assert out == "Giải tích 1"

    def test_bo_heading_rac(self):
        out = C.structural_context("GT Ngành và CTĐT", None, "Chuyên gia an ninh mạng: Bảo vệ và")
        assert out == "GT Ngành và CTĐT"


class TestVanBanDemDiTruyHoi:
    def test_boi_canh_dung_truoc_noi_dung(self):
        out = C.indexed_text("Ngành An toàn thông tin", "HỌC PHẦN CHUNG\nLập trình Python")
        assert out.startswith("Ngành An toàn thông tin")
        assert "Lập trình Python" in out

    def test_khong_co_boi_canh_thi_giu_nguyen(self):
        assert C.indexed_text(None, "nội dung") == "nội dung"
        assert C.indexed_text("   ", "nội dung") == "nội dung"


class TestBuildKhongGoiModel:
    def test_tat_model_van_ra_boi_canh_cau_truc(self):
        chunks = [
            {"content": "Lập trình Python", "heading_path": "HỌC PHẦN CHUNG", "page_from": 6},
            {"content": "Kỹ nghệ phần mềm", "heading_path": None, "page_from": 8},
        ]
        out = C.build(chunks, doc_title="GT Ngành và CTĐT", use_llm=False)
        assert out == ["GT Ngành và CTĐT — HỌC PHẦN CHUNG", "GT Ngành và CTĐT"]

    def test_dung_do_dai_voi_dau_vao(self):
        assert C.build([], doc_title="x", use_llm=True) == []


class TestBuildCoModel:
    def test_boi_canh_model_dung_truoc_phan_cau_truc(self, monkeypatch):
        # Phần model cụ thể hơn nên phải sống sót khi bối cảnh bị cắt bớt.
        monkeypatch.setattr(
            C, "llm_page_contexts",
            lambda title, page, chunks, **kw: ["Ngành An toàn thông tin"] * len(chunks),
        )
        chunks = [{"content": "Lập trình Python", "heading_path": "HỌC PHẦN CHUNG", "page_from": 6}]
        out = C.build(chunks, doc_title="GT Ngành", use_llm=True)
        assert out[0].startswith("Ngành An toàn thông tin")
        assert "HỌC PHẦN CHUNG" in out[0]

    def test_model_tra_rong_thi_giu_tang_cau_truc(self, monkeypatch):
        monkeypatch.setattr(
            C, "llm_page_contexts", lambda title, page, chunks, **kw: [""] * len(chunks)
        )
        chunks = [{"content": "x", "heading_path": "HỌC PHẦN CHUNG", "page_from": 6}]
        assert C.build(chunks, doc_title="GT", use_llm=True) == ["GT — HỌC PHẦN CHUNG"]

    def test_model_hong_thi_khong_lam_do_viec_nap(self, monkeypatch):
        from app.llm import vertex

        monkeypatch.setattr(vertex, "generate_json", lambda *a, **k: None)
        monkeypatch.setattr(C.time, "sleep", lambda *_: None)
        out = C.llm_page_contexts("GT", 6, [{"content": "x"}], attempts=2)
        assert out == [""]

    def test_gom_theo_trang_chu_khong_goi_tung_chunk(self, monkeypatch):
        # Model cần nhìn phần văn bản NGOÀI chunk mới biết chunk thuộc ngành
        # nào — nên phải gọi theo trang, và gọi theo trang cũng rẻ hơn.
        calls: list = []

        def spy(title, page, chunks, **kw):
            calls.append(page)
            return [""] * len(chunks)

        monkeypatch.setattr(C, "llm_page_contexts", spy)
        chunks = [
            {"content": "a", "heading_path": None, "page_from": 6},
            {"content": "b", "heading_path": None, "page_from": 6},
            {"content": "c", "heading_path": None, "page_from": 7},
        ]
        C.build(chunks, doc_title="GT", use_llm=True)
        assert calls == [6, 7]


class TestXepHangLaiTheoBoiCanh:
    """Bản thay thế miễn phí cho cross-encoder khi RERANK_ENABLED=0."""

    def test_chunk_dung_chu_de_len_truoc(self):
        from app.core.rerank import by_context

        # Hai chunk gần như trùng nhau về nội dung (phần đại cương giống hệt
        # giữa các ngành); chỉ dòng bối cảnh phân biệt được.
        chunks = [
            {"chunk_id": 1, "context_text": "Ngành Công nghệ thông tin"},
            {"chunk_id": 2, "context_text": "Chương trình đào tạo ngành An toàn thông tin"},
        ]
        got = by_context("ngành An toàn thông tin học những môn gì", chunks)
        assert [c["chunk_id"] for c in got] == [2, 1]

    def test_hoa_diem_thi_giu_nguyen_thu_hang_rrf(self):
        from app.core.rerank import by_context

        chunks = [
            {"chunk_id": 1, "context_text": "Ngành Kế toán"},
            {"chunk_id": 2, "context_text": "Ngành Kế toán"},
        ]
        got = by_context("ngành kế toán", chunks)
        assert [c["chunk_id"] for c in got] == [1, 2]

    def test_khong_co_boi_canh_thi_khong_xao_tron(self):
        from app.core.rerank import by_context

        chunks = [{"chunk_id": 1}, {"chunk_id": 2}, {"chunk_id": 3}]
        got = by_context("câu hỏi bất kỳ về ngành nào đó", chunks)
        assert [c["chunk_id"] for c in got] == [1, 2, 3]


class TestLopChunkKeKhongDuocTroiChuDe:
    """Bậc thử lại của tầng sinh không được đổi sang đối tượng khác.

    Ca thật: "ngành Khoa học máy tính ra trường làm gì" — lớp đầu đúng ngành
    nhưng model bảo không thấy, lớp kế kéo lên chunk Công nghệ thông tin và
    model trả lời trôi chảy về NGÀNH KHÁC. Người hỏi không có cách nào biết.
    """

    def test_chi_lay_chunk_bam_chu_de_ngang_lop_dau(self):
        import inspect

        from app.core import rag

        src = inspect.getsource(rag._generate_pass)
        assert 'c.get("context_score", 0.0) >= best_score' in src, (
            "lớp chunk kế phải lọc theo context_hits, nếu không câu trả lời "
            "trôi sang đối tượng khác"
        )


class TestCumPhaiDacTrungMoiDuocThuong:
    """Cụm dài chưa chắc đặc trưng.

    Ca thật: "ngành Bảo hiểm đào tạo ra sao" cho ra hai cụm — "chương trình
    đào tạo" (4 từ, có mặt khắp kho) và "ngành Bảo hiểm" (3 từ, đúng hai
    chunk). Thưởng theo SỐ TỪ thì cụm chung chung thắng, và câu trả lời thành
    chương trình của sáu ngành khác.
    """

    def _chunks(self, contexts):
        return [{"chunk_id": i, "context_text": c} for i, c in enumerate(contexts)]

    def test_cum_pho_bien_khong_duoc_thuong(self):
        from app.core.rerank import by_context

        # "chương trình đào tạo" có ở 4/5 ứng viên -> không phân biệt được gì.
        chunks = self._chunks([
            "Chương trình đào tạo ngành Tài chính",
            "Chương trình đào tạo ngành Kế toán",
            "Chương trình đào tạo ngành Marketing",
            "Chương trình đào tạo ngành Luật",
            "Ngành Bảo hiểm — chứng chỉ nghề nghiệp",
        ])
        got = by_context(
            "ngành Bảo hiểm đào tạo ra sao", chunks,
            phrases=["chương trình đào tạo", "ngành Bảo hiểm"],
        )
        assert got[0]["chunk_id"] == 4

    def test_cum_hiem_van_duoc_thuong(self):
        from app.core.rerank import by_context

        chunks = self._chunks([
            "Chương trình Kinh tế học tài chính",
            "Chương trình Kinh tế đầu tư",
            "Chương trình Kinh tế quốc tế",
            "Chương trình Kinh tế phát triển",
            "Ngành Kinh tế số — Tổng số tín chỉ 130",
        ])
        got = by_context(
            "chương trình Kinh tế số học gì", chunks,
            phrases=["Kinh tế số", "chương trình"],
        )
        assert got[0]["chunk_id"] == 4


class TestTronKetQuaNhieuCum:
    def test_xen_ke_theo_thu_hang(self):
        from app.core.retrieval import _interleave

        a = [{"chunk_id": 1}, {"chunk_id": 2}]
        b = [{"chunk_id": 3}, {"chunk_id": 4}]
        assert [c["chunk_id"] for c in _interleave([a, b], 4)] == [1, 3, 2, 4]

    def test_bo_trung_va_ton_trong_limit(self):
        from app.core.retrieval import _interleave

        a = [{"chunk_id": 1}, {"chunk_id": 2}]
        b = [{"chunk_id": 1}, {"chunk_id": 3}]
        assert [c["chunk_id"] for c in _interleave([a, b], 3)] == [1, 2, 3]

    def test_danh_sach_rong(self):
        from app.core.retrieval import _interleave

        assert _interleave([], 5) == []
        assert _interleave([[], []], 5) == []
