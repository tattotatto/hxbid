from unittest.mock import AsyncMock

import pytest
from app.services.format_extractor import (
    extract_format_from_document,
    extract_format_template,
    locate_format_section,
    _fallback_tail,
    _find_by_keyword,
    _find_by_semantic_chunks,
    _find_by_toc,
    _split_by_chapter_headers,
)

# 填充文本，用于满足 locate_format_section 的 500 字符最低要求
_PADDING = (
    "本项目为公开招标项目，投标人应仔细阅读招标文件的所有内容，"
    "按照招标文件的要求编制投标文件，并保证所提供全部资料的真实性、"
    "准确性和完整性，以使其投标对招标文件做出实质性响应。"
    "否则，其投标将被拒绝。投标人应承担其编制投标文件与递交投标"
    "文件所涉及的一切费用。无论投标结果如何，招标人均无义务向投标"
    "人解释其未中标的原因。"
)

SAMPLE_TEXT_CHAPTER6 = f"""
第一章 招标公告...{_PADDING}
第二章 投标人须知...{_PADDING}
第六章 投标文件格式
一、商务部分
（一）开标一览表
（二）投标函
二、技术部分
（一）项目投入服务人员一览表
{_PADDING}
第七章 合同条款...
"""

SAMPLE_TEXT_CHAPTER5 = f"""
第三章 招标内容...{_PADDING}
第五章 投标书格式
一、投标函
二、法定代表人证明
三、投标报价表
{_PADDING}
第六章 评标办法...
"""


def test_find_by_keyword_chapter6():
    result = _find_by_keyword(SAMPLE_TEXT_CHAPTER6)
    assert result is not None
    assert "第六章" in result["chapter_title"]
    assert result["method"] == "keyword_match"


def test_find_by_keyword_chapter5():
    result = _find_by_keyword(SAMPLE_TEXT_CHAPTER5)
    assert result is not None
    assert "第五章" in result["chapter_title"]


def test_locate_chapter6_async():
    import asyncio
    result = asyncio.run(locate_format_section(SAMPLE_TEXT_CHAPTER6))
    assert result is not None
    assert "一、商务部分" in result["section_text"]


def test_locate_short_text():
    import asyncio
    result = asyncio.run(locate_format_section("短文本"))
    assert result is None


def test_split_by_chapter_headers():
    chapters = _split_by_chapter_headers(SAMPLE_TEXT_CHAPTER6)
    assert len(chapters) >= 2
    assert any("投标文件格式" in c["title"] for c in chapters)


# ---------------------------------------------------------------------------
# Strategy 2 — TOC inference
# ---------------------------------------------------------------------------

# 目录中包含格式章节引用，正文在偏移 2000 字符之后
SAMPLE_TOC = (
    "目  录\n"
    "第一章  招标公告\n"
    "第六章  投标文件格式\n"
    + _PADDING * 16  # ~2080 chars, pushes body past _TOC_BODY_SEEK
    + "\n第六章  投标文件格式\n一、商务部分\n（一）投标函\n（二）开标一览表\n" + _PADDING
)


def test_find_by_toc_locates_format():
    """Strategy 2: locate format chapter via TOC reference."""
    result = _find_by_toc(SAMPLE_TOC)
    assert result is not None
    assert result["method"] == "toc_inference"
    assert "投标文件格式" in result["chapter_title"]
    assert "商务部分" in result["section_text"]


def test_find_by_toc_no_toc_section():
    """Strategy 2 returns None when no TOC section exists."""
    result = _find_by_toc("无目录的普通文档。" + _PADDING)
    assert result is None


# ---------------------------------------------------------------------------
# Strategy 3 — semantic chunking
# ---------------------------------------------------------------------------

SAMPLE_SEMANTIC = (
    "第一章 项目概述\n项目背景介绍。" + _PADDING * 2
    + "\n第二章 投标文件编制要求\n投标文件格式应包含：投标函、开标一览表、"
    + "法定代表人授权委托书、承诺书。以上文件需签章。装订应符合目录要求。\n" + _PADDING
    + "\n第三章 技术规格\n技术参数说明。" + _PADDING
)


def test_find_by_semantic_chunks_scores():
    """Strategy 3: semantic chunking finds format chapter by keyword scoring."""
    chapters = _split_by_chapter_headers(SAMPLE_SEMANTIC)
    assert len(chapters) >= 3
    result = _find_by_semantic_chunks(chapters)
    assert result is not None
    assert result["method"] == "semantic_chunking"
    assert "投标文件编制" in result["chapter_title"]


def test_find_by_semantic_chunks_no_match():
    """Strategy 3 returns None when no chapter scores >= 3."""
    text = (
        "第一章 项目概述\n" + _PADDING * 2
        + "\n第二章 技术规格\n" + _PADDING
    )
    chapters = _split_by_chapter_headers(text)
    result = _find_by_semantic_chunks(chapters)
    assert result is None


