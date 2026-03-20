"""Telegram approval workflow for OptiX Bot.

Flow:
  1. Bot sends draft thread preview to TELEGRAM_CHAT_ID
  2. Inline keyboard: ✅ Approve | ❌ Reject | ✏️ Edit
  3. Approve → queue.status = 'approved'  → poster picks it up
  4. Reject  → queue.status = 'rejected'
  5. Edit    → bot asks for replacement text → re-previews

Usage (called from main.py post pipeline):
  from src.telegram_bot import send_pending_previews, run_approval_bot
"""

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import settings
from src.db import (
    QueueItem,
    get_paper,
    get_pending_queue,
    set_queue_status,
    set_telegram_message_id,
)

log = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────────────

WAITING_EDIT_TEXT = 1  # ConversationHandler state: waiting for user's edited text

# ── Callback data prefixes ───────────────────────────────────────────────────

CB_APPROVE = "approve"
CB_REJECT = "reject"
CB_EDIT = "edit"


# ── Keyboard builder ──────────────────────────────────────────────────────────

def _keyboard(queue_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"{CB_APPROVE}:{queue_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"{CB_REJECT}:{queue_id}"),
            InlineKeyboardButton("✏️ Edit",    callback_data=f"{CB_EDIT}:{queue_id}"),
        ]
    ])


# ── Send previews ─────────────────────────────────────────────────────────────

async def send_pending_previews(app: Application) -> int:
    """Push all pending queue items to Telegram for approval.

    Returns number of messages sent.
    """
    items = get_pending_queue()
    if not items:
        log.info("No pending items to send")
        return 0

    sent = 0
    for item in items:
        if item.telegram_message_id:
            log.debug("Queue %d already sent (msg_id=%d)", item.id, item.telegram_message_id)
            continue
        paper = get_paper(item.paper_id)
        header = (
            f"📄 *New paper draft* (queue #{item.id})\n"
            f"Score: `{paper.score:.3f}`\n"
            f"[{paper.title}]({paper.url})\n\n"
            if paper else f"📄 *New draft* (queue #{item.id})\n\n"
        )
        body = _escape_md(item.draft_text)
        text = header + body

        try:
            msg = await app.bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=_keyboard(item.id),
                disable_web_page_preview=True,
            )
            set_telegram_message_id(item.id, msg.message_id)
            log.info("Sent preview for queue %d (tg_msg=%d)", item.id, msg.message_id)
            sent += 1
        except Exception as exc:
            log.error("Failed to send preview for queue %d: %s", item.id, exc)

    return sent


# ── Callback handlers ─────────────────────────────────────────────────────────

async def _handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    queue_id = int(query.data.split(":")[1])
    set_queue_status(queue_id, "approved")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"✅ Queue #{queue_id} approved — will post on next run.")
    log.info("Queue %d approved by user", queue_id)


async def _handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    queue_id = int(query.data.split(":")[1])
    set_queue_status(queue_id, "rejected")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"❌ Queue #{queue_id} rejected.")
    log.info("Queue %d rejected by user", queue_id)


async def _handle_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt the user to send replacement draft text."""
    query = update.callback_query
    await query.answer()
    queue_id = int(query.data.split(":")[1])
    context.user_data["editing_queue_id"] = queue_id
    await query.message.reply_text(
        f"✏️ Send the new draft for queue #{queue_id}.\n"
        "Format: one tweet per line, or a JSON array.\n"
        "Send /cancel to abort."
    )
    return WAITING_EDIT_TEXT


async def _handle_edit_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the replacement text, update DB, and re-preview."""
    from src.db import set_queue_status  # avoid circular at module level
    import sqlite3, os
    from src.config import settings as cfg

    queue_id = context.user_data.get("editing_queue_id")
    if not queue_id:
        await update.message.reply_text("No edit in progress.")
        return ConversationHandler.END

    new_text = update.message.text.strip()

    # Persist the edited draft text directly via sqlite3
    db_path = cfg.db_path
    try:
        import sqlite3 as _sq3
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        con = _sq3.connect(db_path)
        con.execute(
            "UPDATE queue SET draft_text=?, updated_at=?, telegram_message_id=NULL WHERE id=?",
            (new_text, now, queue_id),
        )
        con.commit()
        con.close()
        log.info("Queue %d draft updated by user", queue_id)
    except Exception as exc:
        log.error("Failed to update draft for queue %d: %s", queue_id, exc)
        await update.message.reply_text(f"Error saving edit: {exc}")
        return ConversationHandler.END

    # Re-send preview
    await update.message.reply_text(
        f"✅ Draft updated! Re-sending preview for queue #{queue_id}..."
    )
    # Re-send via bot directly
    try:
        msg = await update.message.reply_text(
            f"📄 *Updated draft* (queue #{queue_id})\n\n{_escape_md(new_text)}",
            parse_mode="Markdown",
            reply_markup=_keyboard(queue_id),
        )
        set_telegram_message_id(queue_id, msg.message_id)
    except Exception as exc:
        log.error("Failed to re-preview queue %d: %s", queue_id, exc)

    context.user_data.pop("editing_queue_id", None)
    return ConversationHandler.END


async def _handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("editing_queue_id", None)
    await update.message.reply_text("Edit cancelled.")
    return ConversationHandler.END


# ── /status command ───────────────────────────────────────────────────────────

async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show queue stats."""
    import sqlite3
    try:
        con = sqlite3.connect(settings.db_path)
        rows = con.execute(
            "SELECT status, COUNT(*) as n FROM queue GROUP BY status"
        ).fetchall()
        con.close()
        lines = ["📊 *Queue status*"]
        for status, n in rows:
            lines.append(f"  {status}: {n}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as exc:
        await update.message.reply_text(f"Error: {exc}")


# ── Build application ─────────────────────────────────────────────────────────

def build_app() -> Application:
    """Build and configure the telegram Application (does not start polling)."""
    app = Application.builder().token(settings.telegram_bot_token).build()

    # Edit conversation handler
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(_handle_edit_start, pattern=f"^{CB_EDIT}:")],
        states={
            WAITING_EDIT_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_edit_receive),
            ]
        },
        fallbacks=[CommandHandler("cancel", _handle_cancel)],
    )

    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(_handle_approve, pattern=f"^{CB_APPROVE}:"))
    app.add_handler(CallbackQueryHandler(_handle_reject, pattern=f"^{CB_REJECT}:"))
    app.add_handler(CommandHandler("status", _cmd_status))

    return app


async def send_previews_only() -> int:
    """Build app, send pending previews, then shut down.

    Used by the 'scrape' cron job after generating drafts.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping preview send")
        return 0
    app = build_app()
    async with app:
        return await send_pending_previews(app)


async def run_approval_bot() -> None:
    """Start the bot in polling mode (long-running process).

    Typically launched as a separate Railway service or local process.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return

    app = build_app()
    log.info("Starting Telegram approval bot (polling)…")
    async with app:
        await app.initialize()
        await send_pending_previews(app)  # Send any already-pending items on startup
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        log.info("Bot running — press Ctrl-C to stop")
        # Keep running until interrupted
        import asyncio
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await app.updater.stop()
            await app.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _escape_md(text: str) -> str:
    """Minimal Markdown escaping for Telegram (V1 parse mode)."""
    # In Telegram MarkdownV1, only * _ ` [ need escaping in body text
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text
