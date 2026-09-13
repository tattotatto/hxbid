"""generate 第一阶段「自动展开章节标题树」测试.

背景（2026-09-13 玉溪大红山诊断）：

``refine_chapter_titles`` 只能通过 ``POST /bid/{pid}/chapters/{id}/refine``
逐章手动触发，不在 extract-chapters / outline/confirm / generate 任何自动
链路里。于是 ``outline/confirm`` 后每章 ``children_json`` 仍是 ``"[]"``，
``generate`` 走「整章当唯一叶子」的 fallback —— 「服务方案」只有一个生成
任务，篇幅被钉死在 25,679 字，而中标标书该章 224,460 字。

修法：把展开挪进 generate 的 SSE 流当第一阶段（用户已确认此位置——
放在 outline/confirm 里会撞前端 axios 120s 超时）。只展开
``children_json`` 为空的章节；用户在 UI 里亲手细化过的树一律不动。
"""

import json

import pytest

from app.services.ai_pipeline import (
    expand_pending_chapter_trees,
    requirements_with_rubric,
)


class TestRequirementsWithRubric:
    """生成期 requirements 必须带上项目级评分指标.

    ``generate`` 处理器只读 ``parsed_requirements_json``，而评分指标存在
    另一个列 ``scoring_rubric_json`` 里。不并进来，``requirements``
    里就没有 ``scoring_rubric`` 键，标题展开与内容生成全部看不到评分点——
    这正是「目录不贴评分点」缺陷链的最后一环。
    """

    def test_merges_rubric_with_items(self):
        rubric_json = json.dumps({"status": "found", "items": [
            {"id": "item-04", "name": "门岗方案", "points": 10},
        ]}, ensure_ascii=False)
        merged = requirements_with_rubric({"project_name": "某项目"}, rubric_json)

        assert merged["scoring_rubric"]["items"][0]["name"] == "门岗方案"
        assert merged["project_name"] == "某项目", "原有要求不得丢"

    def test_does_not_mutate_input(self):
        requirements = {"project_name": "某项目"}
        requirements_with_rubric(
            requirements, json.dumps({"items": [{"name": "x", "points": 1}]}))
        assert "scoring_rubric" not in requirements, "不得就地改写调用方的 dict"

    def test_empty_rubric_leaves_requirements_unchanged(self):
        assert requirements_with_rubric({"a": 1}, "") == {"a": 1}
        assert requirements_with_rubric({"a": 1}, "{}") == {"a": 1}
        assert requirements_with_rubric(
            {"a": 1}, json.dumps({"status": "none", "items": []})) == {"a": 1}

    def test_corrupt_rubric_json_is_ignored(self):
        assert requirements_with_rubric({"a": 1}, "{not json") == {"a": 1}


class FakeChapter:
    """ProjectChapter 的最小替身：本阶段只读写这几个字段。"""

    def __init__(self, title, chapter_type="ai_generated", children_json="",
                 chapter_meta_json=""):
        self.id = f"id-{title}"
        self.title = title
        self.chapter_type = chapter_type
        self.children_json = children_json
        self.chapter_meta_json = chapter_meta_json


class FakeAdapter:
    """按「只列二级标题」分派的两级展开假适配器。"""

    def __init__(self, sections=("二级甲", "二级乙"), leaves_per_section=2,
                 broken=False):
        self.sections = list(sections)
        self.leaves_per_section = leaves_per_section
        self.broken = broken
        self.calls = 0

    async def chat_completion(self, messages, **kwargs):
        self.calls += 1
        if self.broken:
            return "not json at all"
        prompt = messages[-1]["content"]
        if "只列二级标题" in prompt:
            return json.dumps({"children": [
                {"title": s, "token_budget_hint": "large", "children": []}
                for s in self.sections
            ]}, ensure_ascii=False)
        target = next((s for s in self.sections if s in prompt), "叶子")
        return json.dumps({"children": [
            {"title": f"{target}-{i + 1}", "token_budget_hint": "medium",
             "children": []}
            for i in range(self.leaves_per_section)
        ]}, ensure_ascii=False)


