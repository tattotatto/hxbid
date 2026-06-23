"""宏曦标书 - AI Generation API Routes.

Provides upload-and-parse, SSE streaming generation, export, and download
endpoints that wire together the full AI bid-document pipeline.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import asyncio
import json
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
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.schemas.bid import ExportRequest, ExportResponse, GenerateRequest, ParseResponse
from app.services.ai_pipeline import (
    generate_chapter_with_materials,
    generate_outline,
    parse_bid_requirements,
)
from app.services.anti_ai import analyze_chapter, analyze_project_chapters, report_to_dict
from app.services.document_parser import parse_document
from app.services.edit_analyzer import analyze_chapter_edits, edit_analysis_to_dict
from app.services.notification import send_notification
from app.services.rag import assemble_chapter_context
from app.services.render_engine import export_to_pdf, render_bid_to_docx
from app.services.vector_store import vector_store
from app.utils.permissions import require_editor
from app.utils.security import get_current_user
from app.models.template import BidTemplate

router = APIRouter()


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
        status="parsed",
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
                    source_summary = {}

                    try:
                        similar_chapters, matched_qualifications, matched_personnel, source_summary = \
                            await assemble_chapter_context(
                                chapter_title=ch["title"],
                                requirements=requirements,
                                project_id=project_id,
                                db=gen_db,
                            )
                    except Exception:
                        source_summary = {"similar_count": 0, "qual_count": 0, "personnel_count": 0}

                    # Emit RAG source info to frontend
                    yield {
                        "event": "rag_sources",
                        "data": json.dumps(
                            {
                                "chapter_id": ch["id"],
                                **source_summary,
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

    # -- Render .docx --
    docx_path = render_bid_to_docx(chapters_payload, project.name, style_config=style_config)
    docx_filename = Path(docx_path).name

    # -- Optionally render .pdf --
    pdf_path: str | None = None
    if data.format in ("pdf", "both"):
        pdf_path = export_to_pdf(docx_path)

    # -- Mark project as exported --
    project.status = "exported"
    await db.flush()

    # -- Build download URLs relative to the API prefix --
    base = f"{request.base_url}api/v1/bid/download/"
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
