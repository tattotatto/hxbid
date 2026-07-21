"""宏曦标书 - Recursive Subsection Generator.

The core generation engine for deep bid documents.  Replaces the flat
"one AI call per chapter" approach with recursive depth-first generation:

  1. Walk the deep outline tree (3-4 levels, 50-200+ leaf sections).
  2. For each leaf section, build a progressive-disclosure prompt.
  3. Call the AI with an elastic token budget (not the flat 4096 default).
  4. Stream tokens back via SSE, tracking subsection-level progress.
  5. Assemble all subsection content into parent chapters.

Together with token_budget.py and content_assembler.py this enables
generating 2000-page bid documents instead of the current 30-page output.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from app.services.ai_adapter import ai_adapter
from app.services.deid import deidentify_text
from app.services.token_budget import (
    assign_budgets,
    collect_leaf_sections,
    get_section_length_guidance,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Max chars of reference context per section (proportional to budget)
REF_CONTEXT_RATIO = 0.18  # 18% of token budget

# Max chars from a single reference source
MAX_REF_CHARS_PER_SOURCE = 8000

# Max chars total for all references in one prompt
MAX_REF_TOTAL_CHARS = 32000


# ---------------------------------------------------------------------------
# Progressive-disclosure prompt builder
# ---------------------------------------------------------------------------

def _build_progressive_prompt(
    section_title: str,
    section_path: List[str],
    depth: int,
    requirements: dict,
    sibling_summaries: List[str] | None = None,
    reference_sections: List[str] | None = None,
    company_context: str = "",
    extra_guidance: str = "",
    max_tokens: int = 4096,
) -> str:
    """Build a progressive-disclosure prompt for a single leaf section.

    Six layers of context, from broadest to most specific:

    Layer 1 — Ancestry: full tree path from root
    Layer 2 — Parent context: what the parent section covers
    Layer 3 — Sibling awareness: summaries of sibling sections (anti-duplication)
    Layer 4 — Tender requirements: filtered to this section's domain
    Layer 5 — Reference examples: similar sections from historical bids
    Layer 6 — Section-specific writing guidance

    Args:
        section_title: The title of this leaf section.
        section_path: Full path from root to this section (inclusive).
        depth: Depth in the outline tree (0 = root part).
        requirements: Parsed tender requirements dict.
        sibling_summaries: One-line summaries of sibling sections under the
            same parent, used to prevent content duplication.
        reference_sections: Full text of similar sections from historical
            bids (retrieved via RAG).
        company_context: Company info block (with anti-fabrication rules).
        extra_guidance: Additional writing guidance for this section.
        max_tokens: The token budget for this section (drives length guidance).

    Returns:
        A complete user-prompt string ready for the AI.
    """
    parts: List[str] = []

    # ── Layer 1: Ancestry (where are we in the document?) ──
    if section_path:
        ancestry = " > ".join(section_path)
        parts.append(f"【当前撰写位置】{ancestry}")
    parts.append(f"章节标题：{section_title}")

    # ── Layer 2: Parent context ──
    if len(section_path) >= 2:
        parent_title = section_path[-2] if len(section_path) >= 2 else ""
        if parent_title:
            parts.append(f"\n【父章节】{parent_title}")
            parts.append("请确保本节内容与父章节的整体主题紧密相关。")

    # ── Layer 3: Sibling awareness ──
    if sibling_summaries:
        parts.append("\n【同级章节摘要 — 以下是与本章节同级的其他章节，请确保内容不与其重复】")
        for i, summary in enumerate(sibling_summaries, 1):
            parts.append(f"  {i}. {summary}")

    # ── Layer 4: Tender requirements ──
    req_text = _format_requirements_for_section(requirements, section_title)
    if req_text:
        parts.append(f"\n【招标要求（与本节约相关的部分）】\n{req_text}")

    # ── Layer 5: Reference examples ──
    if reference_sections:
        total_chars = sum(len(r) for r in reference_sections)
        budget_chars = int(max_tokens * REF_CONTEXT_RATIO)

        parts.append(f"\n【参考素材 — 历史标书中类似章节的写法（共 {len(reference_sections)} 篇）】")
        parts.append("请参考其写作风格和详细程度，但不要照搬内容。")

        used_chars = 0
        shown = 0
        for i, ref_text in enumerate(reference_sections):
            if used_chars >= min(budget_chars, MAX_REF_TOTAL_CHARS):
                parts.append(f"\n（还有 {len(reference_sections) - shown} 篇参考因篇幅限制未展示）")
                break
            truncated = ref_text[:MAX_REF_CHARS_PER_SOURCE]
            parts.append(f"\n--- 参考 {i + 1} ---\n{truncated}")
            used_chars += len(truncated)
            shown += 1

    # ── Layer 6: Section guidance ──
    if extra_guidance:
        parts.append(f"\n{extra_guidance}")

    # ── Length guidance (based on token budget) ──
    parts.append(get_section_length_guidance(section_title, max_tokens))

    # ── Company context (injected last so it's fresh in the AI's context) ──
    if company_context:
        parts.insert(0, company_context)

    return "\n".join(parts)


def _format_requirements_for_section(requirements: dict, section_title: str) -> str:
    """Format the requirements dict, filtering for relevance to *section_title*."""
    lines: List[str] = []
    title_lower = section_title.lower()

    # Service requirements — always relevant for 服务/技术 sections
    service_reqs = requirements.get("service_requirements", [])
    if service_reqs and any(kw in title_lower for kw in ["服务", "技术", "方案", "管理"]):
        lines.append(f"服务要求：{'；'.join(service_reqs)}")

    # Personnel — relevant for 人员 sections
    personnel = requirements.get("personnel_requirements", "")
    if personnel and any(kw in title_lower for kw in ["人员", "配置", "团队", "组织"]):
        lines.append(f"人员要求：{personnel}")

    # Special requirements — always include
    special = requirements.get("special_requirements", [])
    if special:
        lines.append(f"特殊要求：{'；'.join(special)}")

    # Evaluation criteria — relevant for 方案 sections
    eval_criteria = requirements.get("evaluation_criteria", "")
    if eval_criteria and any(kw in title_lower for kw in ["方案", "技术", "服务", "质量"]):
        lines.append(f"评标标准：{eval_criteria}")

    # Project info — always include
    if requirements.get("project_name"):
        lines.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("project_duration"):
        lines.append(f"服务期限：{requirements['project_duration']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt augmentation for deep sections
# ---------------------------------------------------------------------------

DEEP_SECTION_PROMPT_SUFFIX = """
深度章节撰写额外要求：
1. 本小节是标书中一个具体的子章节，请直接开始撰写实质内容，不需要写"本部分将介绍..."之类的引导语
2. 内容必须有层次感：先概述，再分点详述，最后总结
3. 每个要点必须包含具体操作步骤、量化指标或实际案例
4. 与同级章节的内容要互补不重复，形成完整体系
5. 适当使用表格呈现对比数据、人员配置、时间安排等信息
6. 专业性要求：使用行业标准术语和规范表达"""


# ---------------------------------------------------------------------------
# Core generation functions
# ---------------------------------------------------------------------------

async def generate_section(
    section_title: str,
    section_path: List[str],
    depth: int,
    requirements: dict,
    max_tokens: int = 4096,
    sibling_summaries: List[str] | None = None,
    reference_sections: List[str] | None = None,
    company_profile: dict | None = None,
    extra_guidance: str = "",
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """Generate content for a single leaf section.

    Builds a progressive-disclosure prompt and streams the AI response
    token-by-token.

    Args:
        section_title: Title of this leaf section.
        section_path: Full path from root to this section.
        depth: Depth in the outline tree.
        requirements: Parsed tender requirements.
        max_tokens: Token budget for this section (from token_budget.py).
        sibling_summaries: Summaries of siblings to prevent duplication.
        reference_sections: Similar sections from historical bids.
        company_profile: Company info dict.
        extra_guidance: Additional writing instructions.
        temperature: AI temperature (default 0.7).

    Yields:
        Generated content chunks (str).
    """
    # ── Build company context block ──
    from app.services.ai_pipeline import build_company_info_block
    company_context = build_company_info_block(company_profile) if company_profile else ""

    # ── De-identify PII in reference sections ──
    safe_references: List[str] = []
    if reference_sections:
        for ref in reference_sections:
            safe_ref, _ = deidentify_text(ref)
            safe_references.append(safe_ref if safe_ref else ref)

    # ── Build the progressive prompt ──
    user_prompt = _build_progressive_prompt(
        section_title=section_title,
        section_path=section_path,
        depth=depth,
        requirements=requirements,
        sibling_summaries=sibling_summaries,
        reference_sections=safe_references,
        company_context=company_context,
        extra_guidance=extra_guidance,
        max_tokens=max_tokens,
    )

    # ── Build system prompt with deep-section augmentation ──
    from app.services.ai_pipeline import _build_system_prompt
    system_content = _build_system_prompt()
    if depth >= 2:
        system_content += "\n" + DEEP_SECTION_PROMPT_SUFFIX

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_prompt},
    ]

    # ── Stream from AI ──
    logger.info(
        "Generating section [depth=%d, budget=%d]: %s",
        depth, max_tokens, section_title,
    )

    stream = ai_adapter.chat_completion_stream(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    async for chunk in stream:
        yield chunk


async def generate_section_tree(
    outline_tree: list,
    requirements: dict,
    company_profile: dict | None = None,
    rag_service=None,
    db=None,
    project_id: str = "",
    progress_callback: Callable | None = None,
    temperature: float = 0.7,
) -> Dict[str, str]:
    """Recursively generate content for all leaf sections in an outline tree.

    Traverses the tree depth-first. Leaf sections get individual AI generation
    calls. Non-leaf sections are assembled from their children.

    Args:
        outline_tree: The deep outline tree (list of dicts with title, children,
            max_tokens, estimated_pages, depth).
        requirements: Parsed tender requirements.
        company_profile: Company info dict.
        rag_service: Optional RAG service for fetching reference sections.
        db: Database session for RAG queries.
        project_id: Current project ID (for exclusion from RAG).
        progress_callback: Called as progress_callback(event_type, data) for
            each significant milestone.
        temperature: AI temperature.

    Returns:
        Dict mapping section full paths (joined with " > ") to generated content.
    """
    generated: Dict[str, str] = {}

    async def _generate_node(
        nodes: list,
        parent_path: List[str],
    ) -> None:
        # Collect sibling titles for anti-duplication summaries
        sibling_titles = [
            n.get("title", "") for n in nodes
        ]

        for node in nodes:
            title = node.get("title", "")
            node_path = parent_path + [title]
            path_key = " > ".join(node_path)
            children = node.get("children", [])
            depth = node.get("depth", len(parent_path))

            if children:
                # Non-leaf: recurse into children
                await _generate_node(children, node_path)
                continue

            # Leaf section — generate content
            max_tokens = node.get("max_tokens", 4096)
            estimated_pages = node.get("estimated_pages", 1)

            if progress_callback:
                progress_callback("subsection_status", {
                    "title": title,
                    "path": node_path,
                    "depth": depth,
                    "max_tokens": max_tokens,
                    "estimated_pages": estimated_pages,
                })

            # ── Gather sibling summaries (exclude self) ──
            sibling_summaries = [
                f"{t}（详见该章节）" for t in sibling_titles
                if t != title
            ]

            # ── Fetch reference sections via RAG ──
            reference_sections: List[str] = []
            try:
                if rag_service and hasattr(rag_service, 'retrieve_similar_chapters'):
                    similar = await rag_service.retrieve_similar_chapters(
                        chapter_title=title,
                        requirements=requirements,
                        project_id=project_id,
                        n_results=3,
                    )
                    reference_sections = [s.get("content", "") for s in similar if s.get("content")]
            except Exception as exc:
                logger.debug("RAG retrieval failed for section '%s': %s", title, exc)

            # ── Build section-specific guidance ──
            from app.services.ai_pipeline import _get_section_guidance
            guidance = _get_section_guidance(title)
            if depth >= 2:
                guidance += "\n\n注意：本小节是" + " > ".join(parent_path) + " 下的具体子章节，请聚焦本小节的主题深入展开，不要重复父章节的概述性内容。"

            # ── Generate ──
            full_content = ""
            try:
                async for chunk in generate_section(
                    section_title=title,
                    section_path=node_path,
                    depth=depth,
                    requirements=requirements,
                    max_tokens=max_tokens,
                    sibling_summaries=sibling_summaries[:8],  # cap at 8
                    reference_sections=reference_sections,
                    company_profile=company_profile,
                    extra_guidance=guidance,
                    temperature=temperature,
                ):
                    full_content += chunk
                    if progress_callback:
                        progress_callback("subsection_chunk", {
                            "title": title,
                            "path": node_path,
                            "text": chunk,
                        })
            except Exception as exc:
                logger.error("Section generation failed for '%s': %s", title, exc)
                full_content = f"\n\n[本节生成失败：{exc}]\n\n"

            generated[path_key] = full_content

            if progress_callback:
                progress_callback("subsection_done", {
                    "title": title,
                    "path": node_path,
                    "content_length": len(full_content),
                })

    await _generate_node(outline_tree, parent_path=[])
    return generated


# ---------------------------------------------------------------------------
# Convenience: prepare outline tree from AI-generated outline JSON
# ---------------------------------------------------------------------------

def prepare_outline_tree(
    outline_json: list,
    requirements: dict,
) -> list:
    """Prepare an outline tree for generation.

    1. Assign token budgets to all leaf nodes.
    2. Collect statistics for logging.

    Args:
        outline_json: The AI-generated deep outline (list of nested dicts).
        requirements: Parsed tender requirements.

    Returns:
        The same tree with budgets assigned (mutated in-place).
    """
    from app.config import settings

    tree = assign_budgets(
        outline_json,
        total_budget=settings.GENERATION_TOKEN_BUDGET_TOTAL,
    )

    leaves = collect_leaf_sections(tree)
    total_pages = sum(l["estimated_pages"] for l in leaves)
    total_tokens = sum(l["max_tokens"] for l in leaves)

    logger.info(
        "Outline prepared: %d leaf sections, ~%d tokens budgeted, ~%d estimated pages",
        len(leaves), total_tokens, total_pages,
    )

    # Log budget distribution
    category_counts: Dict[str, int] = {}
    for leaf in leaves:
        cat = leaf.get("category_key", "unknown")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    logger.info("Budget distribution: %s", category_counts)

    return tree


def get_outline_stats(outline_tree: list) -> dict:
    """Return summary statistics for an outline tree.

    Useful for the `outline_generated` SSE event to show the user
    what will be generated before generation begins.
    """
    leaves = collect_leaf_sections(outline_tree)
    total_pages = sum(l["estimated_pages"] for l in leaves)
    total_tokens = sum(l["max_tokens"] for l in leaves)

    # Count nodes at each depth
    depth_counts: Dict[int, int] = {}

    def _count_depth(nodes: list):
        for n in nodes:
            d = n.get("depth", 0)
            depth_counts[d] = depth_counts.get(d, 0) + 1
            if n.get("children"):
                _count_depth(n["children"])

    _count_depth(outline_tree)

    return {
        "total_leaf_sections": len(leaves),
        "estimated_pages": total_pages,
        "estimated_tokens": total_tokens,
        "max_depth": max(depth_counts.keys()) if depth_counts else 0,
        "depth_distribution": depth_counts,
        "top_level_parts": len(outline_tree),
    }
