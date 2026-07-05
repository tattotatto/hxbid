"""宏曦标书 - Personnel CRUD API Routes.

Provides full CRUD for personnel records with file uploads, OCR, and
nested experiences/certificates.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.personnel import Personnel, PersonnelCertificate, PersonnelExperience
from app.models.user import User
from app.schemas.personnel import (
    PersonnelCreate,
    PersonnelRead,
    PersonnelUpdate,
)
from app.services.ocr_service import analyze_document_image
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

router = APIRouter()
UPLOAD_DIR = Path(settings.UPLOAD_DIR)


def _save_upload(upload: UploadFile, subdir: str = "personnel") -> str:
    sub_path = UPLOAD_DIR / subdir
    sub_path.mkdir(parents=True, exist_ok=True)
    ext = Path(upload.filename).suffix if upload.filename else ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = sub_path / filename
    with open(filepath, "wb") as f:
        f.write(upload.file.read())
    return str(filepath.relative_to(Path.cwd()))


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
        gender=data.gender,
        id_card=data.id_card,
        education=data.education,
        phone=data.phone,
        address=data.address,
        id_valid_from=data.id_valid_from,
        id_valid_to=data.id_valid_to,
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


# ── Image upload & OCR ─────────────────────────────────────────────────


@router.post("/{p_id}/upload-id-front")
async def upload_id_front(
    p_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload ID card front image and auto-OCR to fill fields."""
    personnel = await db.get(Personnel, p_id)
    if not personnel:
        raise HTTPException(status_code=404, detail="人员不存在")
    path = _save_upload(file, "personnel")
    personnel.id_front_image = path

    # Attempt OCR
    try:
        content = await file.read() if hasattr(file, 'read') else open(path, 'rb').read()
    except Exception:
        content = open(path, 'rb').read()
    if not content:
        try:
            with open(path, 'rb') as f:
                content = f.read()
        except Exception:
            content = b''

    if content:
        try:
            result = await analyze_document_image(content, file.filename or "id_front.jpg", "id_card")
            if not personnel.name and result.get("name"):
                personnel.name = result["name"]
            if not personnel.gender and result.get("gender"):
                personnel.gender = result["gender"]
            if not personnel.id_card and result.get("id_number"):
                personnel.id_card = result["id_number"]
            if not personnel.address and result.get("address"):
                personnel.address = result["address"]
            if not personnel.id_valid_from and result.get("id_valid_from"):
                personnel.id_valid_from = result["id_valid_from"]
            if not personnel.id_valid_to and result.get("id_valid_to"):
                personnel.id_valid_to = result["id_valid_to"]
        except Exception:
            pass  # OCR is best-effort

    await db.flush()
    return {
        "path": path,
        "name": personnel.name,
        "gender": personnel.gender,
        "id_card": personnel.id_card,
        "address": personnel.address,
    }


@router.post("/{p_id}/upload-id-back")
async def upload_id_back(
    p_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload ID card back image."""
    personnel = await db.get(Personnel, p_id)
    if not personnel:
        raise HTTPException(status_code=404, detail="人员不存在")
    path = _save_upload(file, "personnel")
    personnel.id_back_image = path
    await db.flush()
    return {"path": path}


@router.post("/{p_id}/upload-health-report")
async def upload_health_report(
    p_id: str,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload one or more health report images."""
    personnel = await db.get(Personnel, p_id)
    if not personnel:
        raise HTTPException(status_code=404, detail="人员不存在")
    paths = []
    for file in files:
        path = _save_upload(file, "personnel")
        paths.append(str(path))
    existing = json.loads(personnel.health_report_images_json or "[]")
    existing.extend(paths)
    personnel.health_report_images_json = json.dumps(existing, ensure_ascii=False)
    await db.flush()
    return {"paths": paths, "total": len(existing)}


@router.post("/{p_id}/upload-certificate")
async def upload_certificate(
    p_id: str,
    file: UploadFile = File(...),
    cert_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload a certificate file for a personnel and create a PersonnelCertificate record."""
    personnel = await db.get(Personnel, p_id)
    if not personnel:
        raise HTTPException(status_code=404, detail="人员不存在")
    path = _save_upload(file, "personnel")
    cert = PersonnelCertificate(
        personnel_id=p_id,
        cert_name=cert_name or file.filename or "证书",
        cert_number="",
        issuing_authority="",
        attachment_path=str(path),
    )
    db.add(cert)
    await db.flush()
    await db.refresh(cert)
    return {"id": cert.id, "cert_name": cert.cert_name, "attachment_path": cert.attachment_path}


@router.post("/ocr-id")
async def ocr_id_card(
    file: UploadFile = File(...),
    current_user: User = Depends(require_editor),
):
    """OCR an ID card image and return extracted fields (name, gender, id_number, address)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    content = await file.read()
    result = await analyze_document_image(content, file.filename, "id_card")
    return result
