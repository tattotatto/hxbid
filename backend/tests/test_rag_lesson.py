"""retrieve_lesson_references 过滤逻辑测试(注入假 vector_store)."""

import asyncio
from unittest.mock import patch

from app.services import rag


class FakeStore:
    def is_available(self):
        return True

    def search_similar(self, query, n_results):
        return [
            {"title": "a", "content": "x", "distance": 0.3,
             "metadata": {"source": "lesson", "pair_id": "p1"}},
            {"title": "b", "content": "y", "distance": 0.5,
             "metadata": {"source": "history", "project_id": "proj1"}},
        ]


@patch("app.services.rag.vector_store", FakeStore())
def test_retrieve_lesson_references_filters_source():
    result = asyncio.run(
        rag.retrieve_lesson_references(
            {"service_requirements": ["保安服务"]}, "cur", n_results=5
        )
    )
    assert all(r["metadata"].get("source") == "lesson" for r in result)
