"""宏曦标书 - 资料选择合并逻辑 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services.collection import (
    _auto_occupy_confident_matches,
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
            {"model": "qualification", "requirement_name": "保安服务许可证", "resource_id": "q1", "count": 1},
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
            {"model": "contract", "requirement_name": "业绩合同", "resource_id": "c1", "count": 1},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 1},
        ]

    def test_contract_caps_at_one_high_match(self):
        # 文档类需求容量恒为 1，多个 high-confidence 匹配也只取首个
        items = [{
            "requirement": {"name": "业绩合同", "category": "contract_performance"},
            "match_status": "auto",
            "matches": [
                {"id": "c1", "confidence": "high"},
                {"id": "c2", "confidence": "high"},
                {"id": "c3", "confidence": "high"},
            ],
        }]
        rows = _pick_auto_occupy(items, [])
        assert rows == [
            {"model": "contract", "requirement_name": "业绩合同", "resource_id": "c1", "count": 1},
        ]

    def test_personnel_caps_at_count(self):
        # personnel 需求按 count 封顶：count=2 且有 3 个 high 候选 → 只产出 2 行
        items = [{
            "requirement": {"name": "项目经理", "category": "personnel", "count": 2},
            "match_status": "auto",
            "matches": [
                {"id": "p1", "confidence": "high"},
                {"id": "p2", "confidence": "high"},
                {"id": "p3", "confidence": "high"},
            ],
        }]
        rows = _pick_auto_occupy([], items)
        assert rows == [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
        ]


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class TestAutoOccupyConfidentMatches:
    @pytest.mark.asyncio
    async def test_inserts_when_below_capacity(self):
        # 已落库 1 行 + count=2 → 仍插入第 2 行
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_FakeResult([object()]))
        db.add = MagicMock()
        db.flush = AsyncMock()

        rows = [{"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2}]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)

        assert occupied == 1
        db.add.assert_called_once()
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_at_capacity(self):
        # 已落库 2 行 + count=2 → 跳过
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_FakeResult([object(), object()]))
        db.add = MagicMock()
        db.flush = AsyncMock()

        rows = [{"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2}]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)

        assert occupied == 0
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_caps_batch_inserts(self):
        # 同需求 3 行 + count=2 + 无已落库 → 本批只插入 2 行
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_FakeResult([]))
        db.add = MagicMock()
        db.flush = AsyncMock()

        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p3", "count": 2},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)

        assert occupied == 2
        assert db.add.call_count == 2
        db.flush.assert_awaited_once()
