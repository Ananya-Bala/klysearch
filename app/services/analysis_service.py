"""
services/analysis_service.py
------------------------------
Orchestrates the complete Phase 3A analysis pipeline:

    fetch_market_data → fetch_news → analyze_sentiment → generate_report → persist

This is the single function that routes.py calls.
All sub-services are called here in sequence. Any failure at any step
is caught, persisted as a FAILED report, and surfaced as a clean error.

Cache behaviour:
    If a COMPLETE report for the same (ticker, organization_id) exists and
    is younger than REPORT_CACHE_MINUTES, it is returned immediately without
    calling OpenAI. This keeps costs predictable and response times fast
    for the common "view my earlier NVDA report" pattern.

    `force_refresh=True` bypasses the cache — useful for admins who want
    a fresh analysis after a major news event.

Multi-tenancy:
    organization_id is always sourced from the caller's JWT.
    Each org gets its own report records — Org A cannot read Org B's reports.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.research_report import ResearchReport, ReportStatus
from app.schemas.research_report import ReportOutput, ReportPublic
from app.services.ai_research_service import AIResearchError, generate_research_report
from app.services.market_data_service import MarketSnapshot, fetch_market_data
from app.services.news_service import fetch_news
from app.services.sentiment_service import analyze_news_sentiment

logger = logging.getLogger(__name__)


class AnalysisError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _get_cached_report(
    db: Session,
    ticker: str,
    organization_id: int,
) -> ResearchReport | None:
    """
    Return a recent COMPLETE report for this (ticker, org) if one exists
    within the REPORT_CACHE_MINUTES window, else None.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=settings.REPORT_CACHE_MINUTES
    )
    return (
        db.query(ResearchReport)
        .filter(
            ResearchReport.ticker == ticker.upper(),
            ResearchReport.organization_id == organization_id,
            ResearchReport.status == ReportStatus.COMPLETE,
            ResearchReport.generated_at >= cutoff,
        )
        .order_by(ResearchReport.generated_at.desc())
        .first()
    )


