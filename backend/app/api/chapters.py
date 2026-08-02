"""宏曦标书 - 章节提取、审核、对话编辑 API.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ExtractChaptersResponse(BaseModel):
    chapters: list = []
    health: dict = {}
    source_pages: list = []
    error: str = ""


class ChapterChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = None


class ChapterChatResponse(BaseModel):
    reply: str = ""
    chapters: list = []
    conversation_id: str = ""


class LockChaptersResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# POST /{project_id}/extract-chapters
# ---------------------------------------------------------------------------

@router.post("/{project_id}/extract-chapters", response_model=ExtractChaptersResponse)
async def extract_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从招标文件 PDF 提取并解析投标文件章节结构.

    流程：
    1. 加载项目
    2. 从 PDF 定位第六章并提取文本
    3. 编码健康检查
    4. AI 解析为结构化章节列表
    5. 保存到 chapter_structure_json
    6. 返回章节列表给前端
    """
    # Load project
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.original_file_path:
        raise HTTPException(status_code=400, detail="未上传招标文件，请先上传")

    # Extract chapters from PDF
    try:
        from app.services.chapter_extractor import extract_chapters_from_pdf
        from app.services.ai_adapter import ai_adapter as ai

        extraction_result = await extract_chapters_from_pdf(
            pdf_path=project.original_file_path,
            ai_adapter=ai,
        )

        chapters = extraction_result["chapters"]
        health = extraction_result["health"]
        source_pages = extraction_result["source_pages"]

        # Save to project
        project.chapter_structure_json = json.dumps(chapters, ensure_ascii=False)
        await db.commit()

        logger.info(
            "Extracted %d chapters for project %s (health: cjk_ratio=%.2f)",
            len(chapters), project_id, health.get("cjk_ratio", 0),
        )

        return ExtractChaptersResponse(
            chapters=chapters,
            health=health,
            source_pages=source_pages,
        )

    except ValueError as exc:
        # Encoding health failure — explicit rejection
        logger.warning("Chapter extraction rejected for project %s: %s", project_id, exc)
        return ExtractChaptersResponse(
            chapters=[],
            health={"healthy": False, "message": str(exc)},
            source_pages=[],
            error=str(exc),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="招标文件不存在，请重新上传")
    except Exception as exc:
        logger.exception("Chapter extraction failed for project %s", project_id)
        raise HTTPException(status_code=500, detail=f"章节提取失败: {exc}")


# ---------------------------------------------------------------------------
# POST /{project_id}/chapters/chat
# ---------------------------------------------------------------------------

@router.post("/{project_id}/chapters/chat", response_model=ChapterChatResponse)
async def chat_edit_chapters(
    project_id: str,
    data: ChapterChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对话式编辑章节结构.

    发送修改建议，AI 返回修改后的章节列表。
    支持多轮对话（传 conversation_id 保持上下文）。
    """
    # Load project
    result = await db.execute(
        select(BidProject).where(BidProject.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get current chapters
    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="请先提取章节（POST /extract-chapters）再进行编辑",
        )

    # Chat edit
    try:
        from app.services.chapter_chat import chat_edit_chapters as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_chat(
            chapters_json=chapters_json,
            user_message=data.message,
            conversation_id=data.conversation_id,
            ai_adapter=ai,
        )

        # Save updated chapters
        project.chapter_structure_json = json.dumps(
            result["chapters"], ensure_ascii=False,
        )
        await db.commit()

        return ChapterChatResponse(
            reply=result["reply"],
            chapters=result["chapters"],
            conversation_id=result["conversation_id"],
        )

    except Exception as exc:
        logger.exception("Chapter chat edit failed")
        raise HTTPException(status_code=500, detail=f"章节编辑失败: {exc}")


# ---------------------------------------------------------------------------
# POST /{project_id}/chapters/lock
# ---------------------------------------------------------------------------

@router.post("/{project_id}/chapters/lock", response_model=LockChaptersResponse)
async def lock_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定章节结构并创建 ProjectChapter 记录.

    锁定后：
    - chapter_structure_json 不可再通过 chat 修改
    - 为每个章节创建 ProjectChapter 记录
    - ai_generated 类型的章节进入待细化状态
    - 项目状态从 collecting 推进
    """
    # Load project
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="请先提取章节（POST /extract-chapters）再进行锁定",
        )

    try:
        chapters = json.loads(chapters_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="章节数据格式错误")

    # Delete existing chapters
    for ch in list(project.chapters):
        await db.delete(ch)
    await db.flush()

    # Create ProjectChapter records
    for ch_data in chapters:
        ch_type = ch_data.get("type", "ai_generated")
        chapter = ProjectChapter(
            project_id=project_id,
            title=ch_data.get("title", ""),
            order_index=ch_data.get("order_index", 0),
            status="pending",
            chapter_type=ch_type,
            chapter_meta_json=json.dumps({
                "number": ch_data.get("number", ""),
                "format_notes": ch_data.get("format_notes", ""),
                "scoring_context": ch_data.get("scoring_context", ""),
                "table_columns": ch_data.get("table_columns", []),
            }, ensure_ascii=False),
            children_json=json.dumps(ch_data.get("children", []), ensure_ascii=False),
            review_status="locked" if ch_type in ("fixed_form", "table", "attachment") else "refining",
        )
        db.add(chapter)

    # Update project status
    project.status = "collecting"  # Ready for resource collection
    await db.commit()

    logger.info(
        "Locked %d chapters for project %s",
        len(chapters), project_id,
    )

    return LockChaptersResponse(
        success=True,
        chapters_count=len(chapters),
        message=f"已锁定 {len(chapters)} 个章节。文件/表格类型章节可直接生成，AI撰写章节请先细化标题。",
    )


