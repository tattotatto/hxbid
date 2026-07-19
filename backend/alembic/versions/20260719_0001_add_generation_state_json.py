"""add generation_state_json to bid_projects

Revision ID: 0001
Revises: (none)
Create Date: 2026-07-19

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_projects",
        sa.Column(
            "generation_state_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bid_projects", "generation_state_json")
