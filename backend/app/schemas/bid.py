"""宏曦标书 - AI Generation Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from typing import Optional, List

from pydantic import BaseModel, Field


class ParseResponse(BaseModel):
    project_id: str = ""
    project_name: str = ""
    requirements: dict = {}
    format_template: dict | None = None


class GenerateRequest(BaseModel):
    project_id: str
    regenerate_chapter_ids: Optional[List[str]] = None
    target_pages: Optional[int] = Field(default=None, ge=100, le=10000)


class ExportRequest(BaseModel):
    project_id: str
    format: str = "docx"  # "docx" | "pdf" | "both"
    chapter_ids: Optional[List[str]] = None
    template_id: Optional[str] = None
    include_checklist: bool = True  # 新增：默认导出检查清单
    checklist_items: Optional[List[dict]] = None  # 新增：自定义行 [{key,label}...]
    checklist_removed: Optional[List[str]] = None  # 新增：被删除的预置行 key


class RetryFailedRequest(BaseModel):
    project_id: str
    section_paths: Optional[List[str]] = None  # if None, retry all failed sections


class ExportResponse(BaseModel):
    docx_url: str = ""
    pdf_url: str = ""
    checklist_docx_url: str = ""  # 新增：清单失败/未启用时为空串
    checklist_pdf_url: str = ""  # 新增


class FormatVerificationCheck(BaseModel):
    check: str = ""
    item: str = ""
    status: str = ""  # "pass" | "warning" | "fail"
    detail: str = ""
    can_auto_fix: bool = False


class FormatVerificationResult(BaseModel):
    overall_status: str = "pass"  # "pass" | "pass_with_warnings" | "fail"
    checks: list[dict] = []
    auto_fixes_applied: int = 0
    manual_review_required: int = 0
    message: str = ""
