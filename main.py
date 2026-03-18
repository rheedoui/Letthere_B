"""OptiX Bot — CLI entry point.

Usage:
  python main.py scrape   # Scrape sources and store new papers
  python main.py post     # Process approved queue and post to X (Phase 2)
"""

import logging
import sys

from src.config import settings
from src.db import init_db, insert_paper, paper_exists
from src.scraper import scrape_all

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("main")


def cmd_scrape() -> None:
    init_db()
    papers = scrape_all()

    new_count = 0
    for paper in papers:
        if paper_exists(paper.source, paper.source_id):
            log.debug("Already in DB: %s/%s", paper.source, paper.source_id)
            continue
        row_id = insert_paper(paper)
        if row_id:
            new_count += 1
            log.info("+ [%s] %s", paper.source_id, paper.title[:80])

    log.info("Scrape done — %d new / %d total fetched", new_count, len(papers))


def cmd_post() -> None:
    # Phase 2: scorer → generator → poster
    log.info("Post pipeline not yet implemented (Phase 2)")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "scrape"
    if command == "scrape":
        cmd_scrape()
    elif command == "post":
        cmd_post()
    else:
        print(f"Unknown command: {command}")
        print("Usage: python main.py [scrape|post]")
        sys.exit(1)


if __name__ == "__main__":
    main()
