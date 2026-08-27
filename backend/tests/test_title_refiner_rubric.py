"""生成期感知：结构化评分指标注入标题细化提示 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json

import pytest

from app.services.scoring_rubric import rubric_context_lines
from app.services.title_refiner import refine_chapter_titles


def test_scoring_context_filters_dimension_items():
    rubric = {"items": [
        {"name": "服务方案", "points": 15, "kind": "content", "criteria": "完整", "dimension": "技术部分"},
        {"name": "团队配置", "points": 10, "kind": "content", "criteria": "齐全", "dimension": "人员部分"},
    ]}
    lines = rubric_context_lines(rubric, "技术部分")
    joined = "\n".join(lines)
    assert "服务方案" in joined and "团队配置" not in joined


def test_empty_rubric_renders_no_lines():
    assert rubric_context_lines({"items": []}, "") == []
    assert rubric_context_lines({}, "") == []


class _FakeAdapter:
    """捕获 user_prompt 的桩适配器，返回空 children 树."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    async def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps({"children": []})


def _user_prompt(calls, index=0):
    return next(m["content"] for m in calls[index] if m["role"] == "user")


@pytest.mark.asyncio
async def test_title_refiner_uses_rubric_context_lines_when_rubric_present():
    """requirements 带 scoring_rubric 时，提示用结构化指标行替换自由文本."""
    adapter = _FakeAdapter()
    rubric = {"status": "found", "items": [
        {"name": "服务方案", "points": 15, "kind": "content", "criteria": "完整且针对本项目", "dimension": "技术部分"},
        {"name": "服务承诺", "points": 10, "kind": "quality", "criteria": "切实可行", "dimension": "技术部分"},
        {"name": "报价合理", "points": 30, "kind": "price", "criteria": "低于成本价除外", "dimension": "价格部分"},
    ]}
    await refine_chapter_titles(
        chapter_title="服务方案",
        chapter_meta={"scoring_context": "技术部分"},
        requirements={"evaluation_criteria": "自由文本旧分支", "scoring_rubric": rubric},
        ai_adapter=adapter,
    )
    prompt = _user_prompt(adapter.calls)
    assert "评标要求·内容项需在正文中逐项覆盖" in prompt
    assert "服务方案（15分）：完整且针对本项目" in prompt
    assert "评分要点·质量项作为写作指导" in prompt
    assert "服务承诺（10分）：切实可行" in prompt
    # price 不进入提示；自由文本旧分支被替换
    assert "报价合理" not in prompt
    assert "评标标准：自由文本旧分支" not in prompt


@pytest.mark.asyncio
async def test_title_refiner_falls_back_to_evaluation_criteria_without_rubric():
    """无 rubric（或 items 为空）时沿用旧自由文本分支，不破坏现有生成."""
    adapter = _FakeAdapter()
    await refine_chapter_titles(
        chapter_title="服务方案",
        chapter_meta={"scoring_context": "技术部分"},
        requirements={"evaluation_criteria": "方案完整；针对性"},
        ai_adapter=adapter,
    )
    prompt = _user_prompt(adapter.calls)
    assert "评标标准：方案完整；针对性" in prompt

    adapter2 = _FakeAdapter()
    await refine_chapter_titles(
        chapter_title="服务方案",
        chapter_meta={},
        requirements={"evaluation_criteria": "方案完整；针对性", "scoring_rubric": {"status": "none", "items": []}},
        ai_adapter=adapter2,
    )
    prompt2 = _user_prompt(adapter2.calls)
    assert "评标标准：方案完整；针对性" in prompt2
    assert "评标要求·内容项需在正文中逐项覆盖" not in prompt2