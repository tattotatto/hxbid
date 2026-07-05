"""宏曦标书 - Contract Pydantic Schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ContractCreate(BaseModel):
    """Schema for creating a contract (step 1 — metadata only)."""

    project_name: str = Field(min_length=1, max_length=300)
    procurement_unit: str = Field(default="", max_length=300)
    procurement_content: str = Field(default="", max_length=2000)
    contract_amount: str = Field(default="", max_length=100)
    service_period: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)


class ContractUpdate(BaseModel):
    """Schema for updating contract metadata."""

    project_name: Optional[str] = Field(None, max_length=300)
    procurement_unit: Optional[str] = Field(None, max_length=300)
    procurement_content: Optional[str] = Field(None, max_length=2000)
    contract_amount: Optional[str] = Field(None, max_length=100)
    service_period: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=2000)


class ContractRead(BaseModel):
    """Schema for reading contract data."""

    id: str
    project_name: str
    procurement_unit: str
    procurement_content: str
    contract_amount: str
    service_period: str
    notes: str
    image_paths_json: str  # JSON array string
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
