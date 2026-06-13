"""
schemas/research_report.py
---------------------------
Pydantic v2 schemas for the Phase 3A AI research report pipeline.

Schema hierarchy:
    AnalyzeRequest          → POST /research/analyze  (request body)
    ─────────────────────────────────────────────────────────────────
    FundamentalData         → raw yfinance metrics + interpretation
    TechnicalData           → SMA/RSI/signals + interpretation
    NewsItem                → single article with sentiment
    NewsSentiment           → aggregated news intelligence
    AnalystConsensus        → buy/hold/sell ratings
    ScenarioCase            → bull / base / bear case
    ScenarioAnalysis        → all three scenarios + probabilities
    RiskItem                → individual risk with severity
    RiskAnalysis            → collection of risks
    TimingAnalysis          → entry timing recommendation
    AIReport                → full narrative text report
    ExecutiveSummary        → top-level recommendation block
    ─────────────────────────────────────────────────────────────────
    ReportOutput            → the complete assembled report (stored as JSON)
    ReportPublic            → envelope returned to the API caller

Design note on `model_dump_json()`:
    ReportOutput is serialised to a string and stored in research_reports.report_data.
    On read it's deserialised with `ReportOutput.model_validate_json(row.report_data)`.
    This lets us evolve the schema without a DB migration — old reports simply
    lack new optional fields, which Pydantic handles gracefully.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Request ───────────────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    ticker: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Stock ticker symbol, e.g. NVDA",
    )

    # Allow the caller to force a fresh analysis even within the cache window
    force_refresh: bool = Field(
        default=False,
        description="If true, bypass the report cache and re-run the AI pipeline.",
    )


# ── Sub-schemas ───────────────────────────────────────────────────────────────

class PriceTarget(BaseModel):
    three_months: float | None = None
    six_months: float | None = None
    twelve_months: float | None = None


class ExecutiveSummary(BaseModel):
    recommendation: Literal["Strong Buy", "Buy", "Hold", "Reduce", "Sell"]
    conviction_score: int = Field(..., ge=0, le=100)
    key_catalyst: str
    entry_zone: str          # e.g. "$420–$440"
    price_targets: PriceTarget


class FundamentalData(BaseModel):
    current_price: float | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None       # as decimal, e.g. 0.22 = 22%
    free_cash_flow: float | None = None
    debt_to_equity: float | None = None
    profit_margins: float | None = None
    interpretation: str                        # AI-generated paragraph


class TechnicalData(BaseModel):
    sma_50: float | None = None
    sma_200: float | None = None
    rsi: float | None = None
    volume_trend: str | None = None           # "increasing" | "decreasing" | "neutral"
    golden_cross: bool = False
    death_cross: bool = False
    overbought: bool = False                  # RSI > 70
    oversold: bool = False                    # RSI < 30
    interpretation: str


class NewsItem(BaseModel):
    title: str
    source: str
    date: str
    sentiment_score: float         # -1.0 (very negative) to +1.0 (very positive)
    sentiment_label: Literal["bullish", "bearish", "neutral"]


class NewsSentiment(BaseModel):
    overall_score: float           # average of article scores
    overall_label: Literal["bullish", "bearish", "neutral"]
    articles: list[NewsItem]
    top_positive_drivers: list[str]
    top_negative_drivers: list[str]


class AnalystConsensus(BaseModel):
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    mean_target_price: float | None = None
    summary: str


class ScenarioCase(BaseModel):
    target_price: float
    narrative: str


class ScenarioAnalysis(BaseModel):
    bull_case: ScenarioCase
    base_case: ScenarioCase
    bear_case: ScenarioCase
    bull_probability: int = Field(..., ge=0, le=100)
    base_probability: int = Field(..., ge=0, le=100)
    bear_probability: int = Field(..., ge=0, le=100)


class RiskItem(BaseModel):
    risk: str
    severity: Literal["Low", "Medium", "High", "Critical"]
    mitigation: str


class RiskAnalysis(BaseModel):
    risks: list[RiskItem]


class TimingAnalysis(BaseModel):
    should_buy_now: bool
    reasoning: str          # paragraph explaining RSI + MA + sentiment signals


class AIReport(BaseModel):
    investment_thesis: str
    growth_drivers: str
    risks: str
    valuation_view: str
    recommendation: str
    conclusion: str


# ── Top-level report output ───────────────────────────────────────────────────

class ReportOutput(BaseModel):
    """
    The complete, serialisable research report.
    Stored as JSON in research_reports.report_data.
    """
    ticker: str
    company_name: str | None
    generated_at: datetime

    executive_summary: ExecutiveSummary
    fundamentals: FundamentalData
    technicals: TechnicalData
    news_sentiment: NewsSentiment
    analyst_consensus: AnalystConsensus
    scenario_analysis: ScenarioAnalysis
    risk_analysis: RiskAnalysis
    timing_analysis: TimingAnalysis
    ai_report: AIReport

    model_config = ConfigDict(from_attributes=True)


# ── API response envelope ─────────────────────────────────────────────────────

class ReportPublic(BaseModel):
    """Envelope returned by POST /research/analyze."""
    report_id: int
    ticker: str
    company_name: str | None
    status: str
    generated_at: datetime
    cached: bool = False          # True if this report was served from cache
    report: ReportOutput | None   # None only if status == "failed"

    model_config = ConfigDict(from_attributes=True)
