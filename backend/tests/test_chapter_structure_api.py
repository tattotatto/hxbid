"""宏曦标书 - 章节结构保存端点 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

import pytest

from app.api.chapters import (
    USER_SELECTABLE_TYPES,
    _normalize_chapter_tree,
    _prune_attachments,
)


class TestNormalizeChapterTree:
    def test_order_index_is_renumbered(self):
        tree = [{"title": "甲", "type": "ai_generated"},
                {"title": "乙", "type": "ai_generated"}]
        out = _normalize_chapter_tree(tree)
        assert [c["order_index"] for c in out] == [0, 1]

    def test_invalid_type_falls_back_to_ai_generated(self):
        out = _normalize_chapter_tree([{"title": "甲", "type": "胡说"}])
        assert out[0]["type"] == "ai_generated"

    def test_mixed_is_preserved_but_not_selectable(self):
        out = _normalize_chapter_tree([{"title": "甲", "type": "mixed"}])
        assert out[0]["type"] == "mixed"
        assert "mixed" not in USER_SELECTABLE_TYPES

    def test_children_recursed(self):
        out = _normalize_chapter_tree([
            {"title": "甲", "type": "ai_generated",
             "children": [{"title": "甲一", "type": "table"}]},
        ])
        assert out[0]["children"][0]["order_index"] == 0
        assert out[0]["children"][0]["type"] == "table"

    def test_match_and_attachments_survive_roundtrip(self):
        tree = [{
            "title": "投标函", "type": "fixed_form",
            "match": {"status": "matched", "start": 10, "end": 40,
                      "corpus_hash": "sha1:x"},
            "attachments": [{"kind": "qualification", "id": "q1", "label": "营业执照",
                             "path": "ocr/a.png"}],
        }]
        out = _normalize_chapter_tree(tree)
        assert out[0]["match"]["start"] == 10
        assert out[0]["attachments"][0]["id"] == "q1"


class TestPruneAttachments:
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _FakeDB:
        """资源库查询一律返回空 —— 模拟"引用的行已被删除"。"""

        async def execute(self, _stmt):
            return TestPruneAttachments._FakeResult([])

    @pytest.mark.asyncio
    async def test_missing_library_row_is_pruned(self):
        tree = [{"title": "资质", "type": "attachment", "attachments": [
            {"kind": "qualification", "id": "gone", "label": "已删除的资质",
             "path": "ocr/x.png"},
        ]}]
        pruned = await _prune_attachments(tree, self._FakeDB())
        assert tree[0]["attachments"] == []
        assert "已删除的资质" in pruned

    @pytest.mark.asyncio
    async def test_path_outside_upload_dir_is_pruned(self):
        tree = [{"title": "附件", "type": "attachment", "attachments": [
            {"kind": "upload", "id": "u1", "label": "越界文件",
             "path": "../../etc/passwd"},
        ]}]
        pruned = await _prune_attachments(tree, self._FakeDB())
        assert tree[0]["attachments"] == []
        assert "越界文件" in pruned

    @pytest.mark.asyncio
    async def test_valid_upload_survives(self):
        tree = [{"title": "附件", "type": "attachment", "attachments": [
            {"kind": "upload", "id": "u1", "label": "保证金凭证",
             "path": "project_p1/receipt.png"},
        ]}]
        pruned = await _prune_attachments(tree, self._FakeDB())
        assert len(tree[0]["attachments"]) == 1
        assert pruned == []

    @pytest.mark.asyncio
    async def test_same_image_content_in_two_chapters_kept_once(self, tmp_path, monkeypatch):
        """场景：同一张营业执照挂了两个章节（两个不同路径、字节相同）。

        与 materials_injection.drop_already_embedded 同一口径：按**内容**判重，
        否则同一张扫描件会在两个章节里各出一次图。
        """
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "a.png").write_bytes(b"same-bytes")
        (tmp_path / "b.png").write_bytes(b"same-bytes")

        tree = [
            {"title": "资质", "type": "attachment", "attachments": [
                {"kind": "upload", "id": "u1", "label": "营业执照", "path": "a.png"}]},
            {"title": "其他材料", "type": "attachment", "attachments": [
                {"kind": "upload", "id": "u2", "label": "营业执照副本", "path": "b.png"}]},
        ]
        pruned = await _prune_attachments(tree, self._FakeDB())
        assert len(tree[0]["attachments"]) == 1
        assert tree[1]["attachments"] == []
        assert "营业执照副本" in pruned

    @pytest.mark.asyncio
    async def test_children_are_walked_too(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        tree = [{"title": "父", "type": "ai_generated", "children": [
            {"title": "子", "type": "attachment", "attachments": [
                {"kind": "upload", "id": "u1", "label": "越界", "path": "../x.png"}]},
        ]}]
        pruned = await _prune_attachments(tree, self._FakeDB())
        assert tree[0]["children"][0]["attachments"] == []
        assert "越界" in pruned


class TestGenerationChapterType:
    def test_mixed_maps_to_ai_generated(self):
        """mixed 落库归到 ai_generated——否则它进不了生成管线的任何一个循环，
        最终渲染成「（待补充…）」占位文本（spec §8.4）。"""
        from app.api.chapters import _generation_chapter_type

        assert _generation_chapter_type("mixed") == "ai_generated"

    def test_four_selectable_types_pass_through(self):
        from app.api.chapters import _generation_chapter_type

        for t in ("fixed_form", "table", "attachment", "ai_generated"):
            assert _generation_chapter_type(t) == t

    def test_unknown_falls_back_to_ai_generated(self):
        from app.api.chapters import _generation_chapter_type

        assert _generation_chapter_type("") == "ai_generated"
        assert _generation_chapter_type("胡说") == "ai_generated"