def _leaves(tree):
    out = []
    for n in tree:
        if n.get("children"):
            out.extend(_leaves(n["children"]))
        else:
            out.append(n["title"])
    return out


@pytest.mark.asyncio
async def test_empty_chapter_tree_gets_expanded_in_place():
    """children_json 为空的章节被就地展开成嵌套树."""
    chapter = FakeChapter("服务方案")
    adapter = FakeAdapter(sections=("门岗值守", "井口值守"))

    report = await expand_pending_chapter_trees(
        [chapter], requirements={}, ai_adapter=adapter, target_pages=2000)

    tree = json.loads(chapter.children_json)
    assert [n["title"] for n in tree] == ["门岗值守", "井口值守"]
    assert _leaves(tree) == ["门岗值守-1", "门岗值守-2", "井口值守-1", "井口值守-2"]
    assert [c["title"] for c in report["expanded"]] == ["服务方案"]


@pytest.mark.asyncio
async def test_user_refined_tree_is_left_alone():
    """用户已在 UI 里细化过的树不得被自动展开覆盖."""
    original = [{"title": "用户手工标题", "children": []}]
    chapter = FakeChapter("服务方案", children_json=json.dumps(original, ensure_ascii=False))
    adapter = FakeAdapter()

    report = await expand_pending_chapter_trees(
        [chapter], requirements={}, ai_adapter=adapter, target_pages=2000)

    assert json.loads(chapter.children_json) == original
    assert adapter.calls == 0, "已有子树的章节不应再调用 AI"
    assert report["expanded"] == []


@pytest.mark.asyncio
async def test_non_ai_chapters_are_skipped():
    """fixed_form / table 章节不参与展开."""
    chapters = [FakeChapter("投标函", chapter_type="fixed_form"),
                FakeChapter("开标一览表", chapter_type="table")]
    adapter = FakeAdapter()

    report = await expand_pending_chapter_trees(
        chapters, requirements={}, ai_adapter=adapter, target_pages=2000)

    assert adapter.calls == 0
    assert all(c.children_json == "" for c in chapters)
    assert report["expanded"] == []


@pytest.mark.asyncio
async def test_expansion_failure_keeps_fallback_and_does_not_raise():
    """展开失败不得抛异常，也不得写空树——保留 generate 既有的整章兜底."""
    chapter = FakeChapter("服务方案")

    report = await expand_pending_chapter_trees(
        [chapter], requirements={}, ai_adapter=FakeAdapter(broken=True),
        target_pages=2000)

    assert chapter.children_json == "", "失败时不得把空树写成「已细化」"
    assert report["expanded"] == []
    assert report["failed"] == ["服务方案"], "失败章节要如实上报给 SSE"


@pytest.mark.asyncio
async def test_rubric_reaches_the_expansion_prompts():
    """评分指标要进展开提示词——目录据此对齐评分点."""
    prompts = []

    class RecordingAdapter(FakeAdapter):
        async def chat_completion(self, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return await super().chat_completion(messages, **kwargs)

    chapter = FakeChapter("服务方案")
    rubric = {"items": [{
        "id": "item-04", "dimension": "技术部分",
        "name": "门岗、井口值守、游泳池安保管理综合方案", "points": 10,
        "kind": "content", "criteria": "工作思路、保障措施",
        "key_terms": ["门岗"],
    }]}

    await expand_pending_chapter_trees(
        [chapter], requirements={"scoring_rubric": rubric},
        ai_adapter=RecordingAdapter(), target_pages=2000)

    joined = "\n".join(prompts)
    assert "门岗、井口值守、游泳池安保管理综合方案" in joined, (
        "评分点未进入 generate 第一阶段的展开提示词"
    )
