"""
services/technical_analysis_service.py
----------------------------------------
Phase 3B: Extended technical indicator calculation.

Replaces the basic RSI/SMA computed inside market_data_service with a
dedicated service that adds MACD, volume breakout ratio, and an explicit
trend classification. market_data_service is NOT changed — it still
computes its own quick signals for the MarketSnapshot dataclass.
This service is called as an additional pipeline step and its output
flows into the AI prompt as a richer signal block.

All calculations use pandas + numpy on a yf.download() price series.
No third-party TA library is required — every formula is implemented
from first principles so the calculation is auditable.

Formula references:
    RSI    — Wilder's Smoothed Moving Average (14-period)
    MACD   — EMA(12) − EMA(26); signal = EMA(9) of MACD line
    SMA    — simple rolling mean
    Volume — current_vol / rolling_30d_avg_vol
    Trend  — price position relative to SMA50 and SMA200
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Output dataclass ──────────────────────────────────────────────────────────

@dataclass
class TechnicalSnapshot:
    ticker: str

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi: float | None = None              # 0–100

    # ── MACD ─────────────────────────────────────────────────────────────────
    macd: float | None = None             # MACD line
    macd_signal: float | None = None      # signal line (EMA-9 of MACD)
    macd_histogram: float | None = None   # macd − signal

    # ── Moving averages ───────────────────────────────────────────────────────
    sma_50: float | None = None
    sma_200: float | None = None

    # ── Volume ────────────────────────────────────────────────────────────────
    volume_ratio: float | None = None     # latest vol / 30-day avg vol

    # ── Derived signals ───────────────────────────────────────────────────────
    trend: str = "Neutral"                # "Bullish" | "Neutral" | "Bearish"
    golden_cross: bool = False
    death_cross: bool = False
    overbought: bool = False              # RSI > 70
    oversold: bool = False               # RSI < 30
    macd_bullish: bool = False            # MACD line above signal


# ── Calculation helpers ───────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    """Return float or None; guard against NaN/Inf from pandas."""
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _wilder_rsi(closes: pd.Series, period: int = 14) -> float | None:
    """
    Wilder's Smoothed RSI.
    Requires at least period + 1 data points.
    """
    if len(closes) < period + 1:
        return None

    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Initial seed: simple mean of first `period` values
    avg_gain = gain.iloc[1 : period + 1].mean()
    avg_loss = loss.iloc[1 : period + 1].mean()

    # Wilder smoothing (RMA) for subsequent periods
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with adjust=False (matches most TA tools)."""
    return series.ewm(span=span, adjust=False).mean()


def _macd(closes: pd.Series) -> tuple[float | None, float | None, float | None]:
    """
    Standard MACD(12, 26, 9).
    Returns (macd_line, signal_line, histogram) for the most recent bar.
    """
    if len(closes) < 35:          # need at least 26 + 9 for meaningful values
        return None, None, None

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line   = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    histogram   = macd_line - signal_line

    return (
        _safe_float(macd_line.iloc[-1]),
        _safe_float(signal_line.iloc[-1]),
        _safe_float(histogram.iloc[-1]),
    )


def _volume_ratio(volumes: pd.Series) -> float | None:
    """
    Latest single-day volume divided by 30-day rolling average.
    Ratio > 1.5 → volume breakout; < 0.7 → low-volume drift.
    """
    if len(volumes) < 30:
        return None

    avg_30 = volumes.iloc[-31:-1].mean()     # 30 days before the latest bar
    if avg_30 == 0:
        return None

    return _safe_float(volumes.iloc[-1] / avg_30)


def _classify_trend(
    current_price: float | None,
    sma_50: float | None,
    sma_200: float | None,
) -> str:
    """
    Three-tier trend classification based on price/MA positioning.

    Bullish : price > SMA50 > SMA200
    Bearish : price < SMA50 < SMA200
    Neutral : any other arrangement (mixed signals)
    """
    if current_price is None or sma_50 is None or sma_200 is None:
        return "Neutral"

    if current_price > sma_50 > sma_200:
        return "Bullish"
    if current_price < sma_50 < sma_200:
        return "Bearish"
    return "Neutral"


# ── Main entry point ──────────────────────────────────────────────────────────

def calculate_technical_analysis(ticker: str) -> TechnicalSnapshot:
    """
    Download 1 year of daily price + volume data for `ticker` and compute
    all Phase 3B technical indicators.

    Never raises — any per-indicator failure is caught and logged.
    The caller receives a TechnicalSnapshot with None for failed fields.
    """
    import yfinance as yf

    ticker_upper = ticker.strip().upper()
    snap = TechnicalSnapshot(ticker=ticker_upper)

    try:
        df = yf.download(
            ticker_upper,
            period="1y",
            auto_adjust=True,
            progress=False,
        )

        if df.empty or len(df) < 15:
            logger.warning("Insufficient price history for technical analysis: %s", ticker_upper)
            return snap

        # Flatten multi-level columns that yfinance sometimes returns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        closes  = df["Close"].dropna()
        volumes = df["Volume"].dropna()

        # ── RSI ───────────────────────────────────────────────────────────────
        try:
            snap.rsi = _wilder_rsi(closes)
        except Exception as exc:
            logger.warning("RSI calculation failed for %s: %s", ticker_upper, exc)

        # ── MACD ──────────────────────────────────────────────────────────────
        try:
            snap.macd, snap.macd_signal, snap.macd_histogram = _macd(closes)
        except Exception as exc:
            logger.warning("MACD calculation failed for %s: %s", ticker_upper, exc)

        # ── SMAs ──────────────────────────────────────────────────────────────
        try:
            if len(closes) >= 50:
                snap.sma_50 = _safe_float(closes.iloc[-50:].mean())
            if len(closes) >= 200:
                snap.sma_200 = _safe_float(closes.iloc[-200:].mean())
        except Exception as exc:
            logger.warning("SMA calculation failed for %s: %s", ticker_upper, exc)

        # ── Volume ratio ──────────────────────────────────────────────────────
        try:
            snap.volume_ratio = _volume_ratio(volumes)
        except Exception as exc:
            logger.warning("Volume ratio calculation failed for %s: %s", ticker_upper, exc)

        # ── Derived signals ───────────────────────────────────────────────────
        current_price = _safe_float(closes.iloc[-1])

        snap.trend        = _classify_trend(current_price, snap.sma_50, snap.sma_200)
        snap.golden_cross = (snap.sma_50 or 0) > (snap.sma_200 or 0) and snap.sma_200 is not None
        snap.death_cross  = (snap.sma_50 or 0) < (snap.sma_200 or 0) and snap.sma_200 is not None
        snap.overbought   = (snap.rsi or 0) > 70
        snap.oversold     = (snap.rsi or 100) < 30
        snap.macd_bullish = (
            snap.macd is not None
            and snap.macd_signal is not None
            and snap.macd > snap.macd_signal
        )

        logger.info(
            "Technical analysis complete for %s — RSI=%.1f MACD=%.4f Trend=%s",
            ticker_upper,
            snap.rsi or 0,
            snap.macd or 0,
            snap.trend,
        )

    except Exception as exc:
        logger.error(
            "Technical analysis service error for %s: %s",
            ticker_upper,
            exc,
            exc_info=True,
        )

    return snap
