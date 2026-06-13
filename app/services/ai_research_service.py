"""
services/ai_research_service.py
--------------------------------
Orchestrates the full AI research report generation pipeline:

    1. Receive pre-fetched MarketSnapshot + AggregatedSentiment
    2. Build a structured prompt containing all quantitative data
    3. Call Groq and request a JSON response matching our schema
    4. Parse and validate the JSON response into typed Pydantic schemas
    5. Return the final ReportOutput

Phase 3B additions:
    - Client switched from openai.OpenAI → groq.Groq
    - generate_research_report() now accepts TechnicalSnapshot,
      RiskSnapshot, and ScenarioSnapshot in addition to MarketSnapshot
      and AggregatedSentiment
    - _build_user_prompt() injects all five signal blocks into the prompt
    - System prompt upgraded to institutional multi-signal reasoning rules
    - _parse_ai_response() populates the three new Phase 3B schema fields

Backward compatibility:
    The function signature adds new keyword arguments with defaults so
    callers that only pass snap + sentiment (Phase 3A style) still work.

Prompt engineering principles:
    - System prompt establishes the "senior equity research analyst" persona
    - All quantitative data is injected into the user message — the model
      must not hallucinate numbers it wasn't given
    - Response format is described with an explicit JSON schema so we get
      deterministic, parseable output
    - Temperature = 0.3: enough creativity for narrative, deterministic for numbers

Error handling:
    - JSON parse errors: retry once with a stricter "respond with ONLY JSON" reminder
    - Groq API errors: propagate as AIResearchError with a clear message
    - Probability sum != 100: auto-normalise before returning

Token budget estimate (Phase 3B):
    Prompt:     ~2,400 tokens (richer data blocks)
    Completion: ~2,500 tokens (deeper synthesis)
    Total:      ~4,900 tokens per call
"""

import json
import logging
from typing import Any

from groq import APIError as GroqAPIError
from groq import Groq

from app.core.config import settings
from app.schemas.research_report import (
    AIReport,
    AnalystConsensus,
    ExecutiveSummary,
    FundamentalData,
    NewsItem,
    NewsSentiment,
    PriceTarget,
    ScenarioAnalysis,
    ScenarioCase,
    ReportOutput,
    RiskAnalysis,
    RiskItem,
    ScenarioAnalysis,
    ScenarioCase,
    TechnicalData,
    TimingAnalysis,
)
from app.services.market_data_service import MarketSnapshot
from app.services.risk_service import RiskSnapshot
from app.services.scenario_service import ScenarioSnapshot
from app.services.sentiment_service import AggregatedSentiment
from app.services.technical_analysis_service import TechnicalSnapshot

logger = logging.getLogger(__name__)


class AIResearchError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(value: Any, suffix: str = "", precision: int = 2) -> str:
    """Format a numeric value for the prompt, returning 'N/A' if None."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{precision}f}{suffix}"
    return f"{value}{suffix}"


def _pct(value: float | None, multiply: bool = True) -> str:
    """Format as percentage. multiply=True if value is a decimal (0.22 → 22%)."""
    if value is None:
        return "N/A"
    v = value * 100 if multiply else value
    return f"{v:.1f}%"


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    """
    Institutional multi-signal reasoning prompt.
    The model is instructed to synthesise across conflicting signals
    rather than list them independently.
    """
    return """You are a senior institutional equity research analyst, portfolio manager, and risk analyst combined into one.

Your role is to synthesize multiple quantitative signals into a single, coherent investment thesis.

CORE REASONING RULES:
1. Do NOT summarize or list data. Reason across all signals and explain what they mean together.
2. When signals conflict, explain WHY they conflict and what it implies for the investment.
3. Always separate SHORT-TERM outlook (0–3 months) from LONG-TERM outlook (6–12 months).
4. If fundamentals are strong but technicals are weak: acknowledge both, explain the tension.
5. If sentiment and valuation disagree: explain why and which signal you weight more heavily.

