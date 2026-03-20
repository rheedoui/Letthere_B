"""OptiX Bot — CLI entry point.

Usage:
  python main.py scrape    # Scrape sources, score, generate drafts, enqueue
  python main.py generate  # (Re-)generate drafts for unqueued high-score papers
  python main.py post      # Process approved queue and post to X (Phase 3)
"""

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
    """Scrape → score → generate → enqueue (full Phase 2 pipeline)."""
    init_db()
    papers = scrape_all()

    # Score all papers; split into above/below threshold
    above, below = score_and_filter(papers)

    # Persist all papers first (dedup handled by insert_paper)
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
        # Update score even for already-stored papers? Skipped — dedup means
        # we skip them above. Score updates on re-scrape could be added later.

    log.info("Scrape done — %d new / %d total fetched", new_count, len(papers))

    # For high-scoring new papers, generate thread drafts and enqueue
    high_score_new = [p for p in above if not paper_exists(p.source, p.source_id) is False]
    # Simpler: generate for papers we just stored that scored above threshold
    above_ids = {p.source_id for p in above}
    queued = 0
    for row_id in stored_ids:
        paper = get_paper(row_id)
        if paper is None:
            continue
        if paper.source_id not in above_ids:
            log.debug("Score below threshold — skipping generation for %s", paper.source_id)
            continue
        tweets = generate_thread(paper)
        if tweets:
            draft = "\n\n".join(tweets)
            enqueue(row_id, draft)
            queued += 1
            log.info("Queued draft for %s", paper.source_id)
            log.debug(format_thread_preview(tweets))

    log.info("Pipeline done — %d papers queued for approval", queued)


def cmd_generate() -> None:
    """Re-run generator on papers that are already stored but not yet queued."""
    init_db()
    log.info("Generate command not yet implemented — use 'scrape' for full pipeline")


def cmd_post() -> None:
    # Phase 3: telegram_bot + poster
    log.info("Post pipeline not yet implemented (Phase 3)")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "scrape"
    if command == "scrape":
        cmd_scrape()
    elif command == "generate":
        cmd_generate()
    elif command == "post":
        cmd_post()
    else:
        print(f"Unknown command: {command}")
        print("Usage: python main.py [scrape|generate|post]")
        sys.exit(1)


if __name__ == "__main__":
    main()
