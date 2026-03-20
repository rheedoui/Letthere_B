# CLAUDE.md — OptiX Bot

Automated system that scrapes optical computing papers/news, generates concise research thread
posts via Claude API, and posts to X (Twitter). Targets photonics researchers and EE/physics grad
students. All content in English.

**Mode: Semi-automatic** — generates drafts → sends to Telegram for human approval → posts on approval.

---

## Architecture

```
[Scraper] → [Scorer/Filter] → [Claude API Generator] → [Telegram Preview] → [X Poster]
                                                               ↑
                                                         Human approves
```

## Tech Stack

| Layer       | Choice                                  |
|-------------|----------------------------------------|
| Language    | Python 3.11+                            |
| LLM         | Claude API (`claude-sonnet-4-20250514`) |
| Posting     | X API v2 (tweepy)                       |
| Approval    | Telegram Bot API (python-telegram-bot)  |
| Deployment  | Railway (cron jobs)                     |
| Storage     | SQLite (posted history, pending queue)  |

## Project Structure

```
optix-bot/
├── CLAUDE.md
├── .env.example
├── requirements.txt
├── railway.toml
├── main.py                # CLI entry point
└── src/
    ├── __init__.py
    ├── config.py          # Env/settings via pydantic-settings
    ├── db.py              # SQLite schema + data access layer
    ├── scraper.py         # Source scrapers (arXiv, Semantic Scholar, RSS)
    ├── scorer.py          # Relevance scoring & filtering
    ├── generator.py       # Claude API thread generator
    ├── telegram_bot.py    # Approval workflow
    └── poster.py          # X (Twitter) posting
```

---

## Database Schema (`src/db.py`)

Three tables in `data/optix.db`:

### `papers`
| Column         | Type    | Notes                                      |
|----------------|---------|--------------------------------------------|
| id             | INTEGER | PK, autoincrement                          |
| source         | TEXT    | `'arxiv'`, `'semantic_scholar'`, `'rss'`   |
| source_id      | TEXT    | arXiv ID, DOI, or URL — unique per source  |
| title          | TEXT    |                                            |
| authors        | TEXT    | JSON array of strings                      |
| abstract       | TEXT    |                                            |
| url            | TEXT    |                                            |
| published_date | TEXT    | ISO 8601                                   |
| score          | REAL    | Relevance score 0.0–1.0 (set by scorer)    |
| scraped_at     | TEXT    | ISO 8601                                   |

Unique constraint: `(source, source_id)`

### `queue`
| Column             | Type    | Notes                                              |
|--------------------|---------|----------------------------------------------------|
| id                 | INTEGER | PK                                                 |
| paper_id           | INTEGER | FK → papers.id                                     |
| draft_text         | TEXT    | Generated thread text                              |
| telegram_message_id| INTEGER | Telegram msg ID for callback tracking              |
| status             | TEXT    | `pending` / `approved` / `rejected` / `posted`     |
| created_at         | TEXT    | ISO 8601                                           |
| updated_at         | TEXT    | ISO 8601                                           |

### `posted`
| Column    | Type    | Notes              |
|-----------|---------|--------------------|
| id        | INTEGER | PK                 |
| queue_id  | INTEGER | FK → queue.id      |
| tweet_id  | TEXT    | Returned by X API  |
| posted_at | TEXT    | ISO 8601           |

---

## Scraper Spec (`src/scraper.py`)

### arXiv
- API: `http://export.arxiv.org/api/query` (Atom feed via `feedparser`)
- Search categories: `physics.optics`, `cs.ET`, `eess.SP`, `quant-ph`
- Keywords in title/abstract: `optical computing`, `photonic computing`, `optical neural network`,
  `photonic neural network`, `silicon photonics`, `photonic integrated circuit`, `all-optical`,
  `diffractive neural network`, `optical matrix`, `coherent computing`
