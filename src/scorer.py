"""Relevance scorer for optical computing papers.

Scores each paper 0.0–1.0 using four signals:

  Signal                        Weight
  ─────────────────────────────────────
  Title keyword exact match      0.40
  Abstract keyword density       0.30
  Venue / journal tier           0.15
  Recency (days since published) 0.15

Papers below settings.scorer_min_score are stored in DB but NOT queued.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.config import settings
from src.db import Paper

log = logging.getLogger(__name__)

# ── Keyword lists ─────────────────────────────────────────────────────────────

# Tier 1 — core topic, highest weight
_KEYWORDS_T1 = [
    "optical computing",
    "photonic computing",
    "optical neural network",
    "photonic neural network",
    "diffractive neural network",
    "coherent computing",
    "optical matrix",
    "all-optical",
]

# Tier 2 — related technology, partial weight
_KEYWORDS_T2 = [
    "silicon photonics",
    "photonic integrated circuit",
    "optical interconnect",
    "mach-zehnder",
    "microring resonator",
    "waveguide",
    "nonlinear photonics",
    "integrated photonics",
    "oam multiplexing",
    "free-space optical",
]

# High-tier venues — bonus for venue score
_TIER1_VENUES = {
    "nature photonics",
    "nature",
    "science",
    "physical review letters",
    "optica",
    "light: science & applications",
    "laser & photonics reviews",
}

_TIER2_VENUES = {
    "optics express",
    "optics letters",
    "acs photonics",
    "ieee photonics technology letters",
    "advanced photonics",
    "photonics research",
    "journal of lightwave technology",
    "ieee journal of selected topics in quantum electronics",
}


# ── Scoring helpers ──────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    return text.lower()


def _title_score(title: str) -> float:
    """0.0–1.0 based on keyword exact match in title."""
    t = _normalise(title)
    for kw in _KEYWORDS_T1:
        if kw in t:
            return 1.0
    for kw in _KEYWORDS_T2:
        if kw in t:
            return 0.5
    return 0.0


def _abstract_score(abstract: str) -> float:
    """0.0–1.0 based on keyword density in abstract."""
    if not abstract:
        return 0.0
    a = _normalise(abstract)
    words = len(re.findall(r"\w+", a)) or 1

    hits_t1 = sum(1 for kw in _KEYWORDS_T1 if kw in a)
    hits_t2 = sum(1 for kw in _KEYWORDS_T2 if kw in a)

    # Weighted hit count normalised to word density
    raw = hits_t1 * 2.0 + hits_t2 * 1.0
    density = raw / (words / 100)  # hits per 100 words

    # Saturate: density ≥ 3 → 1.0
    return min(density / 3.0, 1.0)


def _venue_score(paper: Paper) -> float:
    """0.0–1.0 based on journal / venue inferred from the paper's source."""
    # arXiv papers don't carry venue info at scrape time.
    # Return a neutral 0.5 so it doesn't penalise good arXiv preprints.
    # Phase 2+ can enrich with Semantic Scholar venue data.
    if paper.source == "arxiv":
        return 0.5

    abstract_lower = _normalise(paper.abstract)
    title_lower = _normalise(paper.title)
    combined = abstract_lower + " " + title_lower

    for v in _TIER1_VENUES:
        if v in combined:
            return 1.0
    for v in _TIER2_VENUES:
        if v in combined:
            return 0.6
    return 0.3


def _recency_score(published_date: str) -> float:
    """0.0–1.0 based on days since publication. Decays linearly over 30 days."""
    if not published_date:
        return 0.5  # unknown — neutral
    try:
        pub = datetime.fromisoformat(published_date)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - pub).total_seconds() / 86400
        # 0 days old → 1.0,  30 days old → 0.0
        return max(0.0, 1.0 - age_days / 30.0)
    except ValueError:
        return 0.5


# ── Public API ────────────────────────────────────────────────────────────────

WEIGHTS = {
    "title": 0.40,
    "abstract": 0.30,
    "venue": 0.15,
    "recency": 0.15,
}


def score_paper(paper: Paper) -> float:
    """Compute and return the relevance score (0.0–1.0) for a paper."""
    ts = _title_score(paper.title)
    ab = _abstract_score(paper.abstract)
    ve = _venue_score(paper)
    re_ = _recency_score(paper.published_date)

    score = (
        WEIGHTS["title"] * ts
        + WEIGHTS["abstract"] * ab
        + WEIGHTS["venue"] * ve
        + WEIGHTS["recency"] * re_
    )

    log.debug(
        "Score %.3f (t=%.2f a=%.2f v=%.2f r=%.2f)  %s",
        score, ts, ab, ve, re_, paper.source_id,
    )
    return round(score, 4)


def score_and_filter(papers: list[Paper]) -> tuple[list[Paper], list[Paper]]:
    """Score all papers, split into (above_threshold, below_threshold).

    Mutates paper.score in place for all papers.
    Threshold from settings.scorer_min_score.
    """
    above, below = [], []
    for paper in papers:
        paper.score = score_paper(paper)
        if paper.score >= settings.scorer_min_score:
            above.append(paper)
        else:
            below.append(paper)

    log.info(
        "Scored %d papers: %d above threshold (%.2f), %d below",
        len(papers), len(above), settings.scorer_min_score, len(below),
    )
    return above, below
