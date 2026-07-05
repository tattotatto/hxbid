"""宏曦标书 - Information Collection API Routes.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.collection import (
    AssignPersonnelRequest,
    CollectionStatus,
    LinkQualificationRequest,
)
from app.services.collection import (
    analyze_collection_needs,
    assign_personnel,
    confirm_collection,
    get_collected_resources,
    link_qualification,
    unassign_personnel,
    upload_qualification,
)
from app.utils.permissions import require_editor

router = APIRouter()


# ── GET /{project_id}/status ────────────────────────────────────────────


@router.get("/{project_id}/status")
async def get_collection_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Get the information-collection checklist for a project.

    Analyses the parsed tender requirements and auto-matches them
    against the resource library (qualifications, personnel, company).
    """
    try:
        status_data = await analyze_collection_needs(project_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return status_data


# ── POST /{project_id}/personnel/assign ─────────────────────────────────


@router.post("/{project_id}/personnel/assign")
async def assign_personnel_to_project(
    project_id: str,
    data: AssignPersonnelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Assign a personnel record to a project role."""
    try:
        pp = await assign_personnel(
            project_id,
            data.personnel_id,
            data.role,
            data.requirement_desc,
            db,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": pp.id, "role": pp.role, "status": pp.match_status}


# ── POST /{project_id}/personnel/unassign ───────────────────────────────


@router.post("/{project_id}/personnel/unassign")
async def unassign_personnel_from_project(
    project_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Remove a personnel assignment from a project."""
    pp_id = data.get("assignment_id", "")
    await unassign_personnel(project_id, pp_id, db)
    return {"message": "unassigned"}


# ── POST /{project_id}/qualification/upload ─────────────────────────────


@router.post("/{project_id}/qualification/upload")
async def upload_qualification_for_project(
    project_id: str,
    file: UploadFile,
    requirement_name: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload a file to fulfil a missing qualification requirement."""
    # Save file
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix if file.filename else ".bin"
    saved_name = f"collection_{uuid.uuid4().hex[:8]}{ext}"
    saved_path = upload_dir / saved_name

    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    pq = await upload_qualification(
        project_id,
        requirement_name or file.filename or "未命名证件",
        str(saved_path),
        db,
    )
    return {
        "id": pq.id,
        "requirement_name": pq.requirement_name,
        "status": pq.match_status,
    }


# ── POST /{project_id}/qualification/link ───────────────────────────────


@router.post("/{project_id}/qualification/link")
async def link_qualification_to_project(
    project_id: str,
    data: LinkQualificationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Link an existing qualification from the library to this project."""
    pq = await link_qualification(
        project_id, data.qualification_id, data.requirement_name, db
    )
    return {"id": pq.id, "requirement_name": pq.requirement_name, "status": pq.match_status}


# ── POST /{project_id}/confirm ──────────────────────────────────────────


@router.post("/{project_id}/confirm")
async def confirm_collection_step(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Confirm the information-collection step is complete.

    Advances the project status from 'collecting' to 'parsed',
    ready for AI generation.
    """
    try:
        project = await confirm_collection(project_id, db)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"project_id": project.id, "status": project.status}


# ── GET /{project_id}/resources ─────────────────────────────────────────


@router.get("/{project_id}/resources")
async def list_collected_resources(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Return all resources collected for this project.

    Used by the generation step to inject collected qualifications
    and personnel directly into the AI prompt.
    """
    return await get_collected_resources(project_id, db)
