import pytest
from app.services.format_extractor import (
    locate_format_section,
    _find_by_keyword,
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
