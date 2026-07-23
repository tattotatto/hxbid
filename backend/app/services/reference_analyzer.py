"""宏曦标书 - Reference Document Structure Analyzer.

Extracts deep outline hierarchies from historical/reference bid documents
by parsing Chinese heading patterns. The extracted structures are stored in
the reference_outlines table and used to:

  1. Guide deep outline generation (structural template matching)
  2. Provide section length statistics for token budget allocation
  3. Enable structure-aware RAG retrieval

Chinese bid documents use a predictable heading scheme:
  - 第X章 / 第X部分           → depth 0
  - 一、二、三、...           → depth 1
  - （一）（二）（三）...     → depth 2
  - 1. 2. 3. ...  or 1、2、3、→ depth 3
  - (1) (2) (3) ...           → depth 4

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OutlineNode:
    """A single node in a document outline tree."""
    title: str
    depth: int = 0
    order_index: int = 0
    char_offset: int = 0      # approximate character position in source text
    char_length: int = 0       # approximate length of this section's content
    children: List[OutlineNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "depth": self.depth,
            "order_index": self.order_index,
            "char_offset": self.char_offset,
            "char_length": self.char_length,
            "children": [c.to_dict() for c in self.children],
        }

    def count_leaves(self) -> int:
        if not self.children:
            return 1
        return sum(c.count_leaves() for c in self.children)

    def max_depth(self) -> int:
        if not self.children:
            return self.depth
        return max(c.max_depth() for c in self.children)


# ---------------------------------------------------------------------------
# Heading detection patterns
# ---------------------------------------------------------------------------

# Each pattern returns (depth, title_text) or None
HEADING_PATTERNS: List[Tuple[int, re.Pattern]] = [
    # 第X章 / 第X部分 / 第X篇  (depth 0)
    (0, re.compile(r'^第[一二三四五六七八九十\d]+[章节篇部]\s*(.*)$')),
    # 一、二、三、... 十、 (depth 1)
    (1, re.compile(r'^[一二三四五六七八九十]+[、．]\s*(.*)$')),
    # （一）（二）（三） (depth 2)
    (2, re.compile(r'^[（(][一二三四五六七八九十\d]+[）)]\s*(.*)$')),
    # 1. 2. 3. or 1、2、3、 (depth 3, but only at line start)
    (3, re.compile(r'^(\d+)[\.、．]\s+(.*)$')),
    # (1) (2) (3) (depth 4)
    (4, re.compile(r'^[（(]\d+[）)]\s*(.*)$')),
]

# Lines shorter than this are probably not section headings
MIN_HEADING_LENGTH = 2

# Lines longer than this are probably not section headings
MAX_HEADING_LENGTH = 80


def _detect_heading(line: str) -> Tuple[int, str] | None:
    """Try to detect a Chinese document heading in *line*.

    Returns (depth, title) if the line matches a heading pattern,
    otherwise None.
    """
    stripped = line.strip()
    if not stripped or len(stripped) < MIN_HEADING_LENGTH:
        return None
    if len(stripped) > MAX_HEADING_LENGTH:
        return None

    # Skip lines that are clearly table rows (contain pipes or excessive spaces)
    if "|" in stripped and stripped.count("|") >= 2:
        return None
    if "  " in stripped and len(stripped) < 15:
        return None

    for depth, pattern in HEADING_PATTERNS:
        m = pattern.match(stripped)
        if m:
            title = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else stripped
            # Some patterns capture all remaining text; validate
            if not title:
                title = stripped
            # Filter false positives: common non-heading patterns
            if _is_false_positive(title, stripped):
                continue
            return (depth, title)

    return None


def _is_false_positive(title: str, original: str) -> bool:
    """Filter out common false-positive heading matches.

    Examples: dates like "2026年7月", standalone numbers,
    page numbers, score values, etc.
    """
    # Dates: "2026年7月" or similar
    if re.match(r'^\d{4}年\d{1,2}月', title):
        return True
    # Score/points: "5分", "10分"
    if re.match(r'^\d+分$', title):
        return True
    # Years: just a number
    if re.match(r'^\d{4}$', title):
        return True
    # Page numbers: just digits
    if re.match(r'^\d{1,3}$', title) and not any(
        kw in original for kw in ['项目', '服务', '管理', '方案', '制度']
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Outline extraction
# ---------------------------------------------------------------------------

def extract_document_outline(text: str) -> List[OutlineNode]:
    """Extract a hierarchical outline from Chinese bid document text.

    Scans the text line by line, detecting heading patterns, and builds
    a tree where each node represents a section with its approximate
    character offset and length.

    Args:
        text: The full document text.

    Returns:
        List of root OutlineNode objects representing the top-level structure.
    """
    lines = text.split("\n")
    total_chars = len(text)

    # Stack of (depth, node) representing the current path in the tree
    stack: List[Tuple[int, OutlineNode]] = []
    # Dummy root for the top level
    root = OutlineNode(title="__root__", depth=-1, char_offset=0)
    stack.append((-1, root))

    # Also track character offsets
    char_pos = 0
    heading_order: Dict[int, int] = {}  # depth → next order_index

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        result = _detect_heading(line)
        if result:
            depth, title = result
            # Determine order_index at this depth
            heading_order[depth] = heading_order.get(depth, 0) + 1
            order = heading_order[depth]

            # Pop stack until we find a parent with depth < current depth
            while stack and stack[-1][0] >= depth:
                # Close the previous node: record its length
                prev_depth, prev_node = stack.pop()
                prev_node.char_length = char_pos - prev_node.char_offset

            # The current top of stack is the parent
            parent_depth, parent_node = stack[-1]
            new_node = OutlineNode(
                title=title,
                depth=depth,
                order_index=order,
                char_offset=char_pos,
            )
            parent_node.children.append(new_node)
            stack.append((depth, new_node))

        char_pos += line_len

    # Close all remaining open nodes
    while len(stack) > 1:
        depth, node = stack.pop()
        node.char_length = total_chars - node.char_offset

    return root.children


# ---------------------------------------------------------------------------
# Section statistics
# ---------------------------------------------------------------------------

def get_section_stats(outline_nodes: List[OutlineNode]) -> Dict[str, Any]:
    """Compute aggregate statistics from a document outline.

    Returns:
        Dict with keys:
        - total_sections: total number of sections (all nodes)
        - total_leaf_sections: number of leaf (content) sections
        - max_depth: maximum nesting depth
        - avg_section_length: average character length of leaf sections
        - median_section_length: median character length of leaf sections
        - section_lengths_by_depth: dict of depth → [lengths]
        - depth_distribution: dict of depth → count
    """
    leaves: List[OutlineNode] = []
    all_nodes: List[OutlineNode] = []
    depths: Dict[int, int] = {}
    lengths_by_depth: Dict[int, List[int]] = {}

    def _walk(nodes: List[OutlineNode]):
        for n in nodes:
            all_nodes.append(n)
            depths[n.depth] = depths.get(n.depth, 0) + 1
            if n.children:
                _walk(n.children)
            else:
                leaves.append(n)
                if n.depth not in lengths_by_depth:
                    lengths_by_depth[n.depth] = []
                lengths_by_depth[n.depth].append(n.char_length)

    _walk(outline_nodes)

    leaf_lengths = sorted([l.char_length for l in leaves])

    return {
        "total_sections": len(all_nodes),
        "total_leaf_sections": len(leaves),
        "max_depth": max(depths.keys()) if depths else 0,
        "avg_section_length": int(sum(leaf_lengths) / len(leaf_lengths)) if leaf_lengths else 0,
        "median_section_length": leaf_lengths[len(leaf_lengths) // 2] if leaf_lengths else 0,
        "section_lengths_by_depth": {
            str(d): {
                "count": len(lengths),
                "avg": int(sum(lengths) / len(lengths)) if lengths else 0,
                "min": min(lengths) if lengths else 0,
                "max": max(lengths) if lengths else 0,
            }
            for d, lengths in lengths_by_depth.items()
        },
        "depth_distribution": {str(k): v for k, v in depths.items()},
    }


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

async def build_reference_library(db) -> int:
    """Build the reference outline library from all historical bid projects.

    Queries all projects with original_file_path, parses their documents,
    extracts outlines, and stores them in the reference_outlines table.

    Args:
        db: Async database session.

    Returns:
        Number of documents successfully analyzed.
    """
    from sqlalchemy import select
    from app.models.project import BidProject
    from app.services.document_parser import parse_document

    # Ensure the table exists
    await _ensure_reference_outlines_table(db)

    result = await db.execute(
        select(BidProject).where(
            BidProject.original_file_path.isnot(None),
            BidProject.original_file_path != "",
        )
    )
    projects = result.scalars().all()

    analyzed = 0
    for project in projects:
        file_path = Path(project.original_file_path)
        if not file_path.is_file():
            logger.debug("Reference file not found: %s", project.original_file_path)
            continue

        try:
            text = parse_document(str(file_path))
            if not text or len(text) < 200:
                logger.debug("Reference text too short for %s", project.name)
                continue

            outline_nodes = extract_document_outline(text)
            stats = get_section_stats(outline_nodes)

            # Store in DB
            outline_json = json.dumps(
                [n.to_dict() for n in outline_nodes],
                ensure_ascii=False,
            )
            stats_json = json.dumps(stats, ensure_ascii=False)

            # Upsert: delete old entry if exists
            await db.execute(
                text("DELETE FROM reference_outlines WHERE project_id = :pid"),
                {"pid": project.id},
            )
            await db.execute(
                text(
                    "INSERT INTO reference_outlines "
                    "(id, project_id, outline_json, section_stats_json, "
                    " total_chars, total_sections, max_depth) "
                    "VALUES "
                    "(:uid, :pid, :outline_json, :stats_json, "
                    " :total_chars, :total_sections, :max_depth)"
                ),
                {
                    "uid": uuid.uuid4().hex,
                    "pid": project.id,
                    "outline_json": outline_json,
                    "stats_json": stats_json,
                    "total_chars": len(text),
                    "total_sections": stats["total_sections"],
                    "max_depth": stats["max_depth"],
                },
            )
            await db.flush()

            analyzed += 1
            logger.info(
                "Analyzed reference '%s': %d sections, %d leaves, max depth %d",
                project.name, stats["total_sections"],
                stats["total_leaf_sections"], stats["max_depth"],
            )

        except Exception as exc:
            logger.warning(
                "Failed to analyze reference '%s': %s", project.name, exc
            )
            continue

    if analyzed > 0:
        await db.commit()
        logger.info("Reference library built: %d documents analyzed", analyzed)

    return analyzed


async def _ensure_reference_outlines_table(db):
    """Create the reference_outlines table if it doesn't exist."""
    try:
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS reference_outlines ("
            "    id VARCHAR(36) PRIMARY KEY,"
            "    project_id VARCHAR(36) REFERENCES bid_projects(id) ON DELETE CASCADE,"
            "    outline_json TEXT NOT NULL DEFAULT '{}',"
            "    section_stats_json TEXT NOT NULL DEFAULT '{}',"
            "    total_chars INTEGER NOT NULL DEFAULT 0,"
            "    total_sections INTEGER NOT NULL DEFAULT 0,"
            "    max_depth INTEGER NOT NULL DEFAULT 0,"
            "    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"
            ")"
        ))
        await db.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_reference_outlines_project "
            "ON reference_outlines(project_id)"
        ))
        await db.flush()
    except Exception as exc:
        logger.warning("Could not ensure reference_outlines table: %s", exc)