def _deserialise_report(row: ResearchReport) -> ReportOutput | None:
    """Parse stored JSON back into a typed ReportOutput."""
    if not row.report_data:
        return None
    try:
        return ReportOutput.model_validate_json(row.report_data)
    except Exception as exc:
        logger.error("Failed to deserialise report %d: %s", row.id, exc)
        return None


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_analysis(
    db: Session,
    ticker: str,
    organization_id: int,
    requested_by_id: int,
    force_refresh: bool = False,
) -> ReportPublic:
    """
    Full analysis pipeline. Returns a ReportPublic to the route layer.

    Steps:
        0. Check cache (skip if force_refresh)
        1. Fetch market data from yfinance
        2. Fetch news (NewsAPI, optional)
        3. Compute sentiment (TextBlob)
        4. Generate AI report (OpenAI)
        5. Persist to DB
        6. Return ReportPublic

    On any unrecoverable error:
        - Persists a FAILED record to the DB for audit
        - Re-raises as AnalysisError so the route returns a clean HTTP error
    """
    ticker_upper = ticker.strip().upper()

    # ── Step 0: Cache check ───────────────────────────────────────────────────
    if not force_refresh:
        cached = _get_cached_report(db, ticker_upper, organization_id)
        if cached:
            logger.info("Cache hit for %s (report_id=%d)", ticker_upper, cached.id)
            report_out = _deserialise_report(cached)
            return ReportPublic(
                report_id=cached.id,
                ticker=cached.ticker,
                company_name=cached.company_name,
                status=cached.status.value,
                generated_at=cached.generated_at,
                cached=True,
                report=report_out,
            )

    # ── Create a PENDING record immediately ───────────────────────────────────
    # Gives the admin a record to query even if the pipeline fails mid-way.
    pending = ResearchReport(
        ticker=ticker_upper,
        status=ReportStatus.PENDING,
        organization_id=organization_id,
        requested_by_id=requested_by_id,
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)

    try:
        # ── Step 1: Market data ───────────────────────────────────────────────
        logger.info("Fetching market data for %s", ticker_upper)
        try:
            snap: MarketSnapshot = fetch_market_data(ticker_upper)
        except ValueError as exc:
            raise AnalysisError(str(exc), status_code=404)

        # Update company name on the pending record
        pending.company_name = snap.company_name
        db.commit()

        # ── Step 2: News ──────────────────────────────────────────────────────
        logger.info("Fetching news for %s", ticker_upper)
        articles = fetch_news(ticker_upper, snap.company_name)

        # ── Step 3: Sentiment ─────────────────────────────────────────────────
        logger.info("Computing sentiment for %s (%d articles)", ticker_upper, len(articles))
        sentiment = analyze_news_sentiment(articles)

        # ── Step 4: AI report generation ──────────────────────────────────────
        logger.info("Generating AI report for %s", ticker_upper)
        generated_at = datetime.now(timezone.utc)

        try:
            report_out, prompt_tokens, completion_tokens = generate_research_report(
                snap=snap,
                sentiment=sentiment,
                generated_at=generated_at,
            )
        except AIResearchError as exc:
            raise AnalysisError(exc.message, status_code=exc.status_code)

        # ── Step 5: Persist ───────────────────────────────────────────────────
        pending.status            = ReportStatus.COMPLETE
        pending.report_data       = report_out.model_dump_json()
        pending.prompt_tokens     = prompt_tokens
        pending.completion_tokens = completion_tokens
        pending.generated_at      = generated_at
        db.commit()
        db.refresh(pending)

        logger.info(
            "Report complete for %s (id=%d, tokens=%d+%d)",
            ticker_upper, pending.id, prompt_tokens, completion_tokens,
        )

        # ── Step 6: Return ────────────────────────────────────────────────────
        return ReportPublic(
            report_id=pending.id,
            ticker=pending.ticker,
            company_name=pending.company_name,
            status=pending.status.value,
            generated_at=pending.generated_at,
            cached=False,
            report=report_out,
        )

    except AnalysisError:
        # Mark the DB record as failed before re-raising
        pending.status        = ReportStatus.FAILED
        pending.error_message = str(pending.status)
        db.commit()
        raise

    except Exception as exc:
        # Unexpected error — mark failed, log, and surface a generic message
        logger.error("Unexpected pipeline error for %s: %s", ticker_upper, exc, exc_info=True)
        pending.status        = ReportStatus.FAILED
        pending.error_message = str(exc)
        db.commit()
        raise AnalysisError(
            f"Analysis pipeline failed: {exc}",
            status_code=500,
        )


def get_report_by_id(
    db: Session,
    report_id: int,
    organization_id: int,
) -> ReportPublic:
    """
    Fetch a previously generated report by its ID.
    Always filters by organization_id — cross-org access returns 404.
    """
    row = (
        db.query(ResearchReport)
        .filter(
            ResearchReport.id == report_id,
            ResearchReport.organization_id == organization_id,
        )
        .first()
    )
    if not row:
        raise AnalysisError("Report not found.", status_code=404)

    return ReportPublic(
        report_id=row.id,
        ticker=row.ticker,
        company_name=row.company_name,
        status=row.status.value,
        generated_at=row.generated_at,
        cached=True,
        report=_deserialise_report(row),
    )


def list_reports(
    db: Session,
    organization_id: int,
    ticker: str | None = None,
) -> list[ResearchReport]:
    """
    List all COMPLETE reports for an org, optionally filtered by ticker.
    """
    q = (
        db.query(ResearchReport)
        .filter(
            ResearchReport.organization_id == organization_id,
            ResearchReport.status == ReportStatus.COMPLETE,
        )
    )
    if ticker:
        q = q.filter(ResearchReport.ticker == ticker.upper())

    return q.order_by(ResearchReport.generated_at.desc()).all()
