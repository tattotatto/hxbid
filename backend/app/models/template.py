"""宏曦标书 - Bid Template Model.

Stores reusable bid-document style configurations (fonts, margins, headers, etc.)
that users can create, edit, and apply when exporting.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BidTemplate(Base):
    """A saved bid-document style template."""

    __tablename__ = "bid_templates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    # JSON blob: {body_font_name, body_font_size_pt, body_line_spacing, ...}
    style_config_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
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
        return f"<BidTemplate(id={self.id!r}, name={self.name!r})>"
