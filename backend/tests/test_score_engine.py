"""自我评分汇总（Feature B 纯函数部分）单元测试.

判定阈值与折算规则见 spec §4.2：
- points_obtained ≥ 90%×points_total → pass；≥60% → partial；否则 fail
- 最终分数 = total / scored_total（unscored 项按其满分剔除折算）
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json

import pytest

from app.services.score_engine import compute_report, _match_dimensions


def _rubric(items=None):
    return {
        "status": "found",
        "method_name": "综合评分法",
        "max_total": 45,
        "items": items or [
            {"id": "t1", "dimension": "技术部分", "name": "服务方案", "kind": "content", "points": 15, "criteria": "…", "key_terms": ["服务方案"]},
            {"id": "t2", "dimension": "技术部分", "name": "针对性", "kind": "quality", "points": 5, "criteria": "…", "key_terms": ["针对性"]},
            {"id": "p1", "dimension": "报价", "name": "报价合理性", "kind": "price", "points": 30, "criteria": "…", "key_terms": ["报价"]},
        ],
    }


def _g(nid, obtained, status="graded", evidence="5.2 服务方案（P18）"):
    return {"id": nid, "name": "", "dimension": "", "points_obtained": obtained,
            "status": status, "evidence": evidence, "gap": "", "suggestion": ""}

class TestComputeReport:
    def test_scored_and_unscored_totals(self):
        report = compute_report(_rubric(), [
            _g("t1", 13),           # graded
            _g("t2", 5),            # graded
            _g("p1", 0, status="unscored", evidence="未含报价"),  # price 无内容
        ])
        assert report["total"] == 18
        assert report["scored_total"] == 20        # 报价 30 分剔除
        assert report["max_total"] == 45
        # 注记按条目名列示（"未含报价" 以 evidence 形式保留在条目上）
        assert "报价合理性" in report["unscored_note"]
        assert report["items"][2]["evidence"] == "未含报价"
        assert report["items"][2]["status"] == "unscored"

    def test_status_thresholds(self):
        report = compute_report(_rubric(), [
            _g("t1", 15),   # 100% → pass
            _g("t2", 3),    # 60% → partial
            _g("p1", 5, status="unscored"),  # unscored 不参与阈值
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1"]["status"] == "pass"
        assert by_id["t2"]["status"] == "partial"

    def test_overscore_clamped_with_warning(self):
        report = compute_report(_rubric(), [
            _g("t1", 99),   # 超过满分 15 → 钳制
            _g("t2", 0, status="unscored"),
            _g("p1", 0, status="unscored"),
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1"]["points_obtained"] == 15
        assert any("超出满分" in w for w in report["warnings"])

    def test_no_unscored_all_graded_totals(self):
        report = compute_report(_rubric(), [_g("t1", 15), _g("t2", 5), _g("p1", 30)])
        assert report["scored_total"] == 50     # = 参与计分项满分和 15+5+30（本 rubric 指标分和 50 ≠ max_total 字段 45）
        assert report["total"] == 50     # 这里 total 超过 max_total 属正常（判卷结果）
        assert report["unscored_note"] == ""

    def test_no_unscored_scored_equals_max(self):
        # §4.2：无 unscored 时 scored_total == max_total（items 加总 == max_total）
        rubric = _rubric(items=[
            {"id": "t1", "dimension": "技术部分", "name": "服务方案", "kind": "content", "points": 15, "criteria": "…", "key_terms": ["服务方案"]},
            {"id": "t2", "dimension": "技术部分", "name": "针对性", "kind": "quality", "points": 15, "criteria": "…", "key_terms": ["针对性"]},
            {"id": "t3", "dimension": "技术部分", "name": "创新性", "kind": "quality", "points": 15, "criteria": "…", "key_terms": ["创新性"]},
        ])
        rubric["max_total"] = 45
        report = compute_report(rubric, [_g("t1", 15), _g("t2", 15), _g("t3", 15)])
        assert report["scored_total"] == 45
        assert report["total"] == 45

    def test_single_item_failure_marked_unscored_others_intact(self):
        report = compute_report(_rubric(), [
            _g("t1", 15),                 # graded → pass
            _g("t2", 3),                  # graded → partial
            _g("p1", 0, status="unscored", evidence="判卷失败"),  # 单项判卷失败 → unscored
        ])
        by_id = {i["id"]: i for i in report["items"]}
        # 失败项置 unscored 且不得分
        assert by_id["p1"]["status"] == "unscored"
        assert by_id["p1"]["points_obtained"] == 0
        # 其余两项照常判卷
        assert by_id["t1"]["status"] == "pass"
        assert by_id["t1"]["points_obtained"] == 15
        assert by_id["t2"]["status"] == "partial"
        assert by_id["t2"]["points_obtained"] == 3
        # unscored 项满分剔除：scored_total 只含 t1+t2 满分，total 只计实际得分
        assert report["scored_total"] == 20
        assert report["total"] == 18


class TestAutoFixableFlag:
    """报告项要带 kind / auto_fixable —— 前端据此决定显不显示「自动修改」按钮。"""

    def _graded(self, nid, suggestion):
        g = _g(nid, 10)
        g["suggestion"] = suggestion
        return g

    def test_price_item_is_not_auto_fixable(self):
        report = compute_report(_rubric(), [
            self._graded("t1", "补充服务方案细节"),
            self._graded("t2", "增加针对性描述"),
            self._graded("p1", "把报价写清楚"),   # 报价项：必须用户自己填
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["p1"]["kind"] == "price"
        assert by_id["p1"]["auto_fixable"] is False
        assert by_id["t1"]["kind"] == "content"
        assert by_id["t1"]["auto_fixable"] is True
        assert by_id["t2"]["auto_fixable"] is True

    def test_item_without_suggestion_is_not_auto_fixable(self):
        report = compute_report(_rubric(), [
            self._graded("t1", ""),
            self._graded("t2", "   "),
            _g("p1", 0, status="unscored", evidence="未含报价"),
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1"]["auto_fixable"] is False
        assert by_id["t2"]["auto_fixable"] is False
        assert by_id["p1"]["auto_fixable"] is False

    def test_unscored_item_is_not_auto_fixable_even_with_suggestion_text(self):
        """判卷失败时 run_scoring 会把错误写进 suggestion —— 那不是改进建议。"""
        report = compute_report(_rubric(), [
            {"id": "t1", "name": "服务方案", "dimension": "技术部分",
             "points_obtained": 0, "status": "unscored", "evidence": "",
             "gap": "", "suggestion": "判卷失败：Expecting ',' delimiter: line 1 column 195"},
        ])
        item = report["items"][0]
        assert item["status"] == "unscored"
        assert item["auto_fixable"] is False

    def test_missing_kind_defaults_to_auto_fixable(self):
        # 老报告没有 kind 字段时不能把按钮吞掉，price 才是唯一排除项
        rubric = _rubric([
            {"id": "t1", "dimension": "技术部分", "name": "服务方案", "points": 15,
             "criteria": "…", "key_terms": ["服务方案"]},
        ])
        report = compute_report(rubric, [self._graded("t1", "补充细节")])
        assert report["items"][0]["kind"] == ""
        assert report["items"][0]["auto_fixable"] is True


class TestMatchDimensions:
    def test_title_contains_dimension(self):
        dims = {"技术部分": [{"id": "t1"}]}
        chapters = [{"title": "技术部分（一）", "content": "正文1"}, {"title": "投标函", "content": "x"}]
        matched = _match_dimensions(dims, chapters)
        assert matched["技术部分"] == "正文1"

    def test_no_match_returns_empty(self):
        dims = {"业绩": [{"id": "p2"}]}
        matched = _match_dimensions(dims, [{"title": "投标函", "content": "x"}])
        assert matched == {}


@pytest.mark.asyncio
async def test_run_scoring_orchestrates_per_dimension_and_degrades_failure():
    from app.services.score_engine import run_scoring

    rubric = _rubric()  # 3 项：技术部分×2 + 报价×1
    chapters = [{"title": "技术部分", "content": "服务方案正文……"}]

    calls = []

    class FakeAI:
        async def chat_completion(self, messages, **kwargs):
            calls.append(messages[1]["content"])
            # 判定用维度头（_grade_dimension 提示词模板自带"报价项…未含报价"要求行，
            # 裸判 "报价" in content 对每个维度都命中；改用维度头精确区分）
            if "【评分维度】报价" in messages[1]["content"]:
                return '{"items": [{"id": "p1", "points_obtained": 30, "status_source": "含报价", "gap": "", "suggestion": ""}]}'
            return json.dumps({"items": [
                {"id": "t1", "points_obtained": 13, "status_source": "技术部分（一）", "gap": "", "suggestion": ""},
                {"id": "t2", "points_obtained": 2, "status_source": "技术部分（二）", "gap": "缺针对性", "suggestion": "补充针对分析"},
            ]})

    report = await run_scoring(rubric, chapters, FakeAI())
    # 报价维度无匹配章节 → 用全部内容尽力评分（仍产出 unscored 仅当判卷标 unscored）
    assert report["total"] == 45
    assert report["items"][0]["evidence"] == "技术部分（一）"
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["t1"]["status"] == "partial"      # 13/15 = 86.7% < 90% → partial（brief 标注已纠正 pass⇒partial）
    assert by_id["p1"]["status"] == "pass"      # 30/30
    # 技术部分只调了一次 AI（按维度分组，串行）
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_scoring_single_dimension_failure_marks_unscored():
    from app.services.score_engine import run_scoring

    rubric = _rubric()
    chapters = [{"title": "技术部分", "content": "x"}]

    class BrokenAI:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("判卷失败")

    report = await run_scoring(rubric, chapters, BrokenAI())
    assert report["total"] == 0
    assert report["scored_total"] == 0
    assert all(i["status"] == "unscored" for i in report["items"])
    assert any("判卷失败" in i["suggestion"] for i in report["items"])


@pytest.mark.asyncio
async def test_run_scoring_backfills_omitted_item_as_unscored():
    from app.services.score_engine import run_scoring

    rubric = _rubric()
    chapters = [{"title": "技术部分", "content": "服务方案正文……"}]

    class OmitAI:
        async def chat_completion(self, messages, **kwargs):
            if "【评分维度】报价" in messages[1]["content"]:
                return json.dumps({"items": [
                    {"id": "p1", "points_obtained": 30, "status_source": "报价明细", "gap": "", "suggestion": ""},
                ]})
            # 技术部分维度：返回缺 t2（AI 畸形/贪心响应漏掉指标项）
            return json.dumps({"items": [
                {"id": "t1", "points_obtained": 13, "status_source": "技术部分（一）", "gap": "", "suggestion": ""},
            ]})

    report = await run_scoring(rubric, chapters, OmitAI())
    by_id = {i["id"]: i for i in report["items"]}
    # 判卷未覆盖的指标项回填 unscored，报告逐项完整，无静默丢失（§4.2）
    assert len(report["items"]) == len(rubric["items"])
    assert "t2" in by_id
    assert by_id["t2"]["status"] == "unscored"
    assert "判卷未覆盖" in by_id["t2"]["suggestion"]
    # unscored 剔除折算：t1 13/15 + p1 30/30；t2 不参与计分
    assert report["total"] == 43
    assert report["scored_total"] == 45
    # 未省略的两项照常判卷
    assert by_id["t1"]["status"] == "partial"
    assert by_id["p1"]["status"] == "pass"
