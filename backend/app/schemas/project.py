"""宏曦标书 - Project Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(max_length=300)
    bid_deadline: Optional[date] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=300)
    bid_deadline: Optional[date] = None
    status: Optional[str] = Field(default=None, max_length=20)
    bid_result: Optional[str] = Field(default=None, max_length=20)


class ChapterRead(BaseModel):
    id: str
    title: str
    order_index: int
    ai_generated_content: str
    final_content: str
    status: str
    model_config = {"from_attributes": True}


class ChapterUpdate(BaseModel):
    final_content: Optional[str] = None
    status: Optional[str] = None


class ProjectRead(BaseModel):
    id: str
    name: str
    bid_deadline: Optional[date]
    status: str
    bid_result: str
    parsed_requirements_json: str
    outline_json: str
    chapters: List[ChapterRead] = []
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
