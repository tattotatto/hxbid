"""宏曦标书 - AI Generation API Routes.

Provides upload-and-parse, SSE streaming generation, export, and download
endpoints that wire together the full AI bid-document pipeline.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.database import async_session, get_db
from app.models.company_profile import CompanyProfile
from app.models.project import BidProject, ProjectChapter
from app.models.qualification import Qualification
from app.models.user import User
from app.schemas.bid import ExportRequest, ExportResponse, GenerateRequest, ParseResponse, RetryFailedRequest
from app.services.ai_pipeline import (
    generate_bid_with_deep_outline,
    generate_chapter_with_materials,
    generate_outline,
    parse_bid_requirements,
)
from app.services.anti_ai import analyze_chapter, analyze_project_chapters, report_to_dict
from app.services.document_parser import parse_document
from app.services.edit_analyzer import analyze_chapter_edits, edit_analysis_to_dict
from app.services.notification import send_notification
from app.services.collection import get_collected_resources
from app.services.rag import assemble_chapter_context
from app.services.render_engine import export_to_pdf, render_bid_to_docx
from app.services.vector_store import vector_store
from app.utils.permissions import require_editor
from app.utils.security import get_current_user
from app.models.template import BidTemplate

logger = logging.getLogger(__name__)

router = APIRouter()


async def _gather_generation_context(
    project_id: str,
    requirements: dict,
    db,
) -> tuple[dict | None, list, list]:
    """Gather company profile, qualifications, and personnel for generation.

    Returns (company_profile, matched_qualifications, matched_personnel).
    """
    collected = None
    company_profile = None
    matched_qualifications: list = []
    matched_personnel: list = []

    try:
        collected = await get_collected_resources(project_id, db)
    except Exception:
        pass

    if collected:
        matched_qualifications = collected.get("qualifications", [])
        matched_personnel = collected.get("personnel", [])
        company_profile = collected.get("company")
    else:
        try:
            from app.services.rag import assemble_chapter_context
            _, matched_qualifications, matched_personnel, source_summary = \
                await assemble_chapter_context(
                    chapter_title="项目整体",
                    requirements=requirements,
                    project_id=project_id,
                    db=db,
                )
            company_profile = source_summary.get("company")
        except Exception:
            pass

    return company_profile, matched_qualifications, matched_personnel


# ---------------------------------------------------------------------------
# POST /upload-and-parse  (Step 1+2 of the workflow)
# ---------------------------------------------------------------------------

@router.post("/upload-and-parse", response_model=ParseResponse)
async def upload_and_parse(
    file: UploadFile = File(...),
    project_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a bidding document, parse it, and extract requirements via AI.

    Saves the file to UPLOAD_DIR, extracts text, runs AI requirement parsing,
    creates a BidProject record, and returns the extracted information.
    """
    # -- Save uploaded file --
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file.filename).suffix if file.filename else ".tmp"
    saved_filename = f"{uuid.uuid4().hex}{file_ext}"
    saved_path = upload_dir / saved_filename

    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    # -- Parse document text --
    requirements = {}
    document_text = ""
    try:
        document_text = parse_document(str(saved_path))
        requirements = await parse_bid_requirements(document_text)
    except Exception as e:
        requirements = {"project_name": project_name or file.filename or "未命名项目", "parse_error": str(e)}

    # -- Create project record --
    project = BidProject(
        name=project_name or requirements.get("project_name") or file.filename or "未命名项目",
        original_file_path=str(saved_path.absolute()),
        parsed_requirements_json=json.dumps(requirements, ensure_ascii=False),
        status="collecting",
        created_by=current_user.id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # -- Index raw document text into vector store (best-effort) --
    if document_text:
        try:
            vector_store.index_chapter(
                chapter_id=project.id,
                project_id=project.id,
                title=project.name,
                content=document_text,
            )
        except Exception:
            pass

    return ParseResponse(
        project_name=project.name,
        requirements=requirements,
    )


# ---------------------------------------------------------------------------
# POST /upload-history  (Direct historical bid upload)
# ---------------------------------------------------------------------------

@router.post("/upload-history", response_model=ParseResponse)
async def upload_history(
    file: UploadFile = File(...),
    project_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a historical bid document for the resource library.

    Same as upload-and-parse but marks the project as 'archived' immediately,
    since historical bids are reference material, not active projects.
    """
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file.filename).suffix if file.filename else ".tmp"
    saved_filename = f"{uuid.uuid4().hex}{file_ext}"
    saved_path = upload_dir / saved_filename

    content = await file.read()
    with open(saved_path, "wb") as f:
        f.write(content)

    requirements = {}
    document_text = ""
    try:
        document_text = parse_document(str(saved_path))
        requirements = await parse_bid_requirements(document_text)
    except Exception as e:
        # Document parsing failed — still save the file for manual review
        requirements = {"project_name": project_name or file.filename or "未命名项目", "parse_error": str(e)}

    project = BidProject(
        name=project_name or requirements.get("project_name") or file.filename or "未命名项目",
        original_file_path=str(saved_path.absolute()),
        parsed_requirements_json=json.dumps(requirements, ensure_ascii=False),
        status="archived",
        created_by=current_user.id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # -- Index raw document text into vector store (best-effort) --
    if document_text:
        try:
            vector_store.index_chapter(
                chapter_id=project.id,
                project_id=project.id,
                title=project.name,
                content=document_text,
            )
        except Exception:
            pass

    return ParseResponse(
        project_name=project.name,
        requirements=requirements,
    )


# ---------------------------------------------------------------------------
# POST /generate  (Step 3 — SSE streaming generation)
# ---------------------------------------------------------------------------

@router.post("/generate")
async def generate_bid(
    data: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate bid document chapters via AI with SSE streaming.

    Loads the project, ensures an outline and chapter records exist, then
    streams chapter-by-chapter generation progress as Server-Sent Events.
    """
    # -- Load project with eager-loaded chapters --
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == data.project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if project.status == "collecting":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先完成信息搜集再生成标书",
        )

    requirements = json.loads(project.parsed_requirements_json)

    # -- Ensure outline exists --
    if not project.outline_json or project.outline_json == "[]":
        outline = generate_outline(requirements)
        project.outline_json = json.dumps(outline, ensure_ascii=False)
        await db.flush()

    outline = json.loads(project.outline_json)

    # -- Create chapter records on first generation --
    if not project.chapters:
        for item in outline:
            chapter = ProjectChapter(
                project_id=project.id,
                title=item["title"],
                order_index=item.get("order_index", 0),
                status="pending",
            )
            db.add(chapter)
        await db.flush()
        await db.refresh(project, ["chapters"])

    # -- Determine which chapters to generate --
    if data.regenerate_chapter_ids:
        chapters_to_generate = [
            c for c in project.chapters
            if c.id in data.regenerate_chapter_ids
        ]
    else:
        chapters_to_generate = sorted(
            project.chapters, key=lambda c: c.order_index
        )

    # -- Snapshot data for the generator (before the handler session closes) --
    project_id = project.id
    project_name = project.name
    chapter_data = [
        {"id": c.id, "title": c.title, "order_index": c.order_index}
        for c in chapters_to_generate
    ]

    # Mark project as generating; the handler's get_db session will commit on return
    project.status = "generating"
    await db.flush()

    # -- SSE event generator (runs after the handler returns) --
    async def event_generator():
        # ── Deep generation mode (new pipeline) ──
        if (
            settings.GENERATION_DEEP_OUTLINE_ENABLED
            and not settings.GENERATION_LEGACY_MODE
        ):
            async with async_session() as gen_db:
                try:
                    # Gather context
                    company_profile, matched_qualifications, matched_personnel = \
                        await _gather_generation_context(
                            project_id, requirements, gen_db,
                        )

                    # Use the deep generation pipeline
                    chapters_payload = None
                    async for event in generate_bid_with_deep_outline(
                        requirements=requirements,
                        company_profile=company_profile,
                        matched_qualifications=matched_qualifications,
                        matched_personnel=matched_personnel,
                        project_id=project_id,
                        db=gen_db,
                    ):
                        yield event
                        # Capture chapters_payload from the done event's data JSON
                        if event.get("event") == "done":
                            try:
                                done_data = json.loads(event.get("data", "{}"))
                                chapters_payload = done_data.get("chapters")
                            except Exception:
                                chapters_payload = None

                    # Save generated chapters to DB
                    if chapters_payload:
                        for i, ch in enumerate(chapters_payload):
                            try:
                                # Find or create ProjectChapter record
                                from sqlalchemy import select as sa_select
                                result_ch = await gen_db.execute(
                                    sa_select(ProjectChapter).where(
                                        ProjectChapter.project_id == project_id,
                                        ProjectChapter.order_index == i + 1,
                                    )
                                )
                                db_ch = result_ch.scalar_one_or_none()
                                if db_ch:
                                    db_ch.ai_generated_content = ch.get("content", "")
                                    db_ch.status = "generated"
                                    db_ch.title = ch.get("title", db_ch.title)
                                else:
                                    db_ch = ProjectChapter(
                                        project_id=project_id,
                                        title=ch.get("title", f"第{i + 1}部分"),
                                        order_index=i + 1,
                                        ai_generated_content=ch.get("content", ""),
                                        status="generated",
                                    )
                                    gen_db.add(db_ch)
                            except Exception:
                                pass
                        await gen_db.commit()

                    # Mark project for review
                    try:
                        db_proj = await gen_db.get(BidProject, project_id)
                        if db_proj:
                            db_proj.status = "review"
                            await gen_db.commit()
                    except Exception:
                        pass

                    return  # deep generation complete — exit event generator

                except Exception as exc:
                    logger.exception("Deep generation failed: %s", exc)
                    try:
                        await gen_db.rollback()
                        db_proj = await gen_db.get(BidProject, project_id)
                        if db_proj:
                            db_proj.status = "error"
                            await gen_db.commit()
                    except Exception:
                        pass
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"message": f"生成失败（深度模式）: {exc}"},
                            ensure_ascii=False,
                        ),
                    }
                    return

        # ── Legacy generation mode (original pipeline) ──
        async with async_session() as gen_db:
            try:
                for i, ch in enumerate(chapter_data):
                    # --- status event ---
                    yield {
                        "event": "status",
                        "data": json.dumps(
                            {
                                "current_chapter": i + 1,
                                "total": len(chapter_data),
                                "chapter_id": ch["id"],
                                "title": ch["title"],
                            },
                            ensure_ascii=False,
                        ),
                    }

                    # --- Stream chapter content ---
                    full_content = ""
                    # RAG: assemble context from all sources
                    similar_chapters = []
                    matched_qualifications = []
                    matched_personnel = []
                    company_profile = None
                    source_summary = {}

                    try:
                        # If project has collected resources, use them directly
                        collected = await get_collected_resources(project_id, gen_db)
                        if collected["qualifications"] or collected["personnel"]:
                            # Use collected qualifications (still do vector search for similar chapters)
                            try:
                                from app.services.rag import retrieve_similar_chapters
                                similar_chapters = await retrieve_similar_chapters(
                                    chapter_title=ch["title"],
                                    requirements=requirements,
                                    project_id=project_id,
                                )
                            except Exception:
                                similar_chapters = []
                            matched_qualifications = collected["qualifications"]
                            matched_personnel = collected["personnel"]
                            company_profile = collected.get("company")
                            source_summary = {
                                "similar_count": len(similar_chapters),
                                "qual_count": len(matched_qualifications),
                                "personnel_count": len(matched_personnel),
                                "has_company": company_profile is not None,
                                "similar_titles": [s.get("title", "") for s in similar_chapters[:5]],
                            }
                        else:
                            # Fallback: keyword-based RAG matching
                            similar_chapters, matched_qualifications, matched_personnel, source_summary = \
                                await assemble_chapter_context(
                                    chapter_title=ch["title"],
                                    requirements=requirements,
                                    project_id=project_id,
                                    db=gen_db,
                                )
                            company_profile = source_summary.pop("company", None)
                    except Exception:
                        source_summary = {"similar_count": 0, "qual_count": 0, "personnel_count": 0}

                    # Emit RAG source info to frontend (company PII stripped)
                    yield {
                        "event": "rag_sources",
                        "data": json.dumps(
                            {
                                "chapter_id": ch["id"],
                                **{k: v for k, v in source_summary.items() if k != "company"},
                            },
                            ensure_ascii=False,
                        ),
                    }

                    try:
                        async for chunk in generate_chapter_with_materials(
                            chapter_title=ch["title"],
                            requirements=requirements,
                            matched_qualifications=matched_qualifications,
                            matched_personnel=matched_personnel,
                            similar_chapters=[s["content"] for s in similar_chapters],
                            company_profile=company_profile,
                        ):
                            full_content += chunk
                            yield {
                                "event": "chunk",
                                "data": json.dumps(
                                    {
                                        "chapter_id": ch["id"],
                                        "text": chunk,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                    except Exception as chapter_err:
                        # Single chapter failure shouldn't kill the whole run
                        yield {
                            "event": "error",
                            "data": json.dumps(
                                {
                                    "chapter_id": ch["id"],
                                    "message": f"Chapter generation failed: {chapter_err}",
                                },
                                ensure_ascii=False,
                            ),
                        }
                        continue

                    # --- Save generated content to DB ---
                    db_chapter = await gen_db.get(ProjectChapter, ch["id"])
                    if db_chapter:
                        db_chapter.ai_generated_content = full_content
                        db_chapter.status = "generated"
                        await gen_db.commit()

                    # --- Anti-AI trace analysis ---
                    try:
                        report = analyze_chapter(
                            chapter_id=ch["id"],
                            chapter_title=ch["title"],
                            content=full_content,
                        )
                        yield {
                            "event": "ai_trace_report",
                            "data": json.dumps(
                                report_to_dict(report),
                                ensure_ascii=False,
                            ),
                        }
                    except Exception:
                        pass  # analysis is best-effort

                    # --- chapter_done event ---
                    yield {
                        "event": "chapter_done",
                        "data": json.dumps(
                            {
                                "chapter_id": ch["id"],
                                "title": ch["title"],
                            },
                            ensure_ascii=False,
                        ),
                    }

                # --- All chapters done — index into vector store ---
                if vector_store.is_available():
                    try:
                        index_chapters = [
                            {"id": ch["id"], "title": ch.get("title", ch["title"]), "content": ch.get("content", "")}
                            for ch in chapter_data
                        ]
                        # Re-fetch chapters with actual content for indexing
                        result = await gen_db.execute(
                            select(ProjectChapter)
                            .where(ProjectChapter.project_id == project_id)
                            .order_by(ProjectChapter.order_index)
                        )
                        db_chapters = result.scalars().all()
                        chapters_for_index = [
                            {"id": c.id, "title": c.title, "content": c.ai_generated_content}
                            for c in db_chapters
                            if c.ai_generated_content
                        ]
                        if chapters_for_index:
                            vector_store.index_project(project_id, chapters_for_index)
                    except Exception:
                        pass  # indexing is best-effort

                # --- All chapters done — mark project for review ---
                db_project = await gen_db.get(BidProject, project_id)
                if db_project:
                    db_project.status = "review"
                    await gen_db.commit()
                    await send_notification("generation_complete", project_name=project_name)

                yield {"event": "done", "data": "{}"}

            except Exception as exc:
                # Try to mark the project as errored
                try:
                    await gen_db.rollback()
                    db_project = await gen_db.get(BidProject, project_id)
                    if db_project:
                        db_project.status = "error"
                        await gen_db.commit()
                except Exception:
                    pass
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": str(exc)}, ensure_ascii=False
                    ),
                }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# POST /generate/retry-failed  (Retry failed sections via deep pipeline)
# ---------------------------------------------------------------------------

@router.post("/generate/retry-failed")
async def retry_failed_sections(
    data: RetryFailedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry previously failed leaf sections in the deep generation pipeline.

    Loads the stored generation_state_json, resets failed sections back
    to pending, and re-enters the deep pipeline in resume mode (skipping
    outline regeneration). Completed sections are preserved.

    Request body:
        - project_id: str
        - section_paths: Optional[List[str]] — specific sections to retry;
          if omitted, all failed sections are retried.
    """
    # ── Load project ──
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == data.project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # ── Validate generation state exists ──
    if not project.generation_state_json or project.generation_state_json == "{}":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该项目没有生成状态记录，请先使用「一键生成」启动生成",
        )

    # ── Validate project status ──
    if project.status == "generating":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="项目正在生成中，请等待当前生成完成后再重试",
        )

    try:
        gen_state = json.loads(project.generation_state_json)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="生成状态数据损坏，请重新启动生成",
        )

    sections = gen_state.get("sections", {})
    if not sections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="生成状态中没有章节数据，请重新启动生成",
        )

    # ── Identify sections to retry ──
    target_paths = set(data.section_paths) if data.section_paths else None
    retry_count = 0

    for path_key, sec in sections.items():
        if sec.get("status") == "failed":
            if target_paths is None or path_key in target_paths:
                sec["status"] = "pending"
                sec["error"] = None
                sec["retries"] = 0
                retry_count += 1

    if retry_count == 0:
        # Check if there were failed sections at all
        total_failed = sum(1 for s in sections.values() if s.get("status") == "failed")
        if total_failed > 0 and target_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"指定的 {len(target_paths)} 个章节路径未匹配到任何失败章节（共 {total_failed} 个失败章节）",
            )
        elif total_failed > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"所有 {total_failed} 个失败章节已达最大重试次数，请调整重试配置或重新生成",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="没有找到失败的章节需要重试",
            )

    # ── Save updated gen_state ──
    gen_state["completed_leaves"] = sum(
        1 for s in sections.values() if s.get("status") == "done"
    )
    gen_state["status"] = "generating"
    project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
    await db.commit()

    logger.info(
        "Retry: reset %d failed sections for project %s (target_paths=%s)",
        retry_count, data.project_id, bool(target_paths),
    )

    # ── Load requirements ──
    requirements = json.loads(project.parsed_requirements_json or "{}")
    project_id = project.id

    # ── SSE event generator ──
    async def event_generator():
        async with async_session() as gen_db:
            try:
                # Gather context
                company_profile, matched_qualifications, matched_personnel = \
                    await _gather_generation_context(
                        project_id, requirements, gen_db,
                    )

                # ── Retry notification ──
                yield {
                    "event": "status",
                    "data": json.dumps({
                        "phase": "retry",
                        "message": f"正在重试 {retry_count} 个失败章节...",
                        "retry_count": retry_count,
                    }, ensure_ascii=False),
                }

                # ── Run deep pipeline in resume mode ──
                chapters_payload = None
                async for event in generate_bid_with_deep_outline(
                    requirements=requirements,
                    company_profile=company_profile,
                    matched_qualifications=matched_qualifications,
                    matched_personnel=matched_personnel,
                    project_id=project_id,
                    db=gen_db,
                    resume=True,  # skip outline regeneration
                ):
                    yield event
                    # Capture chapters_payload from the done event
                    if event.get("event") == "done":
                        try:
                            done_data = json.loads(event.get("data", "{}"))
                            chapters_payload = done_data.get("chapters")
                        except Exception:
                            chapters_payload = None

                # ── Save generated chapters to DB ──
                if chapters_payload:
                    for i, ch in enumerate(chapters_payload):
                        try:
                            from sqlalchemy import select as sa_select
                            result_ch = await gen_db.execute(
                                sa_select(ProjectChapter).where(
                                    ProjectChapter.project_id == project_id,
                                    ProjectChapter.order_index == i + 1,
                                )
                            )
                            db_ch = result_ch.scalar_one_or_none()
                            if db_ch:
                                db_ch.ai_generated_content = ch.get("content", "")
                                db_ch.status = "generated"
                                db_ch.title = ch.get("title", db_ch.title)
                            else:
                                db_ch = ProjectChapter(
                                    project_id=project_id,
                                    title=ch.get("title", f"第{i + 1}部分"),
                                    order_index=i + 1,
                                    ai_generated_content=ch.get("content", ""),
                                    status="generated",
                                )
                                gen_db.add(db_ch)
                        except Exception:
                            pass
                    await gen_db.commit()

                # ── Mark project for review ──
                try:
                    db_proj = await gen_db.get(BidProject, project_id)
                    if db_proj:
                        db_proj.status = "review"
                        await gen_db.commit()
                except Exception:
                    pass

            except Exception as exc:
                logger.exception("Retry generation failed: %s", exc)
                try:
                    await gen_db.rollback()
                    db_proj = await gen_db.get(BidProject, project_id)
                    if db_proj:
                        db_proj.status = "error"
                        await gen_db.commit()
                except Exception:
                    pass
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {"message": f"重试生成失败: {exc}"},
                        ensure_ascii=False,
                    ),
                }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# POST /export  (Step 5 — render to .docx / .pdf)
# ---------------------------------------------------------------------------

@router.post("/export", response_model=ExportResponse)
async def export_bid(
    data: ExportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Render generated chapters into a formatted .docx (and optionally .pdf).

    Builds a chapters payload from ProjectChapter records and hands it off to
    the render engine. Returns download URLs for the produced file(s).

    Accepts optional template_id to apply a saved style template.
    """
    # -- Load project with chapters --
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == data.project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # -- Load style template if provided --
    style_config = None
    template_id = getattr(data, "template_id", None)
    if template_id:
        template = await db.get(BidTemplate, template_id)
        if template:
            import json as _json
            style_config = _json.loads(template.style_config_json)
    else:
        # Use the default template
        result_tpl = await db.execute(
            select(BidTemplate).where(BidTemplate.is_default == True)
        )
        default_tpl = result_tpl.scalar_one_or_none()
        if default_tpl:
            import json as _json
            style_config = _json.loads(default_tpl.style_config_json)

    # Convert JSON-friendly keys to python-docx types
    if style_config:
        from docx.shared import Pt, Cm
        if "body_font_size_pt" in style_config:
            style_config["body_font_size"] = Pt(style_config.pop("body_font_size_pt"))
        if "heading1_font_size_pt" in style_config:
            style_config["heading1_font_size"] = Pt(style_config.pop("heading1_font_size_pt"))
        if "heading2_font_size_pt" in style_config:
            style_config["heading2_font_size"] = Pt(style_config.pop("heading2_font_size_pt"))
        if "heading3_font_size_pt" in style_config:
            style_config["heading3_font_size"] = Pt(style_config.pop("heading3_font_size_pt"))
        if "margin_top_cm" in style_config:
            style_config["margin_top"] = Cm(style_config.pop("margin_top_cm"))
        if "margin_bottom_cm" in style_config:
            style_config["margin_bottom"] = Cm(style_config.pop("margin_bottom_cm"))
        if "margin_left_cm" in style_config:
            style_config["margin_left"] = Cm(style_config.pop("margin_left_cm"))
        if "margin_right_cm" in style_config:
            style_config["margin_right"] = Cm(style_config.pop("margin_right_cm"))

    # -- Filter chapters if chapter_ids provided --
    if data.chapter_ids:
        selected = [
            c for c in project.chapters if c.id in data.chapter_ids
        ]
    else:
        selected = sorted(project.chapters, key=lambda c: c.order_index)

    # -- Build chapters payload for the render engine --
    chapters_payload = []
    for c in selected:
        content = c.final_content or c.ai_generated_content
        chapters_payload.append({"title": c.title, "content": content})

    # -- Collect images for inline injection into chapters --
    # chapter_images: list of lists, one entry per chapter in chapters_payload,
    # each inner list contains {"path": str, "label": str} for inline images.
    chapter_images: list = [[] for _ in chapters_payload]

    # ── Company profile images → injected inline in 资格审查部分 ──
    cp_result = await db.execute(
        select(CompanyProfile).order_by(CompanyProfile.updated_at.desc()).limit(1)
    )
    cp = cp_result.scalar_one_or_none()
    company_images = []
    if cp:
        if cp.business_license_image:
            company_images.append({
                "path": cp.business_license_image,
                "label": f"营业执照 — {cp.company_name or ''}",
            })
        if cp.legal_rep_id_front_image:
            company_images.append({
                "path": cp.legal_rep_id_front_image,
                "label": "法定代表人身份证（正面）",
                "type": "id_card",
            })
        if cp.legal_rep_id_back_image:
            company_images.append({
                "path": cp.legal_rep_id_back_image,
                "label": "法定代表人身份证（反面）",
                "type": "id_card",
            })

    # ── ALL qualifications (with or without images) for structured text injection ──
    # Previously only fetched quals with attachment_path, which meant
    # qualifications without images were completely missing from the document.
    all_quals_result = await db.execute(
        select(Qualification).order_by(Qualification.updated_at.desc())
    )
    all_quals = all_quals_result.scalars().all()

    qual_images = []       # qualifications that have scannable images
    qual_text_items = []   # all qualifications → structured text

    for q in all_quals:
        item = {
            "name": q.name,
            "cert_number": q.cert_number or "",
            "issuing_authority": q.issuing_authority or "",
            "issue_date": str(q.issue_date) if q.issue_date else "",
            "expiry_date": str(q.expiry_date) if q.expiry_date else "",
            "has_image": bool(q.attachment_path and q.attachment_path.strip()),
        }
        qual_text_items.append(item)

        if item["has_image"]:
            qual_images.append({
                "path": q.attachment_path,
                "label": f"{q.name} — {q.cert_number or ''}",
            })

    # ── Build structured company info text block with inline images ──
    company_text_block = ""
    embedded_images = set()  # track (path, label) of images already embedded inline
    if cp:
        cp_parts = []
        if cp.company_name:
            cp_parts.append(f"公司名称：{cp.company_name}")
        if cp.business_license_number:
            cp_parts.append(f"统一社会信用代码：{cp.business_license_number}")
        if cp.legal_rep_name:
            cp_parts.append(f"法定代表人：{cp.legal_rep_name}")
        if cp.address:
            cp_parts.append(f"公司地址：{cp.address}")
        if cp.contact_phone:
            cp_parts.append(f"联系电话：{cp.contact_phone}")
        if cp.website:
            cp_parts.append(f"公司网站：{cp.website}")
        if cp_parts:
            company_text_block = "\n\n投标人基本情况表\n\n" + "\n".join(cp_parts) + "\n"

        # Business license image — inline right after company info
        if cp.business_license_image and cp.business_license_image.strip():
            company_text_block += f"\n[IMG:{cp.business_license_image}|营业执照 — {cp.company_name or ''}]\n"
            embedded_images.add(cp.business_license_image)

        # ID card images — side-by-side pair
        front = cp.legal_rep_id_front_image or ""
        back = cp.legal_rep_id_back_image or ""
        if front.strip() and back.strip():
            company_text_block += f"\n[IDPAIR:{front}|法定代表人身份证（正面）|{back}|法定代表人身份证（反面）]\n"
            embedded_images.add(front)
            embedded_images.add(back)
        elif front.strip():
            company_text_block += f"\n[IMG:{front}|法定代表人身份证（正面）]\n"
            embedded_images.add(front)
        elif back.strip():
            company_text_block += f"\n[IMG:{back}|法定代表人身份证（反面）]\n"
            embedded_images.add(back)

    # ── Build structured qualification text block with inline images ──
    qual_text_block = ""
    if qual_text_items:
        qual_text_block = "\n\n资质证书清单\n\n"
        for i, q in enumerate(qual_text_items, 1):
            qual_text_block += f"{i}. {q['name']}"
            if q['cert_number']:
                qual_text_block += f"（编号：{q['cert_number']}）"
            qual_text_block += "\n"
            if q['issuing_authority']:
                qual_text_block += f"   发证机关：{q['issuing_authority']}\n"
            if q['issue_date']:
                qual_text_block += f"   颁发日期：{q['issue_date']}\n"
            if q['expiry_date']:
                qual_text_block += f"   有效期至：{q['expiry_date']}\n"
            if q['has_image']:
                # Find the matching qual_image entry
                for qi in qual_images:
                    if qi['label'].startswith(q['name']):
                        qual_text_block += f"\n[IMG:{qi['path']}|{qi['label']}]\n"
                        embedded_images.add(qi['path'])
                        break
            qual_text_block += "\n"

    # ── Personnel certificate images ──
    from app.models.personnel import PersonnelCertificate
    pc_result = await db.execute(
        select(PersonnelCertificate).where(PersonnelCertificate.attachment_path.isnot(None))
        .where(PersonnelCertificate.attachment_path != "")
        .order_by(PersonnelCertificate.expiry_date.desc())
    )
    personnel_cert_images = []
    for pc in pc_result.scalars():
        pci = {
            "path": pc.attachment_path,
            "label": f"{pc.cert_name} — {pc.cert_number or ''}",
        }
        personnel_cert_images.append(pci)

    # ── Contract images for 业绩/类似项目 sections ──
    from app.models.contract import Contract
    ct_result = await db.execute(
        select(Contract).order_by(Contract.created_at.desc())
    )
    contracts = ct_result.scalars().all()
    contract_images = []  # flat list for 其他内容 chapter
    contract_images_by_project = {}  # per-project dict
    for ct in contracts:
        try:
            img_paths = json.loads(ct.image_paths_json or "[]")
        except Exception:
            img_paths = []
        for img_path in img_paths:
            if img_path:
                ci = {"path": img_path, "label": f"{ct.project_name} — 合同"}
                contract_images.append(ci)
                if ct.project_name not in contract_images_by_project:
                    contract_images_by_project[ct.project_name] = []
                contract_images_by_project[ct.project_name].append(ci)

    # ── Only add images to chapter_images that weren't already embedded inline ──
    remaining_images = [img for img in (company_images + qual_images)
                        if img['path'] not in embedded_images]
    all_qual_section_images = remaining_images

    # ── Inject company info + qualification text into the 资格审查部分 chapter ──
    QUAL_CHAPTER_KEYWORDS = ["资格审查", "资格", "资质审查", "公司资质", "资质与业绩"]
    PERSONNEL_KEYWORDS = ["人员", "配置", "团队", "组织", "人力", "管理架构", "岗位"]

    for idx, ch in enumerate(chapters_payload):
        title = ch.get("title", "")

        # Inject structured company info, qualification text and inline images
        # PREPEND so images appear at the top of the chapter, before AI-generated text
        if any(kw in title for kw in QUAL_CHAPTER_KEYWORDS):
            if company_text_block or qual_text_block:
                injected = (company_text_block or "") + (qual_text_block or "")
                ch["content"] = injected + "\n\n" + (ch.get("content") or "")
            if all_qual_section_images:
                chapter_images[idx].extend(all_qual_section_images)

        # Personnel cert images → personnel-related chapters
        for kw in PERSONNEL_KEYWORDS:
            if kw in title:
                chapter_images[idx].extend(personnel_cert_images)
                break

        # Contract images → 业绩/其他内容 chapters
        CONTRACT_CHAPTER_KEYWORDS = ["业绩", "类似项目", "项目经验", "成功案例",
                                      "投标人认为需要提供的其他", "其他内容", "其他材料"]
        if any(kw in title for kw in CONTRACT_CHAPTER_KEYWORDS):
            if contract_images:
                chapter_images[idx].extend(contract_images)

    # -- Render .docx (no separate attachments section) --
    docx_path = render_bid_to_docx(
        chapters_payload,
        project.name,
        style_config=style_config,
        chapter_images=chapter_images if any(chapter_images) else None,
        company_name=cp.company_name if cp else "",
    )
    docx_filename = Path(docx_path).name

    # -- Optionally render .pdf --
    pdf_path: str | None = None
    if data.format in ("pdf", "both"):
        pdf_path = export_to_pdf(docx_path)

    # -- Mark project as exported --
    project.status = "exported"
    await db.flush()

    # -- Build download URLs relative to the API prefix --
    # Use relative URLs so the browser resolves them against the current origin.
    # Absolute URLs built from request.base_url can break behind reverse proxies
    # (e.g. nginx stripping the port from the Host header).
    base = "/api/v1/bid/download/"
    return ExportResponse(
        docx_url=f"{base}{docx_filename}",
        pdf_url=f"{base}{Path(pdf_path).name}" if pdf_path else "",
    )


# ---------------------------------------------------------------------------
# GET /download/{filename}
# ---------------------------------------------------------------------------

@router.get("/download/{filename}")
async def download_file(filename: str):
    """Serve a generated file (docx / pdf) from OUTPUT_DIR."""
    file_path = Path(settings.OUTPUT_DIR) / filename
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return FileResponse(
        str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


# ---------------------------------------------------------------------------
# GET /stream/{project_id}  (SSE polling for generation progress)
# ---------------------------------------------------------------------------

@router.get("/stream/{project_id}")
async def stream_progress(project_id: str):
    """Simple SSE polling endpoint for generation progress.

    The frontend can connect to this to receive periodic status updates while
    a generation run is in progress. Stops when the project reaches a terminal
    status (review, exported, or error).
    """

    async def event_generator():
        async with async_session() as poll_db:
            while True:
                # populate_existing=True forces a DB hit, bypassing the identity-map cache
                project = await poll_db.get(
                    BidProject, project_id, populate_existing=True
                )
                if not project:
                    yield {
                        "event": "error",
                        "data": json.dumps(
                            {"message": "Project not found"},
                            ensure_ascii=False,
                        ),
                    }
                    return

                # Gather chapter statuses
                result = await poll_db.execute(
                    select(ProjectChapter)
                    .where(ProjectChapter.project_id == project_id)
                    .order_by(ProjectChapter.order_index)
                )
                chapters = result.scalars().all()

                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "project_status": project.status,
                            "chapters": [
                                {
                                    "id": c.id,
                                    "title": c.title,
                                    "status": c.status,
                                }
                                for c in chapters
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }

                # Stop polling once we reach a terminal status
                if project.status in ("review", "exported", "error"):
                    return

                await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# GET /vector-stats  (Vector store statistics)
# ---------------------------------------------------------------------------


@router.get("/vector-stats")
async def get_vector_stats():
    """Return collection statistics for the vector knowledge base.

    Used by the frontend Workbench and Settings pages to display
    knowledge base health.
    """
    stats = vector_store.get_collection_stats()
    stats["enabled"] = vector_store.is_available()
    return stats


# ---------------------------------------------------------------------------
# POST /rebuild-index  (Rebuild the entire vector index)
# ---------------------------------------------------------------------------


@router.get("/indexed-projects")
async def get_indexed_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return set of project IDs that have chunks in the vector store."""
    if not vector_store.is_available():
        return {"indexed_ids": []}
    try:
        stats = vector_store.get_collection_stats()
        if stats["total_chunks"] == 0:
            return {"indexed_ids": []}
        # Fetch all metadata to find unique project IDs
        all_data = vector_store._collection.get(include=["metadatas"])
        project_ids = set()
        if all_data and all_data["metadatas"]:
            for meta in all_data["metadatas"]:
                pid = meta.get("project_id", "")
                if pid:
                    project_ids.add(pid)
        return {"indexed_ids": list(project_ids)}
    except Exception:
        return {"indexed_ids": []}


@router.post("/index-history/{project_id}")
async def index_history_bid(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Re-parse and index a single historical bid into the vector store.

    Reads the original uploaded document, extracts text, splits into
    sections, and indexes each section as a searchable chunk.
    """
    if not vector_store.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store is not available",
        )

    project = await db.get(BidProject, project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not project.original_file_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No original file to index")

    file_path = Path(project.original_file_path)
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"File not found: {project.original_file_path}")

    # Parse document
    try:
        text = parse_document(str(file_path))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Document parse failed: {e}")

    if not text or len(text.strip()) < 50:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Document text too short")

    # Split into sections by Chinese bid chapter headers
    import re
    sections = re.split(r'\n(?=(?:第[一二三四五六七八九十\d]+[章节篇]|[\（\(][一二三四五六七八九十\d]+[\）\)]|[一二三四五六七八九十\d]+[、．.]))', text)

    if len(sections) < 2:
        # No section markers found, split by double newlines as paragraphs
        sections = [s.strip() for s in re.split(r'\n\s*\n', text) if len(s.strip()) > 50]
        if len(sections) < 2:
            sections = [text]  # Use whole document as one chunk

    # Clear old index entries for this project
    vector_store.delete_project(project_id)

    # Index each section
    indexed = 0
    for i, section in enumerate(sections):
        content = section.strip()
        if len(content) < 50:
            continue
        # Use first line as title, rest as content
        lines = content.split('\n', 1)
        title = lines[0].strip()[:100] if lines else f"Section {i+1}"
        body = lines[1].strip() if len(lines) > 1 else content
        if len(body) < 30:
            body = content

        ok = vector_store.index_chapter(
            chapter_id=f"{project_id}_sec_{i}",
            project_id=project_id,
            title=title,
            content=body,
            metadata={"source": "history", "project_name": project.name},
        )
        if ok:
            indexed += 1

    return {
        "project_id": project_id,
        "project_name": project.name,
        "sections_indexed": indexed,
        "total_chars": len(text),
    }


@router.post("/rebuild-index")
async def rebuild_index():
    """Rebuild the vector index from all projects with generated chapters.

    Queries every project that has generated chapter content, clears the
    existing collection, and re-indexes all chapters. This is a potentially
    long-running operation.
    """
    if not vector_store.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector store is not available",
        )

    async with async_session() as db:
        # Fetch all projects that have chapters with generated content
        result = await db.execute(
            select(ProjectChapter)
            .where(ProjectChapter.ai_generated_content != "")
            .order_by(ProjectChapter.project_id, ProjectChapter.order_index)
        )
        all_chapters = result.scalars().all()

        if not all_chapters:
            return {"message": "No chapters to index", "indexed": 0}

        chapters_data = [
            {
                "id": c.id,
                "project_id": c.project_id,
                "title": c.title,
                "content": c.ai_generated_content,
            }
            for c in all_chapters
        ]

        indexed = vector_store.rebuild_index(chapters_data)
        return {"message": f"Index rebuilt: {indexed} chapters indexed", "indexed": indexed}


# ---------------------------------------------------------------------------
# POST /check-ai-traces  (Anti-AI trace analysis for existing chapters)
# ---------------------------------------------------------------------------


@router.post("/check-ai-traces")
async def check_ai_traces(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run anti-AI trace analysis on a project's chapters.

    Request body: {"project_id": "..."} or {"chapter_ids": ["...", "..."]}

    Returns per-chapter scores and overall project verdict.
    """
    # -- Load chapters --
    if "chapter_ids" in data and data["chapter_ids"]:
        result = await db.execute(
            select(ProjectChapter)
            .where(ProjectChapter.id.in_(data["chapter_ids"]))
            .order_by(ProjectChapter.order_index)
        )
    elif "project_id" in data:
        result = await db.execute(
            select(ProjectChapter)
            .where(ProjectChapter.project_id == data["project_id"])
            .order_by(ProjectChapter.order_index)
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide project_id or chapter_ids",
        )

    chapters = result.scalars().all()
    if not chapters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No chapters found",
        )

    # -- Run analysis --
    chapters_data = [
        {
            "id": c.id,
            "title": c.title,
            "content": c.final_content or c.ai_generated_content,
        }
        for c in chapters
    ]
    reports = analyze_project_chapters(chapters_data)

    # -- Build response --
    report_dicts = [report_to_dict(r) for r in reports]
    overall_score = round(
        sum(r["scores"]["overall"] for r in report_dicts) / max(len(report_dicts), 1),
        1,
    )
    worst_chapter = max(report_dicts, key=lambda r: r["scores"]["overall"]) if report_dicts else None

    return {
        "project_id": data.get("project_id", ""),
        "chapters_count": len(report_dicts),
        "overall_score": overall_score,
        "worst_chapter": worst_chapter,
        "chapters": report_dicts,
    }


# ---------------------------------------------------------------------------
# POST /analyze-edits  (Edit intent analysis)
# ---------------------------------------------------------------------------


@router.post("/analyze-edits")
async def analyze_edits(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Analyze edit intent by comparing AI-generated vs human-edited content.

    Request body: {"project_id": "..."} or {"chapter_ids": ["...", "..."]}

    For each chapter that has both ai_generated_content and final_content,
    computes a paragraph-level diff and uses AI to classify each edit's intent.
    Returns edit type counts, suggested writing rules, and per-segment details.
    """
    # -- Load chapters --
    if "chapter_ids" in data and data["chapter_ids"]:
        result = await db.execute(
            select(ProjectChapter)
            .where(ProjectChapter.id.in_(data["chapter_ids"]))
            .order_by(ProjectChapter.order_index)
        )
    elif "project_id" in data:
        result = await db.execute(
            select(ProjectChapter)
            .where(ProjectChapter.project_id == data["project_id"])
            .order_by(ProjectChapter.order_index)
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide project_id or chapter_ids",
        )

    chapters = result.scalars().all()
    if not chapters:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No chapters found",
        )

    # -- Filter to chapters that have BOTH AI and final content --
    chapters_data = [
        {
            "id": c.id,
            "title": c.title,
            "ai_generated_content": c.ai_generated_content,
            "final_content": c.final_content,
        }
        for c in chapters
        if c.ai_generated_content and c.final_content
    ]

    if not chapters_data:
        return {
            "message": "No chapters with both AI-generated and edited content found",
            "chapters_analyzed": 0,
            "results": [],
        }

    # -- Run analysis --
    analyses = []
    for ch in chapters_data:
        try:
            analysis = await analyze_chapter_edits(
                chapter_id=ch["id"],
                chapter_title=ch["title"],
                ai_generated_content=ch["ai_generated_content"],
                final_content=ch["final_content"],
                use_ai=data.get("use_ai", True),
            )
            analyses.append(edit_analysis_to_dict(analysis))
        except Exception as exc:
            logger.warning("Edit analysis failed for chapter %s: %s", ch["id"], exc)
            analyses.append({
                "chapter_id": ch["id"],
                "chapter_title": ch["title"],
                "error": str(exc),
            })

    # -- Aggregate across all chapters --
    type_totals: dict = {}
    all_rules: list = []
    total_changes = 0
    for a in analyses:
        if "edit_type_counts" in a:
            for t, c in a["edit_type_counts"].items():
                type_totals[t] = type_totals.get(t, 0) + c
            total_changes += a.get("total_changes", 0)
            if a.get("suggested_rules"):
                for rule in a["suggested_rules"]:
                    if rule not in all_rules:
                        all_rules.append(rule)

    return {
        "project_id": data.get("project_id", ""),
        "chapters_analyzed": len(analyses),
        "total_changes": total_changes,
        "edit_type_totals": type_totals,
        "suggested_rules": all_rules,
        "results": analyses,
    }
