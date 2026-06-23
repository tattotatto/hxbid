"""宏曦标书 - Bid Result Analytics Engine.

Analyzes won/lost bid patterns to extract success factors, chapter
effectiveness scores, and actionable recommendations for future bids.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.edit_rule import EditRule
from app.models.project import BidProject, ProjectChapter
from app.services.ai_adapter import ai_adapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Analytics queries
# ---------------------------------------------------------------------------


async def get_project_stats(db: AsyncSession) -> Dict[str, Any]:
    """Return aggregate bid project statistics."""
    # Total
    total_result = await db.execute(select(func.count(BidProject.id)))
    total = total_result.scalar() or 0

    # Won
    won_result = await db.execute(
        select(func.count(BidProject.id)).where(BidProject.bid_result == "won")
    )
    won = won_result.scalar() or 0

    # Lost
    lost_result = await db.execute(
        select(func.count(BidProject.id)).where(BidProject.bid_result == "lost")
    )
    lost = lost_result.scalar() or 0

    # Pending
    pending = total - won - lost

    # Win rate
    decided = won + lost
    win_rate = round(won / decided * 100, 1) if decided > 0 else 0

    # Active (in progress)
    active_result = await db.execute(
        select(func.count(BidProject.id)).where(
            BidProject.status.in_(["parsed", "parsing", "generating", "review"])
        )
    )
    active = active_result.scalar() or 0

    # Exported
    exported_result = await db.execute(
        select(func.count(BidProject.id)).where(BidProject.status == "exported")
    )
    exported = exported_result.scalar() or 0

    # Active rules
    rules_result = await db.execute(
        select(func.count(EditRule.id)).where(EditRule.is_active == True)
    )
    active_rules = rules_result.scalar() or 0

    return {
        "total": total,
        "won": won,
        "lost": lost,
        "pending_result": pending,
        "win_rate": win_rate,
        "active": active,
        "exported": exported,
        "active_rules": active_rules,
    }


async def get_chapter_effectiveness(db: AsyncSession) -> List[Dict[str, Any]]:
    """Score chapters by how often they appear in won vs lost bids.

    Returns chapters grouped by title with won/lost/total counts.
    """
    # Get all chapters from won projects
    won_chapters_result = await db.execute(
        select(ProjectChapter)
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(BidProject.bid_result == "won")
    )
    won_chapters = won_chapters_result.scalars().all()

    # Get all chapters from lost projects
    lost_chapters_result = await db.execute(
        select(ProjectChapter)
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(BidProject.bid_result == "lost")
    )
    lost_chapters = lost_chapters_result.scalars().all()

    # Group by title
    title_stats: Dict[str, Dict[str, int]] = {}
    for ch in won_chapters:
        title = ch.title or "未命名"
        if title not in title_stats:
            title_stats[title] = {"won": 0, "lost": 0}
        title_stats[title]["won"] += 1

    for ch in lost_chapters:
        title = ch.title or "未命名"
        if title not in title_stats:
            title_stats[title] = {"won": 0, "lost": 0}
        title_stats[title]["lost"] += 1

    # Calculate effectiveness score
    result = []
    for title, counts in title_stats.items():
        total = counts["won"] + counts["lost"]
        score = round(counts["won"] / total * 100, 1) if total > 0 else 0
        result.append({
            "title": title,
            "won_count": counts["won"],
            "lost_count": counts["lost"],
            "total": total,
            "effectiveness_score": score,
        })

    result.sort(key=lambda x: x["effectiveness_score"], reverse=True)
    return result


async def get_recent_results(db: AsyncSession, limit: int = 10) -> List[Dict[str, Any]]:
    """Return recently decided projects with their results."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.bid_result.in_(["won", "lost"]))
        .order_by(BidProject.updated_at.desc())
        .limit(limit)
    )
    projects = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "bid_result": p.bid_result,
            "status": p.status,
            "updated_at": str(p.updated_at),
        }
        for p in projects
    ]


