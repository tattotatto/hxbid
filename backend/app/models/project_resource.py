"""宏曦标书 - Project Resource Association Models.

Links collected qualifications and personnel to specific bid projects
during the information-collection step.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProjectQualification(Base):
    """A qualification / certificate linked to a bid project.

    Created during the information-collection step.  May reference an
    existing Qualification from the resource library, or hold an
    independently uploaded file path.
    """

    __tablename__ = "project_qualifications"

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
    qualification_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("qualifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    requirement_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    match_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="missing",
        # Values: "matched" | "uploaded" | "missing"
    )
    uploaded_file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    project: Mapped["BidProject"] = relationship(  # noqa: F821
        back_populates="project_qualifications",
    )
    qualification: Mapped["Qualification | None"] = relationship(  # noqa: F821
        "Qualification",
        foreign_keys=[qualification_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectQualification(id={self.id!r},"
            f" requirement={self.requirement_name!r},"
            f" status={self.match_status!r})>"
        )


class ProjectPersonnel(Base):
    """A personnel assignment for a bid project.

    Created during the information-collection step.  Links a Personnel
    record to a project with a specific role (e.g. 项目负责人, 参与人).
    """

    __tablename__ = "project_personnel"

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
    personnel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("personnel.id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    requirement_desc: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        default="",
    )
    match_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="missing",
        # Values: "assigned" | "missing"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    project: Mapped["BidProject"] = relationship(  # noqa: F821
        back_populates="project_personnel",
    )
    personnel: Mapped["Personnel | None"] = relationship(  # noqa: F821
        "Personnel",
        foreign_keys=[personnel_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectPersonnel(id={self.id!r},"
            f" role={self.role!r},"
            f" status={self.match_status!r})>"
        )
