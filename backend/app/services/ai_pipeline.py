"""宏曦标书 - AI Pipeline Orchestration Engine.

Core AI orchestration module that coordinates bid document analysis,
outline generation, and chapter content creation. All AI calls go through
the ai_adapter singleton; PII is de-identified before entering prompt context.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
from typing import Any, AsyncIterator, Dict, List

import logging

from app.services.ai_adapter import ai_adapter
from app.services.deid import deidentify_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是宏曦标书系统的AI助手，专注于为云南宏曦科技有限公司撰写保安/物业服务类投标文件。

写作规范：
1. 使用中文标书行业地道表达，避免使用"首先""其次""此外""总而言之"等模板化连接词
2. 每个段落必须包含至少1个具体事实（数字、日期、项目名、证书编号等）
3. 禁止使用"经验丰富""技术精湛""服务周到""管理能力强"等空泛形容词和套话
4. 对招标文件的每个要求必须作出针对性回应，措辞不能照搬原文，要用自己的话表达
5. 句式结构多样化，相邻段落开头不能雷同
6. 禁止使用 Markdown 格式符号（不要使用 #、*、**、- 等符号来格式化文本）
7. 输出纯文本内容。如需呈现表格数据，使用"表X：标题"后以分号分隔的键值对描述，不要使用 | 管道表格

版权声明：本文由宏曦标书AI辅助生成，云南宏曦科技有限公司版权所有。"""

DEFAULT_BID_SECTIONS = [
    "投标函及投标函附录",
    "法定代表人身份证明",
    "授权委托书",
    "投标保证金",
    "公司资质与业绩",
    "项目人员配置方案",
    "服务方案与技术方案",
    "应急预案",
    "培训计划",
    "报价明细",
    "服务承诺",
]

MAX_INPUT_CHARS = 15000

# Cache for active constraints (refreshed each generation session)
_active_constraints_cache: List[str] = []
_constraints_cache_version: int = 0


async def _get_active_constraints() -> List[str]:
    """Load active prompt constraints from the feedback loop.

    Cached in-process; refreshed when called from the API handler.
    """
    global _active_constraints_cache, _constraints_cache_version
    try:
        from app.database import async_session
        from app.services.feedback_loop import get_active_prompt_constraints

        async with async_session() as db:
            constraints = await get_active_prompt_constraints(db)
            _active_constraints_cache = constraints
            _constraints_cache_version += 1
            return constraints
    except Exception as exc:
        logger.debug("Failed to load active constraints: %s", exc)
        return _active_constraints_cache


