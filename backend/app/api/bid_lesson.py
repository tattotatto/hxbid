"""宏曦标书 - 历史标书配对学习 API."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.bid_lesson import BidLesson
from app.models.project import BidProject
from app.services.bid_learning import run_analysis
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

_RELEVANT_STATUSES = ("exported", "won", "lost")


def _save_upload(upload_file: UploadFile) -> str:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(upload_file.filename).suffix if upload_file.filename else ".tmp"
    saved = upload_dir / f"{uuid.uuid4().hex}{ext}"
    with open(saved, "wb") as f:
        f.write(upload_file.file.read())
    return str(saved.absolute())


@router.post("/upload")
async def upload_pair(
    tender_file: UploadFile = File(...),
    bid_file: UploadFile = File(...),
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    """上传「招标文件 + 标书」配对,自动启动分析."""
    if not tender_file.filename or not bid_file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须同时上传招标文件和标书")
    tender_path = _save_upload(tender_file)
    bid_path = _save_upload(bid_file)
    pair = BidLesson(
        name=name or Path(tender_file.filename).stem,
        source_type="upload",
        tender_path=tender_path,
        bid_path=bid_path,
        status="pending",
        created_by=current_user.id,
    )
    db.add(pair)
    await db.flush()
    await db.commit()
    asyncio.create_task(run_analysis(pair.id))
    return {"id": pair.id, "name": pair.name, "status": pair.status}


@router.get("")
async def list_lessons(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """列出历史标书配对(含系统完成项目,懒创建配对并自动分析)."""
    lessons = (await db.execute(select(BidLesson).order_by(BidLesson.created_at.desc()))).scalars().all()

    # 系统完成项目 → 懒创建配对
    projects = (await db.execute(
        select(BidProject).where(BidProject.status.in_(_RELEVANT_STATUSES))
    )).scalars().all()
    existing = {l.project_id for l in lessons if l.source_type == "project" and l.project_id}
    created = []
    for p in projects:
        if p.id in existing:
            continue
        if not p.original_file_path:
            continue  # 没有招标文件的项目不纳入
        pair = BidLesson(
            name=p.name,
            source_type="project",
            project_id=p.id,
            tender_path=p.original_file_path,
            status="pending",
        )
        db.add(pair)
        created.append(pair)
    if created:
        await db.flush()
        await db.commit()
        for pair in created:
            asyncio.create_task(run_analysis(pair.id))
        lessons = lessons + created

    items = [
        {
            "id": l.id,
            "name": l.name,
            "source_type": l.source_type,
            "status": l.status,
            "error": l.error,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in lessons
    ]
    return {"items": items, "system_lessons": len(created)}


@router.get("/{id}")
async def get_lesson(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    pair = await db.get(BidLesson, id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    try:
        lesson = json.loads(pair.lesson_json)
    except json.JSONDecodeError:
        lesson = {}
    return {
        "id": pair.id,
        "name": pair.name,
        "source_type": pair.source_type,
        "status": pair.status,
        "error": pair.error,
        "lesson": lesson,
        "created_at": pair.created_at.isoformat() if pair.created_at else None,
    }


@router.post("/{id}/relearn")
async def relearn_pair(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    pair = await db.get(BidLesson, id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    pair.status = "pending"
    pair.error = ""
    pair.lesson_json = "{}"
    await db.commit()
    asyncio.create_task(run_analysis(pair.id))
    return {"id": pair.id, "status": "pending"}


@router.delete("/{id}")
async def delete_pair(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    pair = await db.get(BidLesson, id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    from app.services.vector_store import vector_store
    vector_store.delete_project(id)
    await db.delete(pair)
    await db.commit()
    return {"ok": True}
