"""宏曦标书 - Bid Analytics API Routes.

Endpoints for bid result analysis, win/loss statistics, and AI-powered
success factor analysis.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.services.bid_analytics import (
    analyze_project_result,
    analyze_win_factors,
    get_chapter_effectiveness,
    get_project_stats,
    get_recent_results,
)
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /analytics/stats  — Aggregate statistics
# ---------------------------------------------------------------------------


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return aggregate bid statistics: win rate, counts, active projects, etc."""
    return await get_project_stats(db)


# ---------------------------------------------------------------------------
# GET /analytics/chapter-effectiveness  — Chapter scores
# ---------------------------------------------------------------------------


@router.get("/chapter-effectiveness")
async def chapter_effectiveness(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return chapters ranked by effectiveness in winning bids."""
    return await get_chapter_effectiveness(db)


# ---------------------------------------------------------------------------
# GET /analytics/win-factors  — AI win-factor analysis
# ---------------------------------------------------------------------------


@router.get("/win-factors")
async def win_factors(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI analysis of success factors in won vs lost bids."""
    return await analyze_win_factors(db)


# ---------------------------------------------------------------------------
# GET /analytics/recent  — Recent bid results
# ---------------------------------------------------------------------------


@router.get("/recent")
async def recent_results(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return recently decided projects."""
    return await get_recent_results(db)


# ---------------------------------------------------------------------------
# POST /analytics/analyze-project  — Analyze specific project result
# ---------------------------------------------------------------------------


@router.post("/analyze-project")
async def analyze_project(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """AI analysis of why a specific project won or lost.

    Request: {"project_id": "..."}
    """
    project_id = data.get("project_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project_id required")

    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if project.bid_result not in ("won", "lost"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project must be marked as won or lost first",
        )

    requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}
    chapters = [
        {
            "title": ch.title,
            "content": ch.final_content or ch.ai_generated_content,
        }
        for ch in project.chapters
    ]

    analysis = await analyze_project_result(
        project_name=project.name,
        bid_result=project.bid_result,
        requirements=requirements,
        chapters=chapters,
    )

    return {
        "project_id": project_id,
        "project_name": project.name,
        "bid_result": project.bid_result,
        **analysis,
    }
