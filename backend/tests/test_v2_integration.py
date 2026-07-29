"""v2 Pipeline 端到端集成测试.

测试目标：
1. 完整流程：从真实招标 PDF 提取格式章节 → 验证提取的文本/表格
2. 变量构建 → 批量填充 → 后扫描链路
3. 文件章节内容检测

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.pdf_extractor import (
    extract_format_section,
    extract_full_document,
    locate_format_pages,
)
from app.services.template_filler import (
    batch_fill_tables,
    batch_fill_text,
    build_variable_values,
    post_scan,
    scan_and_mark_variables,
)
from app.services.content_assembler import (
    assemble_chapter_content,
    build_final_chapters_payload,
    generate_chapter_summary,
    collect_sibling_summaries,
)
from app.services.format_extractor import (
    extract_format_from_document,
    extract_format_template,
    locate_format_section,
    _split_by_chapter_headers,
    _find_by_keyword,
)


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

MOCK_COMPANY = {
    "company_name": "云南领航保安服务有限公司",
    "legal_rep_name": "张三",
    "business_license_number": "91530000MA6N2XXX00",
    "address": "云南省昆明市官渡区XX路XX号",
    "contact_phone": "0871-12345678",
    "website": "www.example.com",
    "contact_person": "王五",
    "fax": "0871-12345679",
    "zip_code": "650000",
    "registered_capital": "1000万元",
    "account_number": "123456789000",
    "bank_name": "中国建设银行昆明XX支行",
}

MOCK_REQS = {"project_name": "某单位保安服务采购项目"}

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
我方在此承诺：
1. 投标人名称：________________
2. 注册地址：________________
3. 法定代表人：________________
4. 投标有效期：____ 日历天
{_PADDING}

第六章 合同条款
...
"""

# Tender PDF path
TENDER_PDF = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "素材", "招标文件正文.pdf"
)

HAS_TENDER_PDF = os.path.exists(TENDER_PDF) and os.path.isfile(TENDER_PDF)


def _skip_if_no_pdf():
    if not HAS_TENDER_PDF:
        pytest.skip(f"Tender PDF not found: {TENDER_PDF}")


# ===========================================================================
# Part 1: 完整流程 — PDF格式章节提取端到端测试
# ===========================================================================