async def get_reference_outlines(db) -> List[Dict[str, Any]]:
    """Retrieve all stored reference outlines.

    Returns:
        List of dicts with project_id, outline (parsed from JSON),
        stats (parsed from JSON), total_chars, total_sections, max_depth.
    """
    await _ensure_reference_outlines_table(db)

    try:
        result = await db.execute(
            text("SELECT * FROM reference_outlines ORDER BY total_sections DESC")
        )
        rows = result.fetchall()
        outlines = []
        for row in rows:
            outlines.append({
                "project_id": row[1],
                "outline": json.loads(row[2]) if row[2] else [],
                "stats": json.loads(row[3]) if row[3] else {},
                "total_chars": row[4],
                "total_sections": row[5],
                "max_depth": row[6],
            })
        return outlines
    except Exception as exc:
        logger.warning("Failed to fetch reference outlines: %s", exc)
        return []


async def get_aggregate_section_stats(db) -> Dict[str, Any]:
    """Get aggregate section length statistics across all reference documents.

    Used by the token budget allocator and deep outline generator to
    understand real-world section length distributions.

    Returns:
        Dict with aggregate statistics across all reference documents.
    """
    outlines = await get_reference_outlines(db)
    if not outlines:
        return {"document_count": 0}

    all_stats = [o["stats"] for o in outlines if o["stats"]]
    if not all_stats:
        return {"document_count": len(outlines)}

    # Aggregate across documents
    total_leaves = sum(s.get("total_leaf_sections", 0) for s in all_stats)
    avg_leaves = total_leaves / len(all_stats) if all_stats else 0
    max_depth = max(s.get("max_depth", 0) for s in all_stats)

    return {
        "document_count": len(outlines),
        "total_leaf_sections": total_leaves,
        "avg_leaf_sections_per_doc": round(avg_leaves, 1),
        "max_depth": max_depth,
        "total_chars": sum(o["total_chars"] for o in outlines),
    }
