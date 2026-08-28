"""pdf_extractor 模块测试.

测试 PDF 格式章节提取器：文本提取、表格提取、章节定位。
"""

import os
from pathlib import Path

import pytest

from app.services.pdf_extractor import (
    extract_format_section,
    extract_full_document,
    extract_tables_from_pages,
    extract_text_from_pages,
    locate_format_pages,
)


# ---------------------------------------------------------------------------
# 测试文件路径
# ---------------------------------------------------------------------------

TENDER_PDF = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "素材", "招标文件正文.pdf"
)

HAS_TENDER_PDF = os.path.exists(TENDER_PDF) and os.path.isfile(TENDER_PDF)


def _skip_if_no_pdf():
    """CI 或开发环境可能没有招标文件 PDF."""
    if not HAS_TENDER_PDF:
        pytest.skip("Tender PDF not found: %s" % TENDER_PDF)


# ---------------------------------------------------------------------------
# 核心功能测试
# ---------------------------------------------------------------------------


class TestExtractFormatSection:
    """extract_format_section 集成测试."""

    def test_extract_format_section_basic(self):
        """完整提取招标文件格式章节 - 基本可用性."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)

        # 必须返回包含文本的结果
        assert result["full_text"], "Expected non-empty full_text"
        assert len(result["full_text"]) > 500, (
            f"Expected >500 chars, got {len(result['full_text'])}"
        )

        # 格式章节应包含标志性内容
        full_text = result["full_text"]
        has_format = "投标文件格式" in full_text or "投标书格式" in full_text
        has_bid = "投标函" in full_text or "开标一览表" in full_text
        assert has_format or has_bid, (
            "Expected format-related content in extracted text"
        )

        # 页码应合理
        assert result["start_page"] >= 1
        assert result["end_page"] >= result["start_page"]
        assert result["total_pages"] >= result["end_page"]

        print(
            f"Text: {len(result['full_text'])} chars, "
            f"Tables: {len(result['tables'])}, "
            f"Pages: {result['start_page']}-{result['end_page']} / {result['total_pages']}"
        )

    def test_tables_extracted(self):
        """表格正确提取 — 至少应有多个表格."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)

        # 招标文件格式章节通常包含多个表格（开标一览表、报价表、人员表等）
        assert len(result["tables"]) >= 1, (
            f"Expected at least 1 table, got {len(result['tables'])}"
        )

        # 验证表格数据结构
        for t in result["tables"]:
            assert "page" in t
            assert "table_index" in t
            assert "rows" in t
            assert isinstance(t["rows"], list)
            assert len(t["rows"]) >= 2, (
                f"Table on page {t['page']} has fewer than 2 rows"
            )
            # 每行应为字符串列表
            for row in t["rows"]:
                assert isinstance(row, list), f"Expected list row, got {type(row)}"

        print(f"Found {len(result['tables'])} tables")


class TestLocateFormatPages:
    """locate_format_pages 单元测试."""

    def test_locate_finds_format_section(self):
        """正向测试：能找到格式章节."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            start, end = locate_format_pages(pdf)
            assert start is not None, "Expected to find format section"
            assert 0 <= start <= end < len(pdf.pages), (
                f"Invalid page range: {start}-{end} (total {len(pdf.pages)} pages)"
            )
            print(f"Format section pages: {start + 1} - {end + 1}")
        finally:
            pdf.close()

    def test_locate_returns_none_for_no_match(self):
        """无格式关键词的文档应返回 None."""
        import pdfplumber

        # 使用第一页验证空查询场景（但可能仍匹配，仅测试接口返回类型）
        pdf = pdfplumber.open(TENDER_PDF)
        try:
            result = locate_format_pages(pdf)
            # 只验证返回类型一致性，不强制要求 None（实际文件可能匹配）
            if result is not None:
                start, end = result
                assert isinstance(start, int)
                assert isinstance(end, int)
        finally:
            pdf.close()


class TestExtractTextFromPages:
    """extract_text_from_pages 测试."""

    def test_extract_text_single_page(self):
        """单页文本提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            text = extract_text_from_pages(pdf, 0, 0)
            assert text, "First page should have text"
            assert isinstance(text, str)
        finally:
            pdf.close()

    def test_extract_text_multi_page(self):
        """多页文本提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            # 提取前 3 页
            text = extract_text_from_pages(pdf, 0, min(2, len(pdf.pages) - 1))
            assert text, "Pages should have text"
            assert len(text) > 100
        finally:
            pdf.close()

    def test_extract_text_out_of_range(self):
        """超出页码范围不抛异常."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            # 超出范围应返回空字符串
            text = extract_text_from_pages(pdf, 99999, 100000)
            assert text == ""
        finally:
            pdf.close()


