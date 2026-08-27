"""add scoring_rubric_json, scoring_report_json columns to bid_projects

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_projects",
        sa.Column("scoring_rubric_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "bid_projects",
        sa.Column("scoring_report_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("bid_projects", "scoring_report_json")
    op.drop_column("bid_projects", "scoring_rubric_json")