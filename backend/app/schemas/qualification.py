"""宏曦标书 - Qualification Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class QualificationCreate(BaseModel):
    """Schema for creating a qualification record."""

    name: str = Field(max_length=200)
    cert_number: str = Field(default="", max_length=100)
    issuing_authority: str = Field(default="", max_length=200)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    attachment_path: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=5000)


class QualificationUpdate(BaseModel):
    """Schema for updating a qualification record (all fields optional)."""

    name: Optional[str] = Field(default=None, max_length=200)
    cert_number: Optional[str] = Field(default=None, max_length=100)
    issuing_authority: Optional[str] = Field(default=None, max_length=200)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    attachment_path: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=5000)


class QualificationRead(BaseModel):
    """Schema for reading qualification data (returned by API)."""

    id: str
    name: str
    cert_number: str
    issuing_authority: str
    issue_date: Optional[date]
    expiry_date: Optional[date]
    attachment_path: str
    notes: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
