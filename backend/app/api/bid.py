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
from app.services.document_parser import parse_document
from app.services.notification import send_notification
from app.services.render_engine import export_to_pdf, render_bid_to_docx
from app.utils.security import get_current_user

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
    text = parse_document(str(saved_path))

    # -- AI requirements extraction --
    requirements = await parse_bid_requirements(text)

    # -- Create project record --
    project = BidProject(
        name=project_name or requirements.get("project_name") or "未命名项目",
        original_file_path=str(saved_path.absolute()),
        parsed_requirements_json=json.dumps(requirements, ensure_ascii=False),
        status="parsed",
        created_by=current_user.id,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

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
                    try:
                        async for chunk in generate_chapter_with_materials(
                            chapter_title=ch["title"],
                            requirements=requirements,
                            matched_qualifications=[],
                            matched_personnel=[],
                            similar_chapters=[],
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
    docx_path = render_bid_to_docx(chapters_payload, project.name)
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
