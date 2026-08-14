"""Trình gán metadata 3 tầng: tên file → thư mục → manifest."""

from pathlib import Path

from ingest.meta import resolve


class TestFilename:
    def test_full_convention(self, tmp_path: Path):
        f = tmp_path / "MAT1093_giao-trinh_giai-tich-1_20261.pdf"
        f.touch()
        meta = resolve(f, tmp_path)
        assert meta.course_code == "MAT1093"
        assert meta.doc_type == "giao_trinh"
        assert meta.semester == "20261"
        assert not meta.needs_review

    def test_type_keywords(self, tmp_path: Path):
        cases = {
            "INT2204 de thi cuoi ky.pdf": "de_thi",
            "slide_bai_giang_tuan1_INT2204.pptx": "slide",
            "INT2204-bai-tap-chuong-2.docx": "bai_tap",
            "de-cuong-INT2204-2026.pdf": "de_cuong",
        }
        for name, expected in cases.items():
            f = tmp_path / name
            f.touch()
            assert resolve(f, tmp_path).doc_type == expected, name

    def test_quy_che_needs_no_course(self, tmp_path: Path):
        f = tmp_path / "quy-che-dao-tao-2026.pdf"
        f.touch()
        meta = resolve(f, tmp_path)
        assert meta.doc_type == "quy_che"
        assert not meta.needs_review

    def test_unknown_goes_to_review(self, tmp_path: Path):
        f = tmp_path / "tai lieu linh tinh.pdf"
        f.touch()
        meta = resolve(f, tmp_path)
        assert meta.needs_review


class TestDirectories:
    def test_course_and_type_from_path(self, tmp_path: Path):
        d = tmp_path / "MAT1093" / "de_thi"
        d.mkdir(parents=True)
        f = d / "giua ky 2025.pdf"
        f.touch()
        meta = resolve(f, tmp_path)
        assert meta.course_code == "MAT1093"
        assert meta.doc_type == "de_thi"
        assert not meta.needs_review

    def test_filename_wins_over_directory(self, tmp_path: Path):
        # Tên file nói giáo trình, thư mục nói đề thi → tên file thắng (tầng 1 trước)
        d = tmp_path / "de_thi"
        d.mkdir()
        f = d / "MAT1093_giao_trinh.pdf"
        f.touch()
        assert resolve(f, tmp_path).doc_type == "giao_trinh"


class TestManifest:
    def test_manifest_overrides_everything(self, tmp_path: Path):
        f = tmp_path / "MAT1093_giao_trinh.pdf"
        f.touch()
        (tmp_path / "manifest.csv").write_text(
            "file_path,title,course_code,doc_type,semester,lecturer,access\n"
            "MAT1093_giao_trinh.pdf,Đề thi thật,INT2204,de_thi,20252,Cô Ba,faculty\n",
            encoding="utf-8",
        )
        meta = resolve(f, tmp_path)
        assert meta.course_code == "INT2204"
        assert meta.doc_type == "de_thi"
        assert meta.semester == "20252"
        assert meta.lecturer == "Cô Ba"
        assert meta.access == "faculty"
        assert meta.title == "Đề thi thật"

    def test_invalid_manifest_values_ignored(self, tmp_path: Path):
        f = tmp_path / "MAT1093_giao_trinh.pdf"
        f.touch()
        (tmp_path / "manifest.csv").write_text(
            "file_path,title,course_code,doc_type,semester,lecturer,access\n"
            "MAT1093_giao_trinh.pdf,,,kieu_la,9999,,tum_lum\n",
            encoding="utf-8",
        )
        meta = resolve(f, tmp_path)
        assert meta.doc_type == "giao_trinh"  # giữ kết quả tầng 1
        assert meta.semester is None
        assert meta.access == "public"

    def test_broken_manifest_does_not_crash(self, tmp_path: Path):
        f = tmp_path / "MAT1093_giao_trinh.pdf"
        f.touch()
        (tmp_path / "manifest.csv").write_bytes(b"\xff\xfe broken")
        meta = resolve(f, tmp_path)
        assert meta.course_code == "MAT1093"
