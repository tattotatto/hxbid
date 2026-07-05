"""宏曦标书 - Personnel Models.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import uuid
from datetime import datetime, date

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Personnel(Base):
    """Staff member whose experience and certificates can be used in bids."""

    __tablename__ = "personnel"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    id_card: Mapped[str] = mapped_column(
        String(18),
        nullable=False,
        default="",
    )
    gender: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="",
    )
    education: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="",
    )
    address: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    tags: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    id_valid_from: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="",
    )
    id_valid_to: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="",
    )
    id_front_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    id_back_image: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="",
    )
    health_report_images_json: Mapped[str] = mapped_column(
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

    # Relationships
    experiences: Mapped[list["PersonnelExperience"]] = relationship(
        "PersonnelExperience",
        back_populates="personnel",
        cascade="all, delete-orphan",
    )
    certificates: Mapped[list["PersonnelCertificate"]] = relationship(
        "PersonnelCertificate",
        back_populates="personnel",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Personnel(id={self.id!r}, name={self.name!r})>"


class PersonnelExperience(Base):
    """Work experience entry for a personnel."""

    __tablename__ = "personnel_experiences"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("personnel.id", ondelete="CASCADE"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=True,
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=True,
    )
    organization: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    position: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="",
    )
    project_scale: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="",
    )
    responsibilities: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )
    achievements: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    # Relationships
    personnel: Mapped["Personnel"] = relationship(
        "Personnel",
        back_populates="experiences",
    )

    def __repr__(self) -> str:
        return f"<PersonnelExperience(id={self.id!r}, organization={self.organization!r})>"


class PersonnelCertificate(Base):
    """Professional certificate held by a personnel."""

    __tablename__ = "personnel_certificates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    personnel_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("personnel.id", ondelete="CASCADE"),
        nullable=False,
    )
    cert_name: Mapped[str] = mapped_column(
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

    # Relationships
    personnel: Mapped["Personnel"] = relationship(
        "Personnel",
        back_populates="certificates",
    )

    def __repr__(self) -> str:
        return f"<PersonnelCertificate(id={self.id!r}, cert_name={self.cert_name!r})>"