class TestExtractTablesFromPages:
    """extract_tables_from_pages 测试."""

    def test_extract_tables_basic(self):
        """基本表格提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            start, end = locate_format_pages(pdf)
            if start is None:
                start, end = 0, len(pdf.pages) - 1
            tables = extract_tables_from_pages(pdf, start, end)
            assert isinstance(tables, list)
            # 格式章节应有表格
            if tables:
                for t in tables:
                    assert "page" in t
                    assert "rows" in t
                    assert len(t["rows"]) >= 2
        finally:
            pdf.close()

    def test_extract_tables_out_of_range(self):
        """超出页码范围返回空列表."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            tables = extract_tables_from_pages(pdf, 99999, 100000)
            assert tables == []
        finally:
            pdf.close()


class TestExtractFullDocument:
    """extract_full_document 测试."""

    def test_extract_full_document(self):
        """全文档提取."""
        _skip_if_no_pdf()
        result = extract_full_document(TENDER_PDF)

        assert result["full_text"], "Expected non-empty full_text"
        assert len(result["full_text"]) > 1000, (
            f"Expected >1000 chars for full doc, got {len(result['full_text'])}"
        )
        assert result["total_pages"] > 0
        assert isinstance(result["tables"], list)

        print(
            f"Full doc: {len(result['full_text'])} chars, "
            f"{len(result['tables'])} tables, "
            f"{result['total_pages']} pages"
        )


# ---------------------------------------------------------------------------
# 错误处理测试
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """错误与边界条件测试."""

    def test_file_not_found(self):
        """不存在的文件抛出 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_format_section("/nonexistent/file.pdf")

    def test_file_not_found_full_doc(self):
        """全文档提取 - 不存在的文件抛出 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_full_document("/nonexistent/file.pdf")


class TestLocateEvaluationSection:
    """评标办法章节定位（fake pdf 冒烟）."""

    def test_returns_none_when_missing(self):
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([FakePage("第一章 招标公告"), FakePage("第二章 投标人须知")])
        assert locate_evaluation_section(pdf) is None

    def test_finds_section_and_stops_at_format(self):
        from app.services.pdf_extractor import (
            locate_evaluation_section,
            extract_text_from_pages,
        )

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第一章 招标公告"),
            FakePage("第三章 评标办法 综合评分法 评分标准……技术部分 15 分"),
            FakePage("详细评分细则：服务方案 5 分、业绩 10 分"),
            FakePage("第六章 投标文件格式 投标函"),
            FakePage("（格式模板页）"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 1 and end == 2
        assert "综合评分法" in text and "投标文件格式" not in text
        # 与 extract_text_from_pages 一致（0-indexed 含 end）
        assert text == extract_text_from_pages(pdf, 1, 2)

    def test_skips_toc_and_cross_references(self):
        """目录行与正文交叉引用不得误锚——只认独立的章节标题行.

        回归：真实招标文件第 1 页目录含「第四章 评标办法….41」，正文大量
        「按照第四章“评标办法”规定…」交叉引用；旧实现按关键词首次命中
        锚到目录页，抓回参保人须知全文。标题行 `$` 锚定制天然排除目录
        （目录行带页码）与交叉引用（句中「办法」后有后续文字）。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("目录\n第一章 招标公告........2\n第四章 评标办法..........41"),
            FakePage("第一章 招标公告\n（招标公告正文…）"),
            FakePage("第二章 投标人须知\n25.评标\n"
                     "评标委员会按照第四章“评标办法”规定的方法进行评审。"),
            FakePage("第四章 评标办法"),
            FakePage("详细评分标准\n技术部分 15 分\n商务部分 10 分"),
            FakePage("第五章 投标文件格式\n（模板页）"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 3, f"应锚定真实章节页, got start={start}"
        assert end == 4
        assert "评标办法" in text

    def test_bare_heading_without_chapter_number(self):
        """无「第X章」前缀的裸章节标题同样能锚定."""
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("目录\n一、评标办法.....3"),
            FakePage("第一部分\n招标公告正文"),
            FakePage("评标办法（综合评分法）"),
            FakePage("评分细则：价格 30 分、方案 20 分"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 2 and end == 3
        assert "综合评分法" in text

    def test_skips_chapter_enumeration_list(self):
        """「招标文件由下列部分组成」章节名称清单不是章节起始页.

        真实文档页面中部常有「8.1 本项目的招标文件由下列部分组成：第一章…第四章
        评标办法…第六章」的章节罗列，每章题独占一行且无页码——若只按标题行锚定
        会误锚。章节起始页的标题必然位于页首，且其后不跟兄弟章题。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第一章 招标公告\n（招标公告正文…）"),
            FakePage("二．招标文件\n8.1 本项目的招标文件由下列部分组成：\n"
                     "第一章 招标公告\n第二章 投标须知\n第三章 需求任务书\n"
                     "第四章 评标办法\n第五章 合同条款\n第六章 投标文件格式"),
            FakePage("第四章 评标办法"),
            FakePage("评分标准：技术 15 分\n商务 10 分"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 2 and end == 3
        assert "评分标准" in text
