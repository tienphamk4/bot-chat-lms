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
