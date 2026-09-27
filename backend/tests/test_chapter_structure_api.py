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


class TestMatchPayload:
    def test_best_carries_truncated_preview(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        corpus = "一、投标函\n\n" + "甲" * 5000
        result = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        payload = _to_match_payload(result, corpus)
        assert payload["status"] == "matched"
        assert payload["best"]["title"] == "投标函"
        assert payload["best"]["start"] == 0
        assert len(payload["best"]["preview"]) == 4000
        assert payload["best"]["preview_truncated"] is True
        assert payload["picked"] == "auto"

    def test_missing_has_no_best(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        corpus = "一、投标函\n\n正文"
        result = match_tender_section(
            "不存在", chapter_type="fixed_form", format_section_text=corpus,
        )
        payload = _to_match_payload(result, corpus)
        assert payload["status"] == "missing"
        assert payload["best"] is None

    def test_na_for_ai_generated(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        result = match_tender_section("服务方案", chapter_type="ai_generated")
        payload = _to_match_payload(result, "")
        assert payload["status"] == "na"

    def test_table_preview_present_when_no_text_section(self):
        """表格章节 best 为空时，抽屉靠 table_preview 显示命中内容。"""
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        tables = [{"page": 33, "table_index": 0,
                   "rows": [["序号", "服务内容"], ["1", "安保"]]}]
        corpus = "一、别的章节\n\n正文\n"
        result = match_tender_section(
            "序号服务内容", chapter_type="table",
            format_section_text=corpus, format_tables=tables,
        )
        payload = _to_match_payload(result, corpus, tables)
        assert payload["best"] is None
        assert payload["table_index"] == 0
        assert "| 序号 | 服务内容 |" in payload["table_preview"]

    def test_table_preview_absent_for_text_matches(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        corpus = "一、投标函\n\n正文"
        result = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        payload = _to_match_payload(result, corpus)
        assert payload["table_preview"] is None


class TestAttachmentUploadRules:
    def test_allowed_extensions(self):
        from app.api.chapters import ALLOWED_ATTACHMENT_EXT

        assert ".png" in ALLOWED_ATTACHMENT_EXT
        assert ".pdf" in ALLOWED_ATTACHMENT_EXT
        assert ".exe" not in ALLOWED_ATTACHMENT_EXT

    def test_safe_attachment_filename_strips_path(self):
        from app.api.chapters import _safe_attachment_name

        assert "/" not in _safe_attachment_name("../../evil.png")
        assert _safe_attachment_name("../../evil.png").endswith("evil.png")
        assert _safe_attachment_name("证明 文件.PNG").endswith(".png")

    def test_unsupported_extension_is_rejected(self):
        from app.api.chapters import _is_allowed_attachment

        assert _is_allowed_attachment("a.png") is True
        assert _is_allowed_attachment("a.PDF") is True
        assert _is_allowed_attachment("a.exe") is False
        assert _is_allowed_attachment("noext") is False


class TestBuildChapterMetaCarriesMatchAndAttachments:
    def test_match_and_attachments_are_merged(self):
        from app.api.chapters import _build_chapter_meta

        ch_data = {
            "number": "一",
            "title": "投标函",
            "match": {"status": "matched", "start": 10, "end": 40,
                      "corpus_hash": "sha1:x"},
            "attachments": [{"kind": "upload", "id": "u1", "label": "凭证",
                             "path": "project_p1/a.png"}],
        }
        meta = json.loads(_build_chapter_meta(ch_data, None))
        assert meta["match"]["start"] == 10
        assert meta["attachments"][0]["label"] == "凭证"

    def test_absent_match_is_omitted_not_null(self):
        from app.api.chapters import _build_chapter_meta

        meta = json.loads(_build_chapter_meta({"title": "甲"}, None))
        assert "match" not in meta
        assert meta["attachments"] == []

    def test_part_metadata_and_global_rules_merged(self):
        from app.api.chapters import _build_chapter_meta

        part = {"table_schema": [{"name": "序号"}],
                "signature_block": {"lines": ["投标人：（公章）"]}}
        meta = json.loads(_build_chapter_meta(
            {"title": "投标函"}, part, {"numbering_style": "chinese_legal"}))
        assert meta["table_schema"] == [{"name": "序号"}]
        assert meta["signature_block"]["lines"] == ["投标人：（公章）"]
        assert meta["numbering_style"] == "chinese_legal"


class TestApplySubmittedTree:
    """confirm 时把前端提交的整棵树落库，并把被剔除的附件透出来.

    旧实现把 `_prune_attachments` 的返回值（被剔除的 label）**丢掉**了，
    而 PUT /chapter-structure 却把它返回给前端 —— 于是用户在页面开着时资源库
    某行被删，点「确认并继续」后附件从树里消失、界面上仍显示「2 个附件」、
    没有任何提示，到生成时才发现是占位页。
    """

    @pytest.mark.asyncio
    async def test_pruned_labels_are_returned(self, tmp_path, monkeypatch):
        from app.api.chapters import _apply_submitted_tree
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

        class _Project:
            chapter_structure_json = "[]"
            id = "p1"

        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

            async def flush(self):
                return None

        project = _Project()
        pruned = await _apply_submitted_tree(project, [
            {"title": "资质", "type": "attachment", "attachments": [
                {"kind": "qualification", "id": "gone", "label": "已删除的资质",
                 "path": "ocr/x.png"}]},
        ], _DB())

        assert pruned == ["已删除的资质"], "被剔除的附件必须透出，不能静默"
        assert json.loads(project.chapter_structure_json)[0]["attachments"] == []

    @pytest.mark.asyncio
    async def test_valid_tree_is_stored(self, tmp_path, monkeypatch):
        from app.api.chapters import _apply_submitted_tree
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))

        class _Project:
            chapter_structure_json = "[]"

        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

            async def flush(self):
                return None

        project = _Project()
        pruned = await _apply_submitted_tree(
            project, [{"title": "投标函", "type": "fixed_form"}], _DB(),
        )
        assert pruned == []
        assert json.loads(project.chapter_structure_json)[0]["type"] == "fixed_form"


class TestConfirmResponseSurfacesPruned:
    def test_response_model_has_pruned_attachments(self):
        from app.api.chapters import OutlineConfirmResponse

        res = OutlineConfirmResponse(success=True, pruned_attachments=["营业执照副本"])
        assert res.pruned_attachments == ["营业执照副本"]
        assert OutlineConfirmResponse().pruned_attachments == []


class TestCorpusAvailabilityFlag:
    """语料可用性必须透给前端 —— 「未匹配」的两类原因，用户能采取的动作完全不同.

    有语料但标题没命中 → 改标题/改类型有用，抽屉里也能列候选。
    语料整体为空（扫描件 PDF 没文本层）→ 改标题毫无用处，候选恒为空，
    这时页面必须给一句整体说明，而不是让用户对着几十个红叉逐个点开看
    "建议改标题，或从下方候选中选一个"（下方根本没有候选）。
    """

    def test_flag_true_when_format_section_present(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        corpus = "一、投标函\n\n正文"
        result = match_tender_section("投标函", format_section_text=corpus)
        payload = _to_match_payload(result, corpus, [], corpus_available=True)
        assert payload["corpus_available"] is True

    def test_flag_false_when_no_corpus_anywhere(self):
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        result = match_tender_section("投标函", format_section_text=None, full_text=None)
        payload = _to_match_payload(result, "", [], corpus_available=False)
        assert payload["corpus_available"] is False
        assert payload["status"] == "missing"

    def test_flag_defaults_true_for_backward_compat(self):
        """不给这个参数时默认 True —— 老调用方（与既有测试）不受影响。"""
        from app.api.chapters import _to_match_payload
        from app.services.tender_section_matcher import match_tender_section

        result = match_tender_section("投标函", format_section_text="一、投标函\n\n正文")
        assert _to_match_payload(result, "一、投标函\n\n正文")["corpus_available"] is True


class TestEnrichMissingMatches:
    """页面加载时必须显示真实的匹配状态.

    线上反馈：「不点修改就显示未匹配，实际上是可以匹配的」。
    匹配原本只在「改标题 / 改类型」时触发（前端 onMatchRequest），**加载时不跑**
    —— 于是刚提取/刚打开的结构，每个固定格式/表格章节都挂着「未匹配」，
    用户必须逐个点一次「重命名→保存」才看到真实状态。页面在说谎。
    """

    CORPUS = "一、封面\n\n封面正文\n\n二、投标函\n\n投标函正文\n"
    REQS = {"format_section_text": CORPUS, "format_tables": [], "format_page_map": []}

    def _enrich(self, chapters, reqs=None, full_text=None):
        from app.api.chapters import _enrich_missing_matches

        # 注意用 is None 而不是 `or`：空 dict 是"真的没有语料"，不能被兜底成 REQS
        return _enrich_missing_matches(
            chapters, self.REQS if reqs is None else reqs, full_text)

    def test_fills_match_for_nodes_lacking_one(self):
        tree = [{"title": "一、封面", "type": "fixed_form", "children": []}]
        out = self._enrich(tree)
        assert out[0]["match"]["status"] == "matched"
        assert out[0]["match"]["start"] is not None

    def test_bare_title_also_matches(self):
        """生成侧标题不带序号时也要匹配上（序号归一化）。"""
        tree = [{"title": "封面", "type": "fixed_form"}]
        assert self._enrich(tree)[0]["match"]["status"] == "matched"

    def test_table_node_gets_table_index(self):
        # 表格按表头文字打分选表（语料里没有对应小标题、也没有 page_map 时）
        reqs = dict(self.REQS)
        reqs["format_tables"] = [{"page": 75, "table_index": 0,
                                  "rows": [["序号", "服务内容"], ["1", "安保"]]}]
        tree = [{"title": "序号服务内容", "type": "table",
                 "match": {"status": "missing"}}]
        out = self._enrich(tree, reqs)
        assert out[0]["match"]["table_index"] == 0

    def test_ai_generated_and_attachment_are_left_alone(self):
        tree = [{"title": "服务方案", "type": "ai_generated"},
                {"title": "附件", "type": "attachment"}]
        out = self._enrich(tree)
        assert out[0].get("match") is None
        assert out[1].get("match") is None

    def test_existing_fresh_match_is_not_recomputed(self):
        """已有且语料未变的 match 不动 —— 幂等，避免每次刷新都重算。"""
        from app.services.tender_section_matcher import corpus_hash

        kept = {"status": "matched", "source": "format_section", "start": 0, "end": 9,
                "corpus_hash": corpus_hash(self.CORPUS), "picked": "manual"}
        tree = [{"title": "一、封面", "type": "fixed_form", "match": dict(kept)}]
        out = self._enrich(tree)
        assert out[0]["match"] == kept, "用户手选的结果不该被重算覆盖"

    def test_stale_match_is_recomputed(self):
        tree = [{"title": "一、封面", "type": "fixed_form",
                 "match": {"status": "matched", "corpus_hash": "sha1:stale"}}]
        out = self._enrich(tree)
        assert out[0]["match"]["status"] == "matched"
        assert out[0]["match"]["corpus_hash"] != "sha1:stale"

    def test_children_are_walked(self):
        tree = [{"title": "父", "type": "ai_generated", "children": [
            {"title": "一、封面", "type": "fixed_form"}]}]
        out = self._enrich(tree)
        assert out[0]["children"][0]["match"]["status"] == "matched"

    def test_empty_corpus_yields_missing_not_crash(self):
        tree = [{"title": "一、封面", "type": "fixed_form"}]
        out = self._enrich(tree, reqs={}, full_text=None)
        assert out[0]["match"]["status"] == "missing"

    def test_full_text_fallback_is_used(self):
        tree = [{"title": "一、封面", "type": "fixed_form"}]
        out = self._enrich(tree, reqs={}, full_text=self.CORPUS)
        assert out[0]["match"]["status"] == "matched"
        assert out[0]["match"]["source"] == "full_text"
