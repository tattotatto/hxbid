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

强制要求（必须提取，否则视为提取失败）：
- 商务部分下的 7 个固定子章节必须全部提取：开标一览表、投标函、法定代表人身份证明书、法定代表人授权委托书、投标保证金及基本户凭证、廉洁诚信承诺书、与招标人干部职工不存在关联关系的承诺书
- 开标一览表的 type 必须标为 "table"，并提取完整的 table_columns（含项目名称、招标编号、投标人名称、不含税单价、不含税总价、增值税税率、服务地点、服务期限、发票类型、投标保证金、备注）
- (一)开标一览表 必须存在；招标文件通常会注明"此表应放于投标文件封面后第一页"

注意：
- 直接返回JSON数组，不要包含任何其他文字说明
- 序号使用原文中的序号格式
- 如果原文中某章节下还有子章节，用children字段嵌套
- 固定格式章节的 format_notes 应包含"须有签章：投标人（盖章）/法定代表人或委托代理人（签字或盖章）/日期"等落款要求"""


async def _extract_with_retry(
    ai_adapter,
    user_prompt: str,
    max_tokens: int = 65536,
    max_attempts: int = 2,
) -> str:
    """调用 deepseek-v4-flash 提取章节，空内容时重试一次。

    deepseek-v4-flash 是推理模型：reasoning_tokens 先吃输出预算，推理消耗随机
    波动在 12k~20k+，且随输入规模与复杂度上升。max_tokens=16384 时推理一旦
    超过 ~14.5k，content 被挤成空 → finish_reason=length → RuntimeError → 0 章节；
    32768 实测安全（推理 18.5k + 正文 4.2k = 20.1k）。复杂标书章节正文更长，
    65536 已实测被 API 接受（实测上限 ≥131072），给足余量；重试兜底残余随机性。
    """
    messages = [
        {"role": "system", "content": CHAPTER_EXTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    for attempt in range(max_attempts):
        try:
            return await ai_adapter.chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except RuntimeError as exc:
            if attempt == max_attempts - 1:
                raise
            logger.warning(
                "Chapter extract returned empty content (attempt %d/%d), retrying: %s",
                attempt + 1, max_attempts, exc,
            )


async def extract_chapters_from_text(
    section_text: str,
    ai_adapter,
    max_input_chars: int = 30000,
) -> list[dict]:
    """从格式章节文本中提取结构化章节列表.

    Args:
        section_text: 第六章"投标文件格式"的完整文本
        ai_adapter: AI适配器实例
        max_input_chars: 最大输入字符数。默认 30000：复杂标书「投标文件格式」
            章节文本可超过 1 万字符，截断太狠会漏掉靠后的章节（如技术方案、
            资信标附录）。深挖推理模型也吃输入，30k 字符 ≈ 15k 中文 token，
            配合 max_tokens=65536 输出预算实测安全。

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
        response = await _extract_with_retry(ai_adapter, user_prompt)
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


def merge_format_template_fallback(
    chapters: list[dict],
    format_template: dict,
) -> tuple[list[dict], list[str]]:
    """用格式模板 document_structure 补全必需章节（含子章节）.

    三重作用：
    1. AI 提取结果过少时（<3 章）兜底补齐一级章节（向后兼容旧行为）
    2. 任何时候：补齐一级章节下 required=True 的子章节（固定格式章节）
       例如：商务部分下补 (一)开标一览表、(三)法定代表人身份证明书 等
    3. 强制按 document_structure 的顺序重排一级章节，保证 (一)开标一览表
       排在第一位等招标文件规定的顺序

    Returns:
        (合并后的章节列表, 自动补充的章节标题列表)
    """
    structure = (format_template or {}).get("document_structure", []) or []
    if not structure:
        return chapters, []

    merged = list(chapters)
    added: list[str] = []

    # Build lookup: title -> chapter dict (for children merging)
    by_title = {c.get("title", ""): c for c in merged}

    next_order = max((c.get("order_index", 0) for c in merged), default=0) + 1

    for part in structure:
        part_title = part.get("title", "")
        if not part_title:
            continue
        children_def = part.get("children", []) or []

        # ── 补齐一级章节（仅当 AI 没提取到时） ──
        if part_title not in by_title:
            if not part.get("required", True):
                continue
            merged.append({
                "order_index": next_order,
                "number": part.get("number", str(next_order)),
                "title": part_title,
                "type": part.get("type", "ai_generated"),
                "required": True,
                "format_notes": "按招标文件'投标文件格式'模板自动补充（必选章节）",
                "children": list(children_def),
            })
            next_order += 1
            added.append(part_title)
            by_title[part_title] = merged[-1]
            continue

        # ── 已存在的顶级章节：补齐其下的 required 子章节 ──
        if not children_def:
            continue
        existing_chapter = by_title[part_title]
        existing_children = existing_chapter.get("children", []) or []
        existing_child_titles = {c.get("title", "") for c in existing_children}

        new_children = list(existing_children)
        for child_def in children_def:
            child_title = child_def.get("title", "")
            if not child_title or child_title in existing_child_titles:
                continue
            if not child_def.get("required", True):
                continue
            new_children.append({
                "order_index": len(new_children) + 1,
                "number": child_def.get("number", ""),
                "title": child_title,
                "type": child_def.get("type", "ai_generated"),
                "required": True,
                "format_notes": child_def.get("format_notes")
                    or "按招标文件'投标文件格式'模板自动补充（必选子章节）",
                "table_columns": child_def.get("table_columns"),
                "scoring_context": child_def.get("scoring_context"),
            })
            existing_child_titles.add(child_title)
            added.append(f"{part_title}/{child_title}")

        if new_children != existing_children:
            existing_chapter["children"] = new_children

    if added:
        logger.info(
            "Format-template fallback merged %d required items: %s",
            len(added), added[:20],  # truncate to avoid log flooding
        )
    return merged, added


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
