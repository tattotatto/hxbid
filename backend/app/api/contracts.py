"""宏曦标书 - Historical Contract API Routes.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.contract import Contract
from app.models.user import User
from app.schemas.contract import ContractCreate, ContractRead, ContractUpdate
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = Path(settings.UPLOAD_DIR)


# ── CRUD ────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[ContractRead])
async def list_contracts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Contract).order_by(Contract.created_at.desc())
    )
    return result.scalars().all()


@router.post("/", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
async def create_contract(
    data: ContractCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    contract = Contract(**data.model_dump())
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


@router.get("/{contract_id}", response_model=ContractRead)
async def get_contract(
    contract_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contract = await db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")
    return contract


@router.put("/{contract_id}", response_model=ContractRead)
async def update_contract(
    contract_id: str,
    data: ContractUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    contract = await db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(contract, field, value)
    await db.flush()
    await db.refresh(contract)
    return contract


@router.delete("/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contract(
    contract_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    contract = await db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")
    # Clean up uploaded images
    try:
        paths = json.loads(contract.image_paths_json or "[]")
        for p in paths:
            file_path = Path(p)
            if file_path.exists():
                file_path.unlink()
    except Exception:
        pass
    await db.delete(contract)
    await db.flush()


# ── File upload with PDF-to-image conversion ────────────────────────────


@router.post("/{contract_id}/upload")
async def upload_contract_files(
    contract_id: str,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Upload contract files (images or PDF). PDF pages are converted to images.

    Returns the list of saved image paths.
    """
    contract = await db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []

    for file in files:
        content = await file.read()
        ext = Path(file.filename).suffix.lower() if file.filename else ".bin"

        if ext == ".pdf":
            # Convert PDF pages to images using pypdfium2
            try:
                import pypdfium2 as pdfium
            except ImportError:
                # Fallback: save PDF as-is and note it
                saved_name = f"contract_{contract_id}_{uuid.uuid4().hex[:8]}.pdf"
                saved_path = UPLOAD_DIR / saved_name
                with open(saved_path, "wb") as f:
                    f.write(content)
                saved_paths.append(str(saved_path))
                continue

            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                pdf = pdfium.PdfDocument(tmp_path)
                n_pages = len(pdf)
                for i in range(n_pages):
                    page = pdf[i]
                    bitmap = page.render(scale=2)  # 2x for readability
                    pil_img = bitmap.to_pil()
                    saved_name = f"contract_{contract_id}_{uuid.uuid4().hex[:8]}_p{i+1}.png"
                    saved_path = UPLOAD_DIR / saved_name
                    pil_img.save(str(saved_path), "PNG")
                    saved_paths.append(str(saved_path))
                pdf.close()
            finally:
                Path(tmp_path).unlink(missing_ok=True)

        elif ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
            saved_name = f"contract_{contract_id}_{uuid.uuid4().hex[:8]}{ext}"
            saved_path = UPLOAD_DIR / saved_name
            with open(saved_path, "wb") as f:
                f.write(content)
            saved_paths.append(str(saved_path))

        else:
            logger.warning("Unsupported contract file type: %s", ext)

    # Merge with existing paths
    existing = json.loads(contract.image_paths_json or "[]")
    existing.extend(saved_paths)
    contract.image_paths_json = json.dumps(existing, ensure_ascii=False)
    await db.flush()

    return {"paths": saved_paths, "total": len(existing)}


@router.delete("/{contract_id}/images/{index}")
async def delete_contract_image(
    contract_id: str,
    index: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Delete a single image from a contract by its index."""
    contract = await db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")

    paths = json.loads(contract.image_paths_json or "[]")
    if index < 0 or index >= len(paths):
        raise HTTPException(status_code=400, detail="无效的图片索引")

    removed = paths.pop(index)
    try:
        Path(removed).unlink(missing_ok=True)
    except Exception:
        pass
    contract.image_paths_json = json.dumps(paths, ensure_ascii=False)
    await db.flush()
    return {"message": "已删除", "remaining": len(paths)}
