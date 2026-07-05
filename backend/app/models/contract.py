"""宏曦标书 - Historical Contract Model.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Contract(Base):
    """A historical contract stored in the resource library."""

    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )
    procurement_unit: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        default="",
    )
    procurement_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    contract_amount: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    service_period: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    # JSON array of image file paths, e.g. ["uploads/contract_abc_page1.png", ...]
    image_paths_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
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
        return f"<Contract(id={self.id!r}, project_name={self.project_name!r})>"
