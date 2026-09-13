"""标题展开分治（``expand_chapter_titles``）测试.

背景（2026-09-13 玉溪大红山招标文件诊断）：

``title_refiner`` 原来是**单次调用**出整棵树。目标 2000 页时
``scale = max(1.0, min(4.0, 2000/800)) = 2.5``，提示词要求每章 75-175 个叶子，
模型输出 JSON 到 23,460 字符撞 ``max_tokens=16384`` 被截断 →
``json.loads`` 抛 ``JSONDecodeError`` → 而旧实现的兜底是::

    except (json.JSONDecodeError, Exception) as exc:
        logger.error(...)
        return []

把失败**吞成空树**。端点随后把 ``children_json = "[]"`` 当成功存库、回一个
``leaf_count=0``，前端显示「细化成功」。

后果：``ai_pipeline`` 见 ``children_json`` 为空，走「整章当唯一叶子」的 fallback
（ai_pipeline.py:1442-1462），于是「服务方案」永远只有一个叶子 —— 页数上限被钉死
在 13-25 个叶子，2000 页目标结构上不可达（实测产出 216 页 / 119,013 字，而中标
标书 1194 页、服务方案单章 224,460 字）。

修法：两级分治 —— 先出 8-16 个二级标题（小 JSON），再逐个二级展开叶子，
每次调用的叶子数封顶在安全区内（实测 60-68 个叶子 ≈ 6,700 字符，稳定成功）。
"""

import pytest

from app.services.title_refiner import expand_chapter_titles

MAX_LEAVES_PER_CALL = 60


# ---------------------------------------------------------------------------
# 假 adapter
# ---------------------------------------------------------------------------

def _requested_leaf_counts(prompt: str) -> list[int]:
    """从提示词里解析出「总叶子节点数 N 个」（取区间上限）。"""
    import re

    out = []
    for m in re.finditer(r"总叶子节点数\s*(\d+)(?:\s*-\s*(\d+))?\s*个", prompt):
        out.append(int(m.group(2) or m.group(1)))
    return out


class FakeAdapter:
    """按提示词形态回可预测的 JSON，并记录每次请求的叶子规模。"""

    def __init__(self, sections=("二级甲", "二级乙"), leaves_per_section=3,
                 break_sections=()):
        self.sections = list(sections)
        self.leaves_per_section = leaves_per_section
        self.break_sections = set(break_sections)
        self.prompts: list[str] = []
        self.calls = 0

    async def chat_completion(self, messages, **kwargs):
        import json

        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        self.calls += 1

        # 第一遍：只出二级标题
        if "只列二级标题" in prompt:
            return json.dumps({
                "children": [
                    {"title": s, "token_budget_hint": "large", "children": []}
                    for s in self.sections
                ]
            }, ensure_ascii=False)

        # 第二遍：把某个二级标题展开为叶子
        for s in self.break_sections:
            if s in prompt:
                return '{"children": [{"title": "截断的坏 JSON"'  # 模拟 max_tokens 截断
        target = next((s for s in self.sections if s in prompt), "叶子")
        return json.dumps({
            "children": [
                {"title": f"{target}-{i + 1}", "token_budget_hint": "medium",
                 "children": []}
                for i in range(self.leaves_per_section)
            ]
        }, ensure_ascii=False)


RUBRIC = {
    "items": [
        {"id": "item-04", "dimension": "技术部分",
         "name": "门岗、井口值守、游泳池安保管理综合方案", "points": 10,
         "kind": "content", "criteria": "工作思路、工作措施、组织实施、保障措施",
         "key_terms": ["门岗", "井口值守"]},
    ]
}


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expands_into_nested_tree():
    """两级分治的结果要组装成正确嵌套的树."""
    adapter = FakeAdapter(sections=("二级甲", "二级乙"), leaves_per_section=3)

    tree = await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={}, requirements={},
        ai_adapter=adapter, target_pages=2000,
    )

    assert [n["title"] for n in tree] == ["二级甲", "二级乙"]
    assert [c["title"] for c in tree[0]["children"]] == [
        "二级甲-1", "二级甲-2", "二级甲-3",
    ]
    assert tree[1]["children"], "第二节也要展开"


