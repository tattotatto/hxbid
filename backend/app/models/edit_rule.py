"""宏曦标书 - Edit Rule Model.

Stores accumulated writing rules extracted from edit intent analysis.
Rules that reach the upgrade threshold (≥3 occurrences of the same pattern)
are automatically promoted to active prompt constraints.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EditRule(Base):
    """A writing rule extracted from edit intent analysis.

    Rules start as 'pending' (count < threshold) and graduate to 'active'
    once the same edit type + pattern appears enough times.
    """

    __tablename__ = "edit_rules"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    rule_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    edit_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    # Normalized key for deduplication (e.g. "具体化不足:人员经历缺少具体数字")
    pattern_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        unique=True,
        index=True,
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    # pending → active once occurrence_count >= upgrade_threshold
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    # Source project IDs (for traceability)
    source_project_ids: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # The upgraded prompt text injected into future generations
    prompt_constraint: Mapped[str] = mapped_column(
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

    UPGRADE_THRESHOLD = 3

    def __repr__(self) -> str:
        return f"<EditRule(pattern_key={self.pattern_key!r}, count={self.occurrence_count}, status={self.status!r})>"
