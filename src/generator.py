"""Thread generator — turns a Paper into a 3–5 tweet draft via Claude API.

Output format: JSON array of tweet strings, each ≤ 280 chars, numbered N/N.
Last tweet appends #PhotonicComputing #OpticalAI and the arXiv link.
"""

import json
import logging
import re
from typing import Optional

import anthropic

from src.config import settings
from src.db import Paper

log = logging.getLogger(__name__)

# ── Prompt templates ─────────────────────────────────────────────────────────

_SYSTEM = """\
You are a science communicator who writes viral X (Twitter) threads about \
optical and photonic computing research. Your audience is photonics researchers \
and EE/physics grad students — they are technically sophisticated.

Rules you MUST follow:
1. Produce exactly 3–5 tweets as a JSON array of strings.
2. Each tweet MUST be ≤ 280 characters (including the N/N numbering).
3. Number every tweet: "1/N", "2/N", … "N/N" at the start.
4. Tweet 1: one crisp hook sentence — the most surprising or important result.
5. Tweets 2–(N-1): key technical insight, method, and result (one per tweet).
6. Tweet N: "Why it matters" + the arXiv URL on its own line.
7. Append "#PhotonicComputing #OpticalAI" only to the last tweet.
8. NO hashtags anywhere else.
9. Output ONLY the raw JSON array — no markdown fences, no extra text.

Example output format:
["1/4 Hook sentence here.",
 "2/4 Technical insight.",
 "3/4 Method and result.",
 "4/4 Why it matters.\\nhttps://arxiv.org/abs/XXXX.XXXXX\\n#PhotonicComputing #OpticalAI"]
"""


def _build_prompt(paper: Paper) -> str:
    authors_str = ", ".join(paper.authors[:5])
    if len(paper.authors) > 5:
        authors_str += f" et al. ({len(paper.authors)} authors)"

    return (
        f"Write a Twitter thread about this optical/photonic computing paper.\n\n"
        f"Title: {paper.title}\n"
        f"Authors: {authors_str}\n"
        f"arXiv URL: {paper.url}\n"
        f"Abstract:\n{paper.abstract}\n\n"
        f"Remember: the last tweet must include the arXiv URL and the hashtags."
    )


# ── Tweet validation ──────────────────────────────────────────────────────────

def _validate_tweets(tweets: list[str]) -> list[str]:
    """Check length constraints and truncate gracefully if needed."""
    validated: list[str] = []
    for i, tweet in enumerate(tweets):
        if len(tweet) > 280:
            log.warning("Tweet %d is %d chars — truncating", i + 1, len(tweet))
            tweet = tweet[:277] + "…"
        validated.append(tweet)
    return validated


# ── Claude API call ──────────────────────────────────────────────────────────

def generate_thread(paper: Paper) -> Optional[list[str]]:
    """Call Claude to generate a tweet thread for the given paper.

    Returns a list of tweet strings, or None on failure.
    Uses streaming to handle large outputs reliably.
    """
    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot generate thread")
        return None

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = _build_prompt(paper)

    log.info("Generating thread for %s via %s", paper.source_id, settings.claude_model)

    try:
        raw_text = ""
        with client.messages.stream(
            model=settings.claude_model,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for chunk in stream.text_stream:
                raw_text += chunk
        log.debug("Raw Claude output: %s", raw_text[:200])
    except anthropic.AuthenticationError:
        log.error("Invalid ANTHROPIC_API_KEY")
        return None
    except anthropic.RateLimitError:
        log.error("Claude rate limit hit — will retry on next run")
        return None
    except anthropic.APIStatusError as exc:
        log.error("Claude API error %d: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.error("Claude connection error: %s", exc)
        return None

    # Parse JSON array from response
    tweets = _parse_tweets(raw_text)
    if not tweets:
        log.error("Failed to parse tweet array from response: %s", raw_text[:300])
        return None

    if not (3 <= len(tweets) <= 5):
        log.warning("Expected 3–5 tweets, got %d — proceeding anyway", len(tweets))

    tweets = _validate_tweets(tweets)
    log.info("Generated %d tweets for %s", len(tweets), paper.source_id)
    return tweets


def _parse_tweets(text: str) -> Optional[list[str]]:
    """Extract a JSON array of strings from Claude's response text."""
    # Strip markdown fences if present
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and all(isinstance(t, str) for t in parsed):
            return parsed
    except json.JSONDecodeError:
        pass

    # Fallback: find the first JSON array in the text
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list) and all(isinstance(t, str) for t in parsed):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


# ── Convenience: format thread for preview ───────────────────────────────────

def format_thread_preview(tweets: list[str]) -> str:
    """Return a human-readable preview of the thread (for Telegram, logs, etc.)."""
    lines = []
    for i, tweet in enumerate(tweets, 1):
        lines.append(f"─── Tweet {i} ({'%d chars' % len(tweet)}) ───")
        lines.append(tweet)
    return "\n".join(lines)
