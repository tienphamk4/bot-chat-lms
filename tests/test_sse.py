"""Định dạng SSE và tính nhất quán trả lời ↔ trích dẫn."""

import json

import pytest

from app.core import sse
from app.core.chat import ChatOutcome
from app.core.render import render_documents
from app.schemas import Citation, DocType, Intent, RouterResult


def parse_frames(payload: str) -> list[tuple[str, dict]]:
    """Bộ phân tích SSE tối giản — đúng logic client sẽ dùng."""
    frames = []
    for block in payload.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            frames.append((event, json.loads(data)))
    return frames


def make_outcome() -> ChatOutcome:
    return ChatOutcome(
        answer="Mình tìm thấy 1 giáo trình:\n1. Giáo trình Giải tích 1 [1]",
        citations=[
            Citation(
                n=1, document_id=42, title="Giáo trình Giải tích 1",
                type=DocType.giao_trinh, url="/api/v1/documents/42/download",
            )
        ],
        route=RouterResult(
            intent=Intent.lookup_doc, standalone_question="giáo trình giải tích 1"
        ),
        branch="metadata",
        log_id=7,
        latency_ms=123,
    )


class TestFraming:
    def test_event_format(self):
        frame = sse.event("route", {"a": 1})
        assert frame == 'event: route\ndata: {"a":1}\n\n'

    def test_heartbeat_is_comment(self):
        assert sse.heartbeat().startswith(":")

    def test_data_is_single_line(self):
        # JSON xuống dòng sẽ phá khung SSE
        frame = sse.event("token", {"text": "dòng 1\ndòng 2"})
        event_line, data_line, _, _ = frame.split("\n")
        assert data_line.startswith("data: ")
        json.loads(data_line[6:])  # vẫn parse được


@pytest.mark.anyio
class TestStreamOutcome:
    async def test_full_sequence(self):
        outcome = make_outcome()
        frames = []
        async for frame in sse.stream_outcome(outcome):
            frames.append(frame)
        parsed = parse_frames("".join(frames))

        events = [name for name, _ in parsed]
        assert events[0] == "route"
        assert events[-1] == "done"
        assert "citations" in events
        assert "token" in events

        # Ghép token phải ra đúng answer
        text = "".join(d["text"] for name, d in parsed if name == "token")
        assert text == outcome.answer

        done = dict(parsed)["done"]
        assert done["log_id"] == 7
        assert done["route"] == "metadata"


class TestAnswerCitationConsistency:
    def test_every_marker_has_citation(self):
        rows = [
            {"id": 1, "title": "Giáo trình A", "type": "giao_trinh",
             "semester": "20261", "lecturer": None, "page_count": 200},
            {"id": 2, "title": "Đề thi B", "type": "de_thi",
             "semester": None, "lecturer": "Cô Ba", "page_count": None},
        ]
        route = RouterResult(intent=Intent.lookup_doc, standalone_question="x")
        answer, citations = render_documents(rows, route)

        import re

        markers = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
        numbers = {c.n for c in citations}
        assert markers == numbers  # không có [n] mồ côi, không có nguồn thừa

    def test_empty_result_has_no_markers(self):
        route = RouterResult(intent=Intent.lookup_doc, standalone_question="x")
        answer, citations = render_documents([], route)
        assert citations == []
        assert "[" not in answer.replace("[n]", "")


@pytest.fixture
def anyio_backend():
    return "asyncio"
