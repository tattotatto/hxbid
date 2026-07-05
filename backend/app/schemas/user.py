"""宏曦标书 - User Pydantic Schemas.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """Schema for user registration."""

    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=6, max_length=100)
    display_name: str = Field(default="", max_length=100)
    role: Optional[str] = None  # admin can specify role when creating


class UserRead(BaseModel):
    """Schema for reading user data (returned by API)."""

    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserLogin(BaseModel):
    """Schema for login credentials."""

    username: str
    password: str


class UserUpdate(BaseModel):
    """Schema for admin updating a user's role or active status."""

    display_name: Optional[str] = Field(None, max_length=100)
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, max_length=100)


class UserPasswordReset(BaseModel):
    """Schema for admin resetting a user's password."""

    password: str = Field(min_length=6, max_length=100)


class Token(BaseModel):
    """Schema for JWT token response."""

    access_token: str
    token_type: str = "bearer"
    user: UserRead
