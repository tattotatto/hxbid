"""宏曦标书 - Information Collection API Routes.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.schemas.collection import (
    AssignPersonnelRequest,
    CollectionStatus,
    LinkContractRequest,
    LinkQualificationRequest,
    UnlinkResourceRequest,
)
from app.services.chapter_attachments import set_chapter_attachments
from app.services.collection import (
    analyze_collection_needs,
    assign_personnel,
    confirm_collection,
    get_collected_resources,
    link_contract,
    link_qualification,
    unassign_personnel,
    unlink_contract,
    unlink_qualification,
    upload_requirement_document,
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


# ── POST /{project_id}/qualification/unlink ─────────────────────────────


@router.post("/{project_id}/qualification/unlink")
async def unlink_qualification_from_project(
    project_id: str,
    data: UnlinkResourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """解除某需求下指定资质的链接."""
    deleted = await unlink_qualification(
        project_id, data.requirement_name, data.resource_id, db
    )
    return {"deleted": deleted}


# ── POST /{project_id}/contract/unlink ──────────────────────────────────


@router.post("/{project_id}/contract/unlink")
async def unlink_contract_from_project(
    project_id: str,
    data: UnlinkResourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """解除某业绩要求下指定合同的链接."""
    deleted = await unlink_contract(
        project_id, data.requirement_name, data.resource_id, db
    )
    return {"deleted": deleted}


# ── POST /{project_id}/qualification/upload ─────────────────────────────


@router.post("/{project_id}/qualification/upload")
async def upload_qualification_for_project(
    project_id: str,
    file: UploadFile,
    requirement_name: str = Form(""),
    category: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """上传一份需求材料，并同步建进资源库（业绩类进历史合同，其余进公司资质）.

    路径里的 ``qualification`` 现在名不副实——它会按需求名分流，业绩类需求落的是
    ``Contract``。保留这个路径是为了部署瞬间旧前端标签页不至于 404，不值得为改名
    去冒这个险。带 ``category`` 是为了跟 ``_is_performance_requirement`` 同口径。

    ``requirement_name`` / ``category`` **必须标 Form()**：不标的话 FastAPI 按 query
    参数解析，前端塞在 formData 里的值收不到，会静默退化成 ``file.filename``——
    于是需求名变成「证明.png」，按需求名匹配的行永远回显不出这份上传，
    分流也会因为文件名里没有业绩关键词而走错分支。2026-09-22 实测确认过。
    """
    # Save file
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix if file.filename else ".bin"
    saved_name = f"collection_{uuid.uuid4().hex[:8]}{ext}"
    saved_path = upload_dir / saved_name

    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    result = await upload_requirement_document(
        project_id,
        requirement_name or file.filename or "未命名证件",
        category,
        str(saved_path),
        db,
    )
    # 响应形状保持与改造前一致（id/requirement_name/status），旧前端不破
    return {
        "id": result["link_id"],
        "requirement_name": result["requirement_name"],
        "status": result["status"],
        "kind": result["kind"],
        "library_id": result["library_id"],
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


# ── POST /{project_id}/contract/link ────────────────────────────────────


@router.post("/{project_id}/contract/link")
async def link_contract_to_project(
    project_id: str,
    data: LinkContractRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Link a historical contract to fulfil a performance-contract requirement."""
    pc = await link_contract(
        project_id, data.contract_id, data.requirement_name, db
    )
    return {"id": pc.id, "requirement_name": pc.requirement_name, "status": pc.match_status}


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


# ── PUT /{project_id}/chapters/{chapter_id}/attachments ─────────────────
#
# 附件清单的**第二个编辑入口**（另一个是目录确认页的抽屉）。用户 2026-09-27
# 需求：目录里设成「附件」的章节，到信息搜集这一步要在「资质与证件」里让用户
# 把材料补齐，包含上传和从资源库选。
#
# 两处写的是同一份数据 —— `ProjectChapter.chapter_meta_json["attachments"]`
# （生成期只认它），清洗口径也同一套（`prune_attachments`）。
#
# 上传文件走现成的 `POST /bid/{pid}/attachments/upload`（该端点的状态门禁已放宽到
# 覆盖 collecting），这里只负责把前端选好的清单整份落库。


class SetChapterAttachmentsRequest(BaseModel):
    attachments: list[dict] = Field(default_factory=list)


@router.put("/{project_id}/chapters/{chapter_id}/attachments")
async def set_chapter_attachments_for_project(
    project_id: str,
    chapter_id: str,
    payload: SetChapterAttachmentsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """替换某个附件章节的材料清单，返回落库后的清单与被剔除的项.

    剔除是静默发生的（资源库行已删 / 路径越界 / 跨章节内容重复），所以必须
    把 ``pruned_attachments`` 透给前端 —— 不说一声用户会以为挂上了。
    """
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status == "generating":
        raise HTTPException(status_code=409, detail="标书正在生成中，暂时不能改附件")

    chapter = await db.get(ProjectChapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if chapter.chapter_type != "attachment":
        raise HTTPException(
            status_code=400,
            detail="只有「附件」类型的章节才需要挂材料",
        )

    kept, pruned = await set_chapter_attachments(chapter, payload.attachments, db)
    await db.commit()
    return {"attachments": kept, "pruned_attachments": pruned}
