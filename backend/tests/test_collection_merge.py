"""宏曦标书 - 资料选择合并逻辑 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.collection import (
    _is_confident_auto,
    _merge_matches,
    _pick_auto_occupy,
)


class TestMergeMatches:
    def test_persisted_selection_wins(self):
        persisted = [{"source": "selected", "selection": "selected", "name": "保安服务许可证", "id": "q1"}]
        auto = [{"source": "qualification", "name": "保安服务许可证", "id": "q2"}]
        matches, status = _merge_matches(persisted, auto, "qualification")
        assert status == "selected"
        assert len(matches) == 1
        assert matches[0]["id"] == "q1"

    def test_auto_candidate_when_no_selection(self):
        # 确定性自动候选（qualification 信号匹配）带 confidence="high"
        auto = [{"source": "qualification", "name": "保安服务许可证", "id": "q2", "confidence": "high"}]
        matches, status = _merge_matches([], auto, "qualification")
        assert status == "auto"
        assert matches == auto

    def test_missing_when_nothing(self):
        matches, status = _merge_matches([], [], "qualification")
        assert status == "missing"
        assert matches == []

    def test_multiple_persisted_all_returned(self):
        persisted = [{"name": "A", "id": "1"}, {"name": "B", "id": "2"}]
        matches, status = _merge_matches(persisted, [], "personnel")
        assert status == "selected"
        assert len(matches) == 2


class TestIsConfidentAuto:
    def test_qualification_with_id_is_confident(self):
        assert _is_confident_auto(
            "qualification", [{"id": "q1", "source": "qualification", "confidence": "high"}]
        ) is True

    def test_company_source_match_not_confident(self):
        # 公司资料（营业执照等）无 id、无 confidence，不可落库为 ProjectQualification
        assert _is_confident_auto("qualification", [{"source": "company", "name": "营业执照"}]) is False

    def test_personnel_fallback_candidates_not_confident(self):
        # 无标签匹配时 _match_personnel 返回前 5 人（confidence=low），不可自动占用
        assert _is_confident_auto(
            "personnel", [{"id": "p1", "confidence": "low"}, {"id": "p2", "confidence": "low"}]
        ) is False

    def test_personnel_tag_match_is_confident(self):
        assert _is_confident_auto("personnel", [{"id": "p1", "confidence": "high"}]) is True

    def test_contract_with_id_is_confident(self):
        assert _is_confident_auto(
            "contract", [{"id": "c1", "source": "contract", "confidence": "high"}]
        ) is True


class TestPickAutoOccupy:
    def test_picks_confident_auto_qualification_only(self):
        items = [{
            "requirement": {"name": "保安服务许可证", "category": "qualification"},
            "match_status": "auto",
            "matches": [
                {"id": "q1", "confidence": "high"},
                {"id": "q2", "confidence": "low"},
                {"source": "company", "name": "营业执照"},
            ],
        }]
        rows = _pick_auto_occupy(items, [])
        assert rows == [
            {"model": "qualification", "requirement_name": "保安服务许可证", "resource_id": "q1"},
        ]

    def test_skips_missing_and_matched(self):
        items = [
            {"requirement": {"name": "A", "category": "qualification"}, "match_status": "missing", "matches": []},
            {"requirement": {"name": "B", "category": "qualification"}, "match_status": "matched", "matches": [{"id": "q9", "confidence": "high"}]},
        ]
        assert _pick_auto_occupy(items, []) == []

    def test_personnel_and_contract_branches(self):
        doc = [{"requirement": {"name": "业绩合同", "category": "contract_performance"}, "match_status": "auto", "matches": [{"id": "c1", "confidence": "high"}]}]
        per = [{"requirement": {"name": "项目经理"}, "match_status": "auto", "matches": [{"id": "p1", "confidence": "high"}]}]
        rows = _pick_auto_occupy(doc, per)
        assert rows == [
            {"model": "contract", "requirement_name": "业绩合同", "resource_id": "c1"},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1"},
        ]
