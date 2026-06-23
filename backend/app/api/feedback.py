"""宏曦标书 - Feedback Loop API Routes.

Endpoints for closing the learning loop: bid result marking, rule management,
and triggering the full feedback pipeline.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.edit_rule import EditRule
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.services.edit_analyzer import analyze_chapter_edits, edit_analysis_to_dict
from app.services.feedback_loop import (
    accumulate_rules,
    get_active_prompt_constraints,
    get_all_rules,
    run_feedback_loop,
    set_bid_result,
)
from app.utils.permissions import require_admin, require_editor
from app.utils.security import get_current_user

router = APIRouter()


# ---------------------------------------------------------------------------
# PUT /projects/{project_id}/result  — Mark bid result
# ---------------------------------------------------------------------------


@router.put("/projects/{project_id}/result")
async def mark_bid_result(
    project_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Mark a project as won or lost and update vector weights accordingly.

    Request: {"result": "won" | "lost"}
    """
    result = data.get("result")
    if result not in ("won", "lost", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Result must be 'won', 'lost', or 'pending'",
        )

    project = await set_bid_result(project_id, result, db)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    await db.commit()
    return {
        "project_id": project_id,
        "bid_result": result,
        "status": project.status,
        "vector_updated": result == "won",
    }


# ---------------------------------------------------------------------------
# POST /feedback/run  — Run full feedback loop on a project
# ---------------------------------------------------------------------------


@router.post("/run")
async def run_feedback(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Run the complete feedback loop for a finalized bid project.

    Request: {"project_id": "...", "bid_result": "won" | "lost" | null}

    This will:
    1. Run edit intent analysis comparing AI vs final content
    2. Accumulate extracted rules (new + increment existing)
    3. Upgrade rules that reached threshold (≥3 occurrences)
    4. Update bid result and adjust vector weights if won
    """
    project_id = data.get("project_id")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project_id is required")

    bid_result = data.get("bid_result")

    # Load chapters with both AI and edited content
    result = await db.execute(
        select(ProjectChapter)
        .where(
            ProjectChapter.project_id == project_id,
            ProjectChapter.ai_generated_content != "",
            ProjectChapter.final_content != "",
        )
        .order_by(ProjectChapter.order_index)
    )
    chapters = result.scalars().all()

    if not chapters:
        return {
            "message": "No edited chapters found for analysis",
            "project_id": project_id,
        }

    # Run edit analysis on all edited chapters
    edit_analysis_results = []
    for ch in chapters:
        try:
            analysis = await analyze_chapter_edits(
                chapter_id=ch.id,
                chapter_title=ch.title,
                ai_generated_content=ch.ai_generated_content,
                final_content=ch.final_content,
                use_ai=data.get("use_ai", True),
            )
            edit_analysis_results.append(edit_analysis_to_dict(analysis))
        except Exception:
            pass

    # Run feedback loop
    summary = await run_feedback_loop(
        project_id=project_id,
        edit_analysis_results=edit_analysis_results,
        bid_result=bid_result,
        db=db,
    )

    return {
        **summary,
        "chapters_analyzed": len(edit_analysis_results),
    }


# ---------------------------------------------------------------------------
# GET /feedback/rules  — List all rules
# ---------------------------------------------------------------------------


@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all accumulated writing rules (pending + active)."""
    return await get_all_rules(db)


# ---------------------------------------------------------------------------
# GET /feedback/constraints  — Get active prompt constraints
# ---------------------------------------------------------------------------


@router.get("/constraints")
async def get_constraints(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the list of active prompt constraints to inject into AI prompts.

    These are rules that have been observed ≥3 times across projects.
    """
    constraints = await get_active_prompt_constraints(db)
    return {
        "count": len(constraints),
        "constraints": constraints,
    }


# ---------------------------------------------------------------------------
# DELETE /feedback/rules/{rule_id}  — Reset a rule
# ---------------------------------------------------------------------------


@router.delete("/rules/{rule_id}")
async def reset_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Reset or delete an accumulated rule (admin only)."""
    rule = await db.get(EditRule, rule_id)
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    await db.delete(rule)
    await db.commit()
    return {"message": "Rule deleted"}
