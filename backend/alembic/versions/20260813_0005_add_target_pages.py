"""add target_pages to bid_projects

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-13

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 目标页数 — 生成篇幅按此规划（默认 2000 页）
    op.add_column(
        "bid_projects",
        sa.Column(
            "target_pages",
            sa.Integer(),
            nullable=False,
            server_default="2000",
            comment="目标页数 — 生成篇幅按此规划（默认 2000 页）",
        ),
    )


def downgrade() -> None:
    op.drop_column("bid_projects", "target_pages")
