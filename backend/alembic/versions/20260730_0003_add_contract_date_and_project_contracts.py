"""add contract_date to contracts and create project_contracts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add contract_date to contracts
    op.add_column(
        "contracts",
        sa.Column(
            "contract_date",
            sa.Date(),
            nullable=True,
            comment="合同签订日期，用于按时间范围筛选业绩合同",
        ),
    )

    # 2. Create project_contracts table
    op.create_table(
        "project_contracts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.String(36),
            sa.ForeignKey("contracts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requirement_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="matched"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("project_contracts")
    op.drop_column("contracts", "contract_date")
