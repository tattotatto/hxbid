"""宏曦标书 - Company Profile Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CompanyUpdate(BaseModel):
    """Payload for creating/updating the company profile."""
    company_name: Optional[str] = Field(None, max_length=300)
    business_license_number: Optional[str] = Field(None, max_length=100)
    legal_rep_name: Optional[str] = Field(None, max_length=100)
    legal_rep_id_number: Optional[str] = Field(None, max_length=18)
    address: Optional[str] = Field(None, max_length=500)
    contact_phone: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = None
    website: Optional[str] = Field(None, max_length=200)


class CompanyRead(BaseModel):
    """Response model for company profile."""
    id: str
    company_name: str
    business_license_number: str
    business_license_image: str
    legal_rep_name: str
    legal_rep_id_number: str
    legal_rep_id_front_image: str
    legal_rep_id_back_image: str
    address: str
    contact_phone: str
    notes: str
    logo_image: str
    website: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
