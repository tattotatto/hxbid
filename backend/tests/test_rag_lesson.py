"""retrieve_lesson_references 过滤逻辑测试(注入假 vector_store)."""

import asyncio
from unittest.mock import patch

from app.services import rag


def _make_store(hits):
    class _Store:
        def is_available(self):
            return True

        def search_similar(self, query, n_results):
            return hits

    return _Store()


@patch(
    "app.services.rag.vector_store",
    _make_store([
        {"content": "lesson-content", "distance": 0.3,
         "metadata": {"source": "lesson", "pair_id": "p1",
                      "source_project_id": "other-proj", "title": "t1"}},
        {"content": "history-content", "distance": 0.5,
         "metadata": {"source": "history", "project_id": "proj1"}},
    ]),
)
def test_retrieve_lesson_references_filters_source():
    result = asyncio.run(
        rag.retrieve_lesson_references(
            {"service_requirements": ["保安服务"]}, "cur", n_results=5
        )
    )
    contents = [r["content"] for r in result]
    assert "lesson-content" in contents
    assert "history-content" not in contents


@patch(
    "app.services.rag.vector_store",
    _make_store([
        {"content": "self-content", "distance": 0.2,
         "metadata": {"source": "lesson", "pair_id": "p1",
                      "source_project_id": "cur", "title": "self"}},
        {"content": "other-content", "distance": 0.4,
         "metadata": {"source": "lesson", "pair_id": "p2",
                      "source_project_id": "other", "title": "other"}},
    ]),
)
def test_retrieve_lesson_references_excludes_self():
    result = asyncio.run(
        rag.retrieve_lesson_references(
            {"service_requirements": ["保安服务"]}, "cur", n_results=5
        )
    )
    contents = [r["content"] for r in result]
    assert "self-content" not in contents
    assert "other-content" in contents