SPECIFIC SIGNAL RULES:
- RSI > 70: Explicitly flag overbought conditions and increased short-term pullback risk.
- RSI < 30: Explicitly flag oversold conditions and mean-reversion opportunity.
- Volatility > 35%: Reduce conviction_score by 10–15 points vs a similar low-vol stock.
- If |bull_probability − bear_probability| < 15: Recommend HOLD, not BUY or SELL.
- Golden Cross (SMA50 > SMA200): Mention as medium-term bullish structural signal.
- Death Cross (SMA50 < SMA200): Mention as medium-term bearish structural signal.
- MACD bullish crossover: Mention as short-term momentum confirmation.
- Sharpe < 0.5: Note poor risk-adjusted returns vs alternatives.
- Beta > 1.5: Warn about amplified market sensitivity.
- Max drawdown > 30%: Note elevated historical downside risk.

OUTPUT REQUIREMENTS:
- Use probabilities, not certainties. ("likely", "suggests", "indicates")
- Never claim certainty about future prices.
- All string fields must be substantive paragraphs (3–5 sentences minimum).
- Always respond with valid JSON matching the schema exactly.
- Conviction score must reflect volatility, signal alignment, and probability spread."""


# ── User prompt ───────────────────────────────────────────────────────────────

def _build_user_prompt(
    snap: MarketSnapshot,
    sentiment: AggregatedSentiment,
    technical: TechnicalSnapshot | None = None,
    risk: RiskSnapshot | None = None,
    scenarios: ScenarioSnapshot | None = None,
) -> str:
    """
    Assemble all five signal blocks into a single structured prompt.
    Each block is clearly labelled so the model can reason across them.
    """

    # ── Block 1: Fundamentals ─────────────────────────────────────────────────
    fundamentals_block = f"""=== FUNDAMENTAL DATA ===
Current Price:    {_fmt(snap.current_price, '$')}
Market Cap:       {_fmt(snap.market_cap)}
Trailing P/E:     {_fmt(snap.pe_ratio, 'x')}
Forward P/E:      {_fmt(snap.forward_pe, 'x')}
EPS (TTM):        {_fmt(snap.eps, '$')}
Revenue Growth:   {_pct(snap.revenue_growth)}
Free Cash Flow:   {_fmt(snap.free_cash_flow)}
Debt/Equity:      {_fmt(snap.debt_to_equity)}
Profit Margin:    {_pct(snap.profit_margins)}"""

    # ── Block 2: Technical Analysis ───────────────────────────────────────────
    if technical:
        rsi_val = technical.rsi or 50.0
        if rsi_val > 70:
            rsi_label = f"OVERBOUGHT ({rsi_val:.1f}) ⚠"
        elif rsi_val < 30:
            rsi_label = f"OVERSOLD ({rsi_val:.1f}) ⚠"
        else:
            rsi_label = f"{rsi_val:.1f} (neutral)"

        if technical.golden_cross:
            cross_label = "GOLDEN CROSS (SMA50 > SMA200) — bullish"
        elif technical.death_cross:
            cross_label = "DEATH CROSS (SMA50 < SMA200) — bearish"
        else:
            cross_label = "No cross signal"

        macd_label = (
            f"{_fmt(technical.macd)} vs signal {_fmt(technical.macd_signal)} "
            f"({'BULLISH crossover' if technical.macd_bullish else 'BEARISH crossover'})"
        )

        vol_ratio_label = (
            f"{_fmt(technical.volume_ratio, 'x')} "
            f"({'breakout' if (technical.volume_ratio or 0) > 1.5 else 'normal'})"
        )

        technical_block = f"""=== TECHNICAL ANALYSIS ===
RSI (14):         {rsi_label}
MACD:             {macd_label}
MACD Histogram:   {_fmt(technical.macd_histogram)}
SMA 50:           {_fmt(technical.sma_50, '$')}
SMA 200:          {_fmt(technical.sma_200, '$')}
MA Cross:         {cross_label}
Volume Ratio:     {vol_ratio_label}
Trend:            {technical.trend}"""
    else:
        # Fallback to Phase 3A basic signals from MarketSnapshot
        rsi_val = snap.rsi or 50.0
        rsi_label = f"OVERBOUGHT ({rsi_val:.1f})" if rsi_val > 70 else (
            f"OVERSOLD ({rsi_val:.1f})" if rsi_val < 30 else f"{rsi_val:.1f}"
        )
        technical_block = f"""=== TECHNICAL ANALYSIS ===
RSI (14):         {rsi_label}
SMA 50:           {_fmt(snap.sma_50, '$')}
SMA 200:          {_fmt(snap.sma_200, '$')}
Volume Trend:     {snap.volume_trend or 'N/A'}
Golden Cross:     {snap.golden_cross}"""

    # ── Block 3: Risk Metrics ─────────────────────────────────────────────────
    if risk:
        risk_block = f"""=== RISK METRICS ===
