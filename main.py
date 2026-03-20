"""OptiX Bot — CLI entry point.

Usage:
  python main.py scrape    # Scrape → score → generate → enqueue → send Telegram previews
  python main.py post      # Post approved items to X; send any new previews to Telegram
  python main.py bot       # Run the Telegram approval bot in polling mode (long-running)
  python main.py stats     # Send daily stats digest to Telegram
  python main.py health    # Print health check to stdout
"""

import asyncio
import logging
import sys

from src.config import settings
from src.db import (
    get_paper,
    init_db,
    insert_paper,
    enqueue,
    paper_exists,
    update_paper_score,
)
from src.generator import format_thread_preview, generate_thread
from src.scorer import score_and_filter
from src.scraper import scrape_all

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("main")


def cmd_scrape() -> None:
    """Scrape → score → generate → enqueue (full pipeline)."""
    init_db()

    from src.monitor import alert

    try:
        papers = scrape_all()
    except Exception as exc:
        asyncio.run(alert("scrape_all() failed", exc))
        raise

    # Score all papers; split into above/below threshold
    above, below = score_and_filter(papers)

    # Persist all papers (dedup handled by insert_paper)
    stored_ids: list[int] = []
    new_count = 0
    for paper in papers:
        if paper_exists(paper.source, paper.source_id):
            log.debug("Already in DB: %s/%s", paper.source, paper.source_id)
            continue
        row_id = insert_paper(paper)
        if row_id:
            new_count += 1
            log.info("+ [%.3f] [%s] %s", paper.score, paper.source_id, paper.title[:70])
            stored_ids.append(row_id)

    log.info("Scrape done — %d new / %d total fetched", new_count, len(papers))

    # Generate thread drafts for high-scoring new papers
    above_ids = {p.source_id for p in above}
    queued = 0
    for row_id in stored_ids:
        paper = get_paper(row_id)
        if paper is None:
            continue
        if paper.source_id not in above_ids:
            log.debug("Score below threshold — skipping generation for %s", paper.source_id)
            continue
        try:
            tweets = generate_thread(paper)
        except Exception as exc:
            asyncio.run(alert(f"generate_thread failed for {paper.source_id}", exc))
            continue
        if tweets:
            draft = "\n\n".join(tweets)
            enqueue(row_id, draft)
            queued += 1
            log.info("Queued draft for %s", paper.source_id)
            log.debug(format_thread_preview(tweets))

    log.info("Pipeline done — %d papers queued for approval", queued)

    # Send Telegram previews for newly queued items
    from src.telegram_bot import send_previews_only
    sent = asyncio.run(send_previews_only())
    log.info("Sent %d Telegram preview(s)", sent)


def cmd_post() -> None:
    """Post approved items to X and send previews for any new pending items."""
    init_db()

    from src.poster import post_approved
    from src.telegram_bot import send_previews_only
    from src.monitor import alert

    try:
        posted = post_approved()
    except Exception as exc:
        asyncio.run(alert("post_approved() failed", exc))
        raise
    log.info("Posted %d thread(s) to X", posted)

    sent = asyncio.run(send_previews_only())
    if sent:
        log.info("Sent %d new Telegram preview(s)", sent)


def cmd_bot() -> None:
    """Run the Telegram approval bot in polling mode (long-running)."""
    init_db()
    from src.telegram_bot import run_approval_bot
    asyncio.run(run_approval_bot())


def cmd_stats() -> None:
    """Send daily stats digest to Telegram."""
    init_db()
    from src.monitor import send_daily_stats
    ok = asyncio.run(send_daily_stats())
    log.info("Stats sent: %s", ok)


def cmd_health() -> None:
    """Print health check results to stdout."""
    init_db()
    import json
    from src.monitor import health_check
    result = health_check()
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        sys.exit(1)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "scrape"
    dispatch = {
        "scrape": cmd_scrape,
        "post":   cmd_post,
        "bot":    cmd_bot,
        "stats":  cmd_stats,
        "health": cmd_health,
    }
    fn = dispatch.get(command)
    if fn is None:
        print(f"Unknown command: {command}")
        print(f"Usage: python main.py [{' | '.join(dispatch)}]")
        sys.exit(1)
    fn()


if __name__ == "__main__":
    main()
