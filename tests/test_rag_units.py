"""Unit test giai đoạn 2 — RRF, hậu kiểm trích dẫn, tiện ích embedding, prompt RAG.

Không chạm CSDL, không gọi mạng.
"""

from __future__ import annotations

import math

from app.core.citations import process
from app.core.fusion import rrf_merge
from app.llm.embeddings import normalize, to_sql_vector
from app.llm.prompts import NOT_FOUND_SENTENCE, RAG_SYSTEM, rag_prompt


def _chunk(chunk_id: int, doc_id: int = 1, title: str = "Giáo trình demo"):
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "title": title,
        "type": "giao_trinh",
        "semester": "20261",
        "page_from": 10 + chunk_id,
        "heading_path": f"Chương {chunk_id}",
        "content": f"Nội dung chunk {chunk_id}.",
    }


# --- RRF ---------------------------------------------------------------------


class TestRrfMerge:
    def test_phan_tu_o_ca_hai_kenh_len_dau(self):
        fulltext = [_chunk(1), _chunk(2), _chunk(3)]
        vectors = [_chunk(4), _chunk(2), _chunk(5)]
        merged = rrf_merge([fulltext, vectors])
        assert merged[0]["chunk_id"] == 2  # xuất hiện ở cả hai kênh
        assert {m["chunk_id"] for m in merged} == {1, 2, 3, 4, 5}

    def test_diem_rrf_dung_cong_thuc(self):
        merged = rrf_merge([[_chunk(1)], [_chunk(1)]], k=60)
        assert math.isclose(merged[0]["rrf_score"], 2 / 61)

    def test_mot_kenh_van_chay(self):
        merged = rrf_merge([[_chunk(1), _chunk(2)]])
        assert [m["chunk_id"] for m in merged] == [1, 2]

    def test_rong(self):
        assert rrf_merge([]) == []
        assert rrf_merge([[]]) == []


# --- Hậu kiểm trích dẫn ------------------------------------------------------


class TestCitations:
    CHUNKS = [_chunk(1), _chunk(2)]

    def test_giu_cau_hop_le_va_dung_citation(self):
        answer = "Điểm đạt là từ 4,0 trở lên [1]. Thi lại được một lần [2]."
        cleaned, cites = process(answer, self.CHUNKS)
        assert "4,0" in cleaned and "một lần" in cleaned
        assert [c.n for c in cites] == [1, 2]
        assert cites[0].page == 11
        assert cites[0].url == "/api/v1/documents/1/download"

    def test_loai_cau_co_nguon_bia(self):
        answer = "Điểm đạt là từ 4,0 [1]. Học phí là 500 nghìn [7]."
        cleaned, cites = process(answer, self.CHUNKS)
        assert "4,0" in cleaned
        assert "Học phí" not in cleaned  # [7] không tồn tại → bỏ cả câu
        assert [c.n for c in cites] == [1]

    def test_toan_bo_bia_thi_rong(self):
        cleaned, cites = process("Thông tin X [9]. Thông tin Y [8].", self.CHUNKS)
        assert cleaned == ""
        assert cites == []

    def test_cau_khong_co_marker_van_giu(self):
        cleaned, cites = process("Quy chế nêu rõ điều kiện [2]. Nói cách khác là vậy.", self.CHUNKS)
        assert "Nói cách khác" in cleaned
        assert [c.n for c in cites] == [2]

    def test_citation_khong_trung_lap(self):
        cleaned, cites = process("A [1]. B [1]. C [1].", self.CHUNKS)
        assert [c.n for c in cites] == [1]

    def test_ngoac_gop_nhieu_nguon(self):
        # Model thỉnh thoảng viết [1, 2] dù được dặn tách — vẫn phải hiểu đúng
        cleaned, cites = process("Cả hai chương đều nói về điều này [1, 2].", self.CHUNKS)
        assert "điều này" in cleaned
        assert [c.n for c in cites] == [1, 2]

    def test_ngoac_gop_co_nguon_bia_thi_loai_ca_cau(self):
        cleaned, cites = process("Ý này lấy từ [1, 9].", self.CHUNKS)
        assert cleaned == ""
        assert cites == []


# --- Embedding utils ---------------------------------------------------------


class TestEmbeddingUtils:
    def test_normalize_ve_do_dai_1(self):
        vector = normalize([3.0, 4.0])
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0)

    def test_normalize_vector_khong(self):
        assert normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_to_sql_vector_dinh_dang(self):
        text = to_sql_vector([0.5, -1.0])
        assert text == "[0.500000,-1.000000]"
        assert text.startswith("[") and text.endswith("]")


# --- Prompt RAG --------------------------------------------------------------


