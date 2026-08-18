"""Câu hỏi NỘI DUNG không bao giờ được kết thúc bằng một danh sách file.

Ba ca thật do người dùng báo, cùng một cơ chế hỏng: tầng luật nhận đúng là câu
hỏi nội dung, nhưng vì câu không nêu môn cũng không nêu loại tài liệu nên nó
không "chắc", và router LLM lật ngược thành lookup_doc rồi tự đoán thêm loại
tài liệu. Sau đó nhánh khớp tiêu đề trúng một chữ trong tên file và dừng lại,
trả về đúng một dòng danh sách.

Hai lớp chặn được ghim ở đây, cả hai đều theo CƠ CHẾ chứ không theo câu chữ —
lối nói mới sẽ còn xuất hiện, nên bổ sung từ khóa không phải là cách chặn.
"""

from __future__ import annotations

import pytest

from app.core import router, router_rules
from app.llm.vertex import LLMResult
from app.schemas import Intent


CAU_HOI_NOI_DUNG = [
    "lộ trình ngành An toàn thông tin của trường là gì",
    "vậy còn ngành kỹ thuật phần mềm thì sao",
    "ngành khoa học máy tính học những môn gì",
    "chương trình học ngành marketing gồm những gì",
    "em muốn biết về ngành kinh tế số",
]

CAU_TRA_CUU = [
    "cho xin slide kiểm thử phần mềm",
    "INT3115 có tài liệu gì",
    "danh sách tài liệu môn XSTK",
    "cho xin quy chế đào tạo",
]


class TestNhanDienCauHoiNoiDung:
    @pytest.mark.parametrize("cau", CAU_HOI_NOI_DUNG)
    def test_cau_hoi_noi_dung_duoc_nhan(self, cau):
        assert router_rules.looks_like_content_question(cau)

    @pytest.mark.parametrize("cau", CAU_TRA_CUU)
    def test_cau_tra_cuu_khong_bi_nhan_nham(self, cau):
        # Nhận nhầm ở đây thì hỏng chiều ngược lại: câu đi tìm file bị đẩy vào
        # RAG, tốn token mà trả về văn xuôi thay vì danh sách tài liệu.
        assert not router_rules.looks_like_content_question(cau)


class TestModelKhongDuocLatNguocTangLuat:
    """Bằng chứng thắng suy diễn: luật chỉ nhận cụm TƯỜNG MINH ("là gì",
    "học những môn gì"), còn model thì đang đoán."""

    def test_giu_ask_content_khi_model_doi_sang_lookup_doc(self, monkeypatch):
        # Ca thật, chép nguyên trace: model đổi thành lookup_doc VÀ tự bịa
        # doc_type="de_cuong", trong khi kho chưa từng có đề cương nào.
        cau = "lộ trình ngành An toàn thông tin của trường là gì"
        assert router_rules.route(cau).result.intent is Intent.ask_content

        monkeypatch.setattr(
            router.vertex, "generate_json",
            lambda **kw: LLMResult(
                data={
                    "intent": "lookup_doc",
                    "doc_type": "de_cuong",
                    "standalone_question": cau,
                },
                tokens_in=1, tokens_out=1, tokens_cached=0, model="test",
            ),
        )
        got = router.resolve(cau)
        assert got.intent is Intent.ask_content
        assert got.source == "llm"      # vẫn dùng phần còn lại của model

    def test_model_van_toan_quyen_khi_luat_khong_phan_ask_content(self, monkeypatch):
        # Lớp chặn chỉ bảo vệ phán quyết CÓ BẰNG CHỨNG, không phủ quyết model.
        cau = "cho xin tài liệu"
        monkeypatch.setattr(
            router.vertex, "generate_json",
            lambda **kw: LLMResult(
                data={"intent": "lookup_doc", "standalone_question": cau},
                tokens_in=1, tokens_out=1, tokens_cached=0, model="test",
            ),
        )
        assert router.resolve(cau).intent is Intent.lookup_doc


    def test_bo_luon_doc_type_model_bia_kem(self, monkeypatch):
        # doc_type bịa không chỉ vô hại: nó siết truy hồi vào đúng chỗ không có
        # gì, và nếu loại đó tình cờ có vài tài liệu thì câu trả lời lấy từ
        # nhầm chỗ mà vẫn trông hợp lý.
        cau = "lộ trình ngành An toàn thông tin của trường là gì"
        assert router_rules.route(cau).result.doc_type is None

        monkeypatch.setattr(
            router.vertex, "generate_json",
            lambda **kw: LLMResult(
                data={
                    "intent": "lookup_doc",
                    "doc_type": "de_cuong",
                    "standalone_question": cau,
                },
                tokens_in=1, tokens_out=1, tokens_cached=0, model="test",
            ),
        )
        assert router.resolve(cau).doc_type is None

    def test_giu_doc_type_khi_tang_luat_cung_thay(self, monkeypatch):
        # Câu nêu rõ loại tài liệu thì luật cũng bắt được — lúc đó doc_type là
        # bằng chứng chứ không phải suy đoán, phải giữ.
        cau = "quy chế đào tạo quy định gì về thi lại"
        assert router_rules.route(cau).result.doc_type is not None

        monkeypatch.setattr(
            router.vertex, "generate_json",
            lambda **kw: LLMResult(
                data={"intent": "lookup_doc", "standalone_question": cau},
                tokens_in=1, tokens_out=1, tokens_cached=0, model="test",
            ),
        )
        assert router.resolve(cau).doc_type is not None


class TestPhamViTheoBotLaDuLieu:
    """Danh sách "ngoài phạm vi" phải theo từng bot, không viết cứng trong mã.

    Ca thật: bot tư vấn tuyển sinh trả lời "Nội dung này nằm ngoài phạm vi tư
    vấn tuyển sinh của em ạ" cho câu hỏi ĐIỂM CHUẨN — vì prompt router viết
    cứng "học phí, tra điểm" là ngoài phạm vi theo đặc thù bot tra cứu tài liệu.
    """

    def test_bot_khong_cam_gi_thi_prompt_khong_co_muc_cam(self):
        from app.llm.prompts import router_system

        assert "KHÔNG phụ trách" not in router_system(())

    def test_chu_de_cam_cua_bot_di_vao_prompt(self):
        from app.llm.prompts import router_system

        got = router_system(("hoc phi", "thoi khoa bieu"))
        assert "- hoc phi" in got and "- thoi khoa bieu" in got

    def test_phan_bat_bien_van_dung_dau_de_con_cache_tien_to(self):
        from app.llm.prompts import router_system

        assert router_system(("hoc phi",)).startswith(router_system(()))

    def test_prompt_khong_con_coi_hoc_phi_la_ngoai_pham_vi_mac_dinh(self):
        from app.llm.prompts import router_system

        base = router_system(())
        # "học phí" chỉ được là ngoài phạm vi khi CHÍNH BOT khai như vậy.
        assert "học phí" not in base
        # Và model không được lấy "kho không có dữ liệu" làm lý do out_of_scope.
        assert "điểm chuẩn" in base

    def test_persona_truyen_blocked_topics_xuong_router(self):
        import inspect

        from app.core import chat

        src = inspect.getsource(chat.handle)
        assert "blocked_topics=persona.blocked_topics" in src
