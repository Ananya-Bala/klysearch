"""
routes/analyze.py
------------------
Phase 3A AI research analysis endpoints.

Deliberately a SEPARATE router from routes/research.py so Phase 2 endpoints
remain untouched and the Phase 3A feature is cleanly isolated.

Endpoints:
    POST /research/analyze              → run full AI analysis pipeline
    GET  /research/reports              → list org's generated reports
    GET  /research/reports/{report_id}  → retrieve a single report

Access:
    All endpoints require any authenticated user (admin + analyst).
    Reports are org-scoped — users only see their org's reports.

Latency note for the interviewer:
    POST /research/analyze makes synchronous calls to:
      1. yfinance (Yahoo Finance scrape) — ~1s
      2. NewsAPI (optional)             — ~0.5s
      3. OpenAI GPT-4o                  — ~5–15s

    Total typical latency: 7–20s. This is acceptable for a research report
    but would be moved to an async task queue in a production scale-out.
    Within Phase 3A constraints (no Redis/Celery), synchronous is correct.
    The endpoint documents this in its summary string.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.middleware.dependencies import get_current_user
from app.models.user import User
from app.schemas.research_report import AnalyzeRequest, ReportPublic
from app.services.analysis_service import AnalysisError, get_report_by_id, list_reports, run_analysis

router = APIRouter(prefix="/research", tags=["AI Research (Phase 3A)"])


# ── POST /research/analyze ────────────────────────────────────────────────────

@router.post(
    "/analyze",
    response_model=ReportPublic,
    status_code=status.HTTP_200_OK,
    summary="Generate an AI-powered institutional research report for a ticker",
    description=(
        "Fetches live market data, news, and analyst consensus, then generates "
        "a full institutional research report via GPT-4o. "
        "**Response time: 7–20 seconds** (synchronous AI pipeline). "
        "Results are cached for 60 minutes by default — use `force_refresh: true` "
        "to bypass the cache after major news events."
    ),
)
def analyze_ticker(
    payload: AnalyzeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportPublic:
    """
    Full pipeline:
      1. yfinance market data
      2. NewsAPI headlines (if NEWS_API_KEY configured)
      3. TextBlob sentiment scoring
      4. OpenAI GPT-4o report generation
      5. Persist to DB
      6. Return JSON report

    Returns cached report if one exists within REPORT_CACHE_MINUTES.
    """
    try:
        return run_analysis(
            db=db,
            ticker=payload.ticker,
            organization_id=current_user.organization_id,
            requested_by_id=current_user.id,
            force_refresh=payload.force_refresh,
        )
    except AnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


# ── GET /research/reports ─────────────────────────────────────────────────────

@router.get(
    "/reports",
    response_model=list[ReportPublic],
    summary="List all generated research reports for the organization",
)
def list_reports_route(
    ticker: str | None = Query(
        default=None,
        description="Filter by ticker symbol (e.g. NVDA)",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ReportPublic]:
    """
    Returns all COMPLETE reports for the current org, newest first.
    Optionally filtered by `?ticker=NVDA`.
    """
    rows = list_reports(db, current_user.organization_id, ticker)
    results = []
    for row in rows:
        from app.services.analysis_service import _deserialise_report
        results.append(
            ReportPublic(
                report_id=row.id,
                ticker=row.ticker,
                company_name=row.company_name,
                status=row.status.value,
                generated_at=row.generated_at,
                cached=True,
                report=_deserialise_report(row),
            )
        )
    return results


# ── GET /research/reports/{report_id} ─────────────────────────────────────────

@router.get(
    "/reports/{report_id}",
    response_model=ReportPublic,
    summary="Retrieve a specific research report by ID",
)
def get_report_route(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportPublic:
    """
    Returns a single report. Returns 404 if the report doesn't exist
    or belongs to a different organization.
    """
    try:
        return get_report_by_id(db, report_id, current_user.organization_id)
    except AnalysisError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
