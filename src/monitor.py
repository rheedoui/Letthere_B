"""Monitoring utilities for OptiX Bot.

Responsibilities:
- Post a daily stats digest to Telegram
- Send error alerts to Telegram on pipeline failures
- Expose a simple health-check function for Railway

Usage:
  from src.monitor import send_daily_stats, alert, health_check
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.config import settings

log = logging.getLogger(__name__)


# ── Health check ─────────────────────────────────────────────────────────────

def health_check() -> dict:
    """Return a dict with basic health indicators.

    Checks:
    - DB reachable and tables exist
    - Last scrape time
    - Last post time
    - Pending queue length
    """
    result: dict = {"ok": True, "errors": []}

    try:
        con = sqlite3.connect(settings.db_path)
        con.row_factory = sqlite3.Row

        # Paper count + last scrape
        row = con.execute(
            "SELECT COUNT(*) as n, MAX(scraped_at) as last FROM papers"
        ).fetchone()
        result["papers_total"] = row["n"]
        result["last_scraped_at"] = row["last"]

        # Queue breakdown
        rows = con.execute(
            "SELECT status, COUNT(*) as n FROM queue GROUP BY status"
        ).fetchall()
        result["queue"] = {r["status"]: r["n"] for r in rows}

        # Last post
        row = con.execute(
            "SELECT MAX(posted_at) as last FROM posted"
        ).fetchone()
        result["last_posted_at"] = row["last"]

        con.close()
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"DB error: {exc}")

    return result


# ── Telegram helpers ──────────────────────────────────────────────────────────

async def _send_telegram(text: str) -> bool:
    """Send a plain text message to TELEGRAM_CHAT_ID. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("Telegram not configured — skipping message")
        return False

    try:
        from telegram import Bot
        bot = Bot(token=settings.telegram_bot_token)
        async with bot:
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


# ── Public API ────────────────────────────────────────────────────────────────

async def send_daily_stats() -> bool:
    """Send a daily stats digest to Telegram."""
    h = health_check()
    queue = h.get("queue", {})

    lines = [
        "📊 *OptiX Bot — Daily Stats*",
        "",
        f"Papers in DB: `{h.get('papers_total', '?')}`",
        f"Last scrape:  `{_fmt_ts(h.get('last_scraped_at'))}`",
        f"Last post:    `{_fmt_ts(h.get('last_posted_at'))}`",
        "",
        "*Queue*",
    ]
    for status in ("pending", "approved", "rejected", "posted"):
        n = queue.get(status, 0)
        emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌", "posted": "🚀"}.get(status, "·")
        lines.append(f"  {emoji} {status}: `{n}`")

    if not h["ok"]:
        lines.append("")
        lines.append("⚠️ *Errors detected:*")
        for err in h["errors"]:
            lines.append(f"  `{err}`")

    return await _send_telegram("\n".join(lines))


async def alert(message: str, exc: Optional[Exception] = None) -> bool:
    """Send an error alert to Telegram.

    Call from except blocks in the pipeline so operators are notified.
    """
    text = f"🚨 *OptiX Bot Alert*\n\n{message}"
    if exc:
        text += f"\n\n```\n{type(exc).__name__}: {exc}\n```"
    log.error("ALERT: %s %s", message, exc or "")
    return await _send_telegram(text)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _fmt_ts(ts: Optional[str]) -> str:
    """Format an ISO timestamp to a human-readable string, or 'never'."""
    if not ts:
        return "never"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return ts
