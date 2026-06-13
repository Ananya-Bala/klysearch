"""Phase 3A: research_reports table

Revision ID: 0003_phase3a_research_reports
Revises: 0002_phase2_tables
Create Date: 2025-01-01 00:00:00

Tables added:
    research_reports    — stores AI-generated institutional research reports

New enum types:
    reportstatus        — pending | complete | failed

Design notes:
    report_data is TEXT (JSON) not JSONB for SQLite/PostgreSQL portability.
    Composite index on (organization_id, ticker, generated_at) for cache lookups.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase3a_research_reports"
down_revision: Union[str, None] = "0002_phase2_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    reportstatus_enum = sa.Enum("pending", "complete", "failed", name="reportstatus")
    reportstatus_enum.create(op.get_bind())

    op.create_table(
        "research_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "complete", "failed", name="reportstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("report_data", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_research_reports_id", "research_reports", ["id"])
    op.create_index("ix_research_reports_ticker", "research_reports", ["ticker"])
    op.create_index("ix_research_reports_organization_id", "research_reports", ["organization_id"])
    op.create_index(
        "ix_research_reports_org_ticker_date",
        "research_reports",
        ["organization_id", "ticker", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_reports_org_ticker_date", table_name="research_reports")
    op.drop_index("ix_research_reports_organization_id", table_name="research_reports")
    op.drop_index("ix_research_reports_ticker", table_name="research_reports")
    op.drop_index("ix_research_reports_id", table_name="research_reports")
    op.drop_table("research_reports")
    sa.Enum(name="reportstatus").drop(op.get_bind())
