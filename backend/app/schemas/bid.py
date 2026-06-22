"""宏曦标书 - AI Generation Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from typing import Optional, List

from pydantic import BaseModel, Field


class ParseResponse(BaseModel):
    project_name: str = ""
    requirements: dict = {}


class GenerateRequest(BaseModel):
    project_id: str
    regenerate_chapter_ids: Optional[List[str]] = None


class ExportRequest(BaseModel):
    project_id: str
    format: str = "docx"  # "docx" | "pdf" | "both"
    chapter_ids: Optional[List[str]] = None


class ExportResponse(BaseModel):
    docx_url: str = ""
    pdf_url: str = ""
