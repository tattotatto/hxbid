"""宏曦标书 - Qualification CRUD API Routes.

Provides full CRUD for qualification / certificate records.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.qualification import Qualification
from app.models.user import User
from app.schemas.qualification import (
    QualificationCreate,
    QualificationRead,
    QualificationUpdate,
)
from app.utils.security import get_current_user

router = APIRouter()


@router.get("/", response_model=list[QualificationRead])
async def list_qualifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all qualifications, ordered by created_at descending."""
    result = await db.execute(
        select(Qualification).order_by(Qualification.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/",
    response_model=QualificationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_qualification(
    data: QualificationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new qualification record."""
    qual = Qualification(**data.model_dump())
    db.add(qual)
    await db.flush()
    await db.refresh(qual)
    return qual


@router.get("/{qual_id}", response_model=QualificationRead)
async def get_qualification(
    qual_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single qualification by ID."""
    result = await db.execute(
        select(Qualification).where(Qualification.id == qual_id)
    )
    qual = result.scalar_one_or_none()
    if not qual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Qualification not found",
        )
    return qual


@router.put("/{qual_id}", response_model=QualificationRead)
async def update_qualification(
    qual_id: str,
    data: QualificationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a qualification record. Only supplied fields are changed."""
    result = await db.execute(
        select(Qualification).where(Qualification.id == qual_id)
    )
    qual = result.scalar_one_or_none()
    if not qual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Qualification not found",
        )
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(qual, field, value)
    await db.flush()
    await db.refresh(qual)
    return qual


@router.delete("/{qual_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_qualification(
    qual_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a qualification record."""
    result = await db.execute(
        select(Qualification).where(Qualification.id == qual_id)
    )
    qual = result.scalar_one_or_none()
    if not qual:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Qualification not found",
        )
    await db.delete(qual)
