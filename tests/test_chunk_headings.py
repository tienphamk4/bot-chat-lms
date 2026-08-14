"""Test văn phạm heading v2 + cắt câu bảo vệ viết tắt — đúng bộ mẫu kiểm toán."""

from __future__ import annotations

from ingest.chunk import classify_heading, split, split_sentences
from ingest.extract import Extraction, Page


class TestClassifyHeading:
    def test_bo_mau_kiem_toan_phai_loai(self):
        # Các dòng thật từng thành heading rác trong DB
        assert classify_heading("93     {") is None
        assert classify_heading("22 }") is None
        assert classify_heading("15 phút làm bài kiểm tra") is None  # từ đầu thường
        assert classify_heading("BÀI 1: TỔNG QUAN LẬP TRÌNH PHP ............ 10") is None
        assert classify_heading("3.0 được chính thức công bố vào tháng 6") is None

    def test_bo_mau_phai_nhan(self):
        assert classify_heading("Điều 5. Đăng ký học phần") == (3, "Điều 5. Đăng ký học phần")
        assert classify_heading("CHƯƠNG II") is not None
        assert classify_heading("PHỤ LỤC I") is not None
        assert classify_heading("2.1 Điều kiện dự thi")[0] == 4
        assert classify_heading("Chương 3")[0] == 2
        assert classify_heading("# Tiêu đề markdown") == (1, "Tiêu đề markdown")

    def test_cap_theo_tu_khoa(self):
        assert classify_heading("Phần 1. Quy định chung")[0] == 1
        assert classify_heading("Bài 15: Kế thừa")[0] == 2
        assert classify_heading("Mục 3. Hồ sơ")[0] == 3
        assert classify_heading("1.2.3 Chi tiết")[0] == 5


class TestHeadingStack:
    def _paths(self, lines: list[str]) -> list[str | None]:
        text = "\n\n".join(lines)
        body = "Nội dung đủ dài để thành block riêng. " * 30
        pages = [Page(number=1, text=text.replace("BODY", body))]
        return [c.heading_path for c in split(Extraction(pages=pages, page_count=1))]

    def test_dieu_la_con_cua_chuong(self):
        paths = self._paths(["CHƯƠNG II", "Điều 15. Thi lại", "BODY"])
        assert any(p and "CHƯƠNG II > Điều 15. Thi lại" in p for p in paths)

    def test_quy_che_phang_dieu_dung_mot_minh(self):
        paths = self._paths(["Điều 12. Đánh giá học phần", "BODY"])
        assert any(p == "Điều 12. Đánh giá học phần" for p in paths)

    def test_sibling_replace_khong_ghim_goc(self):
        # Bài 2 phải THAY Bài 1, không thành con của Bài 1
        paths = self._paths(["Bài 1: Mở đầu", "BODY", "Bài 2: Biến", "BODY"])
        later = [p for p in paths if p and "Bài 2" in p]
        assert later and all("Bài 1" not in p for p in later)

    def test_noi_heading_wrap(self):
        pages = [Page(number=1, text="Bài 8: Tạo cơ sở dữ liệu với\nMySQL\n\n" + "Nội dung. " * 80)]
        chunks = split(Extraction(pages=pages, page_count=1))
        assert any(
            c.heading_path == "Bài 8: Tạo cơ sở dữ liệu với MySQL" for c in chunks
        )


class TestRunDemotion:
    def test_bang_de_cuong_bi_ha_cap_bai_that_thoat_nan(self):
        # Bảng 'Nội dung tổng quát': 6 dòng 'Bài N' liền nhau + ô số đệm,
        # rồi 'BÀI 1' THẬT mở chương (số khởi động lại) + thân bài.
        rows = "\n".join(
            f"Bài {n}: Nội dung bài {n}\n{n + 3}" for n in range(1, 7)
        )
        text = rows + "\nBÀI 1: TỔNG QUAN LẬP TRÌNH PHP\n" + "Nội dung thật của chương. " * 40
        chunks = split(Extraction(pages=[Page(number=1, text=text)], page_count=1))
        paths = [c.heading_path for c in chunks if c.heading_path]
        assert any(p == "BÀI 1: TỔNG QUAN LẬP TRÌNH PHP" for p in paths)
        assert not any(p and "Bài 4" in p for p in paths)  # dòng bảng không thành path

    def test_heading_thua_thot_khong_bi_oan(self):
        text = (
            "Chương 1. Mở đầu\n" + "Nội dung chương một. " * 30
            + "\n\nChương 2. Phát triển\n" + "Nội dung chương hai. " * 30
        )
        chunks = split(Extraction(pages=[Page(number=1, text=text)], page_count=1))
        paths = {c.heading_path for c in chunks}
        assert "Chương 1. Mở đầu" in paths and "Chương 2. Phát triển" in paths


class TestSentenceSplit:
    def test_bao_ve_viet_tat(self):
        parts = split_sentences("Giảng viên là TS. Nguyễn Văn A. Môn học rất hay.")
        assert parts[0] == "Giảng viên là TS. Nguyễn Văn A."
        assert parts[1] == "Môn học rất hay."

    def test_cau_thuong_van_cat(self):
        parts = split_sentences("Câu một. Câu hai! Câu ba?")
        assert len(parts) == 3
