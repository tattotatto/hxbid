"""叶子预算与篇幅目标解耦测试.

背景（2026-09-13 大红山「服务方案」实测）：

同一个叶子、同一个 prompt，只改 ``max_tokens``：

| max_tokens | finish_reason | reasoning | 正文 token | 正文字数 |
|---|---|---|---|---|
| 3,525（当时生产实发） | length | 2,823 | 702 | 1,166（截断） |
| 8,192 | length | 8,192 | 0 | 0（= empty_content） |
| 16,384 | stop | 3,625 | 7,965 | 13,139（完整） |

推理量会随预算膨胀、最高吃满 100%，撞上自然上限后才开始写正文。所以
``max_tokens`` 必须显著高于推理上限，而该值是「给推理留的空间」，不是
「让模型写多长」。

但旧实现把两者绑死了：``get_section_length_guidance`` 拿
``max_tokens × CHARS_PER_TOKEN`` 反推「目标篇幅约 X 字」写进 prompt，
预算同时被当成篇幅指标 —— 想给推理留空间就得同时叫模型写更多
（实测同一叶子 16,384 档写了 11 倍字数）。

修法：``max_tokens`` 只做 headroom（恒为 ``GENERATION_LEAF_MAX_TOKENS``），
篇幅目标另走路传给 prompt。
"""

import pytest

from app.config import settings
from app.services.token_budget import (
    assign_target_budgets,
    get_section_length_guidance,
)


def _tasks(n: int, hint: str = "medium", depth: int = 2) -> list[dict]:
    return [{"token_budget_hint": hint, "depth": depth} for _ in range(n)]


class TestMaxTokensIsPureHeadroom:
    """max_tokens 只保证「推理写完还有地方写正文」，不承载篇幅语义."""

    def test_max_tokens_is_max_regardless_of_page_target(self):
        """页数目标再小，单叶预算也必须是满额 headroom，不能被缩放压小.

        旧实现在 target_pages=450 时把每叶压到约 880 token —— 推理一上来
        就吃满，正文一个字都写不出来。
        """
        tasks = _tasks(180)
        assign_target_budgets(tasks, target_pages=450)
        assert {t["max_tokens"] for t in tasks} == {settings.GENERATION_LEAF_MAX_TOKENS}

    def test_max_tokens_is_max_regardless_of_hint(self):
        """hint 只影响篇幅目标，不该再影响 headroom 大小."""
        tasks = _tasks(10, hint="large") + _tasks(10, hint="medium")
        assign_target_budgets(tasks, target_pages=450)
        assert {t["max_tokens"] for t in tasks} == {settings.GENERATION_LEAF_MAX_TOKENS}

    def test_headroom_covers_measured_reasoning_ceiling(self):
        """headroom 必须高于实测推理上限（这片约 3,600），否则推理吃满即空返回.

        实测 8,192 不够（reasoning 吃满 8,192、正文 0 字），16,384 够。
        """
        tasks = _tasks(1)
        assign_target_budgets(tasks, target_pages=450)
        assert tasks[0]["max_tokens"] > 8192


class TestTargetCharsCarriesLength:
    """篇幅目标改由 target_chars 承载."""

    def test_total_target_chars_tracks_page_target(self):
        tasks = _tasks(180)
        assign_target_budgets(tasks, target_pages=450)
        total = sum(t["target_chars"] for t in tasks)
        expected = 450 * settings.GENERATION_CHARS_PER_PAGE
        assert total == pytest.approx(expected, rel=0.02)

    def test_calibrates_to_winning_bid_scale(self):
        """450 页 × 550 字/页 ÷ 180 叶 ≈ 1,375 字/叶.

        对标中标标书「十四、服务方案」= 566 页 / 246,696 字 / 137 标题。
        """
        tasks = _tasks(180)
        assign_target_budgets(tasks, target_pages=450)
        per_leaf = sum(t["target_chars"] for t in tasks) / 180
        assert 1200 < per_leaf < 1600

    def test_hint_still_weights_the_share(self):
        """大节仍该比小节写得多 —— 加权按 hint 保留."""
        tasks = _tasks(1, hint="large") + _tasks(1, hint="medium")
        assign_target_budgets(tasks, target_pages=450)
        assert tasks[0]["target_chars"] > tasks[1]["target_chars"]

    def test_unknown_hint_falls_back_to_medium(self):
        """``high`` 不是合法类别（只有 tiny/small/medium/large/xlarge）.

        旧实现在 ``hint_to_tokens`` 里静默回落 8192 —— 大红山那 47 片
        「high」叶子拿的其实是 medium 基数。此处锁定该回落行为，避免
        悄悄变成 0 或抛异常。
        """
        tasks = _tasks(1, hint="high") + _tasks(1, hint="medium")
        assign_target_budgets(tasks, target_pages=450)
        assert tasks[0]["target_chars"] == pytest.approx(tasks[1]["target_chars"])

    def test_empty_task_list_is_safe(self):
        assert assign_target_budgets([], target_pages=450) == []


class TestLengthGuidanceUsesTargetChars:
    """prompt 里的【篇幅要求】必须来自 target_chars，不再从 max_tokens 反推."""

    def test_uses_target_chars_when_given(self):
        text = get_section_length_guidance("某节", max_tokens=16384, target_chars=1400)
        assert "1,400" in text
        # 16384 × 1.6 = 26,214 不该出现
        assert "26,214" not in text

    def test_falls_back_to_max_tokens_when_target_chars_absent(self):
        """不留 target_chars 的老调用方行为不变（向后兼容）."""
        text = get_section_length_guidance("某节", max_tokens=8192)
        assert "13,107" in text

    def test_target_chars_beats_headroom(self):
        """两个值都给时以 target_chars 为准 —— 这正是解耦的意义."""
        a = get_section_length_guidance("某节", max_tokens=16384, target_chars=1000)
        b = get_section_length_guidance("某节", max_tokens=16384, target_chars=3000)
        assert a != b


class TestPromptCarriesTargetChars:
    """target_chars 必须一路透传到 prompt 的【篇幅要求】."""

    def _prompt(self, **kw):
        from app.services.subsection_generator import _build_progressive_prompt

        return _build_progressive_prompt(
            section_title="项目背景、服务范围与需求理解",
            section_path=["服务方案", "服务总体理解与整体实施方案",
                          "项目背景、服务范围与需求理解"],
            depth=2,
            requirements={"project_name": "某项目"},
            max_tokens=kw.pop("max_tokens", 16384),
            **kw,
        )

    def test_prompt_uses_target_chars(self):
        text = self._prompt(target_chars=1400)
        assert "1,400" in text
        # headroom 16384 × 1.6 = 26,214 不该被当成篇幅写进 prompt
        assert "26,214" not in text

    def test_prompt_falls_back_without_target_chars(self):
        assert "26,214" in self._prompt()

    def test_prompt_is_shorter_target_for_smaller_target_chars(self):
        assert "1,400" in self._prompt(target_chars=1400)
        assert "3,000" in self._prompt(target_chars=3000)