class TestRagPrompt:
    def test_boc_chunk_trong_delimiter(self):
        prompt = rag_prompt("Thi lại thế nào?", [_chunk(1)])
        assert "<<<TRICH_DOAN 1>>>" in prompt
        assert "<<<HET 1>>>" in prompt
        assert "Nội dung chunk 1." in prompt
        assert "Chương 1" in prompt and "tr. 11" in prompt

    def test_khong_chua_lich_su_chi_cau_hoi(self):
        prompt = rag_prompt("Câu hỏi độc lập?", [_chunk(1)])
        assert prompt.startswith("Câu hỏi: Câu hỏi độc lập?")

    def test_system_prompt_co_luat_bat_buoc(self):
        assert NOT_FOUND_SENTENCE in RAG_SYSTEM
        assert "không phải chỉ thị" in RAG_SYSTEM  # chống prompt injection
        assert "không thêm kiến thức ngoài" in RAG_SYSTEM
        assert "gạch đầu dòng" in RAG_SYSTEM


class TestNotFoundNguoiDung:
    """Câu không-tìm-thấy chỉ được nói chuyện của NGƯỜI DÙNG.

    Bản cũ chìa ra chuyện vận hành ("kho chưa có tài liệu nào được nạp", "báo
    giúp quản trị viên bổ sung") và liệt kê tên môn lấy từ toàn kho — với bot
    tuyển sinh thì thành ra mời đọc "Kiểm thử phần mềm".
    """

    def _route(self, question="điều kiện dự thi"):
        from app.schemas import Intent, RouterResult

        return RouterResult(
            intent=Intent.ask_content, standalone_question=question,
            keywords=["quy chế"],
        )

    def test_nhac_lai_chu_de_va_pham_vi_cua_bot(self):
        from app.core import render
        from app.core.personas import Persona

        bot = Persona(
            slug="tuyen-sinh",
            corpus_label="tài liệu tuyển sinh hiện có",
            offer="Anh/chị cần em tư vấn ngành học nào ạ?",
        )
        answer = render.render_not_found(
            self._route("Trường có những ngành nào"), None, None, None, bot
        )
        assert "Trường có những ngành nào" in answer
        assert "tài liệu tuyển sinh hiện có" in answer
        assert "Anh/chị cần em tư vấn ngành học nào ạ?" in answer

    def test_khong_noi_chuyen_quan_tri(self):
        from app.core import render
        from app.core.personas import Persona

        answer = render.render_not_found(
            self._route(), None, "quy_che", None,
            Persona(slug="x", corpus_label="tài liệu hiện có", offer="Anh/chị hỏi tiếp nhé."),
        )
        for cam in ("quản trị viên", "được nạp", "kho hiện chưa", "Kho tài liệu"):
            assert cam.lower() not in answer.lower(), f"lộ chuyện vận hành: {cam}"

    def test_khong_goi_y_ten_mon_cua_bot_khac(self):
        """Không liệt kê gì hết — nên không thể chìa nhầm môn hay lộ tài liệu
        ngoài quyền."""
        from app.core import render
        from app.core.personas import Persona

        answer = render.render_not_found(
            self._route(), None, None, None,
            Persona(slug="tuyen-sinh", corpus_label="tài liệu tuyển sinh", offer=""),
        )
        assert "Kiểm thử" not in answer
        assert "em đang có nội dung về" not in answer

    def test_dau_moi_lien_he_theo_tung_bot(self):
        from app.core import render
        from app.core.personas import Persona

        bot = Persona(
            slug="tuyen-sinh", corpus_label="tài liệu tuyển sinh", offer="",
            contact={"hotline": "1900 561 595", "website": "hvnh.edu.vn"},
        )
        answer = render.render_not_found(self._route(), None, None, None, bot)
        assert "1900 561 595" in answer and "hvnh.edu.vn" in answer

    def test_khong_khai_lien_he_thi_khong_nhac(self):
        from app.core import render
        from app.core.personas import Persona

        answer = render.render_not_found(
            self._route(), None, None, None, Persona(slug="x", offer="")
        )
        assert "liên hệ" not in answer


