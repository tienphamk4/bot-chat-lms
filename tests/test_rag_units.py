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


class TestNotFoundEmptyScope:
    """Kho trống thì phải nói thẳng, không xúi đổi từ khóa (sự cố 14/08)."""

    def _route(self):
        from app.schemas import RouterResult, Intent

        return RouterResult(
            intent=Intent.ask_content, standalone_question="điều kiện dự thi",
            keywords=["quy chế"],
        )

    def test_kho_quy_che_trong(self, monkeypatch):
        import app.db
        from app.core import render

        render._indexed_cache.clear()
        monkeypatch.setattr(app.db, "fetch_value", lambda *a, **k: 0)
        answer = render.render_not_found(self._route(), None, "quy_che")
        assert "chưa có văn bản quy chế" in answer
        assert "từ khóa" not in answer

    def test_kho_con_tai_lieu_giu_goi_y(self, monkeypatch):
        import app.db
        from app.core import render

        render._indexed_cache.clear()
        monkeypatch.setattr(app.db, "fetch_value", lambda *a, **k: 5)
        answer = render.render_not_found(self._route(), None, "quy_che")
        assert "từ khóa" in answer

    def test_db_hong_roi_ve_thong_diep_cu(self, monkeypatch):
        import app.db
        from app.core import render

        def boom(*a, **k):
            raise RuntimeError("db down")

        render._indexed_cache.clear()
        monkeypatch.setattr(app.db, "fetch_value", boom)
        answer = render.render_not_found(self._route(), None, "quy_che")
        assert "từ khóa" in answer  # không chắc kho trống thì không đổ oan