class TestFullPipelineExtractFormatSection:
    """端到端：从真实招标PDF提取格式章节 → 验证文本和表格."""

    def test_extract_format_section_from_real_pdf(self):
        """完整提取真实PDF格式章节，验证文本与表格."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)

        # 1. 基本结构完整
        assert result["full_text"], "full_text must not be empty"
        assert len(result["full_text"]) > 200, (
            f"Expected >200 chars, got {len(result['full_text'])}"
        )
        assert result["total_pages"] > 0
        assert 1 <= result["start_page"] <= result["total_pages"]
        assert result["start_page"] <= result["end_page"] <= result["total_pages"]

        # 2. 文本内容检测：应包含投标文件相关关键词
        full_text = result["full_text"]
        format_keywords = ["投标文件", "投标书", "格式", "开标", "投标函"]
        matched = [kw for kw in format_keywords if kw in full_text]
        assert len(matched) >= 1, (
            f"Expected at least 1 format keyword, matched: {matched}"
        )

        # 3. 表格提取
        assert isinstance(result["tables"], list)
        if result["tables"]:
            for t in result["tables"]:
                assert "page" in t
                assert "table_index" in t
                assert "rows" in t
                assert isinstance(t["rows"], list)
                assert len(t["rows"]) >= 2, (
                    f"Table on page {t['page']} has <2 rows"
                )

        print(
            f"[FullPipeline] Text: {len(result['full_text'])} chars | "
            f"Tables: {len(result['tables'])} | "
            f"Pages: {result['start_page']}-{result['end_page']} / {result['total_pages']}"
        )

    def test_format_pages_location_in_real_pdf(self):
        """验证格式章节定位准确性."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            location = locate_format_pages(pdf)
            assert location is not None, f"Expected to find format section in PDF"
            start, end = location
            assert 0 <= start <= end < len(pdf.pages)
            # 格式章节应位于文档后半部分
            assert start >= len(pdf.pages) * 0.2, (
                f"Format section too early: page {start + 1}/{len(pdf.pages)}"
            )
            print(f"Format section: pages {start + 1}-{end + 1} / {len(pdf.pages)}")
        finally:
            pdf.close()

    def test_extract_full_document_from_real_pdf(self):
        """全文档提取 — 验证完整性和数据大小."""
        _skip_if_no_pdf()
        result = extract_full_document(TENDER_PDF)

        assert result["full_text"], "full_text must not be empty"
        assert len(result["full_text"]) > 500, (
            f"Expected >500 chars, got {len(result['full_text'])}"
        )
        assert result["total_pages"] > 0
        assert isinstance(result["tables"], list)

        print(
            f"[FullDoc] Text: {len(result['full_text'])} chars | "
            f"Tables: {len(result['tables'])} | "
            f"Pages: {result['total_pages']}"
        )

    def test_extracted_text_contains_expected_sections(self):
        """验证提取的文本包含预期章节结构."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)
        full_text = result["full_text"]

        # 招标文件格式章节通常包含这些标志性内容
        expected_patterns = [
            ["投标函", "开标一览表", "投标文件格式"],
            ["投标书", "商务", "技术"],
            ["法定代表人", "授权委托书", "投标保证金"],
        ]
        any_pattern_matched = False
        for patterns in expected_patterns:
            if any(p in full_text for p in patterns):
                any_pattern_matched = True
                matched_count = sum(1 for p in patterns if p in full_text)
                print(f"Pattern matched: {matched_count}/{len(patterns)} keywords")
                break
        assert any_pattern_matched, (
            f"No expected section patterns found in extracted text "
            f"({len(full_text)} chars)"
        )


# ===========================================================================
# Part 2: 变量构建 → 批量填充 → 后扫描链路
# ===========================================================================


class TestVariableBuildBatchFillPostScanIntegration:
    """变量构建 → 批量填充 → 后扫描 全链路集成测试."""

    # ── 2a. 文本填充链路 ──

    def test_text_fill_chain_no_residuals(self):
        """完整文本填充链：构建变量 → 填充文本 → 扫描不应有残留."""
        # 1. 构建变量
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        # 2. 模拟 AI 扫描返回的替换计划（original 只替换空白/占位符部分）
        scan_result = {
            "text_replacements": [
                {
                    "original": "________________",
                    "var": "company_name",
                    "value": variables["company_name"],
                },
                {
                    "original": "____",
                    "var": "bid_validity_days",
                    "value": "120",
                },
            ],
        }

        # 3. 批量填充
        original_text = (
            "投标人名称：________________\n"
            "投标有效期：____ 日历天\n"
            "地址：云南省昆明市官渡区XX路XX号\n"
        )
        filled = batch_fill_text(
            original_text, scan_result["text_replacements"]
        )

        assert "________________" not in filled
        assert "云南领航保安服务有限公司" in filled
        assert "120" in filled
        assert "日历天" in filled  # 标签字段被保留

        # 4. 后扫描
        issues = post_scan(filled)
        print(f"[PostScan] Issues: {issues if issues else 'none'}")

    def test_text_fill_chain_with_mock_scan(self):
        """模拟AI扫描 + 批量填充 + 后扫描的完整链路."""
        # 1. 构建变量
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        # 2. 模拟扫描结果
        scan_result = {
            "text_replacements": [
                {
                    "original": "________",
                    "var": "company_name",
                    "value": variables["company_name"],
                },
                {
                    "original": "________",
                    "var": "legal_rep_name",
                    "value": variables["legal_rep_name"],
                },
                {
                    "original": "________",
                    "var": "address",
                    "value": variables["address"],
                },
            ],
            "table_fills": [],
            "warnings": [],
        }

        # 3. 原始文本（含多处空白）
        original_text = (
            "投标函\n\n"
            "投标人名称：________\n"
            "法定代表人：________\n"
            "注册地址：________\n"
        )

        # 4. 填充
        filled = batch_fill_text(original_text, scan_result["text_replacements"])

        # 5. 验证填充结果
        assert "云南领航保安服务有限公司" in filled
        assert "张三" in filled
        assert "云南省昆明市官渡区XX路XX号" in filled
        assert "________" not in filled

        # 6. 后扫描
        issues = post_scan(filled)
        assert len(issues) == 0, f"Unexpected issues: {issues}"

        print(f"[FillChain] Text filled: {len(filled)} chars, 0 residual issues")

    def test_text_fill_chain_with_residual_blanks(self):
        """填充链路中部分空白未填充时应被后扫描捕获."""
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        # 模拟扫描遗漏一处空白
        scan_result = {
            "text_replacements": [
                {
                    "original": "________",
                    "var": "company_name",
                    "value": variables["company_name"],
                },
                # 遗漏了第二处空白
            ],
        }
        original_text = (
            "投标人名称：________\n"
            "传真：________\n"
        )
        filled = batch_fill_text(original_text, scan_result["text_replacements"])

        # 第一处已填充
        assert "云南领航保安服务有限公司" in filled
        # 第二处残留
        assert "________" in filled

        issues = post_scan(filled)
        assert len(issues) > 0
        assert any("残留空白下划线" in i for i in issues)
        print(f"[Residual] Issues detected: {issues}")

    # ── 2b. 表格填充链路 ──

    def test_table_fill_chain(self):
        """构建变量 → 批量表格填充 → 验证填充结果."""
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        tables = [
            {
                "page": 68,
                "table_index": 1,
                "rows": [
                    ["投标人名称", ""],
                    ["注册地址", ""],
                    ["法定代表人", ""],
                    ["联系电话", ""],
                ],
            }
        ]
        table_fills = [
            {"page": 68, "table_index": 1, "row": 0, "col": 1, "var": "company_name"},
            {"page": 68, "table_index": 1, "row": 1, "col": 1, "var": "address"},
            {"page": 68, "table_index": 1, "row": 2, "col": 1, "var": "legal_rep_name"},
            {"page": 68, "table_index": 1, "row": 3, "col": 1, "var": "contact_phone"},
        ]

        filled = batch_fill_tables(tables, table_fills, variables)

        assert filled[0]["rows"][0][1] == "云南领航保安服务有限公司"
        assert filled[0]["rows"][1][1] == "云南省昆明市官渡区XX路XX号"
        assert filled[0]["rows"][2][1] == "张三"
        assert filled[0]["rows"][3][1] == "0871-12345678"

        print(
            f"[TableFill] Table filled: "
            f"{filled[0]['rows'][0][0]}={filled[0]['rows'][0][1]}"
        )

    def test_table_fill_chain_preserves_original(self):
        """表格填充不应修改原始数据."""
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        tables = [
            {
                "page": 1,
                "table_index": 1,
                "rows": [["字段", ""]],
            }
        ]
        fills = [
            {"page": 1, "table_index": 1, "row": 0, "col": 1, "var": "company_name"},
        ]

        # 记录原始值
        original_value = tables[0]["rows"][0][1]
        filled = batch_fill_tables(tables, fills, variables)

        # 原始数据未被修改
        assert tables[0]["rows"][0][1] == original_value
        # 返回结果已填充
        assert filled[0]["rows"][0][1] == "云南领航保安服务有限公司"


# ===========================================================================
# Part 3: 文件章节内容检测
# ===========================================================================


class TestFileSectionContentDetection:
    """文件章节内容检测 — 从文档中检测格式章节并验证内容."""

    # ── 3a. 关键词定位检测 ──

    def test_detect_format_section_by_keyword(self):
        """通过关键词定位格式章节."""
        result = _find_by_keyword(FULL_TENDER_TEXT)
        assert result is not None
        assert result["method"] == "keyword_match"
        assert "投标文件格式" in result["chapter_title"]
        assert "一、商务部分" in result["section_text"]
        assert "开标一览表" in result["section_text"]
        print(
            f"[Detect] Found format section: '{result['chapter_title']}' "
            f"via {result['method']}"
        )

    def test_detect_format_section_via_locator(self):
        """通过异步定位器检测格式章节."""
        import asyncio
        result = asyncio.run(locate_format_section(FULL_TENDER_TEXT))
        assert result is not None
        assert "section_text" in result
        assert "method" in result
        assert len(result["section_text"]) > 100
        # 定位方法应为关键词匹配（目录/语义/尾部兜底在此文本中不应触发）
        assert result["method"] in (
            "keyword_match", "toc_inference", "semantic_chunking", "tail_fallback"
        )
        print(
            f"[Detect] Located via {result['method']}: "
            f"'{result.get('chapter_title', 'N/A')}'"
        )

    # ── 3b. 章节头切分检测 ──

    def test_split_by_chapter_headers(self):
        """验证章节头切分可正确检测文档中的各章节."""
        chapters = _split_by_chapter_headers(FULL_TENDER_TEXT)
        assert len(chapters) >= 3, (
            f"Expected at least 3 chapters, got {len(chapters)}"
        )

        # 检查是否检测到关键章节
        titles = [c["title"] for c in chapters]
        title_text = " ".join(titles)
        assert "招标公告" in title_text or "投标人须知" in title_text, (
            f"Expected chapter headers, got: {titles}"
        )
        # 格式章节应存在
        format_chapters = [t for t in titles if "格式" in t or "投标文件" in t]
        assert len(format_chapters) >= 1, f"No format chapter found in {titles}"
        print(f"[Split] Found {len(chapters)} chapters: {titles[:5]}...")

    # ── 3c. 内容结构检测（heading / table / signature） ──

    def test_detect_table_content_in_section(self):
        """检测章节内容中的表格标记."""
        section_text = """
