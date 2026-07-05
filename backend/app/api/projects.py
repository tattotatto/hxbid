"""宏曦标书 - Project CRUD API Routes.

Provides full CRUD for bid projects with nested chapters.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.schemas.project import (
    ChapterUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

router = APIRouter()


async def _get_project_with_chapters(
    project_id: str, db: AsyncSession
) -> BidProject | None:
    """Fetch a project with its chapters eagerly loaded."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    return result.scalar_one_or_none()


@router.get("/", response_model=list[ProjectRead])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    include_archived: bool = False,
):
    """List projects with their chapters, ordered by creation time descending.

    By default, archived projects (historical bids uploaded for AI learning)
    are excluded. Set ``include_archived=true`` to include them.
    """
    stmt = (
        select(BidProject)
        .options(selectinload(BidProject.chapters))
        .order_by(BidProject.created_at.desc())
    )
    if not include_archived:
        stmt = stmt.where(BidProject.status != "archived")

    result = await db.execute(stmt)
    return result.scalars().all()


@router.post(
    "/",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new bid project."""
    project = BidProject(
        name=data.name,
        bid_deadline=data.bid_deadline,
        created_by=current_user.id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project, ["chapters"])
    return project


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single project with its chapters."""
    project = await _get_project_with_chapters(project_id, db)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


@router.put("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: str,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update project fields. Only supplied fields are changed."""
    project = await _get_project_with_chapters(project_id, db)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)
    await db.flush()
    await db.refresh(project, ["chapters"])
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a project. Cascade removes associated chapters."""
    project = await _get_project_with_chapters(project_id, db)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    await db.delete(project)


@router.put("/{project_id}/chapters/{chapter_id}", response_model=dict)
async def update_chapter(
    project_id: str,
    chapter_id: str,
    data: ChapterUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a single chapter's final_content and/or status.

    Used by the frontend editor to save chapter edits.
    """
    # Verify the project exists
    project = await db.get(BidProject, project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Fetch the chapter belonging to this project
    result = await db.execute(
        select(ProjectChapter).where(
            ProjectChapter.id == chapter_id,
            ProjectChapter.project_id == project_id,
        )
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chapter not found",
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(chapter, field, value)
    await db.flush()
    await db.refresh(chapter)
    return {"id": chapter.id, "status": chapter.status}
