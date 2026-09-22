"""宏曦标书 - 资料选择合并逻辑 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.models.company_profile import CompanyProfile
from app.models.contract import Contract
from app.models.personnel import Personnel
from app.models.project_resource import (
    ProjectContract,
    ProjectPersonnel,
    ProjectQualification,
)
from app.models.qualification import Qualification
from app.services.collection import (
    _auto_occupy_confident_matches,
    _is_confident_auto,
    _is_performance_requirement,
    _merge_matches,
    _persisted_matches_for,
    _pick_auto_occupy,
    analyze_collection_needs,
    upload_requirement_document,
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


class _Row:
    """轻量假行对象——helper 只按属性取值，鸭子类型即可."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_pq(req_name: str, qual_id: str, name: str):
    return _Row(
        requirement_name=req_name, qualification_id=qual_id, id=f"link-{qual_id}",
        qualification=_Row(name=name, cert_number=f"NO-{qual_id}",
                           issuing_authority="某发证机构"),
    )


def _fake_pc(req_name: str, contract_id: str, name: str):
    return _Row(
        requirement_name=req_name, contract_id=contract_id, id=f"link-{contract_id}",
        contract=_Row(project_name=name, procurement_unit="某采购单位",
                      contract_amount="100万元", contract_date=None),
    )


def _fake_pp(role: str, person_id: str, name: str):
    return _Row(
        role=role, personnel_id=person_id, id=f"link-{person_id}",
        personnel=_Row(name=name, education="本科", tags="保安员"),
    )


class TestPersistedMatchesFor:
    """某需求名下已落库资源的**三路**汇总.

    回归：历史上每个需求只取「对应那一类」——业绩行取合同、其余行只取资质，
    人员行只按 role 取。用户从资源库挂了另一类，数据落了库却在信息搜集页面
    读不回来（看不见 = 以为没挂上）。现在三路都取。
    """

    def test_qualification_and_contract_on_same_row(self):
        """核心场景：从资源库同时挂资质 + 合同到同一行，两路都要读出来."""
        pq = _fake_pq("投标人认为有必要提供的声明和文件", "q1", "保安服务许可证")
        pc = _fake_pc("投标人认为有必要提供的声明和文件", "c1", "某安保服务合同")
        got = _persisted_matches_for(
            "投标人认为有必要提供的声明和文件", [pq], [pc], []
        )
        assert [m["source"] for m in got] == ["qualification", "contract"]
        assert [m["id"] for m in got] == ["q1", "c1"]

    def test_personnel_matched_by_role(self):
        """人员是按 role 存需求名的——文档行挂人员同样落这个字段."""
        pp = _fake_pp("其他材料", "p1", "张三")
        got = _persisted_matches_for("其他材料", [], [], [pp])
        assert len(got) == 1
        assert got[0]["source"] == "personnel"
        assert got[0]["name"] == "张三"
        assert got[0]["role"] == "其他材料"

    def test_all_three_sources_in_fixed_order(self):
        """顺序固定为 资质 → 合同 → 人员，前端标签顺序才不抖."""
        got = _persisted_matches_for(
            "其他材料",
            [_fake_pq("其他材料", "q1", "A")],
            [_fake_pc("其他材料", "c1", "B")],
            [_fake_pp("其他材料", "p1", "C")],
        )
        assert [m["source"] for m in got] == ["qualification", "contract", "personnel"]
        assert [m["name"] for m in got] == ["A", "B", "C"]

    def test_other_requirements_excluded(self):
        got = _persisted_matches_for(
            "投标函", [_fake_pq("营业执照", "q1", "营业执照")], [], []
        )
        assert got == []

    def test_empty_when_nothing_linked(self):
        assert _persisted_matches_for("其他材料", [], [], []) == []

    def test_each_match_carries_link_id_for_removal(self):
        """前端取消勾选要按 link_id 回删，字段不能缺."""
        got = _persisted_matches_for(
            "其他材料", [_fake_pq("其他材料", "q1", "A")], [], []
        )
        assert got[0]["link_id"] == "link-q1"
        assert got[0]["selection"] == "selected"


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