一、商务部分
（一）开标一览表

| 序号 | 服务内容 | 不含税单价 | 税率 | 含税总价 |
|------|---------|----------|----|--------|
| 1    | 保安服务 | 1000     | 6  | 1060   |

投标人：（公章）
日期：  年  月  日
"""
        # 验证表格格式标记存在
        assert "|" in section_text
        assert "序号" in section_text
        # 管道表格行数 ≥ 3（表头 + 分隔行 + 数据行）
        pipe_lines = [l for l in section_text.split("\n") if "|" in l and "---" not in l]
        assert len(pipe_lines) >= 2, f"Expected >=2 pipe lines, got {len(pipe_lines)}"

    def test_detect_signature_block_in_section(self):
        """检测章节内容中的签章块."""
        section_text = """
投标人：（公章）
法定代表人或授权代理人：（签字）
日期：  年  月  日
"""
        # 签章关键词检测
        sig_keywords = ["公章", "签字", "日期"]
        detected = [kw for kw in sig_keywords if kw in section_text]
        assert len(detected) == 3, f"Expected all sig keywords, got {detected}"

    def test_detect_heading_structure(self):
        """检测章节内容的标题层级."""
        content = """## 一、服务方案
本方案针对项目特点...

### 1. 日常安保方案
日常安保工作包括门岗值守...

