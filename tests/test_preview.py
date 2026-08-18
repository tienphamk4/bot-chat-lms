"""Test sinh PDF xem trước — dispatch và độ lì (không gọi Office thật)."""

from __future__ import annotations

from pathlib import Path

from ingest import preview


class TestDispatch:
    def test_dinh_dang_khong_ho_tro_bo_qua(self, tmp_path):
        assert preview.generate_preview(tmp_path / "x.md", 1, tmp_path) is False
        assert preview.generate_preview(tmp_path / "x.pdf", 1, tmp_path) is False

    def test_khong_co_bo_chuyen_tra_false_khong_no(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preview.shutil, "which", lambda *_: None)
        monkeypatch.setattr(preview.sys, "platform", "linux")
        src = tmp_path / "bai.pptx"
        src.write_bytes(b"fake")
        assert preview.generate_preview(src, 7, tmp_path) is False

    def test_soffice_thanh_cong(self, tmp_path, monkeypatch):
        src = tmp_path / "bai.pptx"
        src.write_bytes(b"fake")
        monkeypatch.setattr(preview.shutil, "which", lambda *_: "soffice")

        def fake_run(cmd, capture_output, timeout):
            outdir = Path(cmd[cmd.index("--outdir") + 1])
            (outdir / "bai.pdf").write_bytes(b"%PDF-fake")

            class R: returncode = 0; stderr = b""
            return R()

        monkeypatch.setattr(preview.subprocess, "run", fake_run)
        assert preview.generate_preview(src, 7, tmp_path) is True
        assert preview.preview_path(tmp_path, 7).read_bytes() == b"%PDF-fake"

    def test_convert_hong_khong_lam_hong_me_nap(self, tmp_path, monkeypatch):
        src = tmp_path / "bai.docx"
        src.write_bytes(b"fake")
        monkeypatch.setattr(preview.shutil, "which", lambda *_: "soffice")

        def boom(*a, **k):
            raise RuntimeError("soffice chet")

        monkeypatch.setattr(preview.subprocess, "run", boom)
        assert preview.generate_preview(src, 7, tmp_path) is False  # không ném

    def test_remove_preview_thieu_file_khong_sao(self, tmp_path):
        preview.remove_preview(tmp_path, 99)


class TestLatXemTruoc:
    """Popup trích dẫn chỉ cần vài trang, không cần cả tệp.

    Tài liệu quảng bá 83 trang nặng 26,8 MB. Nhúng cả tệp vào iframe nghĩa là
    mỗi lần bấm một trích dẫn máy chủ đẩy đi 26,8 MB để hiện MỘT trang; popup
    trắng vài giây rồi mới hiện, người dùng đọc là "lỗi". Lát ba trang 0,46 MB.
    """

    def test_lay_mot_trang_moi_ben(self):
        from ingest.preview import slice_range

        assert slice_range(11, 83) == (10, 12)

    def test_kep_o_dau_tai_lieu(self):
        from ingest.preview import slice_range

        assert slice_range(1, 83) == (1, 2)

    def test_kep_o_cuoi_tai_lieu(self):
        from ingest.preview import slice_range

        assert slice_range(83, 83) == (82, 83)

    def test_tai_lieu_mot_trang(self):
        from ingest.preview import slice_range

        assert slice_range(1, 1) == (1, 1)

    def test_khong_biet_so_trang_thi_van_ra_khoang_hop_le(self):
        from ingest.preview import slice_range

        start, end = slice_range(5, 0)
        assert start == 4 and end >= start

    def test_ten_lat_gan_voi_khoang_trang(self):
        from pathlib import Path

        from ingest.preview import slice_path

        got = slice_path(Path("/srv/files"), 56, 10, 12)
        assert got.name == "56-p10-12.pdf"

    def test_xoa_tai_lieu_don_luon_cac_lat(self):
        import inspect

        from ingest import preview

        src = inspect.getsource(preview.remove_preview)
        assert '-p*.pdf' in src, (
            "bỏ sót lát thì tài liệu mới trùng id sẽ dùng lại lát của tài liệu cũ"
        )
