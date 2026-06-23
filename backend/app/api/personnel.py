"""宏曦标书 - Personnel CRUD API Routes.

Provides full CRUD for personnel records with nested experiences and certificates.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.personnel import Personnel, PersonnelCertificate, PersonnelExperience
from app.models.user import User
from app.schemas.personnel import (
    PersonnelCreate,
    PersonnelRead,
    PersonnelUpdate,
)
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

router = APIRouter()


async def _get_with_relations(p_id: str, db: AsyncSession) -> Personnel | None:
    """Fetch a personnel record with its experiences and certificates eagerly loaded."""
    result = await db.execute(
        select(Personnel)
        .where(Personnel.id == p_id)
        .options(
            selectinload(Personnel.experiences),
            selectinload(Personnel.certificates),
        )
    )
    return result.scalar_one_or_none()


@router.get("/", response_model=list[PersonnelRead])
async def list_personnel(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all personnel with their experiences and certificates."""
    result = await db.execute(
        select(Personnel)
        .options(
            selectinload(Personnel.experiences),
            selectinload(Personnel.certificates),
        )
        .order_by(Personnel.created_at.desc())
    )
    return result.scalars().all()


@router.post(
    "/",
    response_model=PersonnelRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_personnel(
    data: PersonnelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Create a personnel record with nested experiences and certificates."""
    # Build the main personnel record (without nested collections)
    personnel = Personnel(
        name=data.name,
        id_card=data.id_card,
        education=data.education,
        phone=data.phone,
        tags=data.tags,
    )
    db.add(personnel)
    await db.flush()  # flush to get the personnel.id for FK references

    # Create nested experience entries
    for exp_data in data.experiences:
        exp = PersonnelExperience(
            personnel=personnel,
            **exp_data.model_dump(),
        )
        db.add(exp)

    # Create nested certificate entries
    for cert_data in data.certificates:
        cert = PersonnelCertificate(
            personnel=personnel,
            **cert_data.model_dump(),
        )
        db.add(cert)

    await db.flush()
    await db.refresh(personnel, ["experiences", "certificates"])
    return personnel


@router.get("/{p_id}", response_model=PersonnelRead)
async def get_personnel(
    p_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single personnel record with experiences and certificates."""
    personnel = await _get_with_relations(p_id, db)
    if not personnel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )
    return personnel


@router.put("/{p_id}", response_model=PersonnelRead)
async def update_personnel(
    p_id: str,
    data: PersonnelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Update basic fields of a personnel record. Only supplied fields are changed."""
    personnel = await _get_with_relations(p_id, db)
    if not personnel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(personnel, field, value)
    await db.flush()
    await db.refresh(personnel, ["experiences", "certificates"])
    return personnel


@router.delete("/{p_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personnel(
    p_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Delete a personnel record. Cascade removes nested experiences and certificates."""
    personnel = await _get_with_relations(p_id, db)
    if not personnel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personnel not found",
        )
    await db.delete(personnel)
