"""宏曦标书 - 章节提取器.

从招标文件"第六章 投标文件格式"中提取结构化章节列表。
包含编码健康检查和 AI 解析。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 编码健康检查
# ---------------------------------------------------------------------------

# 有效 CJK 字符比例阈值 — 低于此值视为 PDF 提取乱码
MIN_CJK_RATIO = 0.30

# 最小文本长度（字符）
MIN_TEXT_LENGTH = 200


def _cjk_ratio(text: str) -> float:
    """计算文本中有效 CJK（中日韩）字符的比例."""
    if not text:
        return 0.0
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    # 排除空白字符
    meaningful = sum(1 for ch in text if not ch.isspace())
    if meaningful == 0:
        return 0.0
    return cjk_count / meaningful


def check_encoding_health(text: str) -> dict:
    """检查提取文本的编码健康状态.

    Returns:
        {"healthy": bool, "cjk_ratio": float, "total_chars": int, "message": str}
    """
    total_chars = len(text)
    ratio = _cjk_ratio(text)

    if total_chars < MIN_TEXT_LENGTH:
        return {
            "healthy": False,
            "cjk_ratio": ratio,
            "total_chars": total_chars,
            "message": f"提取文本过短（{total_chars}字符），PDF可能为扫描件或加密文件",
        }

    if ratio < MIN_CJK_RATIO:
        return {
            "healthy": False,
            "cjk_ratio": round(ratio, 3),
            "total_chars": total_chars,
            "message": f"有效中文字符比例过低（{ratio:.1%}），PDF文本提取出现乱码，请确认PDF不是扫描件或加密文件",
        }

    return {
        "healthy": True,
        "cjk_ratio": round(ratio, 3),
        "total_chars": total_chars,
        "message": "编码健康检查通过",
    }


# ---------------------------------------------------------------------------
# AI 章节解析
# ---------------------------------------------------------------------------

CHAPTER_EXTRACT_SYSTEM_PROMPT = """你是招标文件分析专家。你的任务是从招标文件的"投标文件格式"章节中提取完整的、结构化的投标文件章节列表。

提取规则：
1. 严格按照招标文件原文的章节顺序和编号提取
2. 每个章节标注类型：
   - "fixed_form": 固定格式文本（投标函、承诺书、法定代表人证明、授权委托书等需要签章的文书）
   - "table": 表格（开标一览表、报价表、人员配置表等）
   - "ai_generated": 需要投标人自行撰写的内容（服务方案、技术方案、应急预案、管理制度等）
   - "attachment": 附件/证明材料（营业执照、资质证书、合同复印件等）
3. 从原文中提取章节的序号（如"一""（一）""1."等）
4. 表格章节应提取表格列定义（table_columns）
5. 固定格式章节应标注格式说明（format_notes）
6. AI撰写章节应标注相关评分上下文（scoring_context）
7. 如果原文明确该章节为必需，mark required=true

注意：
- 直接返回JSON数组，不要包含任何其他文字说明
- 序号使用原文中的序号格式
- 如果原文中某章节下还有子章节，用children字段嵌套"""


async def extract_chapters_from_text(
    section_text: str,
    ai_adapter,
    max_input_chars: int = 10000,
) -> list[dict]:
    """从格式章节文本中提取结构化章节列表.

    Args:
        section_text: 第六章"投标文件格式"的完整文本
        ai_adapter: AI适配器实例
        max_input_chars: 最大输入字符数

    Returns:
        结构化章节列表，每个章节包含:
        - order_index: int
        - number: str (序号，如"一""（一）")
        - title: str
        - type: str (fixed_form|table|ai_generated|attachment|mixed)
        - required: bool
        - table_columns: list[str] | None
        - format_notes: str | None
        - scoring_context: str | None
        - children: list | None
    """
    truncated = section_text[:max_input_chars]

    # 先做编码健康检查
    health = check_encoding_health(truncated)
    if not health["healthy"]:
        logger.error("Chapter extraction aborted: %s", health["message"])
        raise ValueError(health["message"])

    user_prompt = f"""请从以下招标文件"投标文件格式"章节中提取完整的投标文件章节列表。

返回JSON数组，每个元素格式如下：
{{
  "order_index": 1,
  "number": "一",
  "title": "投标函",
  "type": "fixed_form",
  "required": true,
  "format_notes": "须按招标文件固定格式，不得修改措辞",
  "children": []
}}

类型说明：
- fixed_form: 含固定措辞的文书（投标函、承诺书、证明、授权书等）
- table: 表格类内容（一览表、报价表等），需包含 table_columns 字段
- ai_generated: 需投标人撰写的内容（服务方案、技术方案、应急预案等），需包含 scoring_context
- attachment: 附件/证明材料

招标文件"投标文件格式"章节内容：
---
{truncated}
---

直接返回JSON数组，不要包含其他文字。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": CHAPTER_EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)

        # Handle both {"chapters": [...]} and direct [...] formats
        if isinstance(result, dict):
            chapters = result.get("chapters", [])
            if not chapters:
                # Try to find any array value
                for v in result.values():
                    if isinstance(v, list):
                        chapters = v
                        break
        elif isinstance(result, list):
            chapters = result
        else:
            chapters = []

        # Ensure each chapter has required fields
        for i, ch in enumerate(chapters):
            ch.setdefault("order_index", i + 1)
            ch.setdefault("number", str(i + 1))
            ch.setdefault("type", "ai_generated")
            ch.setdefault("required", True)
            ch.setdefault("children", [])

        logger.info("Extracted %d chapters from tender document", len(chapters))
        return chapters

    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Chapter extraction failed: %s", exc)
        raise


async def extract_chapters_from_pdf(
    pdf_path: str,
    ai_adapter,
) -> dict:
    """从招标文件 PDF 中提取章节结构.

    完整流程：
    1. 定位第六章
    2. 提取文本
    3. 编码健康检查
    4. AI 解析章节结构

    Args:
        pdf_path: 招标文件 PDF 路径
        ai_adapter: AI 适配器

    Returns:
        {
            "chapters": [...],
            "health": {...},
            "source_pages": [start, end],
        }

    Raises:
        ValueError: 编码健康检查失败或解析失败
        FileNotFoundError: PDF 文件不存在
    """
    from app.services.pdf_extractor import extract_format_section, locate_format_pages
    import pdfplumber

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))
    try:
        # Step 1: 定位第六章
        location = locate_format_pages(pdf)
        if not location:
            # Fallback: extract from full document
            logger.warning("Format section not found, using full document")
            start, end = 0, len(pdf.pages) - 1
        else:
            start, end = location

        # Step 2: 提取文本
        from app.services.pdf_extractor import extract_text_from_pages
        section_text = extract_text_from_pages(pdf, start, end)

        # Step 3: 编码健康检查
        health = check_encoding_health(section_text)
        if not health["healthy"]:
            pdf.close()
            raise ValueError(health["message"])

        # Step 4: AI 解析章节
        chapters = await extract_chapters_from_text(section_text, ai_adapter)

        return {
            "chapters": chapters,
            "health": health,
            "source_pages": [start + 1, end + 1],  # 1-indexed
        }

    finally:
        pdf.close()