# ---------------------------------------------------------------------------
# AI-powered analysis
# ---------------------------------------------------------------------------


async def analyze_win_factors(
    db: AsyncSession,
) -> Dict[str, Any]:
    """Use AI to analyze patterns in won vs lost bids.

    Samples chapters from won and lost projects, sends to AI for
    comparative analysis, and returns success factors.
    """
    # Get chapters from won projects (sample up to 5)
    won_chapters_result = await db.execute(
        select(ProjectChapter)
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(BidProject.bid_result == "won")
        .limit(5)
    )
    won_chapters = won_chapters_result.scalars().all()

    # Get chapters from lost projects (sample up to 5)
    lost_chapters_result = await db.execute(
        select(ProjectChapter)
        .join(BidProject, ProjectChapter.project_id == BidProject.id)
        .where(BidProject.bid_result == "lost")
        .limit(5)
    )
    lost_chapters = lost_chapters_result.scalars().all()

    if not won_chapters and not lost_chapters:
        return {
            "analyzed": False,
            "message": "没有足够的中标/未中标数据进行分析。至少需要1个已标记结果的标书。",
        }

    # Build analysis prompt
    won_samples = "\n---\n".join(
        f"【中标标书】章节：{ch.title}\n{ch.ai_generated_content[:800]}"
        for ch in won_chapters
    ) or "（无中标样本）"

    lost_samples = "\n---\n".join(
        f"【未中标标书】章节：{ch.title}\n{ch.ai_generated_content[:800]}"
        for ch in lost_chapters
    ) or "（无未中标样本）"

    prompt = f"""请分析以下保安/物业服务类标书的章节内容，总结中标标书的成功要素和未中标标书的改进方向。

中标标书章节样本：
{won_samples}

未中标标书章节样本：
{lost_samples}

请以JSON格式返回分析结果：
{{
  "success_factors": [
    "成功要素1：...",
    "成功要素2：..."
  ],
  "improvement_areas": [
    "改进方向1：...",
    "改进方向2：..."
  ],
  "recommendations": [
    "具体建议1：...",
    "具体建议2：..."
  ],
  "summary": "整体分析总结（一句话）"
}}

注意：直接返回JSON，不要有其他文字。"""

    try:
        messages = [
            {"role": "system", "content": "你是投标策略分析专家，专注于保安/物业服务类标书的成功要素分析。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.5,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        analysis = json.loads(response)
        analysis["analyzed"] = True
        return analysis
    except Exception as exc:
        logger.warning("AI win-factor analysis failed: %s", exc)
        return {
            "analyzed": False,
            "error": str(exc),
            "success_factors": [],
            "improvement_areas": [],
            "recommendations": [],
            "summary": "分析暂时不可用",
        }


async def analyze_project_result(
    project_name: str,
    bid_result: str,
    requirements: dict,
    chapters: List[Dict[str, str]],
) -> Dict[str, Any]:
    """AI analyzes why a specific project won or lost.

    Args:
        project_name: Project name.
        bid_result: "won" or "lost".
        requirements: Parsed requirements dict.
        chapters: List of {title, content} dicts.
    """
    chapters_text = "\n---\n".join(
        f"章节：{ch['title']}\n{ch['content'][:600]}"
        for ch in chapters[:8]
    )

    result_label = "中标" if bid_result == "won" else "未中标"

    prompt = f"""请分析以下{result_label}标书的可能原因。

项目名称：{project_name}
投标结果：{result_label}

标书章节内容：
{chapters_text}

请以JSON格式返回：
{{
  "likely_reasons": ["原因1", "原因2", ...],
  "strengths": ["优势1", "优势2", ...],
  "weaknesses": ["不足1", "不足2", ...],
  "lessons_learned": "经验教训总结"
}}

直接返回JSON。"""

    try:
        messages = [
            {"role": "system", "content": "你是投标结果分析专家。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("Project result analysis failed: %s", exc)
        return {
            "likely_reasons": [],
            "strengths": [],
            "weaknesses": [],
            "lessons_learned": str(exc),
        }