class _AnalyzeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _AnalyzeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _AnalyzeScalars(self._rows)


def _make_analyze_db(project, *, quals=(), personnel=(), company=None,
                     contracts=(), pq=(), pp=(), pc=()):
    """按 select 的实体分派结果 —— 不依赖查询顺序，实现里调整取数次序也不会误报."""
    by_entity = {
        Qualification: list(quals),
        Personnel: list(personnel),
        CompanyProfile: [company] if company else [],
        Contract: list(contracts),
        ProjectQualification: list(pq),
        ProjectPersonnel: list(pp),
        ProjectContract: list(pc),
    }
    db = AsyncMock()
    db.get = AsyncMock(return_value=project)

    async def fake_execute(stmt, *args, **kwargs):
        entity = stmt.column_descriptions[0]["entity"]
        if entity not in by_entity:
            raise AssertionError(f"未预期的查询实体: {entity}")
        return _AnalyzeResult(by_entity[entity])

    db.execute = AsyncMock(side_effect=fake_execute)
    return db


class TestAnalyzeCollectionNeedsMixedSources:
    """接线层：analyze_collection_needs 对同一需求行必须把三路已落库资源都返回.

    上面 TestPersistedMatchesFor 只覆盖汇总函数本身；这里确保它真的被用上——
    历史上三个分支各取一路（业绩行只取合同、其余行只取资质、人员行只按 role），
    兜底行「投标人认为有必要提供的文件或附件」挂上合同就读不回来。
    """

    @pytest.mark.asyncio
    async def test_document_row_returns_all_three_sources(self):
        project = _Row(id="p1", status="parsing", parsed_requirements_json=json.dumps({
            "required_documents": [
                {"name": "投标人认为有必要提供的声明和文件", "category": "other"},
            ],
            "required_personnel": [],
        }))
        db = _make_analyze_db(
            project,
            pq=[_fake_pq("投标人认为有必要提供的声明和文件", "q1", "保安服务许可证")],
            pc=[_fake_pc("投标人认为有必要提供的声明和文件", "c1", "某安保服务合同")],
            pp=[_fake_pp("投标人认为有必要提供的声明和文件", "p1", "张三")],
        )
        got = await analyze_collection_needs("p1", db)

        row = got["document_items"][0]
        assert [m["source"] for m in row["matches"]] == [
            "qualification", "contract", "personnel",
        ]
        assert row["match_status"] == "selected"
        assert row["matched"] is True

    @pytest.mark.asyncio
    async def test_performance_row_also_returns_qualification(self):
        """业绩行以前只读合同；用户从资源库挂了资质到这一行，也要能读出来."""
        project = _Row(id="p1", status="parsing", parsed_requirements_json=json.dumps({
            "required_documents": [{"name": "类似项目业绩证明材料", "category": "other"}],
            "required_personnel": [],
        }))
        db = _make_analyze_db(
            project,
            pq=[_fake_pq("类似项目业绩证明材料", "q1", "保安服务许可证")],
            pc=[_fake_pc("类似项目业绩证明材料", "c1", "某安保服务合同")],
        )
        got = await analyze_collection_needs("p1", db)

        row = got["document_items"][0]
        assert [m["source"] for m in row["matches"]] == ["qualification", "contract"]

    @pytest.mark.asyncio
    async def test_personnel_row_still_reads_by_role(self):
        """人员配置行不受影响：仍按 role 取，且不把文档资源混进来."""
        project = _Row(id="p1", status="parsing", parsed_requirements_json=json.dumps({
            "required_documents": [],
            "required_personnel": [{"role": "项目经理", "certifications": [], "count": 1}],
        }))
        db = _make_analyze_db(
            project,
            pp=[_fake_pp("项目经理", "p1", "李四")],
            pq=[_fake_pq("项目经理", "q1", "不该出现在这里的资质")],
        )
        got = await analyze_collection_needs("p1", db)

        assert got["document_items"] == []
        row = got["personnel_items"][0]
        assert [m["name"] for m in row["matches"]] == ["李四"]
        assert row["match_status"] == "selected"

    @pytest.mark.asyncio
    async def test_unmatched_row_is_missing(self):
        project = _Row(id="p1", status="parsing", parsed_requirements_json=json.dumps({
            "required_documents": [{"name": "投标函", "category": "other"}],
            "required_personnel": [],
        }))
        db = _make_analyze_db(project)
        got = await analyze_collection_needs("p1", db)

        row = got["document_items"][0]
        assert row["matches"] == []
        assert row["match_status"] == "missing"
        assert row["matched"] is False
        assert got["is_complete"] is False