# ---------------------------------------------------------------------------
# GET /{project_id}/chapters
# ---------------------------------------------------------------------------

@router.get("/{project_id}/chapters")
async def get_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目的章节结构（未锁定返回 structure_json，已锁定返回 ProjectChapter 列表）."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check if chapters have been locked (ProjectChapter records exist)
    if project.chapters:
        return {
            "locked": True,
            "chapters": [
                {
                    "id": ch.id,
                    "title": ch.title,
                    "order_index": ch.order_index,
                    "chapter_type": ch.chapter_type,
                    "chapter_meta": json.loads(ch.chapter_meta_json) if ch.chapter_meta_json else {},
                    "children": json.loads(ch.children_json) if ch.children_json else [],
                    "review_status": ch.review_status,
                    "status": ch.status,
                }
                for ch in sorted(project.chapters, key=lambda c: c.order_index)
            ],
        }

    # Return structure_json if not yet locked
    try:
        chapters = json.loads(project.chapter_structure_json) if project.chapter_structure_json else []
    except json.JSONDecodeError:
        chapters = []

    return {
        "locked": False,
        "chapters": chapters,
    }


# ---------------------------------------------------------------------------
# Title Refinement schemas & endpoints
# ---------------------------------------------------------------------------

class RefineTitlesResponse(BaseModel):
    chapter_id: str = ""
    chapter_title: str = ""
    children: list = []
    leaf_count: int = 0
    error: str = ""


class RefineChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class RefineChatResponse(BaseModel):
    reply: str = ""
    children: list = []


class LockTitlesResponse(BaseModel):
    success: bool = False
    leaf_count: int = 0
    message: str = ""


@router.post("/{project_id}/chapters/{chapter_id}/refine", response_model=RefineTitlesResponse)
async def refine_chapter_titles(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对 AI 撰写章节进行标题细化，生成 3-4 级子标题树."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Find the chapter
    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if chapter.chapter_type != "ai_generated":
        raise HTTPException(
            status_code=400,
            detail=f"只有 AI 撰写类型的章节需要标题细化，当前章节类型为 {chapter.chapter_type}",
        )

    try:
        from app.services.title_refiner import refine_chapter_titles as do_refine
        from app.services.ai_adapter import ai_adapter as ai

        chapter_meta = json.loads(chapter.chapter_meta_json) if chapter.chapter_meta_json else {}
        requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}

        children = await do_refine(
            chapter_title=chapter.title,
            chapter_meta=chapter_meta,
            requirements=requirements,
            ai_adapter=ai,
        )

        # Save to chapter
        chapter.children_json = json.dumps(children, ensure_ascii=False)
        chapter.review_status = "refining"
        await db.commit()

        # Count leaves
        def count_leaves(nodes):
            c = 0
            for n in nodes:
                if n.get("children"):
                    c += count_leaves(n["children"])
                else:
                    c += 1
            return c

        leaf_count = count_leaves(children)

        return RefineTitlesResponse(
            chapter_id=chapter_id,
            chapter_title=chapter.title,
            children=children,
            leaf_count=leaf_count,
        )

    except Exception as exc:
        logger.exception("Title refinement failed")
        raise HTTPException(status_code=500, detail=f"标题细化失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/refine/chat", response_model=RefineChatResponse)
