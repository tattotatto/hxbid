"""宏曦标书 - Content Assembler.

Walks the deep outline tree and assembles generated subsection content
into coherent chapter strings with proper heading hierarchy markers.
Produces output in the same `[{"title": ..., "content": ...}]` format
that render_engine.py expects, maintaining full backward compatibility.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

# Heading markers used in the assembled content.
# These map directly to Word heading styles in render_engine.py:
#   #   → Heading 1 (黑体 16pt)
#   ##  → Heading 2 (黑体 14pt)
#   ### → Heading 3 (楷体 12pt)
#   #### → bold body text (no Word heading style, but still visually distinct)

HEADING_MARKERS = {
    0: "# ",      # Root parts (商务部分, 技术部分, ...)
    1: "## ",     # Chapters
    2: "### ",    # Sections
    3: "#### ",   # Sub-sections
    4: "##### ",  # Deep sub-sections (rendered as bold body paragraph)
}


def _content_starts_with_heading(content: str, title: str) -> bool:
    """Check if content already begins with a markdown heading matching the title."""
    if not content or not content.strip():
        return False
    first_line = content.strip().split("\n")[0].strip()
    # Remove leading # markers and whitespace
    cleaned = re.sub(r'^#{1,5}\s*', '', first_line).strip()
    # Fuzzy match: check if the title appears in the first heading line
    return title in cleaned or cleaned in title


def assemble_chapter_content(
    node: dict,
    generated_sections: Dict[str, str],
    depth: int = 0,
    parent_path: list | None = None,
) -> str:
    """Recursively assemble a node's content from its generated subsections.

    For non-leaf nodes, inserts the node's title as a heading and then
    appends all children's content. For leaf nodes, retrieves the
    generated content from *generated_sections*.

    Args:
        node: An outline tree node dict with keys:
            - title: str
            - children: list (optional)
            - depth: int
        generated_sections: Dict mapping " > ".join(path) → content string.
        depth: Current recursion depth (used for heading level).
        parent_path: Accumulated path from root to this node (list of titles).

    Returns:
        Assembled markdown content string for this node and all descendants.
    """
    parts: List[str] = []
    title = node.get("title", "")
    children = node.get("children", [])

    # Determine heading level from the node's actual depth
    node_depth = node.get("depth", depth)
    marker = HEADING_MARKERS.get(node_depth, "#### ")

    # Build the full path for this node
    if parent_path is None:
        parent_path = []
    node_path = parent_path + [title]

    if children:
        # Non-leaf: collect children first, then decide if we need a heading
        child_contents = []
        for child in children:
            child_content = assemble_chapter_content(
                child, generated_sections, depth + 1, node_path
            )
            if child_content.strip():
                child_contents.append(child_content)

        if child_contents:
            # Only add heading if there's actual content below
            parts.append(f"\n\n{marker}{title}\n")
            parts.extend(child_contents)
    else:
        # Leaf: retrieve generated content
        path = " > ".join(node_path)
        content = generated_sections.get(path, "")
        if content.strip():
            # Dedup: skip assembler heading if content already starts with one
            if not _content_starts_with_heading(content, title):
                parts.append(f"\n\n{marker}{title}\n")
            parts.append(content)

    return "\n".join(parts)


def _build_node_path(node: dict) -> str:
    """Build the full path key for a node from its stored path data.

    The node should have a 'path' key (list of str) or we fall back
    to just the title.
    """
    stored_path = node.get("path")
    if stored_path and isinstance(stored_path, list):
        return " > ".join(stored_path)
    return node.get("title", "")


def build_final_chapters_payload(
    outline_tree: list,
    generated_sections: Dict[str, str],
) -> List[dict]:
    """Build the final chapters payload for the render engine.

    Walks the top-level parts of the outline tree, assembles all
    subsection content under each part, and returns the standard
    `[{"title": ..., "content": ...}]` format.

    Args:
        outline_tree: The full outline tree (list of root part dicts).
        generated_sections: Dict mapping path → generated content.

    Returns:
        List of dicts with "title" and "content" keys, ready for
        render_bid_to_docx().
    """
    chapters_payload: List[dict] = []

    for i, part in enumerate(outline_tree):
        part_title = part.get("title", f"第{i + 1}部分")
        content = assemble_chapter_content(part, generated_sections, depth=0)
        chapters_payload.append({
            "title": part_title,
            "content": content,
            "order_index": part.get("order_index", i + 1),
        })

    # Log assembly summary
    total_chars = sum(len(ch.get("content", "")) for ch in chapters_payload)
    logger.info(
        "Assembled %d chapters, total %d characters (~%d pages)",
        len(chapters_payload),
        total_chars,
        total_chars // 650,
    )

    return chapters_payload


def generate_chapter_summary(content: str, max_len: int = 80) -> str:
    """Generate a one-line summary of chapter content.

    Used for sibling awareness in progressive prompts — helps the AI
    know what other sections cover so it can avoid duplication.

    Args:
        content: The full section content.
        max_len: Maximum summary length in characters.

    Returns:
        A one-line summary string.
    """
    if not content:
        return "（空）"

    # Take the first substantive line after any heading
    lines = content.strip().split("\n")
    for line in lines:
        line = line.strip()
        # Skip heading markers and empty lines
        if line.startswith("#") or not line:
            continue
        if len(line) > 10:
            summary = line[:max_len]
            if len(line) > max_len:
                summary += "…"
            return summary

    # Fallback: just take the first N chars
    cleaned = content.strip()[:max_len]
    return cleaned + "…" if len(content.strip()) > max_len else cleaned


def collect_sibling_summaries(
    parent_node: dict,
    generated_sections: Dict[str, str],
) -> List[str]:
    """Collect one-line summaries of all siblings under a parent node.

    Useful for building the sibling-awareness section of the progressive
    prompt. Each sibling gets a brief summary so the AI knows what it
    should NOT duplicate.

    Args:
        parent_node: The parent node whose children we want to summarize.
        generated_sections: Dict of already-generated content.

    Returns:
        List of "标题：摘要" strings.
    """
    summaries: List[str] = []
    # Build parent path list for constructing leaf paths
    parent_title_list = parent_node.get("path", [parent_node.get("title", "")]) \
        if isinstance(parent_node.get("path"), list) else [parent_node.get("title", "")]
    for child in parent_node.get("children", []):
        child_title = child.get("title", "")
        if child.get("children"):
            # Non-leaf: just show the title
            summaries.append(f"{child_title}（含{len(child['children'])}个子章节）")
        else:
            # Build full path: parent path + child title
            child_path = parent_title_list + [child_title]
            path = " > ".join(child_path)
            content = generated_sections.get(path, "")
            summary = generate_chapter_summary(content)
            summaries.append(f"{child_title}：{summary}")
    return summaries
