"""宏曦标书 - 采集素材序列化 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.services.collection import get_collected_resources


class _FakeResult:
    """模拟 db.execute 返回对象：scalars() 直接可迭代，company 用 scalar_one_or_none()."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class TestGetCollectedResources:
    @pytest.mark.asyncio
    async def test_personnel_includes_certificates(self):
        cert = SimpleNamespace(cert_name="一级建造师", cert_number="JG-001")
        personnel = SimpleNamespace(
            name="李四",
            education="本科",
            tags="保安",
            certificates=[cert],
        )
        pp = SimpleNamespace(id="pp1", role="项目经理", personnel=personnel)
        company = SimpleNamespace(
            company_name="云南领航保安服务有限公司",
            business_license_number="91530000ABC",
            legal_rep_name="",
            legal_rep_id_number="",
            address="",
            contact_phone="",
            website="",
            notes="",
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _FakeResult([]),          # qualifications
            _FakeResult([pp]),        # personnel
            _FakeResult([company]),   # company profile（scalar_one_or_none）
            _FakeResult([]),          # contracts
        ])

        resources = await get_collected_resources("proj1", db)

        personnel_list = resources["personnel"]
        assert len(personnel_list) == 1
        assert personnel_list[0]["name"] == "李四"
        assert personnel_list[0]["certificates"] == [
            {"cert_name": "一级建造师", "cert_number": "JG-001"},
        ]

    @pytest.mark.asyncio
    async def test_personnel_empty_certificates_when_no_personnel(self):
        pp = SimpleNamespace(id="pp1", role="项目经理", personnel=None)
        company = SimpleNamespace(
            company_name="云南领航保安服务有限公司",
            business_license_number="",
            legal_rep_name="",
            legal_rep_id_number="",
            address="",
            contact_phone="",
            website="",
            notes="",
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _FakeResult([]),
            _FakeResult([pp]),
            _FakeResult([company]),
            _FakeResult([]),
        ])

        resources = await get_collected_resources("proj1", db)

        assert resources["personnel"][0]["certificates"] == []
