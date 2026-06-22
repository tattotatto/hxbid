"""宏曦标书 - Qualification Model.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import String, Text, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Qualification(Base):
    """Company qualification / certificate record."""

    __tablename__ = "qualifications"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    cert_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    issuing_authority: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    issue_date: Mapped[date] = mapped_column(
        Date,
        nullable=True,
    )
    expiry_date: Mapped[date] = mapped_column(
        Date,
        nullable=True,
    )
    attachment_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    notes: Mapped[str] = mapped_column(
        Text,
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
        return f"<Qualification(id={self.id!r}, name={self.name!r})>"
