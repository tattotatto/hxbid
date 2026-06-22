"""宏曦标书 - Bid Project Models.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BidProject(Base):
    """A bid project being prepared by the application."""

    __tablename__ = "bid_projects"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )
    bid_deadline: Mapped[date] = mapped_column(
        Date,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
    )
    bid_result: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    original_file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    parsed_requirements_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
    )
    outline_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
    )
    created_by: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    # Relationships
    chapters: Mapped[list["ProjectChapter"]] = relationship(
        "ProjectChapter",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<BidProject(id={self.id!r}, name={self.name!r}, status={self.status!r})>"


class ProjectChapter(Base):
    """A chapter / section within a bid project."""

    __tablename__ = "project_chapters"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
    )
    order_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    ai_generated_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    final_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )

    # Relationships
    project: Mapped["BidProject"] = relationship(
        "BidProject",
        back_populates="chapters",
    )

    def __repr__(self) -> str:
        return f"<ProjectChapter(id={self.id!r}, title={self.title!r}, status={self.status!r})>"
