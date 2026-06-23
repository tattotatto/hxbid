"""宏曦标书 - Edit Intent Analysis Engine.

Compares AI-generated chapter content against human-edited final content to
understand *why* each edit was made. Produces categorized edit patterns that
can feed back into the AI prompt pipeline (Phase 3).

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.ai_adapter import ai_adapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EDIT_TYPES = [
    "具体化不足",   # added concrete facts / numbers / examples
    "风格不对",     # tone / wording didn't match bid industry conventions
    "信息错误",     # factual error (cert number, name, date)
    "结构调整",     # chapter / paragraph reordering
    "冗余删除",     # removed filler content
    "补充遗漏",     # added content that the requirements asked for but AI missed
    "措辞优化",     # minor wording polish, no semantic change
    "其他",         # uncategorized
]

SYSTEM_PROMPT = """你是宏曦标书系统的编辑分析助手。你的任务是分析用户对AI生成的标书内容所做的修改，理解每次修改的"为什么"。

分析原则：
1. 逐段对比原文和修订版，识别所有实质性修改
2. 对每处修改判断修改类型
3. 如果能提取可泛化的写作规则，请用简洁中文输出
4. 不要过度分析——只关注实质性变化，忽略标点修正等微小调整

返回严格的JSON格式。"""

# Max chars to send to AI for analysis (to control token usage)
MAX_DIFF_CHARS = 6000

# Min chars of diff to trigger AI analysis (skip if too small)
MIN_DIFF_CHARS = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EditSegment:
    """A single edit segment from the diff."""
    edit_type: str = "其他"
    original_text: str = ""
    edited_text: str = ""
    reason: str = ""
    extracted_rule: Optional[str] = None
    confidence: float = 0.0


@dataclass
class EditAnalysis:
    """Complete edit analysis for one chapter."""
    chapter_id: str = ""
    chapter_title: str = ""
    original_content: str = ""
    edited_content: str = ""

    total_changes: int = 0
    added_chars: int = 0
    removed_chars: int = 0

    segments: List[EditSegment] = field(default_factory=list)
    edit_type_counts: Dict[str, int] = field(default_factory=dict)

    # Aggregated rules that can be injected into future prompts
    suggested_rules: List[str] = field(default_factory=list)

    # Whether AI analysis was performed (vs simple diff stats)
    ai_analyzed: bool = False


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


def compute_diff(original: str, edited: str) -> List[Tuple[str, str, str]]:
    """Compute paragraph-level diff between original and edited text.

    Returns list of (op, original_text, edited_text) tuples where op is
    'equal', 'replace', 'delete', or 'insert'.
    """
    orig_paras = [p.strip() for p in re.split(r"\n\s*\n", original) if p.strip()]
    edit_paras = [p.strip() for p in re.split(r"\n\s*\n", edited) if p.strip()]

    matcher = difflib.SequenceMatcher(None, orig_paras, edit_paras)
    diff_result: List[Tuple[str, str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        orig_block = "\n\n".join(orig_paras[i1:i2])
        edit_block = "\n\n".join(edit_paras[j1:j2])

        if tag == "equal":
            diff_result.append(("equal", orig_block, edit_block))
        elif tag == "replace":
            diff_result.append(("replace", orig_block, edit_block))
        elif tag == "delete":
            diff_result.append(("delete", orig_block, ""))
        elif tag == "insert":
            diff_result.append(("insert", "", edit_block))

    return diff_result


def diff_stats(diff_result: List[Tuple[str, str, str]]) -> Tuple[int, int, int]:
    """Count total changes, added chars, removed chars from diff."""
    changes = 0
    added = 0
    removed = 0
    for op, orig, edit in diff_result:
        if op != "equal":
            changes += 1
            removed += len(orig)
            added += len(edit)
    return changes, added, removed


# ---------------------------------------------------------------------------
# AI-powered analysis
# ---------------------------------------------------------------------------


async def analyze_edits_with_ai(
    chapter_title: str,
    original_content: str,
    edited_content: str,
) -> List[EditSegment]:
    """Use AI to analyze edit intent from the diff between original and edited.

    Args:
        chapter_title: Chapter title for context.
        original_content: AI-generated content.
        edited_content: Human-edited content.

    Returns:
        List of EditSegment with AI-classified edit types and reasons.
    """
    # Compute diff
    diff = compute_diff(original_content, edited_content)
    changed = [(op, orig, edit) for op, orig, edit in diff if op != "equal"]

    if not changed:
        return []

    # Build a compact diff representation for the AI
    diff_text_parts: List[str] = []
    for op, orig, edit in changed:
        if op == "replace":
            diff_text_parts.append(f"【原文】{orig[:500]}\n【修订】{edit[:500]}")
        elif op == "delete":
            diff_text_parts.append(f"【原文（已删除）】{orig[:300]}")
        elif op == "insert":
            diff_text_parts.append(f"【新增】{edit[:500]}")

    diff_text = "\n---\n".join(diff_text_parts)[:MAX_DIFF_CHARS]

    if len(diff_text) < MIN_DIFF_CHARS:
        return []

    edit_types_desc = "\n".join(f"- {t}" for t in EDIT_TYPES)

    prompt = f"""请分析以下标书章节的编辑修改意图。

