"""
services/scenario_service.py
------------------------------
Phase 3B: Deterministic quantitative scenario generation.

Generates three price scenarios (Bull / Base / Bear) and assigns
probability weights using a rules-based model grounded in:
    - Current price and volatility (from risk metrics)
    - Momentum (RSI and MACD signal from technical analysis)
    - Sentiment (news sentiment score)
    - Fundamentals (revenue growth, forward PE)

Design principles:
    1. No hardcoded target prices — all targets are derived from the
       current price, historical volatility, and a scenario multiplier.
    2. Probabilities are computed from a base weight system then adjusted
       by sentiment and momentum signals, then normalised to sum to 100.
    3. All maths is pure Python/numpy — no AI call needed here.

Scenario target methodology:
    Base multiplier  = 1 + (revenue_growth × 0.5) + (1/forward_pe × 0.2)
                         Approx: fundamental-justified 12-month return
    Bull multiplier  = base_mult + 1.5 × annualised_vol
    Bear multiplier  = base_mult − 1.5 × annualised_vol
    (The 1.5× multiplier puts bull/bear targets ~1.5 standard deviations
     from the base case, which is a standard scenario-analysis convention.)

Probability model:
    Start with prior:     bull=30, base=50, bear=20
    RSI adjustment:       rsi>70 → -5 bull, +5 bear (overbought)
                          rsi<30 → +5 bull, -5 bear (oversold)
    MACD adjustment:      bullish histogram → +3 bull, -3 bear
    Sentiment:            score > 0.1 → +3 bull, -3 bear
                          score < -0.1 → -3 bull, +3 bear
    Volatility:           vol > 0.4 → -3 bull, +3 bear (high uncertainty)
    After each adjustment: clamp to [5, 90], then renormalise to sum=100.
"""

import logging
from dataclasses import dataclass

from app.services.market_data_service import MarketSnapshot
from app.services.risk_service import RiskSnapshot
from app.services.sentiment_service import AggregatedSentiment
from app.services.technical_analysis_service import TechnicalSnapshot

logger = logging.getLogger(__name__)

# Probability priors (must sum to 100)
_PRIOR_BULL: int = 30
_PRIOR_BASE: int = 50
_PRIOR_BEAR: int = 20


# ── Output dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScenarioCase:
    target: float
    probability: int        # integer 0–100
    upside_pct: float       # % vs current price, e.g. +22.5 or -15.0


@dataclass
class ScenarioSnapshot:
    ticker: str
    current_price: float | None

    bull: ScenarioCase | None = None
    base: ScenarioCase | None = None
    bear: ScenarioCase | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 5.0, hi: float = 90.0) -> float:
    return max(lo, min(hi, v))


def _normalise(bull: float, base: float, bear: float) -> tuple[int, int, int]:
    """Rescale three floats to integer percentages that sum to 100."""
    total = bull + base + bear
    if total <= 0:
        return _PRIOR_BULL, _PRIOR_BASE, _PRIOR_BEAR

    b1 = round(bull * 100 / total)
    b2 = round(base * 100 / total)
    b3 = 100 - b1 - b2        # absorb rounding remainder into bear
    return int(b1), int(b2), int(b3)


def _upside(target: float, current: float) -> float:
    if current == 0:
        return 0.0
    return round((target - current) / current * 100, 1)


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_scenarios(
    market_data: MarketSnapshot,
    sentiment: AggregatedSentiment,
    technical: TechnicalSnapshot,
    risk: RiskSnapshot,
) -> ScenarioSnapshot:
    """
    Generate deterministic Bull / Base / Bear scenario targets and probabilities.

    Falls back gracefully if any input is missing — returns None cases rather
    than crashing the pipeline.
    """
    ticker = market_data.ticker
    current_price = market_data.current_price

    snap = ScenarioSnapshot(ticker=ticker, current_price=current_price)

    if current_price is None or current_price <= 0:
        logger.warning("No current price for scenario generation: %s", ticker)
        return snap

    # ── Target price multipliers ──────────────────────────────────────────────
    vol = risk.volatility or 0.25         # default 25% if unavailable
    rev_growth = market_data.revenue_growth or 0.05
    fwd_pe = market_data.forward_pe

    # Base case: fundamental-justified appreciation
    # Forward PE < 20 → value territory (more upside); > 40 → stretched (less)
    pe_adj = 0.0
    if fwd_pe and fwd_pe > 0:
        pe_adj = min(0.05, max(-0.05, (25.0 - fwd_pe) / 25.0 * 0.05))

    base_mult  = 1.0 + (rev_growth * 0.5) + pe_adj
    bull_mult  = base_mult + (1.5 * vol)
    bear_mult  = base_mult - (1.5 * vol)

    # Floor bear at -40% — beyond that lies bankruptcy territory not modelled here
    bear_mult  = max(bear_mult, 0.60)
    # Cap bull at +100% — extraordinary returns need dedicated model
    bull_mult  = min(bull_mult, 2.00)

    bull_target = round(current_price * bull_mult, 2)
    base_target = round(current_price * base_mult, 2)
    bear_target = round(current_price * bear_mult, 2)

    # ── Probability model ─────────────────────────────────────────────────────
    bull_w = float(_PRIOR_BULL)
    base_w = float(_PRIOR_BASE)
    bear_w = float(_PRIOR_BEAR)

    # RSI signal
    rsi = technical.rsi or 50.0
    if rsi > 70:
        bull_w -= 5.0; bear_w += 5.0     # overbought → higher pullback risk
    elif rsi < 30:
        bull_w += 5.0; bear_w -= 5.0     # oversold → mean reversion likely

    # MACD signal
    if technical.macd_bullish:
        bull_w += 3.0; bear_w -= 3.0
    elif (technical.macd or 0) < (technical.macd_signal or 0):
        bull_w -= 3.0; bear_w += 3.0

    # Sentiment signal
    sent = sentiment.overall_score
    if sent > 0.10:
        bull_w += 3.0; bear_w -= 3.0
    elif sent < -0.10:
        bull_w -= 3.0; bear_w += 3.0

    # Volatility adjustment — high vol → wider uncertainty → shrink bull prob
    if vol > 0.40:
        bull_w -= 3.0; bear_w += 3.0

    # Trend alignment — if bearish trend, penalise bull case
    if technical.trend == "Bearish":
        bull_w -= 4.0; bear_w += 4.0
    elif technical.trend == "Bullish":
        bull_w += 4.0; bear_w -= 4.0

    # Clamp individual weights before normalising
    bull_w = _clamp(bull_w)
    base_w = _clamp(base_w)
    bear_w = _clamp(bear_w)

    bull_p, base_p, bear_p = _normalise(bull_w, base_w, bear_w)

    snap.bull = ScenarioCase(
        target=bull_target,
        probability=bull_p,
        upside_pct=_upside(bull_target, current_price),
    )
    snap.base = ScenarioCase(
        target=base_target,
        probability=base_p,
        upside_pct=_upside(base_target, current_price),
    )
    snap.bear = ScenarioCase(
        target=bear_target,
        probability=bear_p,
        upside_pct=_upside(bear_target, current_price),
    )

    logger.info(
        "Scenarios for %s: Bull $%.2f (%d%%) | Base $%.2f (%d%%) | Bear $%.2f (%d%%)",
        ticker,
        bull_target, bull_p,
        base_target, base_p,
        bear_target, bear_p,
    )

    return snap
