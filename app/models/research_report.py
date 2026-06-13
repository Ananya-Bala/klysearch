"""
models/research_report.py
--------------------------
Stores AI-generated institutional research reports.

One ResearchReport is produced per (ticker, organization) analysis run.
The full structured JSON output from the AI pipeline is stored in
`report_data` (TEXT column containing JSON) rather than dozens of
individual columns — this keeps the schema stable as the report format
evolves without requiring migrations for every new field.

Retrieval pattern:
    Most queries are:
        - "latest report for ticker X in org Y"
        - "all reports for org Y, newest first"
    Both are covered by the composite index below.

Cache semantics:
    `status` tracks whether generation succeeded or failed.
    `generated_at` is compared against REPORT_CACHE_MINUTES so the route
    layer can decide whether to re-run the AI pipeline or return the
    cached report.

Relationship:
    ResearchReport → User (requested_by)
    ResearchReport → Organization (org-scoped)
    ResearchReport is NOT tied to a Workspace — it is org-level intelligence,
    not workspace-level. A future phase could add an optional workspace_id.
"""

import enum
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, DateTime, Enum as SAEnum, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base


class ReportStatus(str, enum.Enum):
    PENDING    = "pending"      # generation started
    COMPLETE   = "complete"     # report ready
    FAILED     = "failed"       # pipeline error; check error_message


class ResearchReport(Base):
    __tablename__ = "research_reports"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # The security being analysed — stored uppercase (NVDA, AAPL, TSLA…)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Full company name populated from yfinance (for display)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[ReportStatus] = mapped_column(
        SAEnum(ReportStatus, name="reportstatus"),
        nullable=False,
        default=ReportStatus.PENDING,
    )

    # ── Report payload ────────────────────────────────────────────────────────
    # JSON-serialised ReportOutput schema. Storing as TEXT (not JSONB) keeps us
    # compatible with SQLite in dev and PostgreSQL in prod without any driver change.
    # Parse with json.loads() on read; serialise with model.model_dump_json() on write.
    report_data: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Human-readable error for debugging failed runs
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token usage for cost tracking
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)

    # ── Multi-tenancy ─────────────────────────────────────────────────────────
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Alembic-managed timestamp
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", lazy="select"
    )
    requested_by: Mapped["User"] = relationship(  # noqa: F821
        "User", foreign_keys=[requested_by_id], lazy="select"
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    __table_args__ = (
        # Fast cache lookup: "latest NVDA report for org 5"
        Index("ix_research_reports_org_ticker_date", "organization_id", "ticker", "generated_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ResearchReport id={self.id} ticker={self.ticker!r} "
            f"org={self.organization_id} status={self.status}>"
        )
