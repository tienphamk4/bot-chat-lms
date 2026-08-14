"""Chunker: 500/80/120, ưu tiên cắt heading → trang → đoạn → câu."""

from ingest.chunk import MIN_TOKENS, TARGET_TOKENS, estimate_tokens, split
from ingest.extract import Extraction, Page


def make_extraction(*page_texts: str) -> Extraction:
    pages = [Page(number=i, text=t) for i, t in enumerate(page_texts, start=1)]
    return Extraction(pages=pages, page_count=len(pages))


def paragraph(sentences: int = 10, words_per: int = 12) -> str:
    sentence = " ".join(["nội dung"] * words_per) + "."
    return " ".join([sentence] * sentences)


class TestBasics:
    def test_empty_returns_nothing(self):
        assert split(make_extraction("")) == []

    def test_short_doc_is_one_chunk(self):
        chunks = split(make_extraction("Một đoạn ngắn duy nhất."))
        assert len(chunks) == 1
        assert chunks[0].page_from == 1

    def test_chunks_within_target(self):
        text = "\n\n".join(paragraph() for _ in range(12))
        chunks = split(make_extraction(text))
        assert len(chunks) > 1
        # Cho phép vượt nhẹ do một block không chia nhỏ hơn được
        for chunk in chunks:
            assert chunk.token_count <= TARGET_TOKENS * 1.5

    def test_no_chunk_below_min_except_single(self):
        text = "\n\n".join(paragraph() for _ in range(12))
        chunks = split(make_extraction(text))
        for chunk in chunks:
            assert chunk.token_count >= MIN_TOKENS


class TestHeadings:
    def test_heading_path_recorded(self):
        text = (
            "Chương 1. Giới hạn\n\n" + paragraph() + "\n\n"
            "Chương 2. Đạo hàm\n\n" + paragraph()
        )
        chunks = split(make_extraction(text))
        paths = {c.heading_path for c in chunks}
        assert any(p and "Chương 1" in p for p in paths)
        assert any(p and "Chương 2" in p for p in paths)

    def test_heading_starts_new_chunk(self):
        # Nội dung chương 1 đủ dài thì heading chương 2 phải mở chunk mới
        text = (
            "Chương 1. Mở đầu\n\n" + paragraph(sentences=30) + "\n\n"
            "Chương 2. Phần chính\n\n" + paragraph(sentences=30)
        )
        chunks = split(make_extraction(text))
        chapter2_chunks = [c for c in chunks if c.heading_path and "Chương 2" in c.heading_path]
        assert chapter2_chunks
        assert all("Chương 1" not in (c.heading_path or "") for c in chapter2_chunks)

    def test_markdown_heading_detected(self):
        text = "# Tổng quan\n\n" + paragraph()
        chunks = split(make_extraction(text))
        assert chunks[0].heading_path == "Tổng quan"


class TestPages:
    def test_page_range_tracked(self):
        chunks = split(make_extraction(paragraph(4), paragraph(4), paragraph(4)))
        assert chunks[0].page_from == 1
        assert chunks[-1].page_to == 3

    def test_long_sentence_block_is_split(self):
        # Một "đoạn" một câu cực dài vẫn phải bị cắt
        giant = " ".join(["từ"] * 3000) + "."
        chunks = split(make_extraction(giant))
        assert len(chunks) > 1


class TestTokenEstimate:
    def test_estimate_reasonable_for_vietnamese(self):
        text = "sinh viên tra cứu tài liệu học tập" * 20
        tokens = estimate_tokens(text)
        assert 0.2 * len(text) < tokens < 0.6 * len(text)
