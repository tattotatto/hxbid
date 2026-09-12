"""Regression tests for tenderer_name extraction in parse_bid_requirements.

Bug case: user uploads "玉溪大红山矿业有限公司保安业务及矿区井口、生活区
游泳池值守服务业务项目.pdf". The tenderer (招标人) is
玉溪大红山矿业有限公司. AI-generated 投标函 incorrectly addresses
"致：云南昆钢兴达物业服务有限公司" because parse_bid_requirements
never extracted tenderer_name.

These tests cover the two-track fix:
  1. Schema: parse_bid_requirements prompt + defaults now include tenderer_name.
  2. Defensive regex fallback: when AI returns empty, regex rescue.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_pipeline import (
    _extract_tenderer_name_defensive,
    parse_bid_requirements,
)
from app.services.template_filler import build_variable_values


SAMPLE_TENDER_TEXT = """\
玉溪大红山矿业有限公司保安业务及矿区井口、生活区游泳池值守服务业务项目
招标公告

招标人（名称）：玉溪大红山矿业有限公司
招标代理机构：云南中招招标有限公司

一、项目内容
1. 项目名称：玉溪大红山矿业有限公司保安业务及矿区井口、生活区游泳池值守服务业务项目
2. 服务期限：11个月
"""


# ---------------------------------------------------------------------------
# Defensive regex helper
# ---------------------------------------------------------------------------

class TestDefensiveTendererExtraction:
    def test_extracts_from_招标人(self):
        assert _extract_tenderer_name_defensive(SAMPLE_TENDER_TEXT) == "玉溪大红山矿业有限公司"

    def test_extracts_from_采购人(self):
        text = "采购人（名称）：云南某科技有限公司"
        assert _extract_tenderer_name_defensive(text) == "云南某科技有限公司"

    def test_extracts_from_发包人(self):
        text = "发包人：昆明某工程有限公司"
        assert _extract_tenderer_name_defensive(text) == "昆明某工程有限公司"

    def test_extracts_from_业主(self):
        text = "业主：曲靖某（集团）有限公司"
        result = _extract_tenderer_name_defensive(text)
        assert result.startswith("曲靖某")
        assert "有限公司" in result

    def test_no_match_returns_empty(self):
        text = "这是一份普通文档，没有招标人信息"
        assert _extract_tenderer_name_defensive(text) == ""

    def test_rejects_match_containing_项目(self):
        # Should not pick up the project title as a "company"
        text = "项目名称：某某公司保安服务项目"  # 招标人段缺失
        result = _extract_tenderer_name_defensive(text)
        # Either empty (no match) or doesn't contain "项目"
        assert "项目" not in result


# ---------------------------------------------------------------------------
# parse_bid_requirements behavior
# ---------------------------------------------------------------------------

def _stub_ai_returning(payload: dict):
    """Build an AsyncMock ai_adapter whose chat_completion returns JSON payload."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = json.dumps(payload, ensure_ascii=False)
    return mock_ai


_BASE_PAYLOAD = {
    "project_name": "玉溪大红山矿业有限公司保安服务项目",
    "project_budget": "",
    "project_duration": "11个月",
    "qualification_requirements": [],
    "personnel_requirements": "",
    "service_requirements": [],
    "evaluation_criteria": "",
    "special_requirements": [],
    "bid_sections": [],
    "required_documents": [],
    "required_personnel": [],
}