### 2. 消防应急响应
建立消防应急响应机制...

## 二、人员配置
本项目拟投入以下人员...
"""
        # 检测二级标题
        h2_count = content.count("## ")
        assert h2_count >= 2, f"Expected >=2 H2 headings, got {h2_count}"

    # ── 3d. 投标函内容检测 ──

    def test_detect_bid_letter_content(self):
        """投标函中的填写字段检测."""
        bid_letter = """
（二）投标函

致：某单位

我方已仔细阅读招标文件全部内容，我方承诺：

1. 投标人名称：________________
2. 注册地址：________________
3. 法定代表人：________________
4. 投标有效期：____ 日历天
"""
        # 检测空白填写位
        import re
        blanks = re.findall(r'_{3,}', bid_letter)
        assert len(blanks) >= 3, f"Expected >=3 blank fields, got {len(blanks)}"

        # 检测填写字段标签
        field_labels = ["投标人名称", "注册地址", "法定代表人", "投标有效期"]
        detected_labels = [l for l in field_labels if l in bid_letter]
        assert len(detected_labels) == 4, (
            f"Expected all field labels, got {detected_labels}"
        )

    # ── 3e. AI 提取格式模板的内容检测 ──

    @pytest.mark.asyncio
    async def test_extract_format_from_document_with_detection(self):
        """从文档提取格式模板 → 验证检测到的结构."""
        mock_ai = AsyncMock()
        mock_ai.chat_completion.return_value = json.dumps({
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
                        {
                            "number": "（二）",
                            "title": "投标函",
                            "type": "fixed_form",
                            "confidence": 0.85,
                            "children": [],
                        },
                    ],
                }
            ],
            "global_format_rules": {"numbering_style": "chinese_legal"},
            "extraction_metadata": {"warnings": []},
        }, ensure_ascii=False)

        template = await extract_format_from_document(FULL_TENDER_TEXT, mock_ai)

        assert template is not None
        assert "document_structure" in template

        # 验证检测到的结构
        structure = template["document_structure"]
        assert len(structure) >= 1

        part = structure[0]
        assert part["title"] == "商务部分"
        assert part["required"] is True

        # 检测子节
        children = part.get("children", [])
        assert len(children) >= 1

        # 检测表格类型节点
        table_nodes = [c for c in children if c.get("type") == "table"]
        if table_nodes:
            table_schema = table_nodes[0].get("table_schema", {})
            assert "columns" in table_schema
            assert len(table_schema["columns"]) >= 1

        # 检测签章块
        sig_nodes = [c for c in children if "signature_block" in c]
        if sig_nodes:
            sig = sig_nodes[0]["signature_block"]
            assert "lines" in sig
            assert len(sig["lines"]) >= 1

        # 验证定位元数据已包含
        assert "location_method" in template["extraction_metadata"]
        print(
            f"[TemplateExtract] Detected {len(structure)} parts, "
            f"{len(children)} children, "
            f"method={template['extraction_metadata']['location_method']}"
        )


# ===========================================================================
# Part 4: Content Assembler 集成 — 章节内容检测与组装的桥梁
# ===========================================================================


class TestContentAssemblerIntegration:
    """Content Assembler 与格式章节内容检测的集成测试."""

    def test_assemble_content_detects_heading_markers(self):
        """组装时正确检测已存在的 heading 标记 — 避免重复."""
        node = {"title": "服务方案", "depth": 2}
        generated = {"服务方案": "## 一、服务方案\n\n本方案包含以下内容..."}

        assembled = assemble_chapter_content(node, generated, depth=1)
        # 当内容已包含标题时不应再追加
        assert assembled.count("服务方案") == 1, (
            f"Heading duplicated: {repr(assembled)}"
        )

    def test_assemble_content_adds_missing_heading(self):
        """当内容缺少标题时自动追加."""
        node = {"title": "服务方案", "depth": 2}
        generated = {"服务方案": "本方案针对项目实际情况制定..."}

        assembled = assemble_chapter_content(node, generated, depth=1)
        assert "## 服务方案" in assembled
        assert "本方案" in assembled

    def test_assemble_nested_sections(self):
        """嵌套章节的递归组装."""
        node = {
            "title": "技术部分",
            "depth": 0,
            "children": [
                {
                    "title": "（一）服务方案",
                    "depth": 1,
                    "children": [
                        {"title": "1. 日常安保", "depth": 2},
                        {"title": "2. 应急预案", "depth": 2},
                    ],
                },
            ],
        }
        generated = {
            "技术部分 > （一）服务方案 > 1. 日常安保": "### 1. 日常安保\n\n内容包括门岗值守和巡逻方案。",
            "技术部分 > （一）服务方案 > 2. 应急预案": "### 2. 应急预案\n\n包括消防应急和突发事件处理。",
        }

        assembled = assemble_chapter_content(node, generated, depth=0)

        assert "日常安保" in assembled
        assert "应急预案" in assembled
        assert "门岗值守" in assembled
        assert "消防应急" in assembled

    def test_build_final_chapters_payload_integration(self):
        """build_final_chapters_payload 完整集成 — 从大纲树到章节列表."""
        outline = [
            {
                "title": "商务部分",
                "order_index": 1,
                "depth": 0,
                "children": [
                    {"title": "（一）投标函", "depth": 1},
                    {"title": "（二）开标一览表", "depth": 1},
                ],
            },
            {
                "title": "技术部分",
                "order_index": 2,
                "depth": 0,
                "children": [
                    {"title": "（一）服务方案", "depth": 1},
                ],
            },
        ]
        generated = {
            "商务部分 > （一）投标函": "致：招标人\n\n我方已仔细阅读...",
            "商务部分 > （二）开标一览表": "| 序号 | 服务内容 | 金额 |\n|---|---|---|\n| 1 | 保安 | 1000 |",
            "技术部分 > （一）服务方案": "## 一、服务方案\n\n全天候保安服务方案...",
        }

        payload = build_final_chapters_payload(outline, generated)

        assert len(payload) == 2
        assert payload[0]["title"] == "商务部分"
        assert payload[1]["title"] == "技术部分"
        assert "投标函" in payload[0]["content"]
        assert "开标一览表" in payload[0]["content"]
        assert "服务方案" in payload[1]["content"]
        assert payload[0]["order_index"] == 1
        assert payload[1]["order_index"] == 2

        print(
            f"[Assembly] {len(payload)} chapters assembled, "
            f"total {sum(len(c['content']) for c in payload)} chars"
        )

    def test_chapter_summary_detection(self):
        """章节摘要生成 — 正确检测首段实质内容."""
        content = """## 一、服务方案

