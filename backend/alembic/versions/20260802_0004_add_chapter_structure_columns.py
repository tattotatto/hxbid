"""add chapter_type, chapter_meta_json, children_json to project_chapters;
add chapter_structure_json to bid_projects

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-02

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add chapter_structure_json to bid_projects
    op.add_column(
        "bid_projects",
        sa.Column(
            "chapter_structure_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
            comment="用户确认锁定的章节结构JSON",
        ),
    )

    # 2. Add new columns to project_chapters
    op.add_column(
        "project_chapters",
        sa.Column(
            "chapter_type",
            sa.String(20),
            nullable=False,
            server_default="text",
            comment="章节类型: fixed_form|table|ai_generated|attachment|mixed",
        ),
    )
    op.add_column(
        "project_chapters",
        sa.Column(
            "chapter_meta_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
            comment="章节元数据JSON",
        ),
    )
    op.add_column(
        "project_chapters",
        sa.Column(
            "children_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
            comment="子章节树JSON",
        ),
    )
    op.add_column(
        "project_chapters",
        sa.Column(
            "review_status",
            sa.String(20),
            nullable=False,
            server_default="pending_review",
            comment="审核状态: pending_review|locked|refining|generating|generated",
        ),
    )


def downgrade() -> None:
    op.drop_column("project_chapters", "review_status")
    op.drop_column("project_chapters", "children_json")
    op.drop_column("project_chapters", "chapter_meta_json")
    op.drop_column("project_chapters", "chapter_type")
    op.drop_column("bid_projects", "chapter_structure_json")
