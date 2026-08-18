"""Đọc PDF theo CỘT (ingest/extract.py).

Ca thật đã làm bot trả lời sai: trang 10 tài liệu tuyển sinh dàn ba cột, mỗi
cột một ngành. Đọc tuyến tính nối ba cột thành một dòng, nên "Mã ngành:
7310109" (cột 2, Kinh tế số) đứng ngay cạnh "HỆ THỐNG THÔNG TIN QUẢN LÝ"
(cột 1) — bot ghép lại và trả mã ngành của ngành này cho ngành kia, nghe rất
chắc chắn.

Hai chiều đều phải giữ: trang nhiều cột phải tách, trang văn xuôi bình thường
KHÔNG được đụng tới. Chia nhầm một trang một cột còn tệ hơn không chia.
"""

from __future__ import annotations

from ingest.extract import _Line, _column_ranges, order_by_columns


def line(text, x0, x1, y0, height=1000.0, block=0):
    return _Line(
        text=text, size=10.0, bold=False, y0=y0, y1=y0 + 10,
        height=height, block=block, x0=x0, x1=x1,
    )


def ba_cot(width=1200.0):
    """Dựng lại đúng hình trang 10: ba cột, mỗi cột một ngành."""
    return [
        line("HỆ THỐNG THÔNG TIN QUẢN LÝ", 20, 380, 40),
        line("KINH TẾ SỐ", 440, 780, 40),
        line("HỆ THỐNG THÔNG TIN", 820, 1180, 40),
        line("Chỉ tiêu tuyển sinh: 100", 20, 380, 400),
        line("Chỉ tiêu tuyển sinh: 60", 440, 780, 400),
        line("Chỉ tiêu tuyển sinh: 50", 820, 1180, 400),
        line("Mã ngành: 7340405", 20, 380, 470),
        line("Mã ngành: 7310109", 440, 780, 470),
        line("Mã ngành: 7480104", 820, 1180, 470),
        line("Mô tả ngành quản lý", 20, 380, 540),
        line("Mô tả ngành kinh tế số", 440, 780, 540),
        line("Mô tả ngành hệ thống", 820, 1180, 540),
    ]


class TestNhanDienCot:
    def test_trang_ba_cot(self):
        assert len(_column_ranges(ba_cot(), 1200.0)) == 3

    def test_trang_mot_cot_khong_bi_chia(self):
        # Văn xuôi thường: mọi dòng trải gần hết bề ngang, không có rãnh nào.
        lines = [line(f"Đoạn văn số {i}", 60, 1140, 100 + i * 30) for i in range(12)]
        assert _column_ranges(lines, 1200.0) == []

    def test_thut_dau_dong_khong_thanh_cot_rieng(self):
        # Gom theo mép trái thì mỗi mức thụt đầu dòng thành một "cột"; gom
        # theo rãnh trống thì không.
        lines = [
            line("Dòng thường", 60, 1140, 100),
            line("Dòng thụt vào", 110, 1140, 130),
            line("Dòng thụt sâu hơn", 160, 1140, 160),
            line("Dòng thường nữa", 60, 1140, 190),
            line("Lại thụt", 110, 1140, 220),
            line("Thêm dòng", 60, 1140, 250),
        ]
        assert _column_ranges(lines, 1200.0) == []

    def test_cot_qua_it_dong_thi_bo_qua(self):
        # Một chú thích lạc bên lề không biến trang thành hai cột.
        lines = [line(f"Đoạn {i}", 60, 700, 100 + i * 30) for i in range(10)]
        lines.append(line("*ghi chú", 1000, 1150, 200))
        assert _column_ranges(lines, 1200.0) == []

    def test_header_chay_ngang_khong_bit_ranh(self):
        # Header/footer trải hết bề ngang sẽ lấp mọi rãnh nếu đem vào phép đo.
        lines = ba_cot()
        lines.append(line("Thông tin tuyển sinh 2026 — ĐẠI HỌC KTQD", 20, 1180, 5))
        lines.append(line("trang 16", 20, 1180, 960))
        assert len(_column_ranges(lines, 1200.0)) == 3


class TestSapLaiTheoCot:
    def _thu_tu(self, lines):
        return [ln.text for ln in order_by_columns(lines, 1200.0)]

    def test_het_cot_trai_moi_sang_cot_phai(self):
        got = self._thu_tu(ba_cot())
        assert got[:3] == [
            "HỆ THỐNG THÔNG TIN QUẢN LÝ",
            "Chỉ tiêu tuyển sinh: 100",
            "Mã ngành: 7340405",
        ]

    def test_ma_nganh_o_lai_dung_nganh(self):
        got = self._thu_tu(ba_cot())
        # Mã của Kinh tế số phải nằm SAU tên Kinh tế số và TRƯỚC ngành kế.
        i_ktso = got.index("KINH TẾ SỐ")
        i_ma = got.index("Mã ngành: 7310109")
        i_httt = got.index("HỆ THỐNG THÔNG TIN")
        assert i_ktso < i_ma < i_httt

    def test_tieu_de_o_dinh_trang_khong_bi_day_xuong_cuoi(self):
        # Tên ngành in to nằm trong vùng lề trên. Đẩy nó xuống cuối là tách
        # tên ngành khỏi cột của nó — hỏng lại đúng thứ vừa sửa.
        got = self._thu_tu(ba_cot())
        assert got[0] == "HỆ THỐNG THÔNG TIN QUẢN LÝ"

    def test_moi_cot_mot_dai_block_rieng(self):
        # Chuyển cột luôn là ranh giới đoạn, không bao giờ dính chữ hai ngành
        # vào cùng một đoạn khi dựng trang.
        blocks = [ln.block for ln in order_by_columns(ba_cot(), 1200.0)]
        assert len(set(blocks)) >= 3

    def test_trang_mot_cot_giu_nguyen_thu_tu(self):
        lines = [line(f"Đoạn {i}", 60, 1140, 100 + i * 30) for i in range(12)]
        assert order_by_columns(lines, 1200.0) == lines

    def test_khong_mat_dong_nao(self):
        lines = ba_cot()
        assert len(order_by_columns(lines, 1200.0)) == len(lines)


class TestNapLaiKhongLamMatGanBot:
    """Nạp lại và OCR đều xóa bản ghi cũ rồi tạo mới, nên document_id đổi và
    `assistant_documents` bị cascade xóa. Bot mất sạch tài liệu được gán mà
    vẫn trả lời được (quy tắc "chưa gán thì đọc toàn kho") — im lặng hoàn toàn.
    """

    def test_ca_hai_duong_deu_di_qua_ham_giu_gan_bot(self):
        import inspect

        from ingest import loader, ocr

        assert "replace_document" in inspect.getsource(loader.reindex_document)
        assert "replace_document" in inspect.getsource(ocr.ocr_document)

    def test_ham_giu_gan_bot_doc_truoc_khi_xoa(self):
        import inspect

        from ingest import loader

        src = inspect.getsource(loader.replace_document)
        doc_truoc = src.index("SELECT assistant_id FROM assistant_documents")
        xoa_sau = src.index("DELETE FROM documents")
        assert doc_truoc < xoa_sau, "phải đọc danh sách trợ lý TRƯỚC khi xóa"
        assert "INSERT IGNORE INTO assistant_documents" in src