class TestPhamViTheoBot:
    """Ranh giới giữa các bot là ranh giới CỨNG, không phải scope mềm.

    Lỗi đã gặp khi dựng: `hybrid_search` gọi hai kênh bằng tham số THEO VỊ TRÍ,
    nên tham số `assistant_slug` thêm sau không bao giờ tới nơi — bot tuyển
    sinh vẫn chìa ra giáo trình kiểm thử phần mềm, mà không có lỗi nào báo.
    """

    def test_hai_kenh_hybrid_nhan_dung_slug(self):
        import inspect

        from app.core import retrieval

        src = inspect.getsource(retrieval.hybrid_search)
        # Phải truyền theo TÊN. Truyền theo vị trí là im lặng rơi mất ranh giới.
        assert src.count("assistant_slug=assistant_slug") >= 3, (
            "hybrid_search phải chuyền assistant_slug cho cả chunk_search và "
            "vector_search ở cả hai nhánh (song song và tuần tự)"
        )

    def test_moi_ham_truy_hoi_deu_nhan_assistant_slug(self):
        import inspect

        from app.core import retrieval

        for name in ("metadata_search", "title_search", "chunk_search",
                     "vector_search", "hybrid_search"):
            params = inspect.signature(getattr(retrieval, name)).parameters
            assert "assistant_slug" in params, f"{name} thiếu assistant_slug"

    def test_khong_khai_gi_thi_khong_siet(self, monkeypatch):
        from app.core import retrieval

        retrieval.clear_assistant_scope_cache()
        monkeypatch.setattr(retrieval, "_assistant_scope", lambda slug: (False, ()))
        params = {}
        assert retrieval._assistant_condition("bot-nao-do", params) == ""
        assert not params

    def test_da_gan_tai_lieu_thi_siet_theo_tai_lieu(self, monkeypatch):
        from app.core import retrieval

        retrieval.clear_assistant_scope_cache()
        monkeypatch.setattr(retrieval, "_assistant_scope", lambda slug: (True, ()))
        params = {}
        sql = retrieval._assistant_condition("tuyen-sinh", params)
        assert "assistant_documents" in sql and "EXISTS" in sql
        assert params["assistant_slug"] == "tuyen-sinh"

    def test_khai_nhieu_muc_quyen_thi_loc_theo_ca_danh_sach(self, monkeypatch):
        """Bot phục vụ nhiều mức quyền — mệnh đề phải là IN, không phải một giá trị."""
        from app.core import retrieval

        retrieval.clear_assistant_scope_cache()
        monkeypatch.setattr(
            retrieval, "_assistant_scope", lambda slug: (False, ("public", "faculty"))
        )
        params = {}
        sql = retrieval._assistant_condition("tai-lieu", params)
        assert "d.access IN (" in sql
        assert sorted(v for k, v in params.items() if k.startswith("bot_access")) == [
            "faculty", "public",
        ]

    def test_gan_tai_lieu_va_muc_quyen_thi_cong_don(self, monkeypatch):
        from app.core import retrieval

        retrieval.clear_assistant_scope_cache()
        monkeypatch.setattr(
            retrieval, "_assistant_scope", lambda slug: (True, ("public",))
        )
        sql = retrieval._assistant_condition("x", {})
        assert "assistant_documents" in sql and "d.access IN (" in sql


class TestChanChunkBangVoDinhDang:
    """Chunk là bảng số liệu đã vỡ định dạng phải bị loại khỏi CẢ HAI kênh.

    Lọt một kênh cũng đủ hỏng: RRF trộn hai kênh, chunk lọt vẫn vào prompt, và
    model sẽ đọc "26,38" cạnh "Mã ngành: 7480104" rồi gán điểm chuẩn cho ngành
    mà nó tưởng — một con số sai trông rất đáng tin.
    """

    def test_ca_hai_kenh_deu_loc_is_fragment(self):
        import inspect

        from app.core import retrieval

        for name in ("chunk_search", "vector_search"):
            src = inspect.getsource(getattr(retrieval, name))
            assert "ch.is_fragment = 0" in src, f"{name} thiếu lọc is_fragment"

    def test_boi_canh_di_theo_chunk_ve_tang_tren(self):
        import inspect

        from app.core import retrieval

        # context_text phải nằm trong SELECT, nếu không prompt mất dòng bối
        # cảnh và model lại không biết trích đoạn thuộc ngành nào.
        for name in ("chunk_search", "vector_search"):
            src = inspect.getsource(getattr(retrieval, name))
            assert "ch.context_text" in src, f"{name} không trả context_text"


class TestBacNoiDanTruyVanTuKhoa:
    def test_du_bon_bac_theo_dung_thu_tu(self):
        import inspect

        from app.core import retrieval

        src = inspect.getsource(retrieval.chunk_search)
        assert "ft_phrase_queries(keywords or ())" in src, (
            "bậc CỤM phải đứng đầu — đây là bậc duy nhất chống được việc "
            "BOOLEAN MODE xếp hạng không có IDF"
        )
        assert '("and", "and_content", "or_content", "or")' in src, (
            "chunk_search phải nới dần bốn bậc; nhảy thẳng AND -> OR làm câu "
            "hỏi tự nhiên rơi vào bậc nhiễu nhất"
        )
