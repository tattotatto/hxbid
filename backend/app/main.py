"""宏曦标书 - FastAPI Application Entry Point.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.router import api_router
from app.config import settings
from app.database import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create required directories and database tables on startup."""
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)
    os.makedirs(settings.TEMPLATE_DIR, exist_ok=True)

    # Auto-create tables (idempotent - skips existing)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(api_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Uploaded file serving (images, attachments)
# ---------------------------------------------------------------------------


@app.get("/uploads/{file_path:path}")
async def serve_upload(file_path: str):
    """Serve uploaded files (images, attachments) from UPLOAD_DIR.

    This replaces StaticFiles mount which has issues with Docker volume paths.
    """
    full_path = os.path.join(settings.UPLOAD_DIR, file_path)
    # Prevent directory traversal
    real_path = os.path.realpath(full_path)
    real_upload = os.path.realpath(settings.UPLOAD_DIR)
    if not real_path.startswith(real_upload):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    if not os.path.isfile(real_path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    return FileResponse(real_path)


@app.get("/")
async def root():
    """Root health-check endpoint."""
    return {"name": settings.APP_NAME, "version": settings.APP_VERSION}