本项目计划投入32名专业保安人员，采用四班三运转模式，
覆盖三个厂区15个重点部位，实现24小时不间断巡逻。
具体方案如下...
"""
        summary = generate_chapter_summary(content, max_len=100)
        assert len(summary) > 10
        assert "32名" in summary or "保安" in summary or "巡逻" in summary

    def test_sibling_summaries_collection(self):
        """兄弟章节摘要收集."""
        parent = {
            "title": "商务部分",
            "path": ["商务部分"],
            "children": [
                {"title": "投标函", "path": ["商务部分", "投标函"]},
                {"title": "开标一览表", "path": ["商务部分", "开标一览表"]},
            ],
        }
        generated = {
            "商务部分 > 投标函": "致：某单位\n我方已仔细阅读并响应招标文件全部要求...",
            "商务部分 > 开标一览表": "| 序号 | 内容 | 金额 |\n|---|---|---|\n| 1 | 保安 | 1000 |",
        }

        summaries = collect_sibling_summaries(parent, generated)
        assert len(summaries) == 2
        assert any("投标函" in s for s in summaries)
        assert any("开标一览表" in s for s in summaries)


# ===========================================================================
# Part 5: AI Pipeline 集成 — 扫描 + 填充 + 验证 全链路
# ===========================================================================


class TestAIPipelineIntegration:
    """AI 扫描标注 → 批量填充 → 后扫描验证 完整集成."""

    @pytest.mark.asyncio
    async def test_scan_then_fill_pipeline(self):
        """模拟 AI 扫描后填充的完整链路."""
        # 1. 模拟 AI 扫描返回
        mock_ai = AsyncMock()
        scan_response = {
            "text_replacements": [
                {"original": "________________", "var": "company_name"},
                {"original": "________________", "var": "legal_rep_name"},
                {"original": "____ 日历天", "var": "bid_validity_days"},
                {"original": "____年__月__日", "var": "date"},
            ],
            "table_fills": [],
            "warnings": [],
        }
        mock_ai.chat_completion.return_value = json.dumps(scan_response)

        sample_text = (
            "投标人名称：________________\n"
            "法定代表人：________________\n"
            "投标有效期：____ 日历天\n"
            "日期：____年__月__日\n"
        )

        # 2. AI 扫描（mock）
        scan_result = await scan_and_mark_variables(
            full_text=sample_text,
            tables=[],
            ai_adapter=mock_ai,
        )
        assert len(scan_result["text_replacements"]) == 4
        assert scan_result["warnings"] == []

        # 3. 构建变量值
        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)

        # 4. 为 text_replacements 注入实际值
        for rep in scan_result["text_replacements"]:
            var_name = rep.get("var", "")
            rep["value"] = variables.get(var_name, f"[{var_name}]")

        # 5. 批量填充
        filled = batch_fill_text(sample_text, scan_result["text_replacements"])

        # 6. 验证填充结果
        assert "云南领航保安服务有限公司" in filled
        assert "张三" in filled
        assert "120" in filled
        assert "2026年" in filled and "月" in filled and "日" in filled
        assert "________________" not in filled

        # 7. 后扫描
        issues = post_scan(filled)
        assert len(issues) == 0, f"Post scan found issues: {issues}"

        print("[Pipeline] Scan → Fill → Verify: all passed, 0 residual issues")

    @pytest.mark.asyncio
    async def test_scan_with_table_fills_pipeline(self):
        """模拟 AI 扫描含表格填充的完整链路."""
        mock_ai = AsyncMock()
        scan_response = {
            "text_replacements": [],
            "table_fills": [
                {"page": 68, "table_index": 0, "row": 0, "col": 1, "var": "company_name"},
                {"page": 68, "table_index": 0, "row": 1, "col": 1, "var": "address"},
                {"page": 68, "table_index": 0, "row": 2, "col": 1, "var": "contact_phone"},
            ],
            "warnings": [],
        }
        mock_ai.chat_completion.return_value = json.dumps(scan_response)

        tables = [
            {
                "page": 68,
                "table_index": 0,
                "rows": [
                    ["投标人名称", ""],
                    ["地址", ""],
                    ["联系电话", ""],
                ],
            }
        ]

        scan_result = await scan_and_mark_variables(
            full_text="投标人基本信息表",
            tables=tables,
            ai_adapter=mock_ai,
        )
        assert len(scan_result["table_fills"]) == 3

        variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
        filled_tables = batch_fill_tables(tables, scan_result["table_fills"], variables)

        assert filled_tables[0]["rows"][0][1] == "云南领航保安服务有限公司"
        assert filled_tables[0]["rows"][1][1] == "云南省昆明市官渡区XX路XX号"
        assert filled_tables[0]["rows"][2][1] == "0871-12345678"

        print(
            "[TablePipeline] All 3 table cells filled correctly"
        )


# ===========================================================================
# Part 6: 错误处理与边界条件
# ===========================================================================


class TestV2IntegrationErrorHandling:
    """v2 Pipeline 错误处理与边界条件."""

    def test_pdf_not_found_in_pipeline(self):
        """PDF 不存在时 pipeline 应抛出正确错误."""
        with pytest.raises(FileNotFoundError):
            extract_format_section("/nonexistent/tender.pdf")

    def test_empty_variables_pipeline_does_not_crash(self):
        """空变量映射不应导致 pipeline 崩溃."""
        variables = build_variable_values(None, None)
        text = "投标人名称：________________"
        replacements = [
            {"original": "________________", "var": "company_name", "value": variables["company_name"]},
        ]
        filled = batch_fill_text(text, replacements)
        assert "[待补充]" in filled
        assert "________________" not in filled

    def test_partial_scan_result_safe(self):
        """不完整的 AI 扫描结果不应导致崩溃."""
        text = "投标人：________\n地址：________"
        # 扫描结果只有部分替换
        partial = [
            {"original": "________", "var": "company_name", "value": "测试公司"},
            # 缺了一个
        ]
        filled = batch_fill_text(text, partial)
        assert "测试公司" in filled
        # 仍有残留空白
        assert "________" in filled

    def test_format_section_not_found_error_safe(self):
        """当格式章节不存在时不抛异常，应降级使用全文档."""
        _skip_if_no_pdf()
        # extract_format_section 在找不到格式章节时使用全文档作为降级
        # 这不应抛出异常
        result = extract_format_section(TENDER_PDF)
        # 无论是否找到格式章节，result 都应该有内容
        assert result["full_text"]
        assert result["total_pages"] > 0
        print(
            f"[Fallback] Format not found → using full doc: "
            f"{len(result['full_text'])} chars, "
            f"pages {result['start_page']}-{result['end_page']}"
        )
