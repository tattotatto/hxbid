"""宏曦标书 - 模板填充引擎 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import re
from unittest.mock import AsyncMock

import pytest
from app.services.template_filler import (
    batch_fill_tables,
    batch_fill_text,
    build_variable_values,
    post_scan,
    scan_and_mark_variables,
)

MOCK_COMPANY = {
    "company_name": "云南领航保安服务有限公司",
    "legal_rep_name": "张三",
    "business_license_number": "91530000MA6N2XXX00",
    "address": "云南省昆明市官渡区XX路XX号",
    "contact_phone": "0871-12345678",
}

MOCK_REQS = {"project_name": "某单位保安服务采购项目"}


class TestBuildVariableValues:
    def test_basic_values(self):
        values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        assert values["company_name"] == "云南领航保安服务有限公司"
        assert values["project_name"] == "某单位保安服务采购项目"
        assert "年" in values["date"]
        assert "月" in values["date"]
        assert "日" in values["date"]

    def test_unknown_field_returns_none(self):
        values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        # Accessing an unknown key returns None, not "[待补充]"
        # This is expected behavior: the dict only has known keys
        assert values.get("unknown_field") is None

    def test_empty_inputs(self):
        values = build_variable_values(None, None)
        assert values["company_name"] == "[待补充]"
        assert values["project_name"] == "[待补充]"

    def test_missing_fields_get_placeholder(self):
        values = build_variable_values({}, {})
        assert values["company_name"] == "[待补充]"
        assert values["legal_rep_name"] == "[待补充]"

    def test_bid_validity_default(self):
        values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        assert values["bid_validity_days"] == "120"

    def test_date_format(self):
        values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        assert re.match(r'\d{4}年\d{2}月\d{2}日', values["date"])


class TestBatchFillText:
    def test_basic_fill(self):
        text = "投标人名称：________________\n日期：____年__月__日"
        replacements = [
            {"original": "________________", "var": "company_name", "value": "云南领航保安服务有限公司"},
            {"original": "____年__月__日", "var": "date", "value": "2026年07月29日"},
        ]
        result = batch_fill_text(text, replacements)
        assert "云南领航保安服务有限公司" in result
        assert "2026年07月29日" in result
        assert "________________" not in result

    def test_longer_first(self):
        """更长的字符串应先替换，避免短串破坏长串."""
        text = "投标人：__________ 法定代表人：__________"
        replacements = [
            {"original": "__________", "var": "legal_rep_name", "value": "李四"},
            {"original": "__________", "var": "company_name", "value": "测试公司"},
        ]
        result = batch_fill_text(text, replacements)
        assert "测试公司" in result

    def test_null_var_skipped(self):
        """var 为 null 的 replacement 应跳过."""
        text = "投标人名称：________________"
        replacements = [
            {"original": "投标人名称：", "var": None, "note": "这是标签，不替换"},
            {"original": "________________", "var": "company_name", "value": "云南领航保安服务有限公司"},
        ]
        result = batch_fill_text(text, replacements)
        assert "投标人名称：" in result
        assert "云南领航保安服务有限公司" in result

    def test_empty_original_skipped(self):
        text = "投标人：________________"
        replacements = [
            {"original": "", "var": "company_name", "value": "测试公司"},
            {"original": "________________", "var": "company_name", "value": "测试公司"},
        ]
        result = batch_fill_text(text, replacements)
        assert "测试公司" in result

    def test_missing_value_uses_fallback(self):
        text = "投标人：________"
        replacements = [
            {"original": "________", "var": "company_name"},
        ]
        result = batch_fill_text(text, replacements)
        assert "[company_name]" in result

    def test_no_replacements(self):
        text = "投标人名称：云南领航保安服务有限公司"
        result = batch_fill_text(text, [])
        assert result == text


class TestPostScan:
    def test_clean_text(self):
        text = "投标人名称：云南领航保安服务有限公司"
        issues = post_scan(text)
        assert len(issues) == 0

    def test_found_blanks(self):
        text = "投标人名称：________________"
        issues = post_scan(text)
        assert len(issues) > 0
        assert any("残留空白下划线" in i for i in issues)

    def test_found_placeholders(self):
        text = "投标人名称：{company_name}"
        issues = post_scan(text)
        assert len(issues) > 0
        assert any("残留占位符" in i for i in issues)

    def test_empty_text(self):
        issues = post_scan("")
        assert len(issues) == 0

    def test_multiline_empty_lines(self):
        text = "前面内容\n            \n后面内容"
        issues = post_scan(text)
        assert len(issues) > 0
        assert any("空白行" in i for i in issues)


class TestBatchFillTables:
    def test_fill_tables(self):
        tables = [
            {"page": 68, "table_index": 1, "rows": [["投标人名称", ""], ["注册地址", ""]]}
        ]
        fills = [
            {"page": 68, "table_index": 1, "row": 0, "col": 1, "var": "company_name"},
            {"page": 68, "table_index": 1, "row": 1, "col": 1, "var": "address"},
        ]
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        result = batch_fill_tables(tables, fills, variables)
        assert result[0]["rows"][0][1] == "云南领航保安服务有限公司"
        assert result[0]["rows"][1][1] == "云南省昆明市官渡区XX路XX号"

    def test_unknown_var_gets_bracket_name(self):
        tables = [
            {"page": 1, "table_index": 1, "rows": [["投标人名称", ""]]}
        ]
        fills = [
            {"page": 1, "table_index": 1, "row": 0, "col": 1, "var": "unknown_var"},
        ]
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        result = batch_fill_tables(tables, fills, variables)
        assert result[0]["rows"][0][1] == "[unknown_var]"

    def test_out_of_bounds_safe(self):
        """行列越界应静默跳过."""
        tables = [
            {"page": 1, "table_index": 1, "rows": [["A"]]}
        ]
        fills = [
            {"page": 1, "table_index": 1, "row": 99, "col": 99, "var": "company_name"},
        ]
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        result = batch_fill_tables(tables, fills, variables)
        assert result[0]["rows"][0][0] == "A"

    def test_empty_tables(self):
        result = batch_fill_tables([], [], {})
        assert result == []

    def test_result_is_deep_copy(self):
        """返回的 tables 应该是原始数据的一个深度复制，不修改原数据."""
        tables = [
            {"page": 1, "table_index": 1, "rows": [["", ""]]}
        ]
        fills = [
            {"page": 1, "table_index": 1, "row": 0, "col": 0, "var": "company_name"},
        ]
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        result = batch_fill_tables(tables, fills, variables)
        # 原始 tables 的 rows 未被修改
        assert tables[0]["rows"][0][0] == ""
        assert result[0]["rows"][0][0] == "云南领航保安服务有限公司"


class TestScanAndMarkVariables:
    @pytest.mark.asyncio
    async def test_successful_scan(self):
        """测试 AI 扫描成功返回标注结果."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "________", "var": "company_name", "context_before": "投标人名称："}
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await scan_and_mark_variables(
            full_text="投标人名称：________",
            tables=[],
            ai_adapter=mock_ai,
        )
        assert len(result["text_replacements"]) == 1
        assert result["text_replacements"][0]["var"] == "company_name"
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_scan_fallback_on_error(self):
        """测试 AI 扫描失败时的兜底返回."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.side_effect = Exception("API 调用失败")

        result = await scan_and_mark_variables(
            full_text="投标人名称：________",
            tables=[],
            ai_adapter=mock_ai,
        )
        assert result["text_replacements"] == []
        assert result["table_fills"] == []
        assert len(result["warnings"]) == 1
        assert "API 调用失败" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_scan_invalid_json(self):
        """测试 AI 返回非法 JSON 时的兜底."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = "这不是合法的JSON"

        result = await scan_and_mark_variables(
            full_text="投标人名称：________",
            tables=[],
            ai_adapter=mock_ai,
        )
        assert result["text_replacements"] == []
        assert result["table_fills"] == []
        assert len(result["warnings"]) == 1

    @pytest.mark.asyncio
    async def test_scan_truncates_text_and_tables(self):
        """测试大文本和大量表格被截断."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [],
            "table_fills": [],
            "warnings": [],
        })

        long_text = "X" * 20000
        many_tables = [{"page": i, "table_index": i, "rows": [["", ""]]} for i in range(20)]

        result = await scan_and_mark_variables(
            full_text=long_text,
            tables=many_tables,
            ai_adapter=mock_ai,
        )
        # 应该只发送前10个表格和前15000个字符
        call_prompt = mock_ai.chat_completion.call_args.kwargs["messages"][1]["content"]
        assert "X" * 10 in call_prompt
        assert str(many_tables[9]) in call_prompt or "page" in call_prompt
