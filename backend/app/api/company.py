"""宏曦标书 - Company Profile API Routes.

Single-row CRUD for company basic info with image upload support.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.company_profile import CompanyProfile
from app.models.user import User
from app.schemas.company import CompanyRead, CompanyUpdate
from app.services.ocr_service import analyze_document_image
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

router = APIRouter()

UPLOAD_DIR = Path(settings.UPLOAD_DIR)


def _save_upload(upload: UploadFile, subdir: str = "company") -> str:
    """Save an uploaded file and return the relative path."""
    sub_path = UPLOAD_DIR / subdir
    sub_path.mkdir(parents=True, exist_ok=True)
    ext = Path(upload.filename).suffix if upload.filename else ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = sub_path / filename
    with open(filepath, "wb") as f:
        f.write(upload.file.read())
    return str(filepath.relative_to(Path.cwd()))


async def _get_or_create_profile(db: AsyncSession) -> CompanyProfile:
    """Get the existing company profile or create a default one."""
    result = await db.execute(select(CompanyProfile).limit(1))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = CompanyProfile(company_name="云南宏曦科技有限公司")
        db.add(profile)
        await db.flush()
        await db.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# GET /company  — Get company profile
# ---------------------------------------------------------------------------


@router.get("/", response_model=CompanyRead)
async def get_company(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the company profile (auto-creates a default if none exists)."""
    profile = await _get_or_create_profile(db)
    return profile


# ---------------------------------------------------------------------------
# PUT /company  — Update company profile fields + images
# ---------------------------------------------------------------------------


@router.put("/", response_model=CompanyRead)
async def update_company(
    company_name: str = Form(""),
    business_license_number: str = Form(""),
    legal_rep_name: str = Form(""),
    legal_rep_id_number: str = Form(""),
    address: str = Form(""),
    contact_phone: str = Form(""),
    notes: str = Form(""),
    business_license_image: UploadFile | None = File(None),
    legal_rep_id_front_image: UploadFile | None = File(None),
    legal_rep_id_back_image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Update company profile. Uses multipart/form-data for image uploads.

    All form fields are optional — only provided values are updated.
    """
    profile = await _get_or_create_profile(db)

    # Text fields
    field_map = {
        "company_name": company_name,
        "business_license_number": business_license_number,
        "legal_rep_name": legal_rep_name,
        "legal_rep_id_number": legal_rep_id_number,
        "address": address,
        "contact_phone": contact_phone,
        "notes": notes,
    }
    for field, value in field_map.items():
        if value:
            setattr(profile, field, value)

    # Image uploads
    if business_license_image and business_license_image.filename:
        profile.business_license_image = _save_upload(business_license_image)
    if legal_rep_id_front_image and legal_rep_id_front_image.filename:
        profile.legal_rep_id_front_image = _save_upload(legal_rep_id_front_image)
    if legal_rep_id_back_image and legal_rep_id_back_image.filename:
        profile.legal_rep_id_back_image = _save_upload(legal_rep_id_back_image)

    await db.flush()
    await db.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# POST /company/ocr-existing  — OCR an already-uploaded image by path
# ---------------------------------------------------------------------------


@router.post("/ocr-existing")
async def ocr_existing_image(
    data: dict,
    current_user: User = Depends(require_editor),
):
    """OCR an already-uploaded company document image.

    Request: {"image_path": "uploads/company/xxx.jpg", "doc_type": "business_license"}
    """
    image_path = data.get("image_path", "")
    doc_type = data.get("doc_type", "business_license")

    if not image_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_path is required")

    full_path = Path(image_path)
    if not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image not found: {image_path}",
        )

    with open(full_path, "rb") as f:
        file_bytes = f.read()

    result = await analyze_document_image(
        file_bytes=file_bytes,
        filename=full_path.name,
        doc_type=doc_type,
    )
    return result


# ---------------------------------------------------------------------------
# POST /company/ocr  — OCR analyze uploaded file directly
# ---------------------------------------------------------------------------


@router.post("/ocr")
async def ocr_company_document(
    file: UploadFile = File(...),
    doc_type: str = "business_license",
    current_user: User = Depends(require_editor),
):
    """Upload a business license or ID card image and get OCR-extracted fields.

    Args:
        file: Image file.
        doc_type: "business_license" or "id_card".
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No file provided")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 10MB)")

    if doc_type not in ("business_license", "id_card"):
        doc_type = "business_license"

    result = await analyze_document_image(
        file_bytes=content,
        filename=file.filename,
        doc_type=doc_type,
    )
    return result
