"""评分指标服务（评标办法结构化）单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import pytest

from app.services.scoring_rubric import (
    CONTENT_KINDS,
    normalize_rubric,
    validate_rubric,
    rubric_context_lines,
)


class TestNormalizeRubric:
    def test_none_input_defaults_none(self):
        rubric = normalize_rubric(None)
        assert rubric["status"] == "none"
        assert rubric["items"] == []

    def test_cleans_key_terms_and_kind(self):
        raw = {
            "status": "found",
            "max_total": 45,
            "items": [
                {
                    "id": "tech-01", "dimension": "技术部分", "name": "服务方案",
                    "points": 15, "kind": "content", "criteria": "完整、针对本项目",
                    "key_terms": ["服务方案", "针对性", "x"],
                },
                {
                    "name": "历史业绩", "points": 10, "kind": "BAD_KIND",
                    "key_terms": "业绩，类似项目",
                },
            ],
        }
        rubric = normalize_rubric(raw)
        assert len(rubric["items"]) == 2
        # 单字符 key_term 剔除；字符串 key_terms 拆词
        assert rubric["items"][0]["key_terms"] == ["服务方案", "针对性"]
        # 未知 kind 归一为 quality（不补节点、参与评分）
        assert rubric["items"][1]["kind"] == "quality"
        assert rubric["items"][1]["key_terms"] == ["业绩", "类似项目"]
        assert rubric["items"][1]["id"] == "item-01"

    def test_content_kinds_are_content_driven(self):
        assert CONTENT_KINDS == {"content", "cert", "personnel", "performance"}


class TestValidateRubric:
    def test_points_sum_deviation_warns(self):
        rubric = {"max_total": 100, "items": [
            {"id": "a", "name": "甲", "criteria": "…", "points": 40},
            {"id": "b", "name": "乙", "criteria": "…", "points": 40},
        ]}
        problems = validate_rubric(rubric)
        assert any("偏差" in p for p in problems)

    def test_empty_items_warns(self):
        problems = validate_rubric({"max_total": 100, "items": []})
        assert any("为空" in p for p in problems)

    def test_clean_rubric_passes(self):
        problems = validate_rubric({"max_total": 100, "items": [
            {"id": "a", "name": "甲", "criteria": "…", "points": 50},
            {"id": "b", "name": "乙", "criteria": "…", "points": 50},
        ]})
        assert problems == []


class TestRubricContextLines:
    def test_renders_content_and_quality_lines(self):
        rubric = {"items": [
            {"name": "服务方案", "points": 15, "kind": "content", "criteria": "完整", "dimension": "技术部分"},
            {"name": "针对性", "points": 5, "kind": "quality", "criteria": "贴合项目实际", "dimension": "技术部分"},
            {"name": "报价合理性", "points": 30, "kind": "price", "criteria": "合理", "dimension": "报价"},
        ]}
        lines = rubric_context_lines(rubric, "")
        joined = "\n".join(lines)
        assert "服务方案" in joined and "报价合理性" not in joined  # price 不进生成提示
        assert "质量项作为写作指导" in joined


@pytest.mark.asyncio
async def test_extract_rubric_failure_degrades_none():
    from app.services.scoring_rubric import extract_rubric

    class BoomingAdapter:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("AI returned empty content")

    rubric = await extract_rubric("评标办法正文……", BoomingAdapter())
    assert rubric["status"] == "none"
    assert rubric["items"] == []
    assert "评标办法正文" in rubric["raw_text"]