Annualised Volatility: {_pct(risk.volatility)}
Max Drawdown:          {_fmt(risk.max_drawdown, '%', 1)} (worst peak-to-trough in 1y)
Sharpe Ratio:          {_fmt(risk.sharpe_ratio)} (risk-free rate = 4%)
Beta vs S&P 500:       {_fmt(risk.beta)}
Risk Level:            {risk.risk_level}"""
    else:
        risk_block = "=== RISK METRICS ===\nNot available."

    # ── Block 4: Scenario Analysis ────────────────────────────────────────────
    if scenarios and scenarios.bull and scenarios.base and scenarios.bear:
        scenario_block = f"""=== QUANTITATIVE SCENARIO ANALYSIS ===
Current Price: {_fmt(scenarios.current_price, '$')}
Bull Case:  ${scenarios.bull.target:.2f}  (+{scenarios.bull.upside_pct:.1f}%)  — probability: {scenarios.bull.probability}%
Base Case:  ${scenarios.base.target:.2f}  ({scenarios.base.upside_pct:+.1f}%)  — probability: {scenarios.base.probability}%
Bear Case:  ${scenarios.bear.target:.2f}  ({scenarios.bear.upside_pct:+.1f}%)  — probability: {scenarios.bear.probability}%
Note: Probabilities are quantitatively derived from volatility, momentum, and sentiment signals."""
    else:
        scenario_block = "=== QUANTITATIVE SCENARIO ANALYSIS ===\nNot available."

    # ── Block 5: News Sentiment ───────────────────────────────────────────────
    news_lines = "No news data available."
    if sentiment.articles:
        lines = []
        for a in sentiment.articles[:8]:
            lines.append(
                f"  [{a.date}] {a.source}: \"{a.title}\" (score: {a.sentiment_score:+.2f})"
            )
        news_lines = "\n".join(lines)

    total_ratings = snap.strong_buy + snap.buy + snap.hold + snap.sell + snap.strong_sell
    sentiment_block = f"""=== NEWS & SENTIMENT ===
Overall Sentiment: {sentiment.overall_label.upper()} (score: {sentiment.overall_score:+.2f})
Recent Headlines:
{news_lines}

=== ANALYST CONSENSUS ===
Strong Buy: {snap.strong_buy} | Buy: {snap.buy} | Hold: {snap.hold} | Sell: {snap.sell} | Strong Sell: {snap.strong_sell}
Total Ratings: {total_ratings}
Mean Price Target: {_fmt(snap.mean_target_price, '$')}"""

    # ── Assembled prompt ──────────────────────────────────────────────────────
    prompt = f"""Analyze {snap.ticker} ({snap.company_name or 'Unknown Company'}) using ALL signals below.

{fundamentals_block}

{technical_block}

{risk_block}

{scenario_block}

{sentiment_block}

=== YOUR TASK ===
Synthesize ALL the signals above into a single institutional research report.
Do not simply list the data — reason across the signals, explain conflicts, and produce a unified investment thesis.

Apply these rules strictly:
- If RSI > 70: mention overbought conditions in technicals interpretation
- If RSI < 30: mention oversold conditions in technicals interpretation  
- If volatility > 35%: reduce conviction_score accordingly
- If |bull_probability − bear_probability| < 15: recommendation must be "Hold"
- If fundamentals and technicals conflict: explain why in the interpretation fields
- Scenario target prices and probabilities MUST match the Quantitative Scenario Analysis above exactly

Respond with ONLY this JSON object. No markdown. No preamble.

