"""宏曦标书 - Personnel Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ExperienceCreate(BaseModel):
    """Schema for creating a personnel experience entry."""

    start_date: Optional[date] = None
    end_date: Optional[date] = None
    organization: str = Field(default="", max_length=200)
    position: str = Field(default="", max_length=200)
    project_scale: str = Field(default="", max_length=200)
    responsibilities: str = Field(default="", max_length=5000)
    achievements: str = Field(default="", max_length=5000)


class ExperienceRead(ExperienceCreate):
    """Schema for reading a personnel experience entry."""

    id: str
    personnel_id: str

    model_config = {"from_attributes": True}


class CertificateCreate(BaseModel):
    """Schema for creating a personnel certificate entry."""

    cert_name: str = Field(max_length=200)
    cert_number: str = Field(default="", max_length=100)
    issuing_authority: str = Field(default="", max_length=200)
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None


class CertificateRead(CertificateCreate):
    """Schema for reading a personnel certificate entry."""

    id: str
    personnel_id: str

    model_config = {"from_attributes": True}


class PersonnelCreate(BaseModel):
    """Schema for creating a personnel record with nested experiences and certificates."""

    name: str = Field(max_length=50)
    id_card: str = Field(default="", max_length=18)
    education: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=20)
    tags: str = Field(default="", max_length=500)
    experiences: List[ExperienceCreate] = Field(default_factory=list)
    certificates: List[CertificateCreate] = Field(default_factory=list)


class PersonnelUpdate(BaseModel):
    """Schema for updating a personnel record (basic fields only)."""

    name: Optional[str] = Field(default=None, max_length=50)
    id_card: Optional[str] = Field(default=None, max_length=18)
    education: Optional[str] = Field(default=None, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)
    tags: Optional[str] = Field(default=None, max_length=500)


class PersonnelRead(BaseModel):
    """Schema for reading personnel data (returned by API)."""

    id: str
    name: str
    education: str
    phone: str
    tags: str
    experiences: List[ExperienceRead] = []
    certificates: List[CertificateRead] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