class TestParseBidRequirementsTenderer:
    @pytest.mark.asyncio
    async def test_ai_returns_tenderer_name(self):
        """When AI populates tenderer_name, it should pass through unchanged."""
        payload = {**_BASE_PAYLOAD, "tenderer_name": "玉溪大红山矿业有限公司",
                   "tenderer_agency_name": "云南中招招标有限公司"}
        mock_ai = _stub_ai_returning(payload)
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            result = await parse_bid_requirements(SAMPLE_TENDER_TEXT)
        assert result["tenderer_name"] == "玉溪大红山矿业有限公司"
        assert result["tenderer_name"] != result["project_name"]
        assert result["tenderer_agency_name"] == "云南中招招标有限公司"

    @pytest.mark.asyncio
    async def test_ai_missing_tenderer_triggers_regex_fallback(self):
        """If AI returns empty tenderer_name, defensive regex should populate it."""
        payload = {**_BASE_PAYLOAD, "tenderer_name": "", "tenderer_agency_name": ""}
        mock_ai = _stub_ai_returning(payload)
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            result = await parse_bid_requirements(SAMPLE_TENDER_TEXT)
        # Defensive regex should find "玉溪大红山矿业有限公司"
        assert result["tenderer_name"] == "玉溪大红山矿业有限公司"

    @pytest.mark.asyncio
    async def test_defaults_include_tenderer_fields(self):
        """Parse failure path must include empty tenderer_name/agency defaults."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.side_effect = Exception("boom")
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            result = await parse_bid_requirements("garbage text")
        assert "tenderer_name" in result
        assert result["tenderer_name"] == ""
        assert "tenderer_agency_name" in result
        assert result["tenderer_agency_name"] == ""

    @pytest.mark.asyncio
    async def test_failure_path_includes_tender_number_and_location(self):
        """解析失败路径的默认结构必须含招标编号/服务地点/保证金.

        缺 key 会让 build_variable_values 取到 None，落成 [待补充]；
        更糟的是若下游改用 _pick 以外的写法会漏成裸 [var]。
        """
        mock_ai = AsyncMock()
        mock_ai.chat_completion.side_effect = Exception("boom")
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            result = await parse_bid_requirements("garbage text")
        assert result["tender_number"] == ""
        assert result["service_location"] == ""
        assert result["bid_deposit_amount"] == ""

    @pytest.mark.asyncio
    async def test_prompt_requests_tender_number_location_deposit(self):
        """提示词必须显式索要这三个字段.

        parse_bid_requirements 对 AI 返回的 key 是透传的，所以只要 AI 返回就
        能用；缺口在于提示词从没要求过 —— AI 不会主动给出没被索要的 key，
        槽位于是永远落 [待补充：招标编号]。
        """
        mock_ai = _stub_ai_returning(dict(_BASE_PAYLOAD))
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            await parse_bid_requirements(SAMPLE_TENDER_TEXT)
        user_prompt = mock_ai.chat_completion.call_args.kwargs["messages"][1]["content"]
        for field in ["tender_number", "service_location", "bid_deposit_amount"]:
            assert field in user_prompt, f"提示词未索要 {field}"

    @pytest.mark.asyncio
    async def test_ai_tenderer_not_overwritten_by_regex(self):
        """If AI already populated tenderer_name, regex should not clobber it."""
        payload = {**_BASE_PAYLOAD,
                   "tenderer_name": "AI-Extracted Co.有限公司",
                   "tenderer_agency_name": ""}
        mock_ai = _stub_ai_returning(payload)
        with patch("app.services.ai_pipeline.ai_adapter", mock_ai):
            result = await parse_bid_requirements(SAMPLE_TENDER_TEXT)
        # Regex would find 玉溪大红山矿业有限公司, but AI value must win.
        assert result["tenderer_name"] == "AI-Extracted Co.有限公司"


# ---------------------------------------------------------------------------
# 端到端：解析字段 → 固定格式槽位取值
# ---------------------------------------------------------------------------

class TestParseToTemplateVariables:
    """招标编号/服务地点/保证金此前无上游生产者，槽位只能落 [待补充]。

    本类钉住整条链：parse_bid_requirements 提取 → requirements →
    build_variable_values 取数（经 extract_bid_opening_data 的取数约定）。
    任一环断掉，投标函里就是 [待补充：招标编号]。
    """

    @pytest.mark.asyncio
    async def test_parsed_fields_reach_variable_map(self):
        payload = {
            **_BASE_PAYLOAD,
            "tenderer_name": "玉溪大红山矿业有限公司",
            "tenderer_agency_name": "云南中招招标有限公司",
            "tender_number": "YXDHS-2026-001",
            "service_location": "玉溪大红山矿区",
            "bid_deposit_amount": "50000",
        }
        with patch("app.services.ai_pipeline.ai_adapter", _stub_ai_returning(payload)):
            reqs = await parse_bid_requirements(SAMPLE_TENDER_TEXT)

        variables = build_variable_values({}, reqs)
        assert variables["tenderer_name"] == "玉溪大红山矿业有限公司"
        assert variables["tenderer_agency_name"] == "云南中招招标有限公司"
        assert variables["tender_number"] == "YXDHS-2026-001"
        assert variables["service_location"] == "玉溪大红山矿区"
        assert variables["bid_deposit_amount"] == "50000"
        # 关键：不能再落占位
        for var in ["tender_number", "service_location", "bid_deposit_amount"]:
            assert not variables[var].startswith("[待补充"), var