{{
  "executive_summary": {{
    "recommendation": "<Strong Buy|Buy|Hold|Reduce|Sell>",
    "conviction_score": <0-100>,
    "key_catalyst": "<single sentence identifying the primary catalyst>",
    "entry_zone": "<price range or 'Current levels' or 'Wait for pullback to $X'>",
    "price_targets": {{
      "three_months": <float matching bear/base/bull targets above or null>,
      "six_months": <float or null>,
      "twelve_months": <float matching bull/base case above or null>
    }}
  }},
  "fundamentals": {{
    "interpretation": "<3-5 sentence analysis reasoning across the fundamental metrics>"
  }},
  "technicals": {{
    "interpretation": "<3-5 sentence analysis reasoning across RSI, MACD, moving averages, volume — explicitly addressing overbought/oversold if present>"
  }},
  "analyst_consensus": {{
    "summary": "<paragraph summarising analyst ratings and mean target vs current price>"
  }},
  "scenario_analysis": {{
    "bull_case": {{
      "target_price": <use bull target from scenario analysis above>,
      "narrative": "<paragraph explaining what must go right>"
    }},
    "base_case": {{
      "target_price": <use base target from scenario analysis above>,
      "narrative": "<paragraph explaining the most likely path>"
    }},
    "bear_case": {{
      "target_price": <use bear target from scenario analysis above>,
      "narrative": "<paragraph explaining key downside risks>"
    }},
    "bull_probability": <use bull probability from scenario analysis above>,
    "base_probability": <use base probability from scenario analysis above>,
    "bear_probability": <use bear probability from scenario analysis above>
  }},
  "risk_analysis": {{
    "risks": [
      {{
        "risk": "<specific risk title>",
        "severity": "<Low|Medium|High|Critical>",
        "mitigation": "<actionable monitoring or hedging sentence>"
      }}
    ]
  }},
  "timing_analysis": {{
    "should_buy_now": <true|false>,
    "reasoning": "<paragraph explicitly using RSI level, MA position, volatility, and sentiment to justify timing>"
  }},
  "ai_report": {{
    "investment_thesis": "<paragraph: unified thesis synthesizing fundamentals + technicals + risk>",
    "growth_drivers": "<paragraph: primary drivers of upside, with probability context>",
    "risks": "<paragraph: primary risks including technical and fundamental, with context>",
    "valuation_view": "<paragraph: is the current price justified by the data? reference PE, targets>",
    "recommendation": "<paragraph: actionable recommendation with specific entry/sizing guidance>",
    "conclusion": "<paragraph: probability-weighted outlook separating short-term and long-term view>"
  }}
}}"""

    return prompt


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_ai_response(
    raw_json: str,
    snap: MarketSnapshot,
    sentiment: AggregatedSentiment,
    generated_at,
    technical: TechnicalSnapshot | None = None,
    risk: RiskSnapshot | None = None,
    scenarios: ScenarioSnapshot | None = None,
) -> ReportOutput:
    """
    Parse the Groq JSON response into a fully typed ReportOutput.
    Quantitative fields (fundamentals, technicals, risk) are sourced
    from the pre-computed service outputs — the AI only provides narratives.
    """
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise AIResearchError(
            f"AI returned invalid JSON: {exc}. Raw: {raw_json[:300]}"
        )

    # ── Normalise scenario probabilities ──────────────────────────────────────
    sa = data.get("scenario_analysis", {})
    bull_p = int(sa.get("bull_probability", 30))
    base_p = int(sa.get("base_probability", 50))
    bear_p = int(sa.get("bear_probability", 20))
    total  = bull_p + base_p + bear_p
    if total != 100 and total > 0:
        bull_p = round(bull_p * 100 / total)
        base_p = round(base_p * 100 / total)
        bear_p = 100 - bull_p - base_p

    # ── Executive summary ─────────────────────────────────────────────────────
    es = data.get("executive_summary", {})
    pt = es.get("price_targets", {})
    exec_summary = ExecutiveSummary(
        recommendation=es.get("recommendation", "Hold"),
        conviction_score=max(0, min(100, int(es.get("conviction_score", 50)))),
        key_catalyst=es.get("key_catalyst", "Monitoring required."),
        entry_zone=es.get("entry_zone", "N/A"),
        price_targets=PriceTarget(
            three_months=pt.get("three_months"),
            six_months=pt.get("six_months"),
            twelve_months=pt.get("twelve_months"),
        ),
    )

    # ── Fundamentals ──────────────────────────────────────────────────────────
    fd = data.get("fundamentals", {})
    fundamentals = FundamentalData(
        current_price=snap.current_price,
        market_cap=snap.market_cap,
        pe_ratio=snap.pe_ratio,
        forward_pe=snap.forward_pe,
        eps=snap.eps,
        revenue_growth=snap.revenue_growth,
        free_cash_flow=snap.free_cash_flow,
        debt_to_equity=snap.debt_to_equity,
        profit_margins=snap.profit_margins,
        interpretation=fd.get("interpretation", "Fundamental analysis not available."),
    )

    # ── Phase 3A TechnicalData (backward-compat, uses MarketSnapshot) ─────────
    td = data.get("technicals", {})
    technicals = TechnicalData(
        sma_50=snap.sma_50,
        sma_200=snap.sma_200,
        rsi=snap.rsi,
        volume_trend=snap.volume_trend,
        golden_cross=snap.golden_cross,
        death_cross=snap.death_cross,
        overbought=(snap.rsi or 0) > 70,
        oversold=(snap.rsi or 100) < 30,
        interpretation=td.get("interpretation", "Technical analysis not available."),
    )

    # ── Phase 3B TechnicalIndicators (richer, from technical_analysis_service) ─
    tech_indicators: TechnicalIndicators | None = None
    if technical:
        tech_indicators = TechnicalIndicators(
            rsi=technical.rsi,
            overbought=technical.overbought,
            oversold=technical.oversold,
            macd=technical.macd,
            macd_signal=technical.macd_signal,
            macd_histogram=technical.macd_histogram,
            macd_bullish=technical.macd_bullish,
            sma_50=technical.sma_50,
            sma_200=technical.sma_200,
            golden_cross=technical.golden_cross,
            death_cross=technical.death_cross,
            volume_ratio=technical.volume_ratio,
            trend=technical.trend,
        )

    # # ── Phase 3B RiskMetrics ──────────────────────────────────────────────────
    # risk_metrics_schema: RiskMetrics | None = None
    # if risk:
    #     risk_metrics_schema = RiskMetrics(
    #         volatility=risk.volatility,
    #         max_drawdown=risk.max_drawdown,
    #         sharpe_ratio=risk.sharpe_ratio,
    #         beta=risk.beta,
    #         risk_level=risk.risk_level,
    #     )

    # ── Phase 3B ScenarioAnalysis ───────────────────────────────────────
    quant_scenarios_schema: ScenarioAnalysis | None = None
    if scenarios and scenarios.bull and scenarios.base and scenarios.bear:
        quant_scenarios_schema = ScenarioAnalysis(
            current_price=scenarios.current_price,
            bull=ScenarioCase(
                target=scenarios.bull.target,
                probability=scenarios.bull.probability,
                upside_pct=scenarios.bull.upside_pct,
            ),
            base=ScenarioCase(
                target=scenarios.base.target,
                probability=scenarios.base.probability,
                upside_pct=scenarios.base.upside_pct,
            ),
            bear=ScenarioCase(
                target=scenarios.bear.target,
                probability=scenarios.bear.probability,
                upside_pct=scenarios.bear.upside_pct,
            ),
        )

    # ── News sentiment ────────────────────────────────────────────────────────
    news_sentiment = NewsSentiment(
        overall_score=sentiment.overall_score,
        overall_label=sentiment.overall_label,
        articles=[
            NewsItem(
                title=a.title,
                source=a.source,
                date=a.date,
                sentiment_score=a.sentiment_score,
                sentiment_label=a.sentiment_label,
            )
            for a in sentiment.articles
        ],
        top_positive_drivers=sentiment.top_positive_drivers,
        top_negative_drivers=sentiment.top_negative_drivers,
    )

    # ── Analyst consensus ─────────────────────────────────────────────────────
    ac = data.get("analyst_consensus", {})
    analyst_consensus = AnalystConsensus(
        strong_buy=snap.strong_buy,
        buy=snap.buy,
        hold=snap.hold,
        sell=snap.sell,
        strong_sell=snap.strong_sell,
        mean_target_price=snap.mean_target_price,
        summary=ac.get("summary", "No analyst consensus data available."),
    )

    # ── Phase 3A ScenarioAnalysis (AI narratives, uses quant targets) ─────────
    scenario_analysis = ScenarioAnalysis(
        bull_case=ScenarioCase(**sa.get("bull_case", {"target_price": 0, "narrative": "N/A"})),
        base_case=ScenarioCase(**sa.get("base_case", {"target_price": 0, "narrative": "N/A"})),
        bear_case=ScenarioCase(**sa.get("bear_case", {"target_price": 0, "narrative": "N/A"})),
        bull_probability=bull_p,
        base_probability=base_p,
        bear_probability=bear_p,
    )

    # ── Risk analysis (qualitative, AI-generated) ─────────────────────────────
    ra = data.get("risk_analysis", {})
    risk_analysis = RiskAnalysis(
        risks=[
            RiskItem(
                risk=r.get("risk", "Unknown risk"),
                severity=r.get("severity", "Medium"),
                mitigation=r.get("mitigation", "Monitor closely."),
            )
            for r in ra.get("risks", [])
        ]
    )

    # ── Timing analysis ───────────────────────────────────────────────────────
    ta = data.get("timing_analysis", {})
    timing_analysis = TimingAnalysis(
        should_buy_now=bool(ta.get("should_buy_now", False)),
        reasoning=ta.get("reasoning", "Insufficient data for timing recommendation."),
    )

    # ── AI narrative report ───────────────────────────────────────────────────
    ar = data.get("ai_report", {})
    ai_report = AIReport(
        investment_thesis=ar.get("investment_thesis", ""),
        growth_drivers=ar.get("growth_drivers", ""),
        risks=ar.get("risks", ""),
        valuation_view=ar.get("valuation_view", ""),
        recommendation=ar.get("recommendation", ""),
        conclusion=ar.get("conclusion", ""),
    )

    return ReportOutput(
        ticker=snap.ticker,
        company_name=snap.company_name,
        generated_at=generated_at,
        executive_summary=exec_summary,
        fundamentals=fundamentals,
        technicals=technicals,
        news_sentiment=news_sentiment,
        analyst_consensus=analyst_consensus,
        scenario_analysis=scenario_analysis,
        risk_analysis=risk_analysis,
        timing_analysis=timing_analysis,
        ai_report=ai_report,
        # Phase 3B
        # technical_indicators=tech_indicators,
        # risk_metrics=risk_metrics_schema,
        scenarios=scenarios_schema,
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_research_report(
    snap: MarketSnapshot,
    sentiment: AggregatedSentiment,
    generated_at,
    technical: TechnicalSnapshot | None = None,
    risk: RiskSnapshot | None = None,
    scenarios: ScenarioSnapshot | None = None,
) -> tuple[ReportOutput, int, int]:
    """
    Call Groq and return (ReportOutput, prompt_tokens, completion_tokens).

    Phase 3B: accepts three new optional parameters (technical, risk, scenarios).
    If they are None the prompt falls back to Phase 3A behaviour — full backward
    compatibility is maintained.
    """
    if not settings.GROQ_API_KEY:
        raise AIResearchError(
            "GROQ_API_KEY is not set. Add it to your .env file.",
            status_code=503,
        )

    client = Groq(api_key=settings.GROQ_API_KEY)

    system_prompt = _build_system_prompt()
    user_prompt   = _build_user_prompt(snap, sentiment, technical, risk, scenarios)

    logger.info(
        "Calling Groq %s for %s (prompt ~%d chars)",
        settings.GROQ_MODEL,
        snap.ticker,
        len(user_prompt),
    )

    try:
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
    except GroqAPIError as exc:
        raise AIResearchError(f"Groq API error: {exc}", status_code=502)

    raw_content       = response.choices[0].message.content or "{}"
    prompt_tokens     = response.usage.prompt_tokens     if response.usage else 0
    completion_tokens = response.usage.completion_tokens if response.usage else 0

    logger.info(
        "Groq response received: %d prompt + %d completion tokens",
        prompt_tokens,
        completion_tokens,
    )

    # Parse — one retry with temperature=0 on JSON failure
    try:
        report = _parse_ai_response(
            raw_content, snap, sentiment, generated_at, technical, risk, scenarios
        )
    except (AIResearchError, KeyError, ValueError) as exc:
        logger.warning("First parse failed (%s), retrying with strict prompt", exc)
        try:
            retry = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[
                    {"role": "system",    "content": system_prompt},
                    {"role": "user",      "content": user_prompt},
                    {"role": "assistant", "content": raw_content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. "
                            "Respond with ONLY the JSON object, no other text."
                        ),
                    },
                ],
                temperature=0.0,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
            raw_content = retry.choices[0].message.content or "{}"
            report = _parse_ai_response(
                raw_content, snap, sentiment, generated_at, technical, risk, scenarios
            )
            if retry.usage:
                prompt_tokens     += retry.usage.prompt_tokens
                completion_tokens += retry.usage.completion_tokens
        except Exception as retry_exc:
            raise AIResearchError(
                f"AI response could not be parsed after retry: {retry_exc}"
            )

    return report, prompt_tokens, completion_tokens