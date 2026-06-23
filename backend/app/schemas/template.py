"""宏曦标书 - Template Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    """Payload for creating a new template."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    style_config: Dict[str, Any] = Field(default_factory=dict)


class TemplateUpdate(BaseModel):
    """Payload for updating an existing template."""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    style_config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None


class TemplateRead(BaseModel):
    """Response model for a template."""
    id: str
    name: str
    description: str
    style_config: Dict[str, Any]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
