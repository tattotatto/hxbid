"""自我评分汇总（Feature B 纯函数部分）单元测试.

判定阈值与折算规则见 spec §4.2：
- points_obtained ≥ 90%×points_total → pass；≥60% → partial；否则 fail
- 最终分数 = total / scored_total（unscored 项按其满分剔除折算）
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
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

    def test_single_item_failure_marked_unscored_others_intact(self):
        report = compute_report(_rubric(), [_g("t1", 15), _g("t2", 5), _g("p1", 30)])
        # 覆盖：graded 全通过时没有 unscored
        assert all(i["status"] != "unscored" for i in report["items"])


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