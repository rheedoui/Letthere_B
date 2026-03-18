"""SQLite data access layer for OptiX Bot.

Tables:
  papers  — scraped paper metadata
  queue   — draft posts pending Telegram approval
  posted  — successfully posted threads
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generator, Optional

from src.config import settings

log = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS papers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source         TEXT    NOT NULL,
    source_id      TEXT    NOT NULL,
    title          TEXT    NOT NULL,
    authors        TEXT    NOT NULL DEFAULT '[]',   -- JSON array
    abstract       TEXT    NOT NULL DEFAULT '',
    url            TEXT    NOT NULL,
    published_date TEXT    NOT NULL DEFAULT '',
    score          REAL    NOT NULL DEFAULT 0.0,
    scraped_at     TEXT    NOT NULL,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS queue (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id             INTEGER NOT NULL REFERENCES papers(id),
    draft_text           TEXT    NOT NULL,
    telegram_message_id  INTEGER,
    status               TEXT    NOT NULL DEFAULT 'pending',   -- pending/approved/rejected/posted
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS posted (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id  INTEGER NOT NULL REFERENCES queue(id),
    tweet_id  TEXT,
    posted_at TEXT    NOT NULL
);
"""

# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class Paper:
    source: str
    source_id: str
    title: str
    url: str
    authors: list[str] = field(default_factory=list)
    abstract: str = ""
    published_date: str = ""
    score: float = 0.0
    id: Optional[int] = None


@dataclass
class QueueItem:
    paper_id: int
    draft_text: str
    status: str = "pending"
    telegram_message_id: Optional[int] = None
    id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""


# ── Connection helper ───────────────────────────────────────────────────────

@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    os.makedirs(settings.data_dir, exist_ok=True)
    con = sqlite3.connect(settings.db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Init ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist."""
    with _conn() as con:
        con.executescript(_DDL)
    log.info("DB initialised at %s", settings.db_path)


# ── Papers ──────────────────────────────────────────────────────────────────

def insert_paper(paper: Paper) -> Optional[int]:
    """Insert a paper, skip on duplicate (source, source_id). Returns new row id or None."""
    sql = """
        INSERT OR IGNORE INTO papers
            (source, source_id, title, authors, abstract, url, published_date, score, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    now = _utcnow()
    with _conn() as con:
        cur = con.execute(sql, (
            paper.source,
            paper.source_id,
            paper.title,
            json.dumps(paper.authors),
            paper.abstract,
            paper.url,
            paper.published_date,
            paper.score,
            now,
        ))
        if cur.lastrowid and cur.rowcount:
            log.debug("Inserted paper id=%d  source_id=%s", cur.lastrowid, paper.source_id)
            return cur.lastrowid
    return None  # duplicate


def paper_exists(source: str, source_id: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM papers WHERE source=? AND source_id=?", (source, source_id)
        ).fetchone()
    return row is not None


def get_paper(paper_id: int) -> Optional[Paper]:
    with _conn() as con:
        row = con.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    return _row_to_paper(row) if row else None


def update_paper_score(paper_id: int, score: float) -> None:
    with _conn() as con:
        con.execute("UPDATE papers SET score=? WHERE id=?", (score, paper_id))


# ── Queue ────────────────────────────────────────────────────────────────────

def enqueue(paper_id: int, draft_text: str) -> int:
    """Add a draft to the approval queue. Returns queue row id."""
    now = _utcnow()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO queue (paper_id, draft_text, status, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, ?)""",
            (paper_id, draft_text, now, now),
        )
        return cur.lastrowid


def set_telegram_message_id(queue_id: int, message_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE queue SET telegram_message_id=?, updated_at=? WHERE id=?",
            (message_id, _utcnow(), queue_id),
        )


def set_queue_status(queue_id: int, status: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE queue SET status=?, updated_at=? WHERE id=?",
            (status, _utcnow(), queue_id),
        )


def get_pending_queue() -> list[QueueItem]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM queue WHERE status='pending' ORDER BY created_at"
        ).fetchall()
    return [_row_to_queue(r) for r in rows]


def get_approved_queue() -> list[QueueItem]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM queue WHERE status='approved' ORDER BY created_at"
        ).fetchall()
    return [_row_to_queue(r) for r in rows]


# ── Posted ───────────────────────────────────────────────────────────────────

def mark_posted(queue_id: int, tweet_id: Optional[str] = None) -> None:
    now = _utcnow()
    with _conn() as con:
        con.execute(
            "INSERT INTO posted (queue_id, tweet_id, posted_at) VALUES (?, ?, ?)",
            (queue_id, tweet_id, now),
        )
        con.execute(
            "UPDATE queue SET status='posted', updated_at=? WHERE id=?",
            (now, queue_id),
        )


def last_posted_at() -> Optional[str]:
    """Return ISO timestamp of the most recent post, or None."""
    with _conn() as con:
        row = con.execute(
            "SELECT posted_at FROM posted ORDER BY posted_at DESC LIMIT 1"
        ).fetchone()
    return row["posted_at"] if row else None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_paper(row: sqlite3.Row) -> Paper:
    return Paper(
        id=row["id"],
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        authors=json.loads(row["authors"]),
        abstract=row["abstract"],
        url=row["url"],
        published_date=row["published_date"],
        score=row["score"],
    )


def _row_to_queue(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=row["id"],
        paper_id=row["paper_id"],
        draft_text=row["draft_text"],
        telegram_message_id=row["telegram_message_id"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
