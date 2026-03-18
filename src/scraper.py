"""Scrapers for optical computing research content.

Phase 1: arXiv only.
Phase 2+: Semantic Scholar, RSS (Optica / Nature Photonics / IEEE).
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import feedparser

from src.config import settings
from src.db import Paper

log = logging.getLogger(__name__)

# ── arXiv constants ──────────────────────────────────────────────────────────

_ARXIV_BASE = "http://export.arxiv.org/api/query"

# Categories to search within
_ARXIV_CATS = [
    "physics.optics",
    "cs.ET",
    "eess.SP",
    "quant-ph",
]

# Title / abstract keywords (at least one must match for a result to be relevant)
_KEYWORDS = [
    "optical computing",
    "photonic computing",
    "optical neural network",
    "photonic neural network",
    "silicon photonics",
    "photonic integrated circuit",
    "all-optical",
    "diffractive neural network",
    "optical matrix",
    "coherent computing",
]


def _build_arxiv_query() -> str:
    """Build an arXiv search query string.

    Searches across the target categories for papers whose title OR abstract
    contains at least one of the target keywords.
    """
    cat_clause = " OR ".join(f"cat:{c}" for c in _ARXIV_CATS)
    kw_clause = " OR ".join(f'ti:"{kw}" OR abs:"{kw}"' for kw in _KEYWORDS)
    return f"({cat_clause}) AND ({kw_clause})"


def _parse_arxiv_id(entry_id: str) -> str:
    """Extract bare arXiv ID from the full entry URL, e.g. '2401.12345v1' → '2401.12345'."""
    bare = entry_id.split("/abs/")[-1]
    return bare.split("v")[0]  # strip version suffix


def _entry_to_paper(entry) -> Optional[Paper]:
    """Convert a feedparser entry to a Paper dataclass. Returns None on parse error."""
    try:
        source_id = _parse_arxiv_id(entry.id)
        title = entry.title.replace("\n", " ").strip()
        abstract = getattr(entry, "summary", "").replace("\n", " ").strip()
        url = f"https://arxiv.org/abs/{source_id}"

        authors: list[str] = []
        for a in getattr(entry, "authors", []):
            name = getattr(a, "name", "").strip()
            if name:
                authors.append(name)

        published_date = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            published_date = dt.isoformat()

        return Paper(
            source="arxiv",
            source_id=source_id,
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            published_date=published_date,
        )
    except Exception as exc:
        log.warning("Failed to parse arXiv entry %s: %s", getattr(entry, "id", "?"), exc)
        return None


def _is_recent(paper: Paper, lookback_days: int) -> bool:
    """Return True if paper.published_date is within the lookback window."""
    if not paper.published_date:
        return True  # can't tell — include it
    try:
        pub = datetime.fromisoformat(paper.published_date)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        return pub >= cutoff
    except ValueError:
        return True


def scrape_arxiv(
    lookback_days: Optional[int] = None,
    max_results: Optional[int] = None,
) -> list[Paper]:
    """Fetch recent optical computing papers from arXiv.

    Args:
        lookback_days: Only return papers published within this many days.
                       Defaults to settings.scraper_lookback_days.
        max_results:   Maximum papers to request from the API.
                       Defaults to settings.scraper_max_results.

    Returns:
        List of Paper objects (not yet persisted to DB).
        Empty list on any network / parse failure.
    """
    lookback_days = lookback_days if lookback_days is not None else settings.scraper_lookback_days
    max_results = max_results if max_results is not None else settings.scraper_max_results

    query = _build_arxiv_query()
    params = urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{_ARXIV_BASE}?{params}"

    log.info("Fetching arXiv: max_results=%d lookback=%dd", max_results, lookback_days)
    log.debug("arXiv URL: %s", url)

    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        log.error("arXiv fetch failed: %s", exc)
        return []

    if feed.bozo and feed.bozo_exception:
        log.warning("arXiv feed parse warning: %s", feed.bozo_exception)

    entries = getattr(feed, "entries", [])
    log.info("arXiv returned %d entries", len(entries))

    papers: list[Paper] = []
    for entry in entries:
        paper = _entry_to_paper(entry)
        if paper is None:
            continue
        if not _is_recent(paper, lookback_days):
            log.debug("Skipping old paper %s (%s)", paper.source_id, paper.published_date)
            continue
        papers.append(paper)

    log.info("arXiv: %d papers within lookback window", len(papers))
    return papers


# ── Public scrape entry point ────────────────────────────────────────────────

def scrape_all() -> list[Paper]:
    """Run all enabled scrapers and return combined results.

    Phase 1: arXiv only.
    """
    results: list[Paper] = []

    # arXiv
    try:
        arxiv_papers = scrape_arxiv()
        results.extend(arxiv_papers)
    except Exception as exc:
        log.error("Uncaught error in arXiv scraper: %s", exc)

    # Phase 2: add Semantic Scholar, RSS here

    return results
