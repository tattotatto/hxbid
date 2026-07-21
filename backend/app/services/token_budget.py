"""宏曦标书 - Smart Token Budget Allocator.

Assigns per-section generation token budgets based on section type, depth,
and reference document length statistics. Replaces the flat AI_MAX_TOKENS=4096
with elastic budgets that enable each section to reach its natural length.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget categories
# ---------------------------------------------------------------------------

@dataclass
class BudgetCategory:
    """A token-budget tier with its target range and matching keywords."""
    key: str
    min_tokens: int
    max_tokens: int
    keywords: List[str] = field(default_factory=list)
    anti_keywords: List[str] = field(default_factory=list)
    label: str = ""

    def match(self, section_title: str) -> float:
        """Return a match score 0.0-1.0 for *section_title*.

        Positive keywords increase the score; anti_keywords decrease it.
        """
        score = 0.0
        title_lower = section_title.lower()
        for kw in self.keywords:
            if kw in title_lower:
                score += 0.35
        for ak in self.anti_keywords:
            if ak in title_lower:
                score -= 0.5
        return min(max(score, 0.0), 1.0)


# Ordered from smallest to largest — first match wins (highest score breaks tie)
BUDGET_CATEGORIES: List[BudgetCategory] = [
    BudgetCategory("tiny",   512,   1536, keywords=["函", "证明", "委托书", "一览表", "授权", "保证金", "凭证", "声明"], label="微型"),
    BudgetCategory("small",  1536,  4096, keywords=["承诺", "基本情况表", "资格", "资质审查", "投标人认为"], label="小型"),
    BudgetCategory("medium", 4096,  8192, keywords=["人员", "配置", "团队", "组织架构", "管理架构", "岗位", "报价", "业绩"], label="中型"),
    BudgetCategory("large",  8192, 16384, keywords=["应急", "预案", "培训", "制度", "管理", "演练", "考核", "保障", "维修", "保养", "装备", "车辆"], label="大型"),
    BudgetCategory("xlarge", 16384, 32768, keywords=["方案", "技术", "服务", "措施", "流程", "标准", "规范", "操作"], label="超大型"),
]

# Depth modifier: deeper sections get proportionally less budget
# (sibling sections share the parent's allocation conceptually)
DEPTH_MODIFIER = {
    0: 1.0,    # root parts
    1: 1.0,    # top-level chapters
    2: 0.85,   # sections
    3: 0.70,   # sub-sections
    4: 0.55,   # leaf details
    5: 0.40,   # very deep leaves
}

# Total generation budget pool (characters, not tokens)
# At ~500 tokens/page, 2M tokens ≈ 4000 pages of raw generation capacity
DEFAULT_TOTAL_BUDGET_TOKENS = 2_000_000

# Approximate Chinese characters per token (varies by model, ~1.5-2.0)
CHARS_PER_TOKEN = 1.6


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_section(section_title: str, depth: int = 1) -> BudgetCategory:
    """Determine the budget category for a section by its title and depth.

    Returns the best-matching BudgetCategory. Categories are tested in order
    from largest to smallest to ensure "服务方案" matches xlarge rather than
    being caught by "方案" as a generic keyword.
    """
    best_category = BUDGET_CATEGORIES[0]  # default: tiny
    best_score = -1.0

    # Test largest → smallest so specific keywords win
    for cat in reversed(BUDGET_CATEGORIES):
        score = cat.match(section_title)
        if score > best_score:
            best_score = score
            best_category = cat

    if best_score <= 0.0:
        # No keyword matched — use depth as heuristic
        if depth <= 1:
            best_category = BUDGET_CATEGORIES[1]  # small
        elif depth == 2:
            best_category = BUDGET_CATEGORIES[2]  # medium
        else:
            best_category = BUDGET_CATEGORIES[3]  # large

    return best_category


def get_token_budget(
    section_title: str,
    depth: int = 1,
    parent_budget_hint: str = "",
) -> int:
    """Return the recommended max_tokens for a section.

    Args:
        section_title: The section/chapter title.
        depth: Depth in the outline tree (0 = root part, 1 = chapter, ...).
        parent_budget_hint: Optional hint from the parent outline node
            ("tiny", "small", "medium", "large", "xlarge").

    Returns:
        Recommended max_tokens for the AI generation call.
    """
    # Use parent hint if available (from AI-generated outline)
    if parent_budget_hint:
        for cat in BUDGET_CATEGORIES:
            if cat.key == parent_budget_hint:
                base = cat.max_tokens
                modifier = DEPTH_MODIFIER.get(depth, 0.5)
                return max(int(base * modifier), 256)
        # fallthrough

    cat = classify_section(section_title, depth)
    modifier = DEPTH_MODIFIER.get(depth, 0.5)
    budget = int(cat.max_tokens * modifier)
    # Never go below 256 tokens (a paragraph or two)
    return max(budget, 256)


def get_budget_for_section(
    section_title: str,
    section_path: List[str],
    depth: int,
) -> dict:
    """Return a full budget allocation dict for a section.

    Args:
        section_title: The section title.
        section_path: List of ancestor titles (e.g. ["技术部分", "服务方案"]).
        depth: Depth in the tree.

    Returns:
        Dict with keys: max_tokens, category_key, category_label,
        estimated_chars, estimated_pages.
    """
    cat = classify_section(section_title, depth)
    modifier = DEPTH_MODIFIER.get(depth, 0.5)
    max_tokens = max(int(cat.max_tokens * modifier), 256)
    estimated_chars = int(max_tokens * CHARS_PER_TOKEN)
    # Chinese bid format: ~500-800 chars per page (with tables, spacing)
    estimated_pages = max(1, int(estimated_chars / 650))

    return {
        "max_tokens": max_tokens,
        "category_key": cat.key,
        "category_label": cat.label,
        "estimated_chars": estimated_chars,
        "estimated_pages": estimated_pages,
    }


def assign_budgets(
    outline_tree: list,
    total_budget: int = DEFAULT_TOTAL_BUDGET_TOKENS,
) -> list:
    """Walk an outline tree and assign token budgets to every leaf node.

    The tree should be a list of dicts, each with:
      - title: str
      - token_budget_hint: str (optional)
      - children: list (optional)

    Each node is mutated in-place by adding:
      - max_tokens: int
      - estimated_pages: int
      - depth: int

    Returns the mutated tree.

    If the sum of all assigned budgets exceeds *total_budget*, budgets
    are scaled down proportionally.
    """
    all_leaf_budgets: List[dict] = []

    def _walk(nodes: list, depth: int, path: List[str]):
        for node in nodes:
            title = node.get("title", "")
            hint = node.get("token_budget_hint", "")
            node["depth"] = depth
            node_path = path + [title]

            children = node.get("children", [])
            if children:
                _walk(children, depth + 1, node_path)
                # Non-leaf: sum of children's budgets
                node["max_tokens"] = sum(
                    c.get("max_tokens", 0) for c in children
                )
                node["estimated_pages"] = sum(
                    c.get("estimated_pages", 0) for c in children
                )
            else:
                # Leaf node
                budget = get_budget_for_section(title, path, depth)
                node["max_tokens"] = budget["max_tokens"]
                node["estimated_pages"] = budget["estimated_pages"]
                node["category_key"] = budget["category_key"]
                all_leaf_budgets.append(node)

    _walk(outline_tree, depth=0, path=[])

    # Scale down if total exceeds budget
    total_assigned = sum(n.get("max_tokens", 0) for n in all_leaf_budgets)
    if total_assigned > total_budget and all_leaf_budgets:
        scale = total_budget / total_assigned
        logger.info(
            "Token budget oversubscribed (%.1f%%), scaling by %.2f",
            (total_assigned / total_budget) * 100, scale,
        )
        for node in all_leaf_budgets:
            node["max_tokens"] = max(int(node["max_tokens"] * scale), 256)
            node["estimated_pages"] = max(
                int(node["max_tokens"] * CHARS_PER_TOKEN / 650), 1
            )

    return outline_tree


def collect_leaf_sections(outline_tree: list) -> List[dict]:
    """Collect all leaf sections from the outline tree in depth-first order.

    Each leaf dict contains: title, max_tokens, estimated_pages, depth,
    path (list of ancestor titles), category_key.
    """
    leaves: List[dict] = []

    def _collect(nodes: list, path: List[str]):
        for node in nodes:
            title = node.get("title", "")
            node_path = path + [title]
            children = node.get("children", [])
            if children:
                _collect(children, node_path)
            else:
                leaf = {
                    "title": title,
                    "max_tokens": node.get("max_tokens", 4096),
                    "estimated_pages": node.get("estimated_pages", 1),
                    "depth": node.get("depth", 0),
                    "path": node_path,
                    "category_key": node.get("category_key", "medium"),
                }
                leaves.append(leaf)

    _collect(outline_tree, path=[])
    return leaves


def get_section_length_guidance(section_title: str, max_tokens: int) -> str:
    """Return a human-readable length hint for the AI prompt.

    Gives the AI a concrete word-count target rather than letting it
    stop early at an arbitrary point.
    """
    estimated_chars = int(max_tokens * CHARS_PER_TOKEN)
    estimated_pages = max(1, int(estimated_chars / 650))

    return (
        f"\n\n【篇幅要求】本章节目标篇幅约 {estimated_chars:,} 字（约 {estimated_pages} 页）。"
        f"请按此篇幅展开详细撰写，确保内容充实、具体，达到目标篇幅要求。"
    )
