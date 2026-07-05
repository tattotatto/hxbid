"""宏曦标书 - Company Profile Model.

Stores basic company information: name, business license, legal representative
details with ID card images.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyProfile(Base):
    """Single-row company profile (one company per instance)."""

    __tablename__ = "company_profile"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    company_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        default="",
    )
    business_license_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    business_license_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    legal_rep_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    legal_rep_id_number: Mapped[str] = mapped_column(
        String(18),
        nullable=False,
        default="",
    )
    legal_rep_id_front_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    legal_rep_id_back_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    contact_phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="",
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    logo_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    website: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<CompanyProfile(company_name={self.company_name!r})>"
