"""宏曦标书 - 资料解除关联 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.collection import (
    assign_personnel,
    link_contract,
    unlink_contract,
    unlink_qualification,
)


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


def _make_db(rows):
    """构造 AsyncMock db，使 execute(...).scalars().all() 返回指定行."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeResult(rows))
    return db


class TestUnlinkQualification:
    @pytest.mark.asyncio
    async def test_deletes_row_by_resource_id(self):
        # 模拟 SQL WHERE qualification_id == resource_id 过滤后命中一行
        db = _make_db([
            AsyncMock(id="pq1", requirement_name="保安服务许可证", qualification_id="q1"),
        ])
        with patch("app.services.collection.select"):
            deleted = await unlink_qualification("proj1", "保安服务许可证", "q1", db)
        assert deleted is True
        assert db.delete.call_count == 1

    @pytest.mark.asyncio
    async def test_zero_match_is_graceful_noop(self):
        # select 返回零行（deleted is False）：不删除任何记录，返回 False
        db = _make_db([])
        with patch("app.services.collection.select"):
            deleted = await unlink_qualification("proj1", "保安服务许可证", "q1", db)
        assert deleted is False
        db.delete.assert_not_called()


class TestUnlinkContract:
    @pytest.mark.asyncio
    async def test_deletes_row(self):
        db = _make_db([
            AsyncMock(id="pc1", requirement_name="业绩合同", contract_id="c1"),
        ])
        with patch("app.services.collection.select"):
            deleted = await unlink_contract("proj1", "业绩合同", "c1", db)
        assert deleted is True
        assert db.delete.call_count == 1


class TestMultiSelectPersists:
    """去掉删旧逻辑后，第二次 assign/link 不再删除第一次的记录.

    旧实现会在 assign/link 前 `for old in existing.scalars(): db.delete(old)`。
    _FakeResult.scalars() 返回的 _FakeScalars 不可迭代，旧实现会直接 TypeError → 测试红；
    新实现不触 execute/delete → 断言通过。
    """
    @pytest.mark.asyncio
    async def test_assign_personnel_does_not_delete_previous(self):
        db = _make_db([])
        with patch("app.services.collection.select"):
            await assign_personnel("proj1", "p1", "项目经理", "", db)
        db.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_link_contract_does_not_delete_previous(self):
        db = _make_db([])
        with patch("app.services.collection.select"):
            await link_contract("proj1", "c1", "业绩合同", db)
        db.delete.assert_not_called()
