"""X (Twitter) poster for OptiX Bot.

Posts approved queue items as reply-chain threads using Tweepy v4 + OAuth 1.0a.

Rate-limit guard: enforces POSTER_MIN_INTERVAL_MIN between threads.
On success: inserts into `posted`, sets queue.status = 'posted'.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import tweepy

from src.config import settings
from src.db import (
    QueueItem,
    get_approved_queue,
    get_paper,
    last_posted_at,
    mark_posted,
    set_queue_status,
)

log = logging.getLogger(__name__)


# ── Client factory ────────────────────────────────────────────────────────────

def _build_client() -> Optional[tweepy.Client]:
    """Build a Tweepy v2 Client with OAuth 1.0a user context."""
    if not all([
        settings.x_api_key,
        settings.x_api_secret,
        settings.x_access_token,
        settings.x_access_secret,
    ]):
        log.error("X API credentials not fully configured")
        return None

    return tweepy.Client(
        consumer_key=settings.x_api_key,
        consumer_secret=settings.x_api_secret,
        access_token=settings.x_access_token,
        access_token_secret=settings.x_access_secret,
        wait_on_rate_limit=True,
    )


# ── Rate limit guard ──────────────────────────────────────────────────────────

def _within_rate_limit() -> bool:
    """Return True if enough time has passed since the last post."""
    last = last_posted_at()
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        min_gap = timedelta(minutes=settings.poster_min_interval_min)
        elapsed = datetime.now(timezone.utc) - last_dt
        if elapsed < min_gap:
            remaining = (min_gap - elapsed).seconds // 60
            log.info("Rate limit guard: %d min remaining before next post", remaining)
            return False
    except ValueError:
        pass
    return True


# ── Thread posting ────────────────────────────────────────────────────────────

def _post_thread(client: tweepy.Client, tweets: list[str]) -> Optional[str]:
    """Post tweets as a reply-chain thread.

    Returns the tweet ID of the first tweet, or None on failure.
    """
    first_tweet_id: Optional[str] = None
    reply_to_id: Optional[str] = None

    for i, text in enumerate(tweets):
        try:
            kwargs: dict = {"text": text}
            if reply_to_id:
                kwargs["in_reply_to_tweet_id"] = reply_to_id

            response = client.create_tweet(**kwargs)
            tweet_id = str(response.data["id"])

            if i == 0:
                first_tweet_id = tweet_id
            reply_to_id = tweet_id

            log.info("Posted tweet %d/%d  id=%s", i + 1, len(tweets), tweet_id)

        except tweepy.TweepyException as exc:
            log.error("Failed to post tweet %d/%d: %s", i + 1, len(tweets), exc)
            # Return first_tweet_id if at least one tweet went out,
            # so we can still record a partial post.
            return first_tweet_id

    return first_tweet_id


def _parse_draft(draft_text: str) -> list[str]:
    """Split stored draft into individual tweet strings.

    Supports two formats:
      1. JSON array  — ["tweet1", "tweet2", ...]
      2. Double newline separated — tweet1\\n\\ntweet2\\n\\n...
    """
    import json, re

    text = draft_text.strip()

    # Try JSON first
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and all(isinstance(t, str) for t in parsed):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: split on double newlines
    parts = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    return parts


# ── Public entry point ────────────────────────────────────────────────────────

def post_approved() -> int:
    """Post all approved queue items to X.

    Returns number of threads successfully posted.
    """
    if not _within_rate_limit():
        return 0

    client = _build_client()
    if client is None:
        return 0

    items = get_approved_queue()
    if not items:
        log.info("No approved items to post")
        return 0

    posted_count = 0
    for item in items:
        if not _within_rate_limit():
            log.info("Rate limit reached — deferring remaining %d items", len(items) - posted_count)
            break

        tweets = _parse_draft(item.draft_text)
        if not tweets:
            log.error("Queue %d has empty/unparseable draft — rejecting", item.id)
            set_queue_status(item.id, "rejected")
            continue

        paper = get_paper(item.paper_id)
        log.info(
            "Posting queue %d (%d tweets)%s",
            item.id,
            len(tweets),
            f" — {paper.title[:60]}" if paper else "",
        )

        first_tweet_id = _post_thread(client, tweets)
        if first_tweet_id:
            mark_posted(item.id, first_tweet_id)
            posted_count += 1
            log.info("Posted queue %d — first tweet id=%s", item.id, first_tweet_id)
        else:
            log.error("Failed to post any tweets for queue %d", item.id)

    return posted_count
