"""宏曦标书 - 生成期严格按章节标注取材 单元测试.

规则（用户 2026-10-21 裁定）：

- 只有 ``ai_generated`` 章节允许 AI 自由撰写，且必须带入历史标书参考。
- ``fixed_form`` / ``table`` / ``attachment`` 只能用用户在结构页选定/挂载的
  内容；取不到时正文留空（渲染为占位），并在校验报告里记 ``fail``。
- 不允许"匹配不到就悄悄让 AI 整章代写"。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_pipeline import (
    _generate_strict_non_ai_chapter,
    _merge_format_errors,
)
from app.services.tender_section_matcher import corpus_hash

CORPUS = "一、投标函\n\n致：某某单位\n我方已仔细阅读。\n\n二、开标一览表\n\n序号 | 服务内容\n"


class _Chapter:
    def __init__(self, title, chapter_type, meta=None):
        self.title = title
        self.chapter_type = chapter_type
        self.chapter_meta_json = json.dumps(meta or {}, ensure_ascii=False)


class _RaisingAdapter:
    """任何 AI 调用都应被视为违规。"""

    async def chat_completion(self, *args, **kwargs):
        raise AssertionError("非 AI 章节不得调用 AI")


class TestStrictNonAiChapter:
    @pytest.mark.asyncio
    async def test_fixed_form_uses_stored_tender_text(self):
        """有用户选定的原文 → 原文回填，不报错. AI 只负责填空."""
        start = CORPUS.index("一、投标函")
        ch = _Chapter("投标函", "fixed_form", {"match": {
            "status": "matched", "start": start, "end": CORPUS.index("二、"),
            "corpus_hash": corpus_hash(CORPUS),
        }})
        with patch(
            "app.services.template_filler.fill_fixed_form_section_from_template_with_tables",
            new=AsyncMock(return_value=("一、投标函\n\n致：某某单位\n我方已仔细阅读。", [])),
        ):
            content, warnings, errors = await _generate_strict_non_ai_chapter(
                ch, json.loads(ch.chapter_meta_json),
                {"format_section_text": CORPUS}, {}, None, None,
            )
        assert content.startswith("一、投标函")
        assert errors == []

    @pytest.mark.asyncio
    async def test_fixed_form_without_match_does_not_call_ai(self):
        """匹配不到 → 正文留空 + 报错，绝不回退 AI 整章代写."""
        ch = _Chapter("投标函", "fixed_form", {"match": {"status": "missing"}})
        with patch(
            "app.services.template_filler.generate_file_section",
            new=AsyncMock(side_effect=AssertionError("不得调用 AI 整章生成")),
        ):
            content, warnings, errors = await _generate_strict_non_ai_chapter(
                ch, json.loads(ch.chapter_meta_json),
                {}, {}, _RaisingAdapter(), None,
            )
        assert content == ""
        assert errors, "缺用户选定内容必须报 error"
        assert any("留空" in e for e in errors)
        assert not any("AI 撰写" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_fixed_form_fill_failure_does_not_call_ai(self):
        """原文找到了但回填失败 → 仍不得让 AI 代写，只留空报错."""
        start = CORPUS.index("一、投标函")
        ch = _Chapter("投标函", "fixed_form", {"match": {
            "status": "matched", "start": start, "end": CORPUS.index("二、"),
            "corpus_hash": corpus_hash(CORPUS),
        }})
        with patch(
            "app.services.template_filler.fill_fixed_form_section_from_template_with_tables",
            new=AsyncMock(return_value=("", [])),
        ), patch(
            "app.services.template_filler.generate_file_section",
            new=AsyncMock(side_effect=AssertionError("不得调用 AI 整章生成")),
        ):
            content, warnings, errors = await _generate_strict_non_ai_chapter(
                ch, json.loads(ch.chapter_meta_json),
                {"format_section_text": CORPUS}, {}, None, None,
            )
        assert content == ""
        assert errors

    @pytest.mark.asyncio
    async def test_ambiguous_without_pick_reports_error_and_no_ai(self):
        """多候选但用户没点选 → 留空报错，不擅自切片也不退 AI."""
        ch = _Chapter("投标函", "fixed_form", {"match": {
            "status": "ambiguous", "picked": "auto",
        }})
        content, warnings, errors = await _generate_strict_non_ai_chapter(
            ch, json.loads(ch.chapter_meta_json),
            {"format_section_text": CORPUS}, {}, None, None,
        )
        assert content == ""
        assert errors
        assert not any("AI 撰写" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_table_uses_tender_table_without_error(self):
        ch = _Chapter("开标一览表", "table", {"match": {
            "status": "matched", "source": "table", "table_index": 0,
        }})
        content, warnings, errors = await _generate_strict_non_ai_chapter(
            ch, json.loads(ch.chapter_meta_json),
            {"format_section_text": CORPUS, "format_tables": [{
                "page": 33, "table_index": 0,
                "rows": [["序号", "服务内容"], ["1", "安保服务"]],
            }]},
            {}, None, None,
        )
        assert "| 序号 | 服务内容 |" in content
        assert errors == []

    @pytest.mark.asyncio
    async def test_table_missing_is_error(self):
        ch = _Chapter("开标一览表", "table", {"match": {"status": "missing"}})
        content, warnings, errors = await _generate_strict_non_ai_chapter(
            ch, json.loads(ch.chapter_meta_json),
            {"format_section_text": CORPUS, "format_tables": []},
            {}, None, None,
        )
        assert content == ""
        assert errors

    @pytest.mark.asyncio
    async def test_empty_attachment_list_is_error(self):
        ch = _Chapter("保证金凭证", "attachment", {})
        content, warnings, errors = await _generate_strict_non_ai_chapter(
            ch, {}, {}, None, None, None,
        )
        assert content == ""
        assert errors

    @pytest.mark.asyncio
    async def test_attachment_with_files_is_not_error(self):
        ch = _Chapter("保证金凭证", "attachment", {"attachments": [
            {"kind": "upload", "id": "u1", "label": "保证金", "path": "p/a.png"},
        ]})
        content, warnings, errors = await _generate_strict_non_ai_chapter(
            ch, json.loads(ch.chapter_meta_json), {}, None, None, None,
        )
        assert content == "[IMG:p/a.png|保证金]"
        assert errors == []


# ---------------------------------------------------------------------------
# 整条生成管线的接线：非 AI 章节缺内容 → 校验报告 fail；AI 章节带历史参考
# ---------------------------------------------------------------------------

class _ProjectChapter:
    def __init__(self, title, chapter_type, children_json="", meta=None,
                 order_index=0, review_status="refining"):
        self.id = f"ch-{title}"
        self.title = title
        self.chapter_type = chapter_type
        self.children_json = children_json
        self.chapter_meta_json = json.dumps(meta or {}, ensure_ascii=False)
        self.order_index = order_index
        self.review_status = review_status


class _FakeProject:
    def __init__(self, chapters):
        self.chapters = chapters
        self.format_template_json = ""
        self.scoring_rubric_json = ""
        self.format_verification_json = ""


class _FakeResult:
    def __init__(self, project):
        self._project = project

    def scalar_one_or_none(self):
        return self._project


class _FakeDB:
    def __init__(self, project):
        self._project = project

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._project)

    async def flush(self):
        pass

    async def commit(self):
        pass


async def _collect(gen):
    return [json.loads(ev["data"]) async for ev in gen if ev.get("event")]


@pytest.mark.asyncio
async def test_pipeline_marks_missing_fixed_form_as_fail_and_never_calls_ai():
    """固定格式没匹配到原文 → 生成管线整体 fail，且不调用 AI 整章代写."""
    from app.services.ai_pipeline import generate_from_chapter_structure

    project = _FakeProject([_ProjectChapter("投标函", "fixed_form")])
    db = _FakeDB(project)

    with patch(
        "app.services.template_filler.generate_file_section",
        new=AsyncMock(side_effect=AssertionError("不得调用 AI 整章生成")),
    ):
        events = await _collect(generate_from_chapter_structure(
            project_id="p1", requirements={}, db=db,
        ))

    verification = next(e for e in events if "overall_status" in e)
    assert verification["overall_status"] == "fail"
    assert any("投标函" in e for e in verification["errors"])

    done = next(e for e in events if "chapters" in e)
    assert done["chapters"][0]["content"] == ""


@pytest.mark.asyncio
async def test_mixed_or_legacy_type_is_not_silently_skipped():
    """旧数据的 mixed/未知类型：既不 AI 代写，也不能悄悄空着而不报错."""
    from app.services.ai_pipeline import generate_from_chapter_structure

    project = _FakeProject([_ProjectChapter("综合说明", "mixed")])
    db = _FakeDB(project)

    with patch(
        "app.services.template_filler.generate_file_section",
        new=AsyncMock(side_effect=AssertionError("不得调用 AI 整章生成")),
    ):
        events = await _collect(generate_from_chapter_structure(
            project_id="p1", requirements={}, db=db,
        ))

    verification = next(e for e in events if "overall_status" in e)
    assert verification["overall_status"] == "fail"
    assert any("综合说明" in e for e in verification["errors"])


@pytest.mark.asyncio
async def test_ai_chapter_receives_history_bid_references():
    """AI 章节必须拿到历史标书参考；非 AI 分支不受影响."""
    from app.services.ai_pipeline import generate_from_chapter_structure

    tree = [{"title": "门岗值守", "token_budget_hint": "medium", "children": []}]
    project = _FakeProject([_ProjectChapter(
        "服务方案", "ai_generated",
        children_json=json.dumps(tree, ensure_ascii=False),
    )])
    db = _FakeDB(project)

    captured = {}

    async def _fake_generate(**kwargs):
        captured["reference_sections"] = kwargs.get("reference_sections")
        return "门岗值守正文", None

    with patch(
        "app.services.rag.retrieve_similar_chapters",
        new=AsyncMock(return_value=[{"content": "历史标书里的门岗值守写法"}]),
    ), patch(
        "app.services.rag.retrieve_lesson_references",
        new=AsyncMock(return_value=[{"content": "历史应答：每班两人"}]),
    ), patch(
        "app.services.ai_pipeline._generate_single_section_with_retry",
        new=AsyncMock(side_effect=_fake_generate),
    ), patch(
        "app.services.subsection_generator.generate_container_lead_in",
        new=AsyncMock(return_value=""),
    ):
        await _collect(generate_from_chapter_structure(
            project_id="p1", requirements={}, db=db,
        ))

    refs = captured.get("reference_sections") or []
    assert "历史标书里的门岗值守写法" in refs
    assert "历史应答：每班两人" in refs



class TestMergeFormatErrors:
    def test_error_turns_verification_into_fail(self):
        """缺用户选定内容的章节必须让整体校验显示「未通过」."""
        v = {"overall_status": "pass", "checks": []}
        out = _merge_format_errors(
            v, ["「投标函」未取到用户选定的招标原文，正文留空待补充"],
        )
        assert out["overall_status"] == "fail"
        assert any("投标函" in e for e in out["errors"])
        assert out["checks"][0]["status"] == "fail"
        assert "投标函" in out["checks"][0]["detail"]

    def test_existing_fail_is_kept(self):
        v = {"overall_status": "fail", "checks": []}
        out = _merge_format_errors(v, ["「X」正文留空"])
        assert out["overall_status"] == "fail"
        assert len(out["errors"]) == 1

    def test_no_errors_leaves_status_untouched(self):
        v = {"overall_status": "pass", "checks": []}
        assert _merge_format_errors(v, []) == v
