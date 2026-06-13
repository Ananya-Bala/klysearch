"""
services/sentiment_service.py
------------------------------
Scores financial news articles using TextBlob's polarity analysis,
then aggregates into an overall news sentiment signal.

Why TextBlob over a dedicated financial NLP model?
    - Zero API cost / no external calls
    - Fast (pure Python, runs in-process)
    - Sufficient for headline-level sentiment polarity
    Phase 4 could swap this for FinBERT or a GPT-4 batch call for
    deeper semantic understanding.

Polarity scale:
    TextBlob returns [-1.0, +1.0]
    We map to three labels:
        > +0.05   → bullish
        < -0.05   → bearish
        otherwise → neutral

Aggregate score:
    Simple mean of all article scores. Weighted average by recency
    could be added in Phase 4.
"""

import logging
from dataclasses import dataclass
from typing import Literal

from textblob import TextBlob  # type: ignore[import]

from app.services.news_service import RawArticle

logger = logging.getLogger(__name__)

SentimentLabel = Literal["bullish", "bearish", "neutral"]

_BULLISH_THRESHOLD = 0.05
_BEARISH_THRESHOLD = -0.05


@dataclass
class ScoredArticle:
    title: str
    source: str
    date: str
    sentiment_score: float
    sentiment_label: SentimentLabel


@dataclass
class AggregatedSentiment:
    overall_score: float
    overall_label: SentimentLabel
    articles: list[ScoredArticle]
    top_positive_drivers: list[str]    # titles of most bullish articles
    top_negative_drivers: list[str]    # titles of most bearish articles


def _label(score: float) -> SentimentLabel:
    if score > _BULLISH_THRESHOLD:
        return "bullish"
    if score < _BEARISH_THRESHOLD:
        return "bearish"
    return "neutral"


def _score_text(text: str) -> float:
    """Return TextBlob polarity for `text`, clamped to [-1, +1]."""
    try:
        polarity = TextBlob(text).sentiment.polarity
        return round(max(-1.0, min(1.0, polarity)), 4)
    except Exception as exc:
        logger.warning("TextBlob error: %s", exc)
        return 0.0


def analyze_news_sentiment(articles: list[RawArticle]) -> AggregatedSentiment:
    """
    Score every article and compute the aggregate sentiment signal.
    Safe to call with an empty list — returns neutral with no articles.
    """
    if not articles:
        return AggregatedSentiment(
            overall_score=0.0,
            overall_label="neutral",
            articles=[],
            top_positive_drivers=[],
            top_negative_drivers=[],
        )

    scored: list[ScoredArticle] = []
    for art in articles:
        # Score on title + description for richer signal
        combined = art.title
        if art.description:
            combined = f"{art.title}. {art.description}"

        score = _score_text(combined)
        scored.append(
            ScoredArticle(
                title=art.title,
                source=art.source,
                date=art.published_at,
                sentiment_score=score,
                sentiment_label=_label(score),
            )
        )

    # Aggregate
    overall = round(sum(a.sentiment_score for a in scored) / len(scored), 4)

    # Top 3 positive/negative by absolute score magnitude
    positives = sorted(
        [a for a in scored if a.sentiment_label == "bullish"],
        key=lambda a: a.sentiment_score,
        reverse=True,
    )
    negatives = sorted(
        [a for a in scored if a.sentiment_label == "bearish"],
        key=lambda a: a.sentiment_score,
    )

    return AggregatedSentiment(
        overall_score=overall,
        overall_label=_label(overall),
        articles=scored,
        top_positive_drivers=[a.title for a in positives[:3]],
        top_negative_drivers=[a.title for a in negatives[:3]],
    )
