"""宏曦标书 - Information Collection Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from typing import List, Optional

from pydantic import BaseModel


# ── Requirement / match models ──────────────────────────────────────────

class RequirementItem(BaseModel):
    """A single requirement extracted from the tender document."""
    name: str
    category: str               # "company" | "qualification" | "financial" | "other" | "personnel"
    details: Optional[str] = None


class ResourceMatch(BaseModel):
    """Auto-match result for one requirement."""
    requirement: RequirementItem
    matched: bool
    matches: List[dict] = []    # list of matching resources from the library
    match_status: str = "missing"  # "matched" | "partial" | "missing"


class CollectionStatus(BaseModel):
    """Full collection status for a project."""
    project_id: str
    status: str
    document_items: List[ResourceMatch] = []
    personnel_items: List[ResourceMatch] = []
    is_complete: bool = False


# ── Request models ──────────────────────────────────────────────────────

class AssignPersonnelRequest(BaseModel):
    personnel_id: str
    role: str
    requirement_desc: str = ""


class LinkQualificationRequest(BaseModel):
    qualification_id: str
    requirement_name: str = ""


# ── Response models ─────────────────────────────────────────────────────

class CollectedResource(BaseModel):
    id: str
    type: str  # "qualification" | "personnel"
    name: str
    detail: str = ""


class CollectedResources(BaseModel):
    qualifications: List[CollectedResource] = []
    personnel: List[dict] = []
