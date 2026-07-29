"""端到端格式合规集成测试."""
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.format_extractor import (
    locate_format_section,
    extract_format_template,
    extract_format_from_document,
)
from app.services.format_verifier import verify_format

# Padding to meet the 500-char minimum document length in locate_format_section
_PADDING = (
    "本项目为公开招标项目，投标人应仔细阅读招标文件的所有内容，"
    "按照招标文件的要求编制投标文件，并保证所提供全部资料的真实性、"
    "准确性和完整性，以使其投标对招标文件做出实质性响应。"
    "否则，其投标将被拒绝。投标人应承担其编制投标文件与递交投标"
    "文件所涉及的一切费用。无论投标结果如何，招标人均无义务向投标"
    "人解释其未中标的原因。"
)

FULL_TENDER_TEXT = f"""
第一章 招标公告
某单位保安服务采购项目招标公告...
{_PADDING}

第二章 投标人须知
投标人须具备以下条件...
{_PADDING}

第五章 投标文件格式
投标文件由以下部分组成：

一、商务部分
（一）开标一览表
投标报价应按以下格式填写：

| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |
|------|---------|----------------|----------|--------------|------|
|      |         |                |          |              |      |

投标人：（公章）
法定代表人或授权代理人：（签字）
日期：  年  月  日

（二）投标函
致：〔招标人名称〕
我方已仔细阅读并充分理解贵方招标文件的全部内容...
{_PADDING}

第六章 合同条款
...
"""

MOCK_AI_FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "confidence": 0.9,
            "children": [
                {
                    "number": "（一）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {
                        "columns": [
                            {"name": "序号"},
                            {"name": "服务内容"},
                            {"name": "不含税单价（元）"},
                            {"name": "税率（%）"},
                            {"name": "含税总价（元）"},
                            {"name": "备注"},
                        ]
                    },
                    "signature_block": {
                        "lines": [
                            "投标人：（公章）",
                            "法定代表人或授权代理人：（签字）",
                            "日期：  年  月  日",
                        ]
                    },
                    "confidence": 0.9,
                },
            ],
        },
    ],
    "global_format_rules": {
        "numbering_style": "chinese_legal",
        "confidence": 0.85,
    },
    "extraction_metadata": {"warnings": []},
}


@pytest.mark.asyncio
async def test_full_pipeline_locate_extract_verify():
    """端到端测试：定位→提取→校验."""
    # 1. 定位
    location = await locate_format_section(FULL_TENDER_TEXT)
    assert location is not None
    assert location["method"] == "keyword_match"
    assert "一、商务部分" in location["section_text"]

    # 2. AI 提取（mock）
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = json.dumps(
        MOCK_AI_FORMAT_TEMPLATE, ensure_ascii=False
    )
    template = await extract_format_template(
        location["section_text"], mock_ai,
    )
    assert len(template["document_structure"]) == 1
    assert template["document_structure"][0]["title"] == "商务部分"

    # 3. 校验
    chapters_pass = [
        {
            "title": "一、商务部分",
            "content": (
                "| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |\n"
                "|---|---|---|---|---|---|\n"
                "| 1 | 保安服务 | 1000 | 6 | 1060 | |\n\n"
                "投标人：（公章）\n法定代表人或授权代理人：（签字）\n日期：2026年7月29日"
            ),
        }
    ]
    result = verify_format(chapters_pass, template)
    assert result["overall_status"] in ("pass", "pass_with_warnings")

    # 4. 校验 — 列不匹配的章节
    chapters_bad_table = [
        {
            "title": "一、商务部分",
            "content": (
                "| 编号 | 描述 | 金额 |\n|---|---|---|\n| 1 | 保安 | 1000 |"
            ),
        }
    ]
    result_bad = verify_format(chapters_bad_table, template)
    assert result_bad["warning_count"] > 0 or result_bad["fail_count"] > 0


@pytest.mark.asyncio
async def test_extract_format_from_document():
    """端到端：从完整文档提取格式模板."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = json.dumps(
        MOCK_AI_FORMAT_TEMPLATE, ensure_ascii=False
    )
    template = await extract_format_from_document(FULL_TENDER_TEXT, mock_ai)
    assert template is not None
    assert "document_structure" in template
    assert template["extraction_metadata"]["location_method"] == "keyword_match"


@pytest.mark.asyncio
async def test_extract_format_from_document_no_section():
    """端到端：无格式章节的文档."""
    mock_ai = AsyncMock()
    template = await extract_format_from_document(
        "只有招标公告和投标人须知，没有格式章节", mock_ai,
    )
    assert template is None


def test_verify_empty_format_template():
    """没有格式模板时校验应跳过."""
    result = verify_format(
        [{"title": "任意", "content": "..."}],
        {"document_structure": []},
    )
    assert result["overall_status"] == "pass"