def _build_system_prompt(extra_constraints: List[str] | None = None) -> str:
    """Build the full system prompt, appending any active feedback rules."""
    parts = [SYSTEM_PROMPT]
    all_constraints = list(_active_constraints_cache)
    if extra_constraints:
        all_constraints.extend(extra_constraints)
    if all_constraints:
        parts.append("\n额外写作约束（基于历史编辑反馈自动生成）：")
        for i, c in enumerate(all_constraints, 1):
            parts.append(f"  {i}. {c}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Helper: build messages list with system prompt prepended
# ---------------------------------------------------------------------------

def _build_messages(user_content: str, extra_constraints: List[str] | None = None) -> List[Dict[str, str]]:
    """Return a messages list with SYSTEM_PROMPT as the system message."""
    return [
        {"role": "system", "content": _build_system_prompt(extra_constraints)},
        {"role": "user", "content": user_content},
    ]


def _requirements_summary(requirements: dict) -> str:
    """Format a requirements dict into a concise summary string for prompts."""
    parts: List[str] = []

    if requirements.get("project_name"):
        parts.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("project_budget"):
        parts.append(f"项目预算：{requirements['project_budget']}")
    if requirements.get("project_duration"):
        parts.append(f"项目期限：{requirements['project_duration']}")

    qual_reqs = requirements.get("qualification_requirements", [])
    if qual_reqs:
        parts.append(f"资质要求：{'；'.join(qual_reqs)}")

    personnel = requirements.get("personnel_requirements")
    if personnel:
        parts.append(f"人员要求：{personnel}")

    service_reqs = requirements.get("service_requirements", [])
    if service_reqs:
        parts.append(f"服务要求：{'；'.join(service_reqs)}")

    eval_criteria = requirements.get("evaluation_criteria")
    if eval_criteria:
        parts.append(f"评标标准：{eval_criteria}")

    special = requirements.get("special_requirements", [])
    if special:
        parts.append(f"特殊要求：{'；'.join(special)}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. parse_bid_requirements
# ---------------------------------------------------------------------------

async def parse_bid_requirements(document_text: str) -> dict:
    """Parse bidding document text into structured requirements via AI.

    Truncates input to MAX_INPUT_CHARS characters before sending to the model.
    Uses JSON response_format for guaranteed structured output.

    Args:
        document_text: Raw text extracted from the bidding document.

    Returns:
        dict with keys: project_name, project_budget, project_duration,
        qualification_requirements, personnel_requirements,
        service_requirements, evaluation_criteria, special_requirements,
        bid_sections.
    """
    truncated = document_text[:MAX_INPUT_CHARS]

    user_prompt = f"""请分析以下招标文件内容，提取关键信息并以JSON格式返回。

要求提取的字段：
- project_name: 项目名称（字符串）
- project_budget: 项目预算（字符串，如未提及则为空字符串）
- project_duration: 项目期限/服务期限（字符串，如未提及则为空字符串）
- qualification_requirements: 资质要求列表（字符串数组）
- personnel_requirements: 人员配置要求（字符串，概述人员数量、持证要求等）
- service_requirements: 服务内容要求列表（字符串数组）
- evaluation_criteria: 评标办法/评标标准（字符串）
- special_requirements: 特殊要求列表（字符串数组，如保密要求、特殊设备等）
- bid_sections: 招标文件要求的标书章节/组成部分列表（字符串数组，按招标文件规定的顺序排列）

注意：
- 所有字段都必须存在，未提及的字段使用空字符串或空数组
- 直接返回JSON对象，不要包含任何其他文字说明

招标文件内容：
{truncated}"""

    messages = _build_messages(user_prompt)

    response = await ai_adapter.chat_completion(
        messages=messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        # Return a safe default structure on parse failure
        return {
            "project_name": "",
            "project_budget": "",
            "project_duration": "",
            "qualification_requirements": [],
            "personnel_requirements": "",
            "service_requirements": [],
            "evaluation_criteria": "",
            "special_requirements": [],
            "bid_sections": [],
        }

    # Ensure all expected keys are present with sane defaults
    defaults: Dict[str, Any] = {
        "project_name": "",
        "project_budget": "",
        "project_duration": "",
        "qualification_requirements": [],
        "personnel_requirements": "",
        "service_requirements": [],
        "evaluation_criteria": "",
        "special_requirements": [],
        "bid_sections": [],
    }
    for key, default in defaults.items():
        if key not in result:
            result[key] = default

    return result


# ---------------------------------------------------------------------------
# 2. generate_outline
# ---------------------------------------------------------------------------

def generate_outline(requirements: dict) -> list[dict]:
    """Generate a bid document outline from parsed requirements.

    If the requirements already contain bid_sections extracted from the
    document, those are used directly. Otherwise falls back to the default
    security/property service bid section list.

    Args:
        requirements: Parsed requirements dict from parse_bid_requirements().

    Returns:
        List of dicts, each with "order_index" (int) and "title" (str).
    """
    sections = requirements.get("bid_sections") if requirements else None

    if not sections:
        sections = DEFAULT_BID_SECTIONS

    return [
        {"order_index": i + 1, "title": title}
        for i, title in enumerate(sections)
    ]


# ---------------------------------------------------------------------------
# 3. generate_chapter
# ---------------------------------------------------------------------------

async def generate_chapter(
    chapter_title: str,
    requirements: dict,
    context: str = "",
    stream: bool = True,
) -> AsyncIterator[str] | str:
    """Generate content for a single bid document chapter.

    Builds a prompt from the chapter title, a summary of the parsed
    requirements, and any additional context (e.g. matched qualifications,
    personnel info, historical chapters).

    Args:
        chapter_title: The title of the chapter to generate.
        requirements: Parsed requirements dict from parse_bid_requirements().
        context: Additional context string (de-identified if containing PII).
        stream: If True, returns an AsyncIterator[str] for token-by-token
                streaming. If False, returns the complete response as a str.

    Returns:
        AsyncIterator[str] when stream=True; str when stream=False.
    """
    req_summary = _requirements_summary(requirements)

    context_block = ""
    if context:
        context_block = f"\n\n可供参考的资料：\n{context}"

    user_prompt = f"""请撰写标书章节内容。

章节名称：{chapter_title}

招标要求摘要：
{req_summary}{context_block}

要求：
1. 内容必须针对上述招标要求作出实质性回应
2. 使用地道的中文标书行业表达
3. 每个段落至少包含1个具体事实
4. 禁止使用空泛的形容词和套话
5. 输出纯文本，禁止使用任何 Markdown 格式符号（#、*、**、| 管道表格等）
6. 段落之间用空行分隔，标题直接以文字形式呈现"""

    messages = _build_messages(user_prompt)

    if stream:
        return ai_adapter.chat_completion_stream(
            messages=messages,
            temperature=0.7,
        )
    else:
        return await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.7,
        )


# ---------------------------------------------------------------------------
# 4. generate_chapter_with_materials
# ---------------------------------------------------------------------------

async def generate_chapter_with_materials(
    chapter_title: str,
    requirements: dict,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    similar_chapters: list[str] | None = None,
) -> AsyncIterator[str]:
    """Generate a chapter enriched with matched company resources.

    Assembles a rich context string from:
      - Available qualification certificates (name + cert_number)
      - Personnel profiles (de-identified: names and ID numbers replaced
        with placeholders before entering the AI prompt)
      - Similar historical chapters for style and content reference

    Always streams output token-by-token.

    Args:
        chapter_title: The title of the chapter to generate.
        requirements: Parsed requirements dict from parse_bid_requirements().
        matched_qualifications: List of Qualification objects or dicts
            containing at least 'name' and 'cert_number'.
        matched_personnel: List of Personnel objects or dicts containing
            at least 'name', 'id_card', 'education', 'tags'. PII fields
            are de-identified before prompt assembly.
        similar_chapters: List of previously written chapter texts for
            style / content reference.

    Yields:
        Generated chapter text chunks as they arrive from the model.
    """
    context_parts: List[str] = []

    # --- Qualifications ---
    if matched_qualifications:
        qual_lines: List[str] = ["可用资质证书："]
        for q in matched_qualifications:
            if isinstance(q, dict):
                name = q.get("name", "")
                cert = q.get("cert_number", "")
            else:
                name = getattr(q, "name", "")
                cert = getattr(q, "cert_number", "")
            qual_lines.append(f"  - {name}（证书编号：{cert}）")
        context_parts.append("\n".join(qual_lines))

    # --- Personnel (de-identified) ---
    if matched_personnel:
        personnel_lines: List[str] = ["可用项目人员："]
        for p in matched_personnel:
            if isinstance(p, dict):
                name = p.get("name", "")
                id_card = p.get("id_card", "")
                education = p.get("education", "")
                tags = p.get("tags", "")
            else:
                name = getattr(p, "name", "")
                id_card = getattr(p, "id_card", "")
                education = getattr(p, "education", "")
                tags = getattr(p, "tags", "")

            # De-identify name and ID card before prompt assembly
            safe_name, _ = deidentify_text(name) if name else ("", {})
            safe_id, _ = deidentify_text(id_card) if id_card else ("", {})
            display_name = safe_name if safe_name else name
            display_id = safe_id if safe_id else id_card

            line = f"  - {display_name}，学历{education}，持证/特长：{tags}"
            if display_id:
                line += f"，证件号：{display_id}"
            personnel_lines.append(line)

        # Also de-identify the assembled personnel block as a whole for safety
        combined = "\n".join(personnel_lines)
        safe_combined, _ = deidentify_text(combined)
        context_parts.append(safe_combined)

    # --- Similar historical chapters ---
    if similar_chapters:
        history_lines: List[str] = ["历史相似章节参考："]
        for i, chapter_text in enumerate(similar_chapters, 1):
            # Truncate each reference chapter to keep context manageable
            truncated = chapter_text[:3000]
            history_lines.append(f"--- 参考章节 {i} ---\n{truncated}")
        context_parts.append("\n".join(history_lines))

    context = "\n\n".join(context_parts)

    stream_result = await generate_chapter(
        chapter_title=chapter_title,
        requirements=requirements,
        context=context,
        stream=True,
    )

    async for chunk in stream_result:
        yield chunk
