"""宏曦标书 - 表格类章节生成 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_pipeline import _generate_table_chapter

CORPUS = "一、开标一览表\n\n序号 | 服务内容 | 报价\n"
TABLES = [{
    "page": 33, "table_index": 0,
    "rows": [["序号", "服务内容", "报价"], ["1", "安保服务", ""]],
}]


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


@pytest.mark.asyncio
async def test_uses_tender_table_columns_not_ai():
    """场景：用户选了「表格」——必须照招标表格的列定义，不是 AI 编的表。"""
    ch = _Chapter("开标一览表", {"match": {
        "status": "matched", "source": "table", "table_index": 0,
        "start": 0, "end": len(CORPUS),
    }})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": TABLES},
        company_profile={"company_name": "某某公司"}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 | 报价 |" in content
    assert "安保服务" in content
    assert warnings == []


@pytest.mark.asyncio
async def test_falls_back_to_declared_columns_when_no_table():
    """表格索引失效且重匹配不到 → 只出 table_columns 声明的空表头，不编数据。"""
    ch = _Chapter("开标一览表", {
        "table_columns": ["序号", "服务内容"],
        "match": {"status": "missing"},
    })
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": []},
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 |" in content
    assert any("未找到招标表格" in w for w in warnings)


@pytest.mark.asyncio
async def test_no_table_and_no_columns_returns_empty_with_warning():
    ch = _Chapter("某表", {"match": {"status": "missing"}})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": []},
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert content == ""
    assert warnings


@pytest.mark.asyncio
async def test_never_calls_ai_free_generation():
    """表格章节不得再走 AI 自由生成——这正是要修的 bug。"""
    ch = _Chapter("开标一览表", {"match": {"status": "matched", "source": "table",
                                           "table_index": 0, "start": 0,
                                           "end": len(CORPUS)}})
    meta = json.loads(ch.chapter_meta_json)
    with patch("app.services.template_filler.generate_file_section",
               new=AsyncMock(side_effect=AssertionError("不该调用 AI 自由生成"))):
        content, _ = await _generate_table_chapter(
            ch, meta, {"format_section_text": CORPUS, "format_tables": TABLES},
            company_profile={}, ai_adapter=None, full_text=None,
        )
    assert content


@pytest.mark.asyncio
async def test_out_of_range_table_index_relooks_up_by_title():
    """索引失效 → 按标题重匹配一张（靠命中小节的页码就近取表），并告警。"""
    ch = _Chapter("开标一览表", {"match": {
        "status": "matched", "source": "table", "table_index": 99,
        "start": 0, "end": len(CORPUS),
    }})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {
            "format_section_text": CORPUS, "format_tables": TABLES,
            "format_page_map": [{"start": 0, "page": 32}],
        },
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 | 报价 |" in content
    assert any("表格索引已失效" in w for w in warnings)


@pytest.mark.asyncio
async def test_without_page_map_warns_instead_of_guessing_a_table():
    """没有页码映射时，标题对不上表头就不猜表——出空表头并如实告警.

    对废标敏感的文档，宁可给空表头让用户看见，也不能把**猜错的**那张表
    塞进标书。这里是"未找到招标表格"这句告警为真的边界。
    """
    ch = _Chapter("开标一览表", {"match": {
        "status": "matched", "source": "table", "table_index": 99,
        "start": 0, "end": len(CORPUS),
    }, "table_columns": ["序号", "服务内容"]})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": TABLES},
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 |" in content
    assert "安保服务" not in content, "不得猜表——猜错就是错表进标书"
    assert any("未找到招标表格" in w for w in warnings)
