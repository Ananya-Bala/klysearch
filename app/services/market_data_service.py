"""
services/market_data_service.py
--------------------------------
Fetches and computes market and technical data for a given ticker using yfinance.
No API key required — yfinance scrapes Yahoo Finance directly.

Design decisions:
- Every metric is wrapped in try/except so a missing field (e.g., a company
  with no forward PE) never crashes the whole pipeline. The caller gets None
  for that field and the AI prompt handles it gracefully.
- Technical indicators (SMA, RSI) are computed from price history in pure
  numpy/pandas — no third-party TA library dependency.
- All outputs are plain dicts / dataclasses so they can be serialised into
  the AI prompt without any ORM coupling.

RSI formula (Wilder's Smoothed Moving Average):
    RS  = avg_gain / avg_loss  (over 14 periods)
    RSI = 100 - (100 / (1 + RS))
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Typed container for all market + technical data."""
    ticker: str
    company_name: str | None = None

    # ── Fundamentals ─────────────────────────────────────────────────────────
    current_price: float | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None
    free_cash_flow: float | None = None
    debt_to_equity: float | None = None
    profit_margins: float | None = None

    # ── Technicals ───────────────────────────────────────────────────────────
    sma_50: float | None = None
    sma_200: float | None = None
    rsi: float | None = None
    volume_trend: str | None = None      # "increasing" | "decreasing" | "neutral"
    golden_cross: bool = False
    death_cross: bool = False

    # ── Analyst consensus ─────────────────────────────────────────────────────
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    mean_target_price: float | None = None

    # Raw info dict from yfinance — used by the AI prompt for extra context
    raw_info: dict[str, Any] = field(default_factory=dict)


def _safe_float(value: Any) -> float | None:
    """Convert a yfinance value to float, returning None on any error."""
    try:
        if value is None:
            return None
        f = float(value)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    """Convert a yfinance value to int, returning 0 on any error."""
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compute_rsi(prices: list[float], period: int = 14) -> float | None:
    """
    Wilder's RSI over `period` days.
    Returns None if there aren't enough data points.
    """
    if len(prices) < period + 1:
        return None

    arr = np.array(prices, dtype=float)
    deltas = np.diff(arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Initial SMA seed
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    # Wilder smoothing over remaining data
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _volume_trend(volumes: list[float]) -> str:
    """
    Compare mean volume of the last 20 days vs the 20 days before that.
    Returns "increasing", "decreasing", or "neutral".
    """
    if len(volumes) < 40:
        return "neutral"

    recent = np.mean(volumes[-20:])
    prior  = np.mean(volumes[-40:-20])

    if prior == 0:
        return "neutral"

    change = (recent - prior) / prior
    if change > 0.10:
        return "increasing"
    if change < -0.10:
        return "decreasing"
    return "neutral"


def fetch_market_data(ticker: str) -> MarketSnapshot:
    """
    Main entry point. Fetches fundamentals + computes technicals for `ticker`.

    Raises ValueError if the ticker is completely unknown (no price data at all).
    All other errors are caught per-field and logged — the pipeline continues
    with partial data.
    """
    import yfinance as yf  # imported here so the module loads without network

    ticker_upper = ticker.strip().upper()
    snap = MarketSnapshot(ticker=ticker_upper)

    try:
        t = yf.Ticker(ticker_upper)
        info: dict[str, Any] = t.info or {}
        snap.raw_info = info

        # ── Company identity ──────────────────────────────────────────────────
        snap.company_name = info.get("longName") or info.get("shortName")

        # ── Fundamentals ──────────────────────────────────────────────────────
        snap.current_price = _safe_float(
            info.get("currentPrice") or info.get("regularMarketPrice")
        )
        snap.market_cap       = _safe_float(info.get("marketCap"))
        snap.pe_ratio         = _safe_float(info.get("trailingPE"))
        snap.forward_pe       = _safe_float(info.get("forwardPE"))
        snap.eps              = _safe_float(info.get("trailingEps"))
        snap.revenue_growth   = _safe_float(info.get("revenueGrowth"))
        snap.free_cash_flow   = _safe_float(info.get("freeCashflow"))
        snap.debt_to_equity   = _safe_float(info.get("debtToEquity"))
        snap.profit_margins   = _safe_float(info.get("profitMargins"))

        # ── Analyst consensus ─────────────────────────────────────────────────
        snap.strong_buy       = _safe_int(info.get("strongBuyRatings"))
        snap.buy              = _safe_int(info.get("buyRatings"))
        snap.hold             = _safe_int(info.get("holdRatings"))
        snap.sell             = _safe_int(info.get("sellRatings"))
        snap.strong_sell      = _safe_int(info.get("strongSellRatings"))
        snap.mean_target_price = _safe_float(info.get("targetMeanPrice"))

        # ── Historical prices for technicals ──────────────────────────────────
        hist = t.history(period="1y")

        if hist.empty:
            logger.warning("No price history for %s", ticker_upper)
            return snap

        closes  = hist["Close"].tolist()
        volumes = hist["Volume"].tolist()

        # SMA 50 / 200
        if len(closes) >= 50:
            snap.sma_50 = round(float(np.mean(closes[-50:])), 4)
        if len(closes) >= 200:
            snap.sma_200 = round(float(np.mean(closes[-200:])), 4)

        # Golden / Death cross
        if snap.sma_50 is not None and snap.sma_200 is not None:
            snap.golden_cross = snap.sma_50 > snap.sma_200
            snap.death_cross  = snap.sma_50 < snap.sma_200

        # RSI (14-period)
        snap.rsi = _compute_rsi(closes)

        # Volume trend
        snap.volume_trend = _volume_trend(volumes)

    except Exception as exc:
        # Non-fatal — return whatever we managed to collect
        logger.error("market_data_service error for %s: %s", ticker_upper, exc, exc_info=True)

    # Validate we got at least a price; if not the ticker is bogus
    if snap.current_price is None and not snap.raw_info:
        raise ValueError(f"No data found for ticker '{ticker_upper}'. Is it a valid symbol?")

    return snap