def _mock_db():
    """只需 add / flush / refresh 的假 db（上传建条目不查库）."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


class TestUploadRequirementDocument:
    """上传需求材料时**同步建一条资源库条目**，下次可直接从资源库选.

    口径与 _is_performance_requirement 一致：业绩/合同类行进「历史合同」，
    其余行进「公司资质」。上传前只写 ProjectQualification.uploaded_file_path，
    资源库完全不碰，导致同一个文件换个项目还得重传。
    """

    @pytest.mark.asyncio
    async def test_performance_row_creates_contract_in_library(self):
        db = _mock_db()
        got = await upload_requirement_document(
            "proj1", "保安服务业绩证明材料", "other",
            "uploads/collection_ab12.png", db,
        )

        added = [type(c.args[0]).__name__ for c in db.add.call_args_list]
        assert added == ["Contract", "ProjectContract"]

        contract = db.add.call_args_list[0].args[0]
        assert contract.project_name == "保安服务业绩证明材料"
        assert json.loads(contract.image_paths_json) == ["uploads/collection_ab12.png"]

        pc = db.add.call_args_list[1].args[0]
        assert pc.project_id == "proj1"
        assert pc.requirement_name == "保安服务业绩证明材料"
        # FK 必须设上，否则 _persisted_matches_for 读出来的 name 会退化成需求名
        assert pc.contract_id == contract.id
        assert pc.contract_id is not None

        assert got["kind"] == "contract"
        assert got["library_id"] == contract.id

    @pytest.mark.asyncio
    async def test_plain_row_creates_qualification_in_library(self):
        db = _mock_db()
        got = await upload_requirement_document(
            "proj1", "营业执照", "company", "uploads/collection_cd34.png", db,
        )

        added = [type(c.args[0]).__name__ for c in db.add.call_args_list]
        assert added == ["Qualification", "ProjectQualification"]

        qual = db.add.call_args_list[0].args[0]
        assert qual.name == "营业执照"
        assert qual.attachment_path == "uploads/collection_cd34.png"

        pq = db.add.call_args_list[1].args[0]
        assert pq.project_id == "proj1"
        assert pq.requirement_name == "营业执照"
        assert pq.qualification_id == qual.id
        assert pq.qualification_id is not None
        assert pq.match_status == "uploaded"

        assert got["kind"] == "qualification"
        assert got["library_id"] == qual.id

    @pytest.mark.asyncio
    async def test_contract_performance_category_routes_to_contract(self):
        """category 显式给了 contract_performance 也要走合同分支（口径统一）."""
        db = _mock_db()
        got = await upload_requirement_document(
            "proj1", "任意名称", "contract_performance", "uploads/x.png", db,
        )
        assert got["kind"] == "contract"

    @pytest.mark.asyncio
    async def test_library_row_ids_are_generated_up_front(self):
        """库条目 id 在构造时就定下来，不依赖 flush 回填——ProjectContract/
        ProjectQualification 的 FK 直接用它."""
        db = _mock_db()
        await upload_requirement_document("proj1", "营业执照", "", "uploads/a.png", db)
        qual = db.add.call_args_list[0].args[0]
        pq = db.add.call_args_list[1].args[0]
        assert isinstance(qual.id, str) and len(qual.id) == 36
        assert pq.qualification_id == qual.id
        db.flush.assert_awaited()