@pytest.mark.asyncio
async def test_no_single_call_asks_for_more_than_the_cap():
    """任何一次调用请求的叶子数都不得超出安全上限.

    这是本条缺陷的核心：单次请求 75-175 个叶子会把输出顶到 max_tokens
    之外，JSON 被截断。
    """
    adapter = FakeAdapter(sections=tuple(f"二级{i}" for i in range(12)),
                          leaves_per_section=5)

    await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={}, requirements={},
        ai_adapter=adapter, target_pages=2000,
    )

    requested = [n for p in adapter.prompts for n in _requested_leaf_counts(p)]
    assert requested, "提示词里应写明请求的叶子数"
    assert max(requested) <= MAX_LEAVES_PER_CALL, (
        f"有调用请求了 {max(requested)} 个叶子，超出安全上限 {MAX_LEAVES_PER_CALL}"
    )


@pytest.mark.asyncio
async def test_total_leaves_scale_with_target_pages():
    """目标页数越大，请求的总叶子数越多（展开力度随目标放大）."""
    small = FakeAdapter(sections=("甲",))
    big = FakeAdapter(sections=("甲",))

    await expand_chapter_titles("服务方案", {}, {}, small, target_pages=400)
    await expand_chapter_titles("服务方案", {}, {}, big, target_pages=2000)

    def total(a):
        return sum(n for p in a.prompts for n in _requested_leaf_counts(p))

    assert total(big) > total(small), (
        f"2000 页应比 400 页要求更多叶子: {total(big)} vs {total(small)}"
    )


# ---------------------------------------------------------------------------
# 失败处理：不得吞成空树
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_section_call_degrades_to_that_section_as_leaf():
    """某个二级标题的展开失败时，该节降级为叶子——不静默丢章节."""
    adapter = FakeAdapter(sections=("二级甲", "二级乙"), leaves_per_section=3,
                          break_sections=("二级乙",))

    tree = await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={}, requirements={},
        ai_adapter=adapter, target_pages=2000,
    )

    assert [n["title"] for n in tree] == ["二级甲", "二级乙"], "失败的节不得丢失"
    assert tree[0]["children"], "成功的节照常展开"
    assert not tree[1].get("children"), "失败的节降级为叶子，仍会被撰写"


@pytest.mark.asyncio
async def test_pass_two_is_parallel_but_bounded():
    """第二遍必须并行但并发有上限.

    串行时「服务方案」16 个二级标题 × 约 40 秒 = 8-11 分钟，全落在生成
    接口的 SSE 流里；而并发不设限会把推理服务打满。
    """
    import asyncio

    class CountingAdapter(FakeAdapter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.inflight = 0
            self.peak = 0

        async def chat_completion(self, messages, **kwargs):
            if "只列二级标题" in messages[-1]["content"]:
                return await super().chat_completion(messages, **kwargs)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            try:
                await asyncio.sleep(0.01)  # 让并发真正叠加
                return await super().chat_completion(messages, **kwargs)
            finally:
                self.inflight -= 1

    adapter = CountingAdapter(sections=tuple(f"二级{i}" for i in range(10)),
                              leaves_per_section=3)

    await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={}, requirements={},
        ai_adapter=adapter, target_pages=2000,
    )

    from app.services.title_refiner import MAX_CONCURRENCY

    assert adapter.peak > 1, "第二遍应当并行，实际是串行"
    assert 1 < MAX_CONCURRENCY <= 8, f"并发上限取值不合理: {MAX_CONCURRENCY}"
    assert adapter.peak <= MAX_CONCURRENCY, (
        f"并发峰值 {adapter.peak} 超出上限 {MAX_CONCURRENCY}"
    )


@pytest.mark.asyncio
async def test_total_failure_returns_empty_and_does_not_raise():
    """整体失败返回空树且不抛——调用方据此保留「整章一叶子」的既有兜底."""
    class DeadAdapter:
        async def chat_completion(self, messages, **kwargs):
            return "not json at all"

    tree = await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={}, requirements={},
        ai_adapter=DeadAdapter(), target_pages=2000,
    )

    assert tree == []


# ---------------------------------------------------------------------------
# 评分指标必须进提示词（Fix 1 打通后的链路回归）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scoring_points_reach_the_prompt():
    """结构化评分指标要喂进展开提示词——目录据此对齐评分点.

    2026-09-13 修复：评标办法章节定位被正文里「投标函」二字误截断，
    rubric.items 恒为空，评分点驱动的目录补全与标题展开整条链路是死的。
    """
    adapter = FakeAdapter(sections=("甲",))

    await expand_chapter_titles(
        chapter_title="服务方案", chapter_meta={},
        requirements={"scoring_rubric": RUBRIC}, ai_adapter=adapter,
        target_pages=2000,
    )

    joined = "\n".join(adapter.prompts)
    assert "门岗、井口值守、游泳池安保管理综合方案" in joined, (
        "评分点未进入标题展开提示词"
    )
    assert "10分" in joined.replace(" ", ""), "分值也应带入"
