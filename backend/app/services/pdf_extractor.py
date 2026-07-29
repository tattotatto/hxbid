"""宏曦标书 - PDF格式章节完整提取器.

使用 pdfplumber 从招标文件 PDF 中提取格式章节的完整文本和表格数据。
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# 格式章节定位关键词（按优先级）
FORMAT_KEYWORDS = [
    "投标文件格式",
    "投标书格式",
    "投标文件组成",
    "第六章 投标文件格式",
    "第五章 投标书格式",
    "第四章 投标文件格式",
]


def locate_format_pages(pdf) -> Tuple[int, int] | None:
    """定位招标文件中"投标文件格式"章节的起止页码.

    从文档末尾向前搜索关键词以快速定位格式章节（格式章节通常位于文档后半部分）。

    Returns:
        (start_page, end_page) 0-indexed, or None if not found.
        start_page 是章节首页（如"第六章 投标文件格式"所在页）。
        end_page 是文档末尾（格式章节通常包含到文档结束）。
    """
    # 从后向前搜索，格式章节通常位于文档末尾
    num_pages = len(pdf.pages)
    for i in range(num_pages - 1, -1, -1):
        text = pdf.pages[i].extract_text() or ""
        for kw in FORMAT_KEYWORDS:
            if kw in text:
                # 再向前回溯找到章节的真正起始页（关键词可能在标题行之后）
                start_page = i
                # 向前最多回溯 3 页，找更靠前的匹配
                if i > 0:
                    for j in range(i - 1, max(i - 3, -1), -1):
                        prev_text = pdf.pages[j].extract_text() or ""
                        for pk in FORMAT_KEYWORDS:
                            if pk in prev_text:
                                start_page = j
                                break
                        else:
                            continue
                        break

                end_page = num_pages - 1
                logger.info(
                    "Format section: pages %d-%d (keyword: '%s', total pages: %d)",
                    start_page, end_page, kw, num_pages,
                )
                return start_page, end_page
    return None


def extract_text_from_pages(pdf, start: int, end: int) -> str:
    """提取指定页码范围的所有文本.

    Args:
        pdf: pdfplumber.PDF 实例
        start: 起始页（0-indexed）
        end: 结束页（0-indexed，包含）

    Returns:
        合并后的全部文本
    """
    parts = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        text = pdf.pages[i].extract_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def extract_tables_from_pages(pdf, start: int, end: int) -> list[dict]:
    """提取指定页码范围的所有表格.

    Args:
        pdf: pdfplumber.PDF 实例
        start: 起始页（0-indexed）
        end: 结束页（0-indexed，包含）

    Returns:
        list of {"page": int, "table_index": int, "rows": list[list[str | None]]}
    """
    tables = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        page_tables = pdf.pages[i].extract_tables()
        if not page_tables:
            continue
        for j, rows in enumerate(page_tables):
            if rows and len(rows) >= 2:  # 过滤空表和单行"表"
                tables.append({
                    "page": i + 1,  # 1-indexed
                    "table_index": j,
                    "rows": rows,
                })
    return tables


def extract_format_section(pdf_path: str) -> dict:
    """完整提取招标文件格式章节.

    提取流程：
    1. 打开 PDF
    2. 定位"投标文件格式"章节起止页
    3. 提取全部文本（含封面、目录、正文）
    4. 提取所有表格

    Args:
        pdf_path: PDF 文件路径（支持 Path-like 和字符串）

    Returns:
        {
            "full_text": str,          # 格式章节全部文本
            "tables": list[dict],      # 表格数据
            "start_page": int,         # 起始页（1-indexed）
            "end_page": int,           # 结束页（1-indexed）
            "total_pages": int,        # 文档总页数
        }

    Raises:
        FileNotFoundError: PDF 文件不存在
        ValueError: PDF 文件无法解析
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))

    try:
        location = locate_format_pages(pdf)
        if not location:
            logger.warning("Format section not found, using full document")
            start, end = 0, len(pdf.pages) - 1
        else:
            start, end = location

        full_text = extract_text_from_pages(pdf, start, end)
        tables = extract_tables_from_pages(pdf, start, end)

        result = {
            "full_text": full_text,
            "tables": tables,
            "start_page": start + 1,
            "end_page": end + 1,
            "total_pages": len(pdf.pages),
        }

        logger.info(
            "Extracted format section: %d chars text, %d tables from pages %d-%d (total %d pages)",
            len(full_text), len(tables), result["start_page"], result["end_page"], result["total_pages"],
        )

        return result
    finally:
        pdf.close()


def extract_full_document(pdf_path: str) -> dict:
    """提取整个 PDF 文档的文本和表格.

    当不需要定位特定章节时使用此函数。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {"full_text": str, "tables": list[dict], "total_pages": int}
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))

    try:
        full_text = extract_text_from_pages(pdf, 0, len(pdf.pages) - 1)
        tables = extract_tables_from_pages(pdf, 0, len(pdf.pages) - 1)

        return {
            "full_text": full_text,
            "tables": tables,
            "total_pages": len(pdf.pages),
        }
    finally:
        pdf.close()