# ---------------------------------------------------------------------------
# Strategy 4 — tail fallback
# ---------------------------------------------------------------------------

def test_fallback_tail_long_document():
    """Strategy 4: take last 40% of a long document as format section."""
    long_text = "招标文件正文。" + _PADDING * 30  # ~3900 chars, tail > 1000
    result = _fallback_tail(long_text)
    assert result is not None
    assert result["method"] == "tail_fallback"
    assert result["chapter_title"] == "投标文件格式（自动定位）"
    assert len(result["section_text"]) <= 10000


def test_fallback_tail_too_short():
    """Strategy 4 returns None when tail is too short."""
    short_text = "短文本" * 50  # ~150 chars, tail ~60 chars < _TAIL_MIN_CHARS
    result = _fallback_tail(short_text)
    assert result is None


# ---------------------------------------------------------------------------
# Task 3 — AI format template extraction tests
# ---------------------------------------------------------------------------

MOCK_SECTION = """
一、商务部分
（一）开标一览表
投标人应根据本招标文件的要求编制投标报价，格式如下：

| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |
|------|---------|----------------|----------|--------------|------|
|      |         |                |          |              |      |

投标人：（公章）
法定代表人或授权代理人：（签字）
日期：  年  月  日

（二）投标函
致：〔招标人名称〕
我方（投标人名称）已仔细阅读并充分理解贵方招标文件的全部内容...
"""


@pytest.mark.asyncio
async def test_extract_format_template_structure():
    """验证AI提取返回格式模板的基本结构."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = '''{
        "document_structure": [
            {
                "number": "一",
                "title": "商务部分",
                "required": true,
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
                                {"name": "备注"}
                            ]
                        },
                        "signature_block": {
                            "lines": ["投标人：（公章）", "法定代表人或授权代理人：（签字）", "日期：  年  月  日"]
                        },
                        "confidence": 0.9
                    }
                ]
            }
        ],
        "global_format_rules": {
            "numbering_style": "chinese_legal",
            "confidence": 0.8
        },
        "extraction_metadata": {"warnings": []}
    }'''

    result = await extract_format_template(MOCK_SECTION, mock_ai)

    assert "document_structure" in result
    assert len(result["document_structure"]) == 1
    assert result["document_structure"][0]["title"] == "商务部分"

    first_child = result["document_structure"][0]["children"][0]
    assert first_child["type"] == "table"
    assert len(first_child["table_schema"]["columns"]) == 6
    assert "signature_block" in first_child


@pytest.mark.asyncio
async def test_extract_format_template_ai_failure():
    """验证AI调用失败时返回降级空模板."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.side_effect = Exception("AI timeout")

    result = await extract_format_template(MOCK_SECTION, mock_ai)

    assert result["document_structure"] == []
    assert "error" in result.get("extraction_metadata", {})


@pytest.mark.asyncio
async def test_extract_format_from_document_happy_path():
    """验证完整提取流水线：定位成功 + AI提取成功，并合并定位元数据."""
    from unittest.mock import patch

    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = '''{
        "document_structure": [
            {
                "number": "一",
                "title": "商务部分",
                "required": true,
                "confidence": 0.95,
                "children": [
                    {
                        "number": "（一）",
                        "title": "开标一览表",
                        "type": "table",
                        "table_schema": {
                            "columns": [
                                {"name": "序号"},
                                {"name": "服务内容"}
                            ]
                        },
                        "signature_block": {
                            "lines": ["投标人：（公章）"]
                        },
                        "confidence": 0.9
                    }
                ]
            }
        ],
        "global_format_rules": {
            "numbering_style": "chinese_legal",
            "confidence": 0.8
        },
        "extraction_metadata": {"warnings": []}
    }'''

    mock_location = {
        "chapter_number": "六",
        "chapter_title": "第六章 投标文件格式",
        "section_text": MOCK_SECTION,
        "method": "keyword_match",
    }

    with patch(
        "app.services.format_extractor.locate_format_section",
        AsyncMock(return_value=mock_location),
    ):
        result = await extract_format_from_document(SAMPLE_TEXT_CHAPTER6, mock_ai)

    assert result is not None
    assert "document_structure" in result
    assert len(result["document_structure"]) == 1
    assert result["document_structure"][0]["title"] == "商务部分"

    # 验证定位元数据已合并到提取结果
    assert result["extraction_metadata"]["location_method"] == "keyword_match"
    assert result["extraction_metadata"]["chapter_title"] == "第六章 投标文件格式"


@pytest.mark.asyncio
async def test_extract_format_from_document_no_section():
    """验证无法定位格式章节时返回None."""
    mock_ai = AsyncMock()
    result = await extract_format_from_document("无格式章节的短文本", mock_ai)
    assert result is None
