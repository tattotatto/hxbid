"""宏曦标书 - 评分驱动自动修改 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json

import pytest

from app.services.scoring_autofix import (
    AutoFixError,
    apply_auto_fix,
    build_instruction,
    is_auto_fixable,
    locate_section,
)


TREE = json.dumps([
    {
        "title": "服务响应",
        "children": [
            {"title": "响应时间承诺", "content": "旧：接到通知后2小时内到场", "children": []},
            {"title": "协同支撑服务", "content": "旧：未提及协同支撑", "children": []},
        ],
    },
    {"title": "售后服务", "content": "旧：售后服务描述", "children": []},
], ensure_ascii=False)

# 已生成的整章正文：由上面的树组装而来，小节内容原样出现在其中。
# 自动修改是"替换其中一节"，所以这里必须含各节原文，否则定位不到。
CHAPTER_CONTENT = (
    "## 服务响应\n\n### 响应时间承诺\n\n旧：接到通知后2小时内到场"
    "\n\n### 协同支撑服务\n\n旧：未提及协同支撑"
    "\n\n## 售后服务\n\n旧：售后服务描述"
)

# 长小节：改写要复述整节，输出被 max_tokens 截断时会明显变短。
# 取真实出事那一节的量级（大红山「项目背景与招标需求总体解读」= 2969 字）。
LONG_LEAF = "旧：" + "本节服务流程说明。" * 330         # 2972 字
LONG_TREE = json.dumps([
    {"title": "服务响应", "children": [
        {"title": "响应流程", "content": LONG_LEAF, "children": []},
    ]},
], ensure_ascii=False)
LONG_CONTENT = "## 服务响应\n\n### 响应流程\n\n" + LONG_LEAF


class FakeAI:
    """记录收到的 prompt，返回固定改写结果。"""

    def __init__(self, reply: str = "新：协同支撑服务由公司调度中心统一调配"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class FailingAI:
    async def chat_completion(self, **kwargs):
        raise RuntimeError("模型超时")


# ---------------------------------------------------------------------------
# 是否显示「自动修改」按钮
# ---------------------------------------------------------------------------

class TestIsAutoFixable:
    def test_price_item_is_not_auto_fixable(self):
        assert is_auto_fixable("price", "把报价写清楚") is False

    def test_content_item_with_suggestion_is_auto_fixable(self):
        assert is_auto_fixable("content", "补充协同支撑服务描述") is True

    def test_quality_item_with_suggestion_is_auto_fixable(self):
        assert is_auto_fixable("quality", "增加针对性响应") is True

    def test_item_without_suggestion_is_not_auto_fixable(self):
        assert is_auto_fixable("content", "") is False

    def test_blank_suggestion_is_not_auto_fixable(self):
        assert is_auto_fixable("content", "   \n ") is False

    def test_missing_kind_is_auto_fixable_when_suggestion_present(self):
        # 老报告没有 kind 字段：不能因为缺字段就把按钮吞掉，price 才是唯一排除项
        assert is_auto_fixable("", "补充内容") is True

    def test_unscored_item_is_not_auto_fixable(self):
        # 判卷失败/未计分的行，suggestion 里装的是 "判卷失败：…" 这类错误文案，
        # 不是改进建议 —— 拿它当指令只会让 AI 去修一个不存在的失分点。
        assert is_auto_fixable(
            "content", "判卷失败：Expecting ',' delimiter: line 1 column 195", "unscored"
        ) is False

    def test_unscored_item_without_reason_is_not_auto_fixable(self):
        assert is_auto_fixable("content", "判卷未覆盖", "unscored") is False

    def test_scored_item_with_suggestion_is_auto_fixable(self):
        assert is_auto_fixable("content", "补充协同支撑服务描述", "partial") is True
        assert is_auto_fixable("quality", "增加针对性", "fail") is True


# ---------------------------------------------------------------------------
# 修改指令
# ---------------------------------------------------------------------------

class TestBuildInstruction:
    def test_instruction_carries_gap_and_suggestion(self):
        text = build_instruction(
            name="协同支撑服务",
            gap="未说明与招标人的协同机制",
            suggestion="补充调度中心统一调配流程",
        )
        assert "未说明与招标人的协同机制" in text
        assert "补充调度中心统一调配流程" in text

    def test_instruction_forbids_dropping_unrelated_content(self):
        text = build_instruction(name="服务响应", gap="", suggestion="补充响应时间")
        assert "不要删改" in text

    def test_instruction_falls_back_to_criteria_when_no_gap(self):
        text = build_instruction(name="服务响应", gap="", suggestion="", criteria="需承诺2小时到场")
        assert "需承诺2小时到场" in text

    def test_empty_everything_raises(self):
        with pytest.raises(AutoFixError):
            build_instruction(name="服务响应", gap="", suggestion="", criteria="")


# ---------------------------------------------------------------------------
# 目标定位（纯函数）
# ---------------------------------------------------------------------------

class TestLocateSection:
    def test_fixed_form_chapter_is_refused(self):
        path, err = locate_section(
            {"name": "投标函", "key_terms": ["投标函"]}, "fixed_form", TREE
        )
        assert path is None
        assert "固定格式" in err

    def test_table_chapter_is_refused(self):
        path, err = locate_section(
            {"name": "开标一览表", "key_terms": ["开标"]}, "table", TREE
        )
        assert path is None
        assert "固定格式" in err

    def test_chapter_without_tree_targets_whole_chapter(self):
        path, err = locate_section(
            {"name": "服务方案", "key_terms": ["服务"]}, "ai_generated", "[]"
        )
        assert path == []
        assert err == ""

    def test_invalid_children_json_targets_whole_chapter(self):
        path, err = locate_section(
            {"name": "服务方案", "key_terms": ["服务"]}, "ai_generated", "not-json"
        )
        assert path == []
        assert err == ""

    def test_leaf_matching_key_terms_is_picked(self):
        path, err = locate_section(
            {"name": "协同支撑", "key_terms": ["协同", "支撑"]}, "ai_generated", TREE
        )
        assert path == ["服务响应", "协同支撑服务"]
        assert err == ""

    def test_nested_one_level_leaf_is_picked(self):
        path, _ = locate_section(
            {"name": "售后服务", "key_terms": ["售后"]}, "ai_generated", TREE
        )
        assert path == ["售后服务"]

    def test_best_scoring_leaf_wins_over_weaker_match(self):
        # 「响应时间承诺」命中 1 个词，「协同支撑服务」命中 2 个 -> 选后者
        path, _ = locate_section(
            {"name": "服务", "key_terms": ["协同", "支撑", "时间"]}, "ai_generated", TREE
        )
        assert path == ["服务响应", "协同支撑服务"]

    def test_item_name_matches_when_key_terms_miss(self):
        path, err = locate_section(
            {"name": "协同支撑服务能力", "key_terms": ["毫不相关的词"]}, "ai_generated", TREE
        )
        assert path == ["服务响应", "协同支撑服务"]
        assert err == ""

    def test_no_match_is_refused_not_whole_chapter(self):
        path, err = locate_section(
            {"name": "投标报价汇总", "key_terms": ["报价"]}, "ai_generated", TREE
        )
        assert path is None
        assert "未找到" in err

    def test_container_node_is_never_the_target(self):
        # 「服务响应」在树里是容器节点，标题也命中了关键词，但不能被当作改写单元，
        # 只能落到它的叶子「响应时间承诺」上（路径长度为 2 即证明选中的是叶子）
        path, err = locate_section(
            {"name": "服务响应", "key_terms": ["服务响应", "响应时间"]}, "ai_generated", TREE
        )
        assert err == ""
        assert path == ["服务响应", "响应时间承诺"]

    def test_dimension_matching_only_a_container_is_refused(self):
        # 评分项只对得上容器、对不上任何叶子 —— 拒绝，而不是整章重写
        path, err = locate_section(
            {"name": "服务响应", "key_terms": ["服务响应"]}, "ai_generated", TREE
        )
        assert path is None
        assert "未找到" in err


# ---------------------------------------------------------------------------
# 执行改写
# ---------------------------------------------------------------------------

class TestApplyAutoFix:
    @pytest.mark.asyncio
    async def test_leaf_is_rewritten_and_tree_updated(self):
        ai = FakeAI()
        result = await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=TREE,
            chapter_content=CHAPTER_CONTENT,
            report_item={"name": "协同支撑", "gap": "未说明协同机制", "suggestion": "补充调度流程"},
            rubric_item={"key_terms": ["协同", "支撑"]},
            ai_adapter=ai,
        )
        assert result["section_path"] == ["服务响应", "协同支撑服务"]
        assert result["modified_content"] == ai.reply
        leaf = json.loads(result["children_json"])[0]["children"][1]
        assert leaf["content"] == ai.reply

    @pytest.mark.asyncio
    async def test_final_content_replaces_only_the_target_leaf(self):
        ai = FakeAI()
        result = await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=TREE,
            chapter_content=CHAPTER_CONTENT,
            report_item={"name": "协同支撑", "gap": "", "suggestion": "补充调度流程"},
            rubric_item={"key_terms": ["协同"]},
            ai_adapter=ai,
        )
        final = result["final_content"]
        assert ai.reply in final
        # 只替换目标节：同级节、其他章节目录与标题层级原样保留
        assert "旧：接到通知后2小时内到场" in final
        assert "旧：售后服务描述" in final
        assert "### 协同支撑服务" in final
        assert "旧：未提及协同支撑" not in final
        assert final.count("## 服务响应") == 1

    @pytest.mark.asyncio
    async def test_leaf_content_absent_from_chapter_is_refused(self):
        # 章节正文与小节树对不上（人工删过）——宁可拒绝，也不要整章重建丢内容
        with pytest.raises(AutoFixError) as ei:
            await apply_auto_fix(
                chapter_title="服务方案",
                chapter_type="ai_generated",
                children_json=TREE,
                chapter_content="与树完全对不上的正文",
                report_item={"name": "协同支撑", "gap": "", "suggestion": "补充调度流程"},
                rubric_item={"key_terms": ["协同"]},
                ai_adapter=FakeAI(),
            )
        assert "定位" in str(ei.value)

    @pytest.mark.asyncio
    async def test_whole_chapter_rewrite_when_no_tree(self):
        ai = FakeAI("改写后的整章正文")
        result = await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json="[]",
            chapter_content="旧整章正文",
            report_item={"name": "服务方案", "gap": "", "suggestion": "补充实施细节"},
            rubric_item={"key_terms": []},
            ai_adapter=ai,
        )
        assert result["section_path"] == []
        assert result["final_content"] == "改写后的整章正文"
        assert result["children_json"] == "[]"

    @pytest.mark.asyncio
    async def test_fixed_form_chapter_is_refused(self):
        with pytest.raises(AutoFixError) as ei:
            await apply_auto_fix(
                chapter_title="投标函",
                chapter_type="fixed_form",
                children_json=TREE,
                chapter_content="招标原文",
                report_item={"name": "投标函", "gap": "", "suggestion": "补充内容"},
                rubric_item={"key_terms": ["投标函"]},
                ai_adapter=FakeAI(),
            )
        assert "固定格式" in str(ei.value)

    @pytest.mark.asyncio
    async def test_ai_failure_raises_autofix_error(self):
        with pytest.raises(AutoFixError):
            await apply_auto_fix(
                chapter_title="服务方案",
                chapter_type="ai_generated",
                children_json=TREE,
                chapter_content=CHAPTER_CONTENT,
                report_item={"name": "协同支撑", "gap": "", "suggestion": "补充调度流程"},
                rubric_item={"key_terms": ["协同"]},
                ai_adapter=FailingAI(),
            )

    @pytest.mark.asyncio
    async def test_instruction_reaches_the_model(self):
        ai = FakeAI()
        await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=TREE,
            chapter_content=CHAPTER_CONTENT,
            report_item={
                "name": "协同支撑",
                "gap": "未说明协同机制",
                "suggestion": "补充调度中心统一调配流程",
            },
            rubric_item={"key_terms": ["协同"]},
            ai_adapter=ai,
        )
        prompt = ai.calls[0]["messages"][-1]["content"]
        assert "未说明协同机制" in prompt
        assert "补充调度中心统一调配流程" in prompt


class TestRewriteMustNotLoseContent:
    """改写是「补强」，把完整原文换成更短的版本一定是净损失.

    两种成因都在真实模型上实测见过：
    - 模型自行压缩：2969 字的节 → 805 字（摘要写「精简了约 2164 字」）；
    - 输出被 max_tokens 截断：在表格中途断掉，后面两节整段消失（2969 → 2361）。

    ai_adapter 对「content 非空但 finish_reason=length」**有意放行**（见
    ai_adapter.py 227 行注释：那是生成路径上大红山 70% 叶子的常态，半篇正文
    好过没有）。对生成这个取舍成立；对改写则相反——原文本就完整，换成半篇
    是纯粹的损失。所以这道防线只能设在改写这一层。
    """

    @pytest.mark.asyncio
    async def test_much_shorter_rewrite_is_refused(self):
        ai = FakeAI("旧：本节服务流程说明。")     # 残篇（截断 / 压缩）
        with pytest.raises(AutoFixError) as ei:
            await apply_auto_fix(
                chapter_title="服务方案",
                chapter_type="ai_generated",
                children_json=LONG_TREE,
                chapter_content=LONG_CONTENT,
                report_item={"name": "响应流程", "gap": "缺时限", "suggestion": "补充响应时限"},
                rubric_item={"key_terms": ["响应流程"]},
                ai_adapter=ai,
            )
        assert "截断" in str(ei.value)

    @pytest.mark.asyncio
    async def test_equal_length_rewrite_is_accepted(self):
        ai = FakeAI("新：" + "本节服务流程说明。" * 330)
        result = await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=LONG_TREE,
            chapter_content=LONG_CONTENT,
            report_item={"name": "响应流程", "gap": "缺时限", "suggestion": "补充响应时限"},
            rubric_item={"key_terms": ["响应流程"]},
            ai_adapter=ai,
        )
        assert result["modified_content"] == ai.reply

    @pytest.mark.asyncio
    async def test_tiny_section_is_exempt_from_the_ratio_check(self):
        # 9 字的小节，比例是噪声：模型正常补一句也可能"短了一半"
        ai = FakeAI("旧：无")
        result = await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=TREE,
            chapter_content=CHAPTER_CONTENT,
            report_item={"name": "协同支撑", "gap": "", "suggestion": "补充调度流程"},
            rubric_item={"key_terms": ["协同"]},
            ai_adapter=ai,
        )
        assert result["modified_content"] == "旧：无"

    @pytest.mark.asyncio
    async def test_rewrite_budget_covers_the_section(self):
        # 改写要复述整节 → 预算得跟着正文长度走，不能是写死的 4096。
        # 上限不是预留（没写满不计费），所以必须留出远超正文本身的余量：
        # 实测给 5758 时这节仍然被截断。
        ai = FakeAI("新：" + "本节服务流程说明。" * 330)
        await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=LONG_TREE,
            chapter_content=LONG_CONTENT,
            report_item={"name": "响应流程", "gap": "缺时限", "suggestion": "补充响应时限"},
            rubric_item={"key_terms": ["响应流程"]},
            ai_adapter=ai,
        )
        budget = ai.calls[0]["max_tokens"]
        assert budget > 4096
        assert budget >= len(LONG_LEAF) / 2      # 远超正文自身的 token 量

    @pytest.mark.asyncio
    async def test_truncated_output_is_refused_with_a_readable_reason(self):
        """适配器用 finish_reason 判定截断并抛出 → 服务层拒绝，小节保持原样.

        这条链路才是确定的：补写内容后总长可能超过原文，比例看不出截断
        （实测尾句被切掉、末段整段消失，而总长反而变长）。
        """

        class TruncatingAI:
            async def chat_completion(self, **kwargs):
                from app.services.ai_adapter import AIOutputTruncatedError

                raise AIOutputTruncatedError(
                    "AI output truncated (finish_reason=length, max_tokens=5758)"
                )

        with pytest.raises(AutoFixError) as ei:
            await apply_auto_fix(
                chapter_title="服务方案",
                chapter_type="ai_generated",
                children_json=LONG_TREE,
                chapter_content=LONG_CONTENT,
                report_item={"name": "响应流程", "gap": "缺时限", "suggestion": "补充响应时限"},
                rubric_item={"key_terms": ["响应流程"]},
                ai_adapter=TruncatingAI(),
            )
        assert "截断" in str(ei.value)

    @pytest.mark.asyncio
    async def test_short_section_keeps_the_floor_budget(self):
        # 短小节不该把预算压到 4096 以下
        ai = FakeAI("新：协同支撑服务由公司调度中心统一调配")
        await apply_auto_fix(
            chapter_title="服务方案",
            chapter_type="ai_generated",
            children_json=TREE,
            chapter_content=CHAPTER_CONTENT,
            report_item={"name": "协同支撑", "gap": "", "suggestion": "补充调度流程"},
            rubric_item={"key_terms": ["协同"]},
            ai_adapter=ai,
        )
        assert ai.calls[0]["max_tokens"] >= 4096
