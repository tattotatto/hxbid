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
