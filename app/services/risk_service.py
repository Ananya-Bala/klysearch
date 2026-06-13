"""
services/risk_service.py
-------------------------
Phase 3B: Quantitative risk metrics.

Computes four institutional risk measures from 1-year daily returns:
    1. Annualized volatility  — std dev of daily log returns × √252
    2. Maximum drawdown       — largest peak-to-trough decline (%)
    3. Sharpe ratio           — excess return over 4% risk-free rate / vol
    4. Beta vs S&P 500        — OLS slope of stock returns on SPY returns

Why these four?
    Together they answer the three core risk questions a portfolio manager asks:
    - How wild is this thing? (volatility)
    - How bad could it get?   (max drawdown)
    - Am I being paid for the risk? (Sharpe)
    - Does it amplify market moves?  (Beta)

Design:
    - Beta requires a second yfinance download (^GSPC). Both calls are
      date-aligned with an inner join so mismatched trading days don't
      distort the regression.
    - All returns use log returns (ln(P_t/P_{t-1})) for statistical
      consistency. Volatility is annualised assuming 252 trading days.
    - Risk-free rate = 4.0% annual (approximately US 1-year T-bill, mid-2025).
      Update RISK_FREE_RATE if the rate environment changes materially.
    - Every calculation is wrapped individually — a network failure on the
      SPY download degrades gracefully to Beta=None instead of killing the
      whole pipeline.
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RISK_FREE_RATE_ANNUAL: float = 0.04    # 4% — update if rate environment shifts
TRADING_DAYS_PER_YEAR: int   = 252


# ── Output dataclass ──────────────────────────────────────────────────────────

@dataclass
class RiskSnapshot:
    ticker: str

    volatility: float | None    = None   # annualised, e.g. 0.31 = 31%
    max_drawdown: float | None  = None   # e.g. -18.2 means −18.2%
    sharpe_ratio: float | None  = None   # risk-adjusted return
    beta: float | None          = None   # vs S&P 500 (^GSPC)

    # Qualitative risk label derived from volatility
    risk_level: str = "Unknown"          # "Low" | "Moderate" | "High" | "Very High"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 4)
    except (TypeError, ValueError):
        return None


def _log_returns(prices: pd.Series) -> pd.Series:
    """Daily log returns. Drops the first NaN from diff."""
    return np.log(prices / prices.shift(1)).dropna()


def _annualised_volatility(returns: pd.Series) -> float | None:
    """σ_annual = σ_daily × √252"""
    if len(returns) < 20:
        return None
    return _safe_float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def _max_drawdown(prices: pd.Series) -> float | None:
    """
    Maximum peak-to-trough percentage decline.
    Returns a negative value, e.g. -18.2 for a 18.2% drawdown.
    """
    if len(prices) < 5:
        return None

    roll_max  = prices.cummax()
    drawdown  = (prices - roll_max) / roll_max
    max_dd    = drawdown.min()
    return _safe_float(max_dd * 100)       # convert to percentage


def _sharpe_ratio(returns: pd.Series, volatility: float | None) -> float | None:
    """
    Sharpe = (annualised_return − risk_free_rate) / annualised_volatility

    Annualised return is computed as the geometric mean return × 252.
    """
    if volatility is None or volatility == 0 or len(returns) < 20:
        return None

    ann_return = returns.mean() * TRADING_DAYS_PER_YEAR
    excess     = ann_return - RISK_FREE_RATE_ANNUAL
    return _safe_float(excess / volatility)


def _beta(
    stock_returns: pd.Series,
    market_returns: pd.Series,
) -> float | None:
    """
    OLS beta: cov(stock, market) / var(market).
    The two series must be date-aligned before calling.
    """
    if len(stock_returns) < 30 or len(market_returns) < 30:
        return None

    cov = np.cov(stock_returns, market_returns)
    if cov[1, 1] == 0:
        return None

    return _safe_float(cov[0, 1] / cov[1, 1])


def _risk_level(volatility: float | None) -> str:
    """Map annualised volatility to a qualitative risk label."""
    if volatility is None:
        return "Unknown"
    if volatility < 0.20:
        return "Low"
    if volatility < 0.35:
        return "Moderate"
    if volatility < 0.55:
        return "High"
    return "Very High"


# ── Main entry point ──────────────────────────────────────────────────────────

def calculate_risk_metrics(ticker: str) -> RiskSnapshot:
    """
    Download 1 year of daily closes for `ticker` (and ^GSPC for beta)
    and compute all Phase 3B risk metrics.

    Never raises — any failure is caught per-metric and logged.
    """
    import yfinance as yf

    ticker_upper = ticker.strip().upper()
    snap = RiskSnapshot(ticker=ticker_upper)

    try:
        # ── Stock price history ───────────────────────────────────────────────
        df = yf.download(
            ticker_upper,
            period="1y",
            auto_adjust=True,
            progress=False,
        )

        if df.empty or len(df) < 21:
            logger.warning("Insufficient data for risk metrics: %s", ticker_upper)
            return snap

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        closes  = df["Close"].dropna()
        returns = _log_returns(closes)

        # ── Volatility ────────────────────────────────────────────────────────
        try:
            snap.volatility = _annualised_volatility(returns)
        except Exception as exc:
            logger.warning("Volatility calculation failed for %s: %s", ticker_upper, exc)

        # ── Max drawdown ──────────────────────────────────────────────────────
        try:
            snap.max_drawdown = _max_drawdown(closes)
        except Exception as exc:
            logger.warning("Max drawdown calculation failed for %s: %s", ticker_upper, exc)

        # ── Sharpe ratio ──────────────────────────────────────────────────────
        try:
            snap.sharpe_ratio = _sharpe_ratio(returns, snap.volatility)
        except Exception as exc:
            logger.warning("Sharpe ratio calculation failed for %s: %s", ticker_upper, exc)

        # ── Beta vs S&P 500 ───────────────────────────────────────────────────
        try:
            spy_df = yf.download(
                "^GSPC",
                period="1y",
                auto_adjust=True,
                progress=False,
            )
            if not spy_df.empty:
                if isinstance(spy_df.columns, pd.MultiIndex):
                    spy_df.columns = spy_df.columns.get_level_values(0)

                spy_closes  = spy_df["Close"].dropna()
                spy_returns = _log_returns(spy_closes)

                # Align on common dates
                aligned = pd.concat([returns, spy_returns], axis=1, join="inner")
                aligned.columns = ["stock", "market"]
                aligned = aligned.dropna()

                snap.beta = _beta(aligned["stock"], aligned["market"])
        except Exception as exc:
            logger.warning("Beta calculation failed for %s: %s", ticker_upper, exc)

        # ── Qualitative risk label ────────────────────────────────────────────
        snap.risk_level = _risk_level(snap.volatility)

        logger.info(
            "Risk metrics complete for %s — vol=%.2f mdd=%.1f%% sharpe=%.2f beta=%.2f",
            ticker_upper,
            snap.volatility or 0,
            snap.max_drawdown or 0,
            snap.sharpe_ratio or 0,
            snap.beta or 0,
        )

    except Exception as exc:
        logger.error(
            "Risk service error for %s: %s", ticker_upper, exc, exc_info=True
        )

    return snap
