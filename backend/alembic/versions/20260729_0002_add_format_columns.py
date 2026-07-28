"""add format_template and format_verification to bid_projects

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_projects",
        sa.Column(
            "format_template_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="格式模板JSON — 从招标文件'投标文件格式'章节提取的结构化格式定义",
        ),
    )
    op.add_column(
        "bid_projects",
        sa.Column(
            "format_verification_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="格式校验报告JSON — 最近一次生成后的格式合规校验结果",
        ),
    )


def downgrade() -> None:
    op.drop_column("bid_projects", "format_verification_json")
    op.drop_column("bid_projects", "format_template_json")
