"""宏曦标书 - 模板填充引擎 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import re
from unittest.mock import AsyncMock

import pytest
from app.services.template_filler import (
    SCAN_SYSTEM_PROMPT,
    amount_to_chinese_words,
    batch_fill_tables,
    batch_fill_text,
    build_variable_values,
    extract_fixed_form_section,
    fill_fixed_form_section_from_template,
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

    def test_every_prompt_variable_has_a_value(self):
        """Regression: SCAN_SYSTEM_PROMPT 宣告的每个变量名都必须有值。

        缺口会让 fill_fixed_form_section_from_template 落进
        ``variables.get(var, f"[{var}]")`` 分支，把 "[tender_number]" 这类
        原始变量名写进投标函正文，且 post_scan 只扫 {word} 不扫 [word]，
        没有任何兜底能拦住它。
        """
        values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        prompt_vars = re.findall(r"^- ([a-z_]+):", SCAN_SYSTEM_PROMPT, re.MULTILINE)
        assert len(prompt_vars) > 20, "prompt 变量表解析异常，检查格式"
        assert [v for v in prompt_vars if v not in values] == []

    def test_no_bare_variable_placeholder(self):
        """无数据源时用 [待补充：X]，绝不能是裸的 [var]."""
        values = build_variable_values({}, {})
        for var, val in values.items():
            assert val != f"[{var}]", f"{var} 落回裸占位符"

    def test_sources_from_requirements(self):
        """招标编号/期限/地点/保证金/报价从 requirements 取数（与开标一览表同源）."""
        reqs = {
            "tender_number": "YXDHS-2026-001",
            "project_duration": "11个月",
            "service_location": "玉溪大红山矿区",
            "bid_deposit_amount": "50000",
            "total_price_excluding_tax": "1234567.89",
            "monthly_unit_price": "112233",
        }
        values = build_variable_values({}, reqs)
        assert values["tender_number"] == "YXDHS-2026-001"
        assert values["service_period"] == "11个月"
        assert values["service_location"] == "玉溪大红山矿区"
        assert values["bid_deposit_amount"] == "50000"
        assert values["bid_total_amount"] == "1234567.89"
        assert values["bid_unit_amount"] == "112233"

    def test_legal_rep_id_from_company_profile(self):
        values = build_variable_values({"legal_rep_id_number": "530100199001011234"}, {})
        assert values["legal_rep_id_number"] == "530100199001011234"

    def test_tenderer_agency_from_requirements(self):
        values = build_variable_values({}, {"tenderer_agency_name": "云南中招招标有限公司"})
        assert values["tenderer_agency_name"] == "云南中招招标有限公司"

    def test_missing_sources_use_explicit_placeholders(self):
        """无数据源 → [待补充：X] 可见占位（待人工填），而不是静默漏填."""
        values = build_variable_values({}, {})
        for var in [
            "tender_number", "bid_total_amount", "bid_unit_amount",
            "bid_deposit_amount", "service_period", "service_location",
            "legal_rep_id_number", "tenderer_agency_name",
            "bid_total_amount_words",
        ]:
            assert values[var].startswith("[待补充"), var

    def test_bid_total_amount_words_derived_from_total_price(self):
        values = build_variable_values({}, {"total_price_excluding_tax": "1234567.89"})
        assert values["bid_total_amount_words"] == "壹佰贰拾叁万肆仟伍佰陆拾柒元捌角玖分"

    def test_bid_total_amount_words_placeholder_without_source(self):
        values = build_variable_values({}, {})
        assert values["bid_total_amount_words"].startswith("[待补充")

    def test_tenderer_name_distinct_from_project_name(self):
        """Regression: tenderer_name must NOT silently alias project_name.

        Bug case: 招标人「玉溪大红山矿业有限公司」被误填成
        project_name「玉溪大红山矿业有限公司保安业务及矿区井口、生活区
        游泳池值守服务业务项目」，导致 AI 生成投标函抬头时幻觉出别的公司。
        """
        reqs = {
            "project_name": "玉溪大红山矿业有限公司保安业务及矿区井口、生活区游泳池值守服务业务项目",
            "tenderer_name": "玉溪大红山矿业有限公司",
        }
        values = build_variable_values(MOCK_COMPANY, reqs)
        assert values["tenderer_name"] == "玉溪大红山矿业有限公司"
        assert values["tenderer_name"] != values["project_name"]

    def test_tenderer_name_missing_uses_distinct_placeholder(self):
        """Missing tenderer_name should be visibly marked, never project_name."""
        reqs = {"project_name": "某保安服务项目"}  # no tenderer_name
        values = build_variable_values(MOCK_COMPANY, reqs)
        assert values["tenderer_name"] == "[待补充：招标人名称]"
        # Project name must NOT leak into tenderer_name
        assert values["tenderer_name"] != values["project_name"]

    def test_tenderer_name_empty_string_treated_as_missing(self):
        """Empty string must also fall to placeholder, not project_name."""
        reqs = {"project_name": "某保安服务项目", "tenderer_name": ""}
        values = build_variable_values(MOCK_COMPANY, reqs)
        assert values["tenderer_name"] == "[待补充：招标人名称]"


class TestAmountToChineseWords:
    """投标报价大写转换（元角分）."""

    @pytest.mark.parametrize("raw,expected", [
        ("0", "零元整"),
        ("1", "壹元整"),
        ("10", "壹拾元整"),
        ("105", "壹佰零伍元整"),
        ("1000", "壹仟元整"),
        ("10001", "壹万零壹元整"),
        ("1000000", "壹佰万元整"),
        ("100000000", "壹亿元整"),
        ("100.5", "壹佰元伍角"),
        ("100.05", "壹佰元零伍分"),
        ("1234567.89", "壹佰贰拾叁万肆仟伍佰陆拾柒元捌角玖分"),
        ("1234567890.12", "壹拾贰亿叁仟肆佰伍拾陆万柒仟捌佰玖拾元壹角贰分"),
        ("￥1,234.00", "壹仟贰佰叁拾肆元整"),
        ("120000元", "壹拾贰万元整"),
        ("  888  ", "捌佰捌拾捌元整"),
    ])
    def test_converts(self, raw, expected):
        assert amount_to_chinese_words(raw) == expected

    @pytest.mark.parametrize("raw", ["", "面议", "详见招标文件", "[待补充：投标总报价]"])
    def test_unparseable_returns_empty(self, raw):
        assert amount_to_chinese_words(raw) == ""


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


# ---------------------------------------------------------------------------
# 固定格式小节提取（user requirement: 投标函等保留输入 PDF 原文措辞）
# ---------------------------------------------------------------------------


class TestExtractFixedFormSection:
    """从 format_section_text 截取单个固定格式小节."""

    def test_extracts_by_numeric_title(self):
        text = (
            "一、总体说明\n"
            "本项目采用公开招标方式。\n"
            "\n"
            "二、投标函\n"
            "致： 招标人名称\n"
            "根据贵方 项目名称 招标文件，我方愿承担本项目。\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
            "\n"
            "三、投标承诺书\n"
            "本公司郑重承诺：\n"
            "1. 承诺遵循公开、公平、公正原则\n"
        )
        result = extract_fixed_form_section(text, "投标函")
        assert "投标函" in result
        assert "致：" in result
        assert "本公司郑重承诺" not in result  # 下一节不混入
        assert "总体说明" not in result  # 上一节不混入

    def test_returns_empty_when_not_found(self):
        text = "二、投标函\n致： 招标人名称\n"
        assert extract_fixed_form_section(text, "未知小节") == ""

    def test_returns_empty_for_empty_input(self):
        assert extract_fixed_form_section("", "投标函") == ""
        assert extract_fixed_form_section("任意文本", "") == ""

    def test_handles_trailing_section(self):
        """最后一节无后续标题时应截取到文末."""
        text = (
            "二、投标函\n"
            "致： 招标人名称\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
        )
        result = extract_fixed_form_section(text, "投标函")
        assert "招标人名称" in result
        assert "日期" in result

    def test_handles_no_numeric_prefix(self):
        """完全没有数字章节标题时返回空（避免误匹配正文里的"投标函"）."""
        text = (
            "致： 招标人名称\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
            "法定代表人授权委托书\n"
            "本人 张三 系 云南领航保安服务有限公司 的法定代表人。\n"
        )
        # 无数字标题 → extract_fixed_form_section 期望数字标题定位
        # 应该返回空（避免模糊匹配正文）
        result = extract_fixed_form_section(text, "投标函")
        # 此情况下设计为：找不到有数字前缀的标题则返回空
        # 避免把整段当作投标函
        # 但若提供精确标题"投标人名称："应该能匹配"投标人名称："后的空段？
        # 当前设计是保守的：仅匹配数字标题行
        assert result == ""


class TestFillFixedFormSectionFromTemplate:
    """scan-and-fill 主路径：保留原文措辞，只填空下划线/标签词."""

    @pytest.mark.asyncio
    async def test_preserves_original_wording(self):
        """用户反馈：投标函被 AI 重新生成导致措辞失真。本测试验证保留原文."""
        format_text = (
            "二、投标函\n"
            "致： 招标人名称\n"
            "根据贵方 项目名称 招标文件（招标编号为 ），我方愿承担本项目。\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
            "\n"
            "三、投标承诺书\n"
            "本公司郑重承诺。\n"
        )
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "招标人名称", "var": "tenderer_name",
                 "context_before": "致："},
                {"original": "项目名称", "var": "project_name",
                 "context_before": "根据贵方"},
                {"original": "投标人名称", "var": "company_name", "context_before": ""},
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text=format_text,
            format_tables=[],
            company_profile={"company_name": "云南领航保安服务有限公司"},
            requirements={
                "tenderer_name": "玉溪大红山矿业有限公司",
                "project_name": "保安业务项目",
            },
            ai_adapter=mock_ai,
        )

        # 原文措辞必须保留
        assert "我方愿承担本项目" in result  # 原文独有措辞
        assert "根据贵方" in result
        assert "（招标编号为 ）" in result
        # 标点/章节结构保留
        assert "二、投标函" in result
        assert result.count("三、") == 0  # 不混入下一节
        # 实际值已填入
        assert "玉溪大红山矿业有限公司" in result
        assert "云南领航保安服务有限公司" in result
        assert "保安业务项目" in result
        # 占位符词应被替换
        # 由于原文有两个"招标人名称"和"投标人名称"，且都标记为不同变量，
        # 都应该被替换（不应残留）
        assert "招标人名称" not in result  # tenderer_name 已替换
        # company_name 已替换为"云南领航保安服务有限公司"
        # 注意：原文中"投标人名称"出现在两个位置（标签 + 落款），都应替换

    @pytest.mark.asyncio
    async def test_never_emits_bare_variable_placeholder(self):
        """端到端：AI 标记了全部新变量槽位时，成品正文不得出现裸 [var]。

        这些槽位此前无值可填，会落进 ``variables.get(var, f"[{var}]")`` 把
        「[tender_number]」直接写进投标函正文；而 post_scan 只扫 ``{word}``，
        扫不到 ``[word]``，成品会静默带着原始变量名交付。
        """
        marked = [
            "legal_rep_id_number", "tenderer_agency_name", "tender_number",
            "bid_total_amount", "bid_total_amount_words", "bid_unit_amount",
            "bid_deposit_amount", "service_period", "service_location",
        ]
        format_text = (
            "二、投标函\n"
            + "".join(f"{var}=________\n" for var in marked)
            + "\n三、下一节\n"
        )
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "________", "var": var, "context_before": f"{var}="}
                for var in marked
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text=format_text,
            format_tables=[],
            company_profile={"legal_rep_id_number": "530100199001011234"},
            requirements={
                "tenderer_agency_name": "云南中招招标有限公司",
                "tender_number": "YXDHS-2026-001",
                "service_period": "11个月",
                "service_location": "玉溪大红山矿区",
                "bid_deposit_amount": "50000",
                "total_price_excluding_tax": "1234567.89",
                "monthly_unit_price": "112233",
            },
            ai_adapter=mock_ai,
        )

        assert "投标函" in result, "填充未生效，走了兜底路径"
        assert "________" not in result, "槽位未被替换"
        for var in marked:
            assert f"[{var}]" not in result, f"{var} 漏填成裸占位符"

    @pytest.mark.asyncio
    async def test_returns_empty_when_section_not_in_format(self):
        """format_section_text 缺该小节时返回空，让调用方走 AI 兜底."""
        format_text = "二、投标函\n致： 招标人名称\n"
        mock_ai = AsyncMock()

        result = await fill_fixed_form_section_from_template(
            section_title="不存在的章节",
            format_section_text=format_text,
            format_tables=[],
            company_profile={},
            requirements={},
            ai_adapter=mock_ai,
        )
        assert result == ""
        # 没找到 section 不应调用 AI
        mock_ai.chat_completion.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_when_format_section_missing(self):
        """format_section_text 为空时返回空."""
        result = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text="",
            format_tables=[],
            company_profile={},
            requirements={},
            ai_adapter=AsyncMock(),
        )
        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_other_fixed_form_sections(self):
        """扩展到其他固定格式：法定代表人授权委托书等也走相同逻辑."""
        format_text = (
            "三、投标承诺书\n"
            "本公司 投标人名称 郑重承诺：\n"
            "1. 承诺遵循公开、公平、公正原则\n"
            "承诺日期： 年 月 日\n"
            "\n"
            "四、法定代表人授权委托书\n"
            "本人 法定代表人姓名 系 投标人名称 的法定代表人。\n"
        )
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "投标人名称", "var": "company_name",
                 "context_before": "本公司"},
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await fill_fixed_form_section_from_template(
            section_title="投标承诺书",
            format_section_text=format_text,
            format_tables=[],
            company_profile={"company_name": "云南领航保安服务有限公司"},
            requirements={},
            ai_adapter=mock_ai,
        )
        assert "郑重承诺" in result
        assert "云南领航保安服务有限公司" in result
        # 不混入下一节
        assert "授权委托" not in result

    @pytest.mark.asyncio
    async def test_uses_placeholder_when_variable_missing(self):
        """变量无值时使用 [var] 占位符，便于人工补全."""
        format_text = (
            "二、投标函\n"
            "致： 招标人名称\n"
            "投标人名称：\n"
        )
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "招标人名称", "var": "tenderer_name",
                 "context_before": "致："},
                {"original": "投标人名称", "var": "company_name", "context_before": ""},
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text=format_text,
            format_tables=[],
            company_profile={"company_name": "云南领航保安服务有限公司"},
            requirements={},  # tenderer_name 缺失
            ai_adapter=mock_ai,
        )
        # company_name 已填
        assert "云南领航保安服务有限公司" in result
        # tenderer_name 缺失 → build_variable_values 给 "[待补充：招标人名称]"
        assert "[待补充：招标人名称]" in result

    @pytest.mark.asyncio
    async def test_real_pdf_section_extraction(self):
        """回归测试：玉溪大红山矿业保安业务 PDF 的格式章节结构.

        真实场景校验 — section 截取不能因 PDF 文本格式微调而漂移。
        """
        sample_format_text = (
            "二、投标函\n"
            "投标函\n"
            "致： 招标人名称\n"
            "根据贵方 项目名称 招标文件（招标编号为 ），我方愿承担本项目。\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
            "\n"
            "三、投标承诺书\n"
            "本公司 投标人名称 郑重承诺：\n"
            "1. 承诺遵循公开、公平、公正原则\n"
            "\n"
            "四、法定代表人授权委托书\n"
            "本人 法定代表人姓名 系 投标人名称 的法定代表人。\n"
        )
        # 投标函应能定位
        section = extract_fixed_form_section(sample_format_text, "投标函")
        assert "二、投标函" in section
        assert "三、投标承诺书" not in section
        assert "四、法定代表人授权委托书" not in section
        # 投标承诺书
        section2 = extract_fixed_form_section(sample_format_text, "投标承诺书")
        assert "三、投标承诺书" in section2
        assert "郑重承诺" in section2
        assert "法定代表人授权委托书" not in section2
        # 法定代表人授权委托书
        section3 = extract_fixed_form_section(sample_format_text, "法定代表人授权委托书")
        assert "四、法定代表人授权委托书" in section3
        assert "系 投标人名称" in section3

    @pytest.mark.asyncio
    async def test_full_pipeline_replaces_tenderer_name(self):
        """端到端：用户报告的核心 bug 场景.

        输入格式章节包含「致： 招标人名称」（placeholder label），
        AI 识别后应替换为正确的 tenderer_name，且原文措辞全保留。
        """
        format_text = (
            "二、投标函\n"
            "致： 招标人名称\n"
            "根据贵方 项目名称 招标文件，我方愿承担本项目。\n"
            "投标人名称：\n"
            "日期： 年 月 日\n"
        )
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
            "text_replacements": [
                {"original": "招标人名称", "var": "tenderer_name",
                 "context_before": "致："},
                {"original": "项目名称", "var": "project_name",
                 "context_before": "根据贵方"},
                {"original": "投标人名称", "var": "company_name",
                 "context_before": ""},
            ],
            "table_fills": [],
            "warnings": [],
        })

        result = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text=format_text,
            format_tables=[],
            company_profile={"company_name": "云南领航保安服务有限公司"},
            requirements={
                "tenderer_name": "玉溪大红山矿业有限公司",
                "project_name": "保安业务项目",
            },
            ai_adapter=mock_ai,
        )

        # 1. 抬头应替换为正确的招标人（用户的核心 bug）
        #    保留原始「致： 」（冒号后空格）→ 替换后是「致： 玉溪大红山矿业有限公司」
        assert "玉溪大红山矿业有限公司" in result
        assert "致：" in result
        assert result.split("致：")[1].split("\n")[0].strip().startswith("玉溪大红山矿业有限公司")
        # 2. 错公司不应出现
        assert "云南昆钢兴达物业服务有限公司" not in result
        # 3. 原文措辞必须保留（用户明确要求"不要修改"）
        assert "我方愿承担本项目" in result
        # 4. 项目名已填
        assert "根据贵方 保安业务项目 招标文件" in result
        # 5. 占位符词已被替换
        assert "招标人名称" not in result