章节名称：{chapter_title}

修改类型选项：
{edit_types_desc}

修改内容对比：
{diff_text}

请以JSON格式返回分析结果：
{{
  "segments": [
    {{
      "edit_type": "修改类型（从上述选项中选择）",
      "reason": "为什么做这个修改（一句话，中文）",
      "extracted_rule": "如果能提炼出可泛化的写作规则，请输出；否则为null",
      "confidence": 0.8
    }}
  ],
  "summary": "整体修改意图概述（一句话）"
}}

注意：
- 每个修改片段对应一个segment
- 合并相同类型的相邻修改
- extracted_rule格式示例："人员经历描述必须包含≥2个具体数字和≥1个具体项目名"
- 直接返回JSON对象，不要有其他文字"""

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
    except Exception as exc:
        logger.warning("AI edit analysis failed: %s", exc)
        # Return a basic analysis without AI
        return _basic_edit_analysis(changed)

    segments: List[EditSegment] = []
    for seg_data in result.get("segments", []):
        edit_type = seg_data.get("edit_type", "其他")
        if edit_type not in EDIT_TYPES:
            edit_type = "其他"

        segment = EditSegment(
            edit_type=edit_type,
            original_text=seg_data.get("original_text", "")[:200],
            edited_text=seg_data.get("edited_text", "")[:200],
            reason=seg_data.get("reason", ""),
            extracted_rule=seg_data.get("extracted_rule"),
            confidence=float(seg_data.get("confidence", 0.5)),
        )
        segments.append(segment)

    return segments


def _basic_edit_analysis(
    changed: List[Tuple[str, str, str]],
) -> List[EditSegment]:
    """Fallback: basic heuristics-based analysis without AI."""
    segments: List[EditSegment] = []
    for op, orig, edit in changed:
        if op == "insert" and len(edit) > 100:
            segments.append(EditSegment(
                edit_type="补充遗漏",
                original_text="",
                edited_text=edit[:200],
                reason="新增内容段",
                confidence=0.5,
            ))
        elif op == "delete" and len(orig) > 100:
            segments.append(EditSegment(
                edit_type="冗余删除",
                original_text=orig[:200],
                edited_text="",
                reason="删除冗余内容",
                confidence=0.5,
            ))
        elif op == "replace":
            # Simple heuristic: if edited is longer with numbers → 具体化不足
            if len(edit) > len(orig) and re.search(r"\d+", edit):
                segments.append(EditSegment(
                    edit_type="具体化不足",
                    original_text=orig[:200],
                    edited_text=edit[:200],
                    reason="修改增加了具体事实或数字",
                    confidence=0.6,
                ))
            else:
                segments.append(EditSegment(
                    edit_type="措辞优化",
                    original_text=orig[:200],
                    edited_text=edit[:200],
                    reason="文字表达调整",
                    confidence=0.5,
                ))
        else:
            segments.append(EditSegment(
                edit_type="其他",
                original_text=orig[:200],
                edited_text=edit[:200],
                reason="未分类修改",
                confidence=0.3,
            ))
    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_chapter_edits(
    chapter_id: str,
    chapter_title: str,
    ai_generated_content: str,
    final_content: str,
    use_ai: bool = True,
) -> EditAnalysis:
    """Run full edit analysis on a chapter.

    Args:
        chapter_id: Chapter identifier.
        chapter_title: Chapter title.
        ai_generated_content: Original AI-generated content.
        final_content: Human-edited final content.
        use_ai: Whether to use AI for intent analysis (vs basic heuristics).

    Returns:
        EditAnalysis with diff stats and categorized edit segments.
    """
    analysis = EditAnalysis(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        original_content=ai_generated_content,
        edited_content=final_content,
    )

    if not ai_generated_content or not final_content:
        return analysis

    # Compute diff stats
    diff = compute_diff(ai_generated_content, final_content)
    changes, added, removed = diff_stats(diff)
    analysis.total_changes = changes
    analysis.added_chars = added
    analysis.removed_chars = removed

    if changes == 0:
        return analysis

    # AI-powered analysis
    if use_ai:
        try:
            analysis.segments = await analyze_edits_with_ai(
                chapter_title=chapter_title,
                original_content=ai_generated_content,
                edited_content=final_content,
            )
            analysis.ai_analyzed = True
        except Exception as exc:
            logger.warning("AI edit analysis failed, falling back to basic: %s", exc)
            analysis.segments = _basic_edit_analysis(
                [(op, o, e) for op, o, e in diff if op != "equal"]
            )

    if not analysis.segments:
        analysis.segments = _basic_edit_analysis(
            [(op, o, e) for op, o, e in diff if op != "equal"]
        )

    # Aggregate edit type counts
    type_counts: Dict[str, int] = {}
    for seg in analysis.segments:
        type_counts[seg.edit_type] = type_counts.get(seg.edit_type, 0) + 1
    analysis.edit_type_counts = type_counts

    # Extract suggested rules
    rules: List[str] = []
    for seg in analysis.segments:
        if seg.extracted_rule and seg.extracted_rule not in rules:
            rules.append(seg.extracted_rule)
    analysis.suggested_rules = rules

    return analysis


async def analyze_project_edits(
    chapters: List[Dict[str, Any]],
    use_ai: bool = True,
) -> List[EditAnalysis]:
    """Run edit analysis on multiple chapters.

    Args:
        chapters: List of dicts with keys: id, title, ai_generated_content, final_content.
        use_ai: Whether to use AI for intent analysis.

    Returns:
        List of EditAnalysis, one per chapter.
    """
    results: List[EditAnalysis] = []
    for ch in chapters:
        analysis = await analyze_chapter_edits(
            chapter_id=ch.get("id", ""),
            chapter_title=ch.get("title", ""),
            ai_generated_content=ch.get("ai_generated_content", ""),
            final_content=ch.get("final_content", ""),
            use_ai=use_ai,
        )
        results.append(analysis)
    return results


def edit_analysis_to_dict(analysis: EditAnalysis) -> dict:
    """Convert an EditAnalysis to a JSON-serializable dict."""
    return {
        "chapter_id": analysis.chapter_id,
        "chapter_title": analysis.chapter_title,
        "total_changes": analysis.total_changes,
        "added_chars": analysis.added_chars,
        "removed_chars": analysis.removed_chars,
        "edit_type_counts": analysis.edit_type_counts,
        "suggested_rules": analysis.suggested_rules,
        "ai_analyzed": analysis.ai_analyzed,
        "segments": [
            {
                "edit_type": seg.edit_type,
                "original_text": seg.original_text[:300],
                "edited_text": seg.edited_text[:300],
                "reason": seg.reason,
                "extracted_rule": seg.extracted_rule,
                "confidence": seg.confidence,
            }
            for seg in analysis.segments[:15]  # limit to top 15
        ],
    }
