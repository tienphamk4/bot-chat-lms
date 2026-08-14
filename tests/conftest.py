"""Cấu hình pytest chung.

Unit test không cần CSDL. Test nào cần thì đánh dấu `@pytest.mark.db` — mặc
định bị bỏ qua, chạy bằng `pytest -m db` trên máy đã có schema.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Chạy pytest từ bất kỳ thư mục nào cũng import được app/, ingest/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "db: cần MariaDB đã migrate")


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m"):
        return
    skip_db = pytest.mark.skip(reason="cần CSDL — chạy bằng: pytest -m db")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip_db)


@pytest.fixture
def course_catalog(monkeypatch):
    """Nạp chỉ mục môn học giả vào course_index — router test không cần CSDL."""
    from app.core import course_index

    def install(courses: list[dict]):
        by_phrase: dict[str, course_index.CourseEntry] = {}
        by_id: dict[int, course_index.CourseEntry] = {}
        for course in courses:
            entry = course_index.CourseEntry(
                id=course["id"],
                code=course["code"],
                name=course["name"],
                faculty_id=course.get("faculty_id"),
            )
            by_id[entry.id] = entry
            for phrase in course.get("phrases", []):
                by_phrase[phrase] = entry

        monkeypatch.setattr(course_index._index, "by_phrase", by_phrase)
        monkeypatch.setattr(course_index._index, "by_id", by_id)
        monkeypatch.setattr(course_index._index, "loaded_at", time.monotonic())

    return install