async def chat_refine_titles(
    project_id: str,
    chapter_id: str,
    data: RefineChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对话式修改子标题树."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.title_refiner import chat_refine_titles as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_chat(
            children_json=chapter.children_json,
            chapter_title=chapter.title,
            user_message=data.message,
            ai_adapter=ai,
        )

        # Save updated children
        chapter.children_json = json.dumps(result["children"], ensure_ascii=False)
        await db.commit()

        return RefineChatResponse(
            reply=result["reply"],
            children=result["children"],
        )

    except Exception as exc:
        logger.exception("Title refinement chat failed")
        raise HTTPException(status_code=500, detail=f"标题对话修改失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/refine/lock", response_model=LockTitlesResponse)
async def lock_refined_titles(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定细化后的标题，将叶子节点转为生成任务."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        children = json.loads(chapter.children_json) if chapter.children_json else []
    except json.JSONDecodeError:
        children = []

    if not children:
        raise HTTPException(
            status_code=400,
            detail="请先细化标题（POST /refine）再锁定",
        )

    from app.services.title_refiner import flatten_children_to_tasks
    tasks = flatten_children_to_tasks(children, [chapter.title])

    # Store tasks in children_json as flattened leaf list
    chapter.children_json = json.dumps(tasks, ensure_ascii=False)
    chapter.review_status = "generating"
    await db.commit()

    return LockTitlesResponse(
        success=True,
        leaf_count=len(tasks),
        message=f"已锁定 {chapter.title} 的子标题，共 {len(tasks)} 个生成任务。",
    )


# ---------------------------------------------------------------------------
# Section-level modify & regenerate
# ---------------------------------------------------------------------------

class SectionModifyRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    current_content: str = ""
    instruction: str = Field(..., min_length=1, max_length=2000)


class SectionModifyResponse(BaseModel):
    modified_content: str = ""
    diff_summary: str = ""


class SectionRegenerateRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    token_budget_hint: str = "medium"


class SectionSaveRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    content: str = ""


class SectionSaveResponse(BaseModel):
    success: bool = False


@router.post("/{project_id}/chapters/{chapter_id}/sections/modify", response_model=SectionModifyResponse)
async def modify_section(
    project_id: str,
    chapter_id: str,
    data: SectionModifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 针对性修改单个节的内容."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.section_editor import modify_section as do_modify
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_modify(
            chapter_title=chapter.title,
            section_path=data.section_path,
            current_content=data.current_content,
            instruction=data.instruction,
            children_json=chapter.children_json,
            ai_adapter=ai,
        )
        return SectionModifyResponse(**result)

    except Exception as exc:
        logger.exception("Section modify failed")
        raise HTTPException(status_code=500, detail=f"AI 修改失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/sections/regenerate")
async def regenerate_section(
    project_id: str,
    chapter_id: str,
    data: SectionRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重新生成单个节的内容，SSE 流式返回."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}

    # Gather company profile
    company_profile = None
    try:
        from app.services.collection import get_collected_resources
        collected = await get_collected_resources(project_id, db)
        company_profile = collected.get("company") if collected else None
    except Exception:
        pass

    async def event_generator():
        try:
            from app.services.section_editor import regenerate_section as do_regenerate
            from app.services.ai_adapter import ai_adapter as ai

            full = ""
            async for chunk in do_regenerate(
                chapter_title=chapter.title,
                section_path=data.section_path,
                token_budget_hint=data.token_budget_hint,
                requirements=requirements,
                children_json=chapter.children_json,
                company_profile=company_profile,
                ai_adapter=ai,
            ):
                full += chunk
                yield {
                    "event": "chunk",
                    "data": json.dumps({"text": chunk}, ensure_ascii=False),
                }

            yield {
                "event": "done",
                "data": json.dumps({
                    "content": full,
                    "section_path": data.section_path,
                }, ensure_ascii=False),
            }

        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc)}, ensure_ascii=False),
            }

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())


@router.post("/{project_id}/chapters/{chapter_id}/sections/save", response_model=SectionSaveResponse)
async def save_section(
    project_id: str,
    chapter_id: str,
    data: SectionSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存单个节的内容到 children_json 树中."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.section_editor import save_section_content

        updated = save_section_content(chapter.children_json, data.section_path, data.content)
        chapter.children_json = updated
        await db.commit()

        return SectionSaveResponse(success=True)

    except Exception as exc:
        logger.exception("Section save failed")
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")


@router.get("/{project_id}/chapters/{chapter_id}/sections")
async def get_section(
    project_id: str,
    chapter_id: str,
    section_path: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个节的内容."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        path = json.loads(section_path) if section_path else []
    except json.JSONDecodeError:
        path = []

    from app.services.section_editor import get_section_content

    content = get_section_content(chapter.children_json, path)
    return {"content": content, "section_path": path}
