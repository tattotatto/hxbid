"""宏曦标书 - Deep Outline Generation Engine.

Generates 3-4 level bid document outlines with 50-200+ leaf sections by:
  1. Extracting the tender's required section structure
  2. Matching against reference bid outlines from the library
  3. Using AI to expand into a comprehensive multi-level outline

This replaces the flat 11-item DEFAULT_BID_SECTIONS with a deep hierarchy
that mirrors real 2000-page bid documents.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from app.services.ai_adapter import ai_adapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum / maximum number of leaf sections in the generated outline
MIN_LEAF_SECTIONS = 50
MAX_LEAF_SECTIONS = 300

# The system prompt for outline generation
OUTLINE_SYSTEM_PROMPT = """你是投标书大纲设计专家。你的任务是根据招标文件要求和参考标书结构，为投标文件设计一个详尽的多层级大纲。

大纲设计原则：
1. 标书通常包含四大部分：商务部分、技术部分、资格审查部分、投标人认为需要提供的其他内容
2. 技术部分是重点，占标书篇幅的80%以上，必须展开到3-4级深度
3. 每个叶子节点（最终需要撰写的小节）应该是一个具体的、可独立撰写的主题
4. 服务方案类章节（如"日常安保管理方案"）需要进一步拆分为具体的操作方案
5. 参考提供的参考标书结构，确保大纲完整覆盖招标文件的所有评分项
6. 大纲层级：第0层=部分（4个），第1层=章（每部分3-8章），第2层=节（每章3-8节），第3层=小节（按需）
7. token_budget_hint 取值：tiny（投标函等）、small（承诺书等）、medium（人员配置等）、large（应急预案等）、xlarge（服务方案等）
8. 总叶子节点数应在 {min_leaves}-{max_leaves} 之间"""


# ---------------------------------------------------------------------------
# Outline generation
# ---------------------------------------------------------------------------

async def generate_deep_outline(
    requirements: dict,
    reference_outlines: List[Dict[str, Any]] | None = None,
    tender_text: str = "",
    min_leaves: int = MIN_LEAF_SECTIONS,
    max_leaves: int = MAX_LEAF_SECTIONS,
) -> list:
    """Generate a comprehensive multi-level bid document outline.

    Uses AI to create a 3-4 level outline tree based on:
    - The tender requirements (project scope, evaluation criteria)
    - Reference bid outlines from the library (structural templates)
    - The tender document text (for section format requirements)

    Args:
        requirements: Parsed tender requirements dict.
        reference_outlines: List of reference outline structures from the
            reference_outlines table. Each should have "outline" (list of
            nested dicts) and "stats" (section statistics).
        tender_text: Raw tender document text (for extracting required
            section structure from Chapter 6 投标文件格式).
        min_leaves: Minimum number of leaf sections to generate.
        max_leaves: Maximum number of leaf sections to generate.

    Returns:
        List of root outline nodes (dicts), each like:
        {
            "title": "技术部分",
            "order_index": 2,
            "token_budget_hint": "xlarge",
            "children": [...]
        }
    """
    # ── Build the prompt ──
    system_prompt = OUTLINE_SYSTEM_PROMPT.format(
        min_leaves=min_leaves,
        max_leaves=max_leaves,
    )

    user_parts: List[str] = []

    # 1. Tender requirements
    req_text = _format_requirements_for_outline(requirements)
    user_parts.append(f"【招标项目信息】\n{req_text}")

    # 2. Tender document structure (Chapter 6 投标文件格式)
    if tender_text:
        # Extract the most relevant part: 第六章 投标文件格式
        chapter6 = _extract_chapter6(tender_text)
        if chapter6:
            user_parts.append(f"\n【招标文件要求的标书格式】\n{chapter6}")

    # 3. Reference outlines (trimmed to structure only)
    if reference_outlines:
        user_parts.append("\n【参考标书结构 — 以下为成功中标的类似项目标书大纲】")
        # Show the best 2-3 reference outlines
        for i, ref in enumerate(reference_outlines[:3]):
            outline_struct = _outline_to_structure_string(ref.get("outline", []))
            stats = ref.get("stats", {})
            user_parts.append(
                f"\n参考标书 {i + 1}："
                f"{stats.get('total_leaf_sections', '?')}个小节，"
                f"最大深度{stats.get('max_depth', '?')}级"
            )
            user_parts.append(outline_struct)

    # 4. Output format specification
    user_parts.append(f"""