- `max_results`: 50 per run
- `sortBy`: `submittedDate`, `sortOrder`: `descending`
- Lookback: configurable via `SCRAPER_LOOKBACK_DAYS` (default 3)
- Dedup: skip if `(source='arxiv', source_id)` already in `papers`
- Return: list of `Paper` dataclass objects

### Future sources (Phase 2+)
- Semantic Scholar API
- RSS feeds (Optica, Nature Photonics, IEEE Photonics)

---

## Scorer Spec (`src/scorer.py`) — Phase 2

Score 0.0–1.0 per paper. Weights:

| Signal                          | Weight |
|---------------------------------|--------|
| Title keyword exact match       | 0.40   |
| Abstract keyword density        | 0.30   |
| Author h-index / venue tier     | 0.15   |
| Recency (days since published)  | 0.15   |

Threshold: `SCORER_MIN_SCORE` (default `0.4`). Papers below threshold are stored but not queued.

---

## Generator Spec (`src/generator.py`) — Phase 2

System prompt goal: produce a 3–5 tweet thread targeting photonics researchers / EE grad students.

Rules:
- Tweet 1: hook — one crisp sentence summarizing the breakthrough
- Tweet 2–4: key technical insight, method, result (one tweet each)
- Tweet 5: "Why it matters" + arXiv link
- Max 280 chars per tweet; number tweets `1/N … N/N`
- No hashtags in body (append `#PhotonicComputing #OpticalAI` only to last tweet)
- Output format: JSON array of strings

Claude call:
```python
anthropic.Anthropic().messages.create(
    model=settings.claude_model,   # claude-sonnet-4-20250514
    max_tokens=1024,
    messages=[{"role": "user", "content": prompt}]
)
```

---

## Telegram Approval Workflow (`src/telegram_bot.py`) — Phase 2

1. Bot sends draft thread preview to `TELEGRAM_CHAT_ID`
2. Inline keyboard: **✅ Approve** | **❌ Reject** | **✏️ Edit**
3. Approve → sets `queue.status = 'approved'` → triggers poster
4. Reject → sets `queue.status = 'rejected'`
5. Edit → bot prompts for replacement text, then re-previews

---

## Poster Spec (`src/poster.py`) — Phase 2

- Library: `tweepy` with OAuth 1.0a (User context required for posting)
- Post as thread: create first tweet, then reply-chain for subsequent tweets
- On success: insert into `posted`, set `queue.status = 'posted'`
- Rate limit guard: 1 thread per 30 min minimum (`POSTER_MIN_INTERVAL_MIN`)

---

## Environment Variables

See `.env.example` for all variables. Required for each phase:

**Phase 1 (scraper + db):** none strictly required (SQLite path configurable)

**Phase 2+ (full pipeline):**
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`

---

## Railway Deployment

`railway.toml` defines four services:
- **scrape**: cron every 6 hours — `python main.py scrape`
- **post**: cron every 30 min — `python main.py post`
- **bot**: long-running — `python main.py bot` (Telegram polling)
- **stats**: cron daily 09:00 UTC — `python main.py stats`

Additional CLI commands:
- `python main.py health` — print JSON health check (exit 1 if errors)

---

## Development Phases

| Phase | Scope                               | Status      |
|-------|-------------------------------------|-------------|
| 1     | `db.py` + `scraper.py` (arXiv only) | ✅ Done     |
| 2     | `scorer.py` + `generator.py`        | ✅ Done     |
| 3     | `telegram_bot.py` + `poster.py`     | ✅ Done     |
| 4     | Railway deploy + monitoring         | ✅ Done     |

---

## Conventions

- All datetimes stored as UTC ISO 8601 strings (`datetime.utcnow().isoformat() + 'Z'`)
- Dataclasses for inter-module data transfer (`Paper`, `QueueItem`)
- No ORM — plain `sqlite3` with context managers
- Logging via stdlib `logging` (level from `LOG_LEVEL` env var, default `INFO`)
- All scraper functions return `list[Paper]`; empty list on failure (log the error, don't raise)
- DB path: `DATA_DIR/optix.db`, default `./data/optix.db`
