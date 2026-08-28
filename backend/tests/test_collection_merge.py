"""宏曦标书 - 资料选择合并逻辑 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.services.collection import (
    _auto_occupy_confident_matches,
    _is_confident_auto,
    _is_performance_requirement,
    _merge_matches,
    _pick_auto_occupy,
)


class TestIsPerformanceRequirement:
    """业绩/合同类需求判定统一口径.

    回归：parse 侧（AI 提取）从未产出 contract_performance 分类，业绩需求实际
    category=other；若只认 category，用户手动关联的合同在汇总/自动占用时全部
    查不到（2026-08-28 生产「安保业务服务业绩证明材料（合同）」8 份合同落库
    却不显示）。
    """

    def test_contract_performance_category(self):
        assert _is_performance_requirement("任意名称", "contract_performance") is True

    def test_performance_keywords_in_name(self):
        for name in (
            "安保业务服务业绩证明材料（合同）",
            "类似项目业绩证明材料",
            "履约能力证明",
            "中标通知书",
        ):
            assert _is_performance_requirement(name, "other") is True, name
            # 无 category 信息也应命中（部分路径 requirement 外层不带 category）
            assert _is_performance_requirement(name, "") is True, name

    def test_plain_documents_not_misjudged(self):
        """普通资质/文件类需求不得被误判为合同需求.「投标函」「授权委托书」等
        名称无业绩/合同关键词，必须保持资质路径。"""
        for name in (
            "营业执照",
            "保安服务许可证",
            "质量管理体系认证证书",
            "投标函",
            "法定代表人身份证明",
            "授权委托书",
            "开标一览表",
        ):
            assert _is_performance_requirement(name, "other") is False, name
        assert _is_performance_requirement("营业执照", "qualification") is False


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

    def test_contract_branch_by_name_keyword_when_category_other(self):
        """parse 侧业绩需求 category=other 时，自动占用仍应走 contract 模型
        （回归：aws 2026-08-28，否则业绩 auto 占用按 qualification 入错表）。"""
        doc = [{
            "requirement": {"name": "安保业务服务业绩证明材料（合同）", "category": "other"},
            "match_status": "auto",
            "matches": [{"id": "c1", "confidence": "high"}],
        }]
        rows = _pick_auto_occupy(doc, [])
        assert rows == [
            {"model": "contract", "requirement_name": "安保业务服务业绩证明材料（合同）", "resource_id": "c1", "count": 1},
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
    async def test_fills_count_when_no_existing(self):
        # count=2、无既有行、2 条 high 候选 → 插入 2 行
        db = self._make_db([[]])
        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)
        assert occupied == 2
        assert db.add.call_count == 2
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fills_remaining_when_partial_existing(self):
        # count=2、既有 1 行、2 条 high 候选 → 只补 1 行
        db = self._make_db([[object()]])
        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)
        assert occupied == 1
        assert db.add.call_count == 1
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_caps_at_count_one(self):
        # count=1、既有 0 行、2 条 high 候选 → 只插 1 行（cap=1 生效）
        db = self._make_db([[]])
        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 1},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 1},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)
        assert occupied == 1
        assert db.add.call_count == 1
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_already_full(self):
        # count=2、既有 2 行 → 跳过，不插
        db = self._make_db([[object(), object()]])
        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)
        assert occupied == 0
        db.add.assert_not_called()
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queries_existing_once_per_key(self):
        # 两个不同需求 → db.execute 只查两次（query-once-per-key，避免 autoflush 双计数）
        db = self._make_db([[], []])
        rows = [
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1", "count": 2},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p2", "count": 2},
            {"model": "personnel", "requirement_name": "技术负责人", "resource_id": "p3", "count": 1},
        ]
        occupied = await _auto_occupy_confident_matches("proj1", rows, db)
        assert occupied == 3
        assert db.execute.await_count == 2
        db.flush.assert_awaited_once()

    def _make_db(self, existing_by_call):
        """构造 AsyncMock db，让 execute(...).scalars().all() 按查询次序返回既有行.

        新实现每 key 只查一次；这里对超出的查询回退为空列表（旧实现会因
        逐行重查而多出查询，从而被上面的断言捕获）。
        """
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        calls = iter(existing_by_call)

        async def fake_execute(*args, **kwargs):
            try:
                rows = next(calls)
            except StopIteration:
                rows = []
            return _FakeResult(rows)

        db.execute = AsyncMock(side_effect=fake_execute)
        return db