【输出要求】
请以JSON格式返回完整的多层级大纲。格式如下：
{{
  "outline": [
    {{
      "title": "商务部分",
      "order_index": 1,
      "token_budget_hint": "small",
      "children": [
        {{
          "title": "投标函",
          "order_index": 1,
          "token_budget_hint": "tiny",
          "children": []
        }},
        ...
      ]
    }},
    {{
      "title": "技术部分",
      "order_index": 2,
      "token_budget_hint": "xlarge",
      "children": [
        {{
          "title": "项目整体服务方案",
          "order_index": 1,
          "token_budget_hint": "xlarge",
          "children": [
            {{
              "title": "门卫值守方案",
              "order_index": 1,
              "token_budget_hint": "large",
              "children": []
            }}
          ]
        }}
      ]
    }},
    {{
      "title": "资格审查部分",
      "order_index": 3,
      "token_budget_hint": "medium",
      "children": [...]
    }},
    {{
      "title": "投标人认为需要提供的其他内容",
      "order_index": 4,
      "token_budget_hint": "small",
      "children": [...]
    }}
  ]
}}

注意：
- 技术部分是绝对重点，必须展开到3-4级深度，包含至少{min_leaves - 10}个叶子节点
- 技术部分必须覆盖招标文件的所有评分项（如：综合服务方案、管理制度、质量承诺、秩序维护、应急预案、人员管理、装备配置、监督检查、保险方案等）
- 每个叶子节点是一个具体的、可独立撰写的子主题
- 直接返回JSON对象，不要包含任何其他文字说明
- 总叶子节点数（所有部分的叶子节点之和）应在{min_leaves}-{max_leaves}之间""")

    user_prompt = "\n".join(user_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # ── Call AI ──
    logger.info("Generating deep outline (target: %d-%d leaf sections)...", min_leaves, max_leaves)

    response = await ai_adapter.chat_completion(
        messages=messages,
        temperature=0.4,
        max_tokens=16384,
        response_format={"type": "json_object"},
    )

    # ── Parse and validate ──
    try:
        result = json.loads(response)
        outline = result.get("outline", [])
    except json.JSONDecodeError:
        logger.error("Failed to parse AI outline response as JSON")
        outline = _generate_fallback_outline(requirements)

    # Validate leaf count
    leaf_count = _count_leaves(outline)
    logger.info("Generated outline: %d leaf sections across %d top-level parts", leaf_count, len(outline))

    if leaf_count < min_leaves:
        logger.warning(
            "Outline has only %d leaf sections (min %d). Consider re-generating with more reference data.",
            leaf_count, min_leaves,
        )

    return outline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_requirements_for_outline(requirements: dict) -> str:
    """Format requirements for the outline generation prompt."""
    lines: List[str] = []

    if requirements.get("project_name"):
        lines.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("project_budget"):
        lines.append(f"项目预算：{requirements['project_budget']}")
    if requirements.get("project_duration"):
        lines.append(f"服务期限：{requirements['project_duration']}")

    service_reqs = requirements.get("service_requirements", [])
    if service_reqs:
        lines.append(f"服务内容：{'；'.join(service_reqs)}")

    personnel = requirements.get("personnel_requirements", "")
    if personnel:
        lines.append(f"人员要求：{personnel}")

    eval_criteria = requirements.get("evaluation_criteria", "")
    if eval_criteria:
        lines.append(f"评标标准：{eval_criteria}")

    bid_sections = requirements.get("bid_sections", [])
    if bid_sections:
        lines.append(f"招标文件要求的章节：{'、'.join(bid_sections)}")

    return "\n".join(lines)


def _extract_chapter6(text: str) -> str:
    """Extract the 第六章 投标文件格式 section from tender text."""
    # Look for the chapter marker
    patterns = [
        r'第六章\s+投标文件格式',
        r'第六章.*投标文件',
        r'第[六6]章.*格式',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            start = match.start()
            # Take up to 5000 chars from this point
            end = min(start + 5000, len(text))
            return text[start:end]

    # Fallback: return the last portion of the tender (where formats usually are)
    if len(text) > 2000:
        return text[-3000:]
    return ""


import re  # needed for _extract_chapter6


def _outline_to_structure_string(outline_nodes: list, indent: int = 0) -> str:
    """Convert an outline tree to an indented text representation.

    Shows only the structure (headings), not the content.
    Max depth shown: 3 levels to keep the prompt concise.
    """
    lines: List[str] = []
    prefix = "  " * indent

    for i, node in enumerate(outline_nodes[:20]):  # cap at 20 top-level items
        title = node.get("title", "")
        children = node.get("children", [])

        if indent == 0:
            marker = f"{i + 1}."
        elif indent == 1:
            marker = f"（{i + 1}）"
        elif indent == 2:
            marker = f"{i + 1})"
        else:
            marker = f"-"

        lines.append(f"{prefix}{marker} {title}")

        if children and indent < 3:
            lines.append(_outline_to_structure_string(children, indent + 1))

    return "\n".join(lines)


def _count_leaves(outline_nodes: list) -> int:
    """Count the total number of leaf nodes in an outline tree."""
    count = 0
    for node in outline_nodes:
        children = node.get("children", [])
        if children:
            count += _count_leaves(children)
        else:
            count += 1
    return count


def _generate_fallback_outline(requirements: dict) -> list:
    """Generate a reasonable default deep outline if AI generation fails.

    Based on the structure of real 2000-page security service bids.
    """
    from app.services.ai_pipeline import DEFAULT_BID_SECTIONS

    bid_sections = requirements.get("bid_sections", [])
    if not bid_sections:
        bid_sections = DEFAULT_BID_SECTIONS

    # Classify each section into one of the four parts
    outline = [
        {"title": "商务部分", "order_index": 1, "token_budget_hint": "small", "children": []},
        {
            "title": "技术部分",
            "order_index": 2,
            "token_budget_hint": "xlarge",
            "children": _build_tech_section_fallback(requirements),
        },
        {"title": "资格审查部分", "order_index": 3, "token_budget_hint": "medium", "children": []},
        {"title": "投标人认为需要提供的其他内容", "order_index": 4, "token_budget_hint": "small", "children": []},
    ]

    # Classify each bid_section into one of the parts
    for i, section in enumerate(bid_sections):
        title = section if isinstance(section, str) else section.get("title", str(section))
        if any(kw in title for kw in ["投标函", "法定代表人", "授权", "保证金", "承诺", "廉洁", "一览表"]):
            outline[0]["children"].append({
                "title": title, "order_index": len(outline[0]["children"]) + 1,
                "token_budget_hint": "tiny", "children": [],
            })
        elif any(kw in title for kw in ["资格审查", "资质", "营业执照", "许可"]):
            outline[2]["children"].append({
                "title": title, "order_index": len(outline[2]["children"]) + 1,
                "token_budget_hint": "medium", "children": [],
            })
        elif any(kw in title for kw in ["其他", "补充", "附件"]):
            outline[3]["children"].append({
                "title": title, "order_index": len(outline[3]["children"]) + 1,
                "token_budget_hint": "small", "children": [],
            })

    return outline


def _build_tech_section_fallback(requirements: dict) -> list:
    """Build a reasonable tech section outline when AI generation fails.

    Mirrors the structure found in real security service bids.
    """
    service_reqs = requirements.get("service_requirements", [])

    # Core service plan sub-sections
    service_plan_children = [
        {
            "title": "项目概况与服务理念",
            "order_index": 1, "token_budget_hint": "medium",
            "children": [],
        },
        {
            "title": "组织架构与人员配置方案",
            "order_index": 2, "token_budget_hint": "large",
            "children": [
                {"title": "项目管理组织架构", "order_index": 1, "token_budget_hint": "medium", "children": []},
                {"title": "项目投入服务人员一览表", "order_index": 2, "token_budget_hint": "medium", "children": []},
                {"title": "各岗位人员配置明细", "order_index": 3, "token_budget_hint": "medium", "children": []},
                {"title": "人员替补与轮休机制", "order_index": 4, "token_budget_hint": "small", "children": []},
            ],
        },
        {
            "title": "综合服务管理方案",
            "order_index": 3, "token_budget_hint": "xlarge",
            "children": [],
        },
    ]

    # Add service-specific sub-sections based on requirements
    service_keywords_map = {
        "门卫": {"title": "门卫值守管理方案", "hint": "large"},
        "巡逻": {"title": "巡逻防控方案", "hint": "large"},
        "消防": {"title": "消防安全管理方案", "hint": "large"},
        "监控": {"title": "监控室值守方案", "hint": "large"},
        "车辆": {"title": "车辆与停车场管理方案", "hint": "large"},
        "保洁": {"title": "清洁卫生维护方案", "hint": "large"},
        "绿化": {"title": "绿化养护方案", "hint": "medium"},
        "设施": {"title": "设施设备维护方案", "hint": "large"},
        "安保": {"title": "安全保卫方案", "hint": "xlarge"},
        "物业": {"title": "综合物业管理方案", "hint": "xlarge"},
    }

    for req_text in service_reqs:
        text = req_text if isinstance(req_text, str) else str(req_text)
        for kw, info in service_keywords_map.items():
            if kw in text:
                service_plan_children.append({
                    "title": info["title"],
                    "order_index": len(service_plan_children) + 1,
                    "token_budget_hint": info["hint"],
                    "children": [],
                })
                break

    # Always include core sections that every security bid needs
    core_sections = [
        {
            "title": "管理制度与操作规范",
            "order_index": 99, "token_budget_hint": "large",
            "children": [
                {"title": "人力资源管理制度", "order_index": 1, "token_budget_hint": "medium", "children": []},
                {"title": "财务与物资管理制度", "order_index": 2, "token_budget_hint": "medium", "children": []},
                {"title": "各岗位操作规程", "order_index": 3, "token_budget_hint": "large", "children": []},
                {"title": "服务质量检查制度", "order_index": 4, "token_budget_hint": "medium", "children": []},
                {"title": "档案与记录管理制度", "order_index": 5, "token_budget_hint": "small", "children": []},
            ],
        },
        {
            "title": "服务质量承诺与保障措施",
            "order_index": 100, "token_budget_hint": "large",
            "children": [],
        },
        {
            "title": "应急处置预案",
            "order_index": 101, "token_budget_hint": "large",
            "children": [
                {"title": "火灾事故应急预案", "order_index": 1, "token_budget_hint": "medium", "children": []},
                {"title": "治安事件应急预案", "order_index": 2, "token_budget_hint": "medium", "children": []},
                {"title": "自然灾害应急预案", "order_index": 3, "token_budget_hint": "medium", "children": []},
                {"title": "设施故障应急预案", "order_index": 4, "token_budget_hint": "medium", "children": []},
                {"title": "公共卫生事件应急预案", "order_index": 5, "token_budget_hint": "medium", "children": []},
                {"title": "节假日与重大活动保障方案", "order_index": 6, "token_budget_hint": "medium", "children": []},
            ],
        },
        {
            "title": "人员培训与管理方案",
            "order_index": 102, "token_budget_hint": "large",
            "children": [
                {"title": "岗前培训体系", "order_index": 1, "token_budget_hint": "medium", "children": []},
                {"title": "在岗技能提升培训", "order_index": 2, "token_budget_hint": "medium", "children": []},
                {"title": "专项技能培训", "order_index": 3, "token_budget_hint": "medium", "children": []},
                {"title": "绩效考核与激励方案", "order_index": 4, "token_budget_hint": "medium", "children": []},
            ],
        },
        {
            "title": "拟投入装备与物资配置方案",
            "order_index": 103, "token_budget_hint": "large",
            "children": [],
        },
        {
            "title": "监督检查与考核方案",
            "order_index": 104, "token_budget_hint": "large",
            "children": [],
        },
        {
            "title": "人员保险与风险保障方案",
            "order_index": 105, "token_budget_hint": "medium",
            "children": [],
        },
    ]

    return [
        {
            "title": "项目整体服务方案",
            "order_index": 1, "token_budget_hint": "xlarge",
            "children": service_plan_children,
        },
        *core_sections,
    ]


# ---------------------------------------------------------------------------
# Utility: flatten outline to generation targets
# ---------------------------------------------------------------------------

def flatten_outline_to_chapters(outline_tree: list) -> List[dict]:
    """Flatten a deep outline tree into a list of generation targets.

    Each item represents one leaf section that needs AI-generated content.

    Args:
        outline_tree: List of root outline nodes.

    Returns:
        List of dicts with: title, depth, path (list), max_tokens,
        estimated_pages, category_key, parent_title.
    """
    from app.services.token_budget import collect_leaf_sections, assign_budgets
    from app.config import settings

    tree = assign_budgets(outline_tree, total_budget=settings.GENERATION_TOKEN_BUDGET_TOTAL)
    leaves = collect_leaf_sections(tree)

    # Add parent title for each leaf
    for leaf in leaves:
        path = leaf.get("path", [])
        if len(path) >= 2:
            leaf["parent_title"] = path[-2]
        else:
            leaf["parent_title"] = ""

    return leaves
