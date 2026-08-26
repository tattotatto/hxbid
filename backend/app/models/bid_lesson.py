"""宏曦标书 - 历史标书配对学习 Model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BidLesson(Base):
    """历史标书「招标 + 标书」配对及其学习报告."""

    __tablename__ = "bid_lessons"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="upload"
    )  # upload | project
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_projects.id", ondelete="SET NULL"), nullable=True
    )
    tender_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    bid_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lesson_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<BidLesson(id={self.id!r}, name={self.name!r}, status={self.status!r})>"
