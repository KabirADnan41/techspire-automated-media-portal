# Techspire Official - automated bite-size news curation

Techspire turns RSS news feeds into ready-to-review, bite-size Facebook posts for the
[Techspire Official](https://www.facebook.com/techspireofficial) page, in five sectors:
**Technology, Business, Finance, Career Development and Entrepreneurship**, for an audience mainly in Bangladesh.
It:

1. reads your RSS feeds (TBS News plus trusted global sources),
2. skips anything it has already seen,
3. asks an AI editor (Gemini or OpenAI) whether each story belongs to one of the five sectors, and if so writes a
   short headline and a one-to-two-sentence Facebook caption,
4. checks the AI's answer with strict automatic rules,
5. draws a branded 1200x630 news card,
6. puts everything on a local review page for a human.

> **LIVE FACEBOOK PUBLISHING: DISABLED.**
> Nothing is ever posted to Facebook unless three independent switches are all turned on deliberately
> (`DRY_RUN=false`, `PUBLISH_ENABLED=true` **and** the `--publish` command-line flag). They are all off by default.
> See [Publishing safety](#publishing-safety).

---

## Contents

- [How it works](#how-it-works)
- [Folder structure](#folder-structure)
- [Install](#install)
- [Configure](#configure)
- [Use it safely (dry run)](#use-it-safely-dry-run)
- [Review the output](#review-the-output)
- [Database and logs](#database-and-logs)
- [Tests](#tests)
- [Scheduling](#scheduling)
- [The AI editor](#the-ai-editor)
- [The news card](#the-news-card)
- [Facebook module](#facebook-module)
- [Publishing safety](#publishing-safety)
- [Troubleshooting](#troubleshooting)
- [Future live publishing](#future-live-publishing)
- [Reference material](#reference-material)

---

## How it works

```text
config/feeds.yaml
      │
      ▼
 RSS fetch ───────── timeout, honest User-Agent, 3 retries, 10 MB cap; a broken feed never stops the others
      │
      ▼
 Clean + normalise ─ strip HTML/scripts, decode entities, canonical URL (drops utm_*, fbclid, gclid)
      │
      ▼
 Duplicate check ─── SQLite UNIQUE URL hash (+ same-headline check); older than 48 h or undated -> skipped
      │ new articles only
      ▼
 AI editor ───────── five-sector editorial policy, structured JSON answer, strict validation
      │ relevant only                 └─► REJECTED (reason saved)
      ▼
 News card ───────── 1200x630 JPEG, dark RGB(20,24,33), TECHSPIRE OFFICIAL + category + headline
      │
      ▼
 Local review ────── JSON preview + HTML review page + console report  ──►  READY_FOR_REVIEW   (stops here today)
      │
      ▼  FUTURE ONLY (locked)
 Facebook photo post ─► post ID saved immediately ─► "Read full story: <url>" comment ─► PUBLISHED
```

Each run is **fetch -> process -> exit**, so any scheduler can run it. Every step is saved in SQLite, so a crash,
restart or network failure is picked up safely on the next run. More detail: [docs/architecture.md](docs/architecture.md).

## Folder structure

```text
techspire/            the application (config, rss_fetcher, content_filter, image_generator, fb_publisher, pipeline, main ...)
config/feeds.yaml     your RSS feeds
assets/fonts/         bundled Montserrat fonts (SIL Open Font License, see OFL.txt)
demo/                 offline demo: sample feeds and recorded AI answers (fictional companies)
data/                 techspire.db - the state database (created on first run)
output/images/        generated news cards
output/previews/      one JSON preview per card + index.html review page
logs/                 techspire.log (rotating)
tests/                automated tests (no network, no real APIs)
docs/                 architecture, reference audit, operations runbook
scripts/              wrappers for Task Scheduler / cron
.env.example          safe configuration template (copy to .env)
reference/            the project's reference books (local only; not in the repository)
```

## Install

You need **Python 3.11 or newer** (tested with 3.14.7 on Windows 11).

**Windows (PowerShell)**, in the project folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m techspire.main --doctor
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/python -m techspire.main --doctor
```

`requirements.txt` holds only what the app needs; `requirements-dev.txt` adds the test tools. The commands below use
the Windows path `.\.venv\Scripts\python.exe`; on macOS/Linux use `.venv/bin/python`.

## Configure

All settings live in `.env` (never commit it; `.gitignore` already excludes it). Empty values use safe defaults.

### AI provider

| Setting | Meaning |
|---|---|
| `AI_PROVIDER` | `gemini` (default) or `openai` |
| `GEMINI_API_KEY` | Key from Google AI Studio (needed when `AI_PROVIDER=gemini`) |
| `OPENAI_API_KEY` | Key from the OpenAI dashboard (needed when `AI_PROVIDER=openai`) |
| `AI_MODEL` | Empty = `gemini-3.5-flash-lite` (Gemini) or `gpt-6-luna` (OpenAI). `gemini-3.8-flash` writes richer copy, but its free tier allows only 20 requests per day |
| `AI_REASONING_EFFORT` | `low` (default), `medium`, `high`; empty = provider default |
| `AI_TEMPERATURE` | Optional, OpenAI only (current Gemini models do not accept temperature) |

Only the selected provider's key is needed. Unit tests and `--demo` need no key at all.

### Feeds

Edit `config/feeds.yaml`:

```yaml
feeds:
  - name: TechCrunch            # unique name, shown in logs and on cards
    url: https://techcrunch.com/feed/
    enabled: true               # false = paused
```

The list ships with 19 enabled feeds grouped by sector, all checked on 2026-09-28: six **TBS News** (The Business
Standard, Bangladesh) sections - Economy, Stocks, Corporates, Banking, Tech and Pursuit (careers) - plus TechCrunch,
The Verge, The Hacker News, BBC Business, The Guardian Business, Fortune, CNBC Finance, MarketWatch, Guardian Work and
Careers, Indeed Hiring Lab, Inc., Entrepreneur and Small Business Trends. More sources are listed but disabled. Feeds are
processed round-robin, so no single busy feed or sector takes every slot in a run. After editing, run:

```powershell
.\.venv\Scripts\python.exe -m techspire.main --check-feeds
```

### Other settings

| Setting | Default | Meaning |
|---|---|---|
| `MAX_ARTICLES_PER_RUN` | 10 | At most this many articles go through the AI per run (controls cost) |
| `MAX_ARTICLE_AGE_HOURS` | 48 | Older stories are ignored |
| `MAX_RETRIES_PER_ARTICLE` | 3 | Failed steps are retried on later runs up to this many times (per stage) |
| `PUBLISH_REQUIRE_IDS` | true | Future live runs must name the reviewed cards to post (`--publish --ids 12,15`) |
| `RSS_REQUEST_TIMEOUT` / `FACEBOOK_REQUEST_TIMEOUT` / `AI_REQUEST_TIMEOUT` | 20 / 30 / 60 s | Network timeouts |
| `DATABASE_PATH`, `OUTPUT_DIR`, `LOG_DIR`, `FEEDS_FILE` | `data/techspire.db`, `output`, `logs`, `config/feeds.yaml` | Relative paths are relative to the project folder |
| `FONT_HEADLINE_PATH`, `FONT_BRAND_PATH`, `FONT_LABEL_PATH` | bundled Montserrat | Optional custom `.ttf` files |
| `BRAND_LOGO_PATH` | empty | Optional PNG logo drawn next to the wordmark |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows more detail (also available as `--verbose`) |

## Use it safely (dry run)

| Command | What it does | Needs |
|---|---|---|
| `python -m techspire.main --demo` | Full pipeline offline on bundled sample articles with recorded AI answers (the AI is mocked). Writes to `output/demo/` and `data/demo.db`. | nothing |
| `python -m techspire.main --doctor` | Read-only checks: Python, config, database, folders, feeds, AI key, fonts, safety lock | nothing |
| `python -m techspire.main --check-feeds` | Downloads every enabled feed and reports what it contains (no AI, no database) | internet |
| `python -m techspire.main` or `--dry-run` | **The real workflow**: live feeds + real AI -> cards and previews. Nothing is posted. | internet + AI key |
| `python -m techspire.main --status` | Article counts by state, recent runs, anything needing attention | nothing |

Useful options: `--limit 5` (process at most 5 articles), `--feed "TechCrunch"` (only that feed, repeatable),
`--verbose` (debug logging), `--env-file PATH` (another settings file).

A dry run prints one block per approved story:

```text
==================================================
TECHSPIRE DRY RUN
==================================================

Article:
<original title>
...
Generated Image:
D:\...\output\images\2026-09-28_1d57ae88e8123a20.jpg

State:
READY_FOR_REVIEW

Facebook write performed:
NO
==================================================
```

and ends with a run summary (feeds, articles fetched, AI decisions, failed steps, ready for review) that always
states `Facebook writes performed: 0` and `LIVE FACEBOOK PUBLISHING: DISABLED`.

### Daily workflow

```text
add/adjust feeds (config/feeds.yaml)  ->  --check-feeds
        ↓
run a dry run (or let the scheduler do it)
        ↓
open output/previews/index.html
        ↓
for each card: read the headline and caption next to the original article (link on the card)
        ↓
look at "Recently rejected" and "Failed" at the bottom of the page
        ↓
--status and logs/techspire.log if anything looks odd
```

## Review the output

| What | Where |
|---|---|
| Review page (all cards waiting for review, rejected and failed items) | `output/previews/index.html` - open in a browser |
| News cards | `output/images/<published-date>_<url-hash>.jpg` |
| Per-card preview: source, date, category, headline, caption, URL, image path, state, and the exact Facebook payload a future publish would send (no token) | `output/previews/<same-name>.json` |
| Demo output | `output/demo/images`, `output/demo/previews/index.html` |

File names are built only from the date and a hash of the article URL, never from titles.

## Database and logs

- **Database:** `data/techspire.db` (SQLite). Main table `articles` (one row per story, unique per URL);
  `article_events` is the audit trail of every state change; `runs` records each run. States:
  `DISCOVERED -> ANALYZING -> APPROVED -> IMAGE_CREATED -> READY_FOR_REVIEW` (dry run ends here), or `REJECTED`, or
  `FAILED` (with `error_stage`, `last_error`, `retry_count`). `PUBLISHING`, `POST_CREATED`, `COMMENT_PENDING` and
  `PUBLISHED` are only used by future live publishing.
- **Inspect:** `python -m techspire.main --status`, or open the file with any SQLite viewer (e.g. DB Browser for SQLite).
- **Start over:** stop any run, then delete `data/techspire.db` (it is recreated on the next run; every article will
  be treated as new again, so the AI will re-analyse recent stories).
- **Logs:** `logs/techspire.log` (UTC timestamps, 5 files x 2 MB). Secrets are redacted automatically.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check techspire tests
.\.venv\Scripts\python.exe -m compileall -q techspire
```

The tests cover config, URL/text cleaning, RSS parsing and failures, SQLite duplicate protection and recovery, AI
validation (including the real Gemini/OpenAI SDKs against mocked HTTP), image geometry, the Facebook client (mocked)
and full end-to-end runs. Every test runs offline: real network access is blocked, so no test can reach Facebook,
Google or OpenAI.

## Scheduling

The worker runs once and exits, so schedule it every 30-60 minutes. Run it under a normal (non-administrator) user
account that owns the project folder.

**Windows Task Scheduler:** create a basic task -> trigger "Daily", repeat every 1 hour -> action "Start a program":

```text
Program/script:  powershell.exe
Arguments:       -NoProfile -ExecutionPolicy Bypass -File "D:\Automated Media portal - techspire\scripts\run-techspire.ps1"
```

**cron (Linux/macOS)**, every hour at minute 5:

```cron
5 * * * * /path/to/techspire/scripts/run-techspire.sh
```

Two runs can never work at the same time (a database run lock); a run that finds another active run exits with code 2.
Exit codes: `0` ok, `1` error (message says what to fix), `2` another run is active, `3` publishing blocked.
Run Techspire on one computer only, with `data/` on a local disk (SQLite locking is unreliable on network shares).

### Secure the project folder

On this PC the project folder inherits **"Authenticated Users: Modify"** from `D:\`, so every local account could read a
future `.env` (API keys, Page token) or change the code the scheduled task runs. `--doctor` warns about this. Before
adding real keys, either move the project under your user profile (e.g. `C:\Users\<you>\techspire`) or, in an
administrator PowerShell, keep access for yourself, SYSTEM and Administrators only:

```powershell
icacls "D:\Automated Media portal - techspire" /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F"
```

On macOS/Linux: `chmod 700` the folder and `chmod 600 .env`.

## The AI editor

- The AI acts as Techspire's Lead Content Curator. A story is accepted only when its **primary** news value belongs
  to one of the five sectors: **TECHNOLOGY, BUSINESS, FINANCE, CAREER DEVELOPMENT, ENTREPRENEURSHIP**. Politics and
  elections, sports, celebrity and entertainment, lifestyle, opinion columns, advertorials and "get rich quick" pieces
  are rejected, and when relevance is doubtful it rejects. Government decisions count only when their main news is the
  effect on businesses, markets, personal finances, jobs or startups.
- Bite-size style (modelled on Nutshell Today and similar Bangladeshi news pages): 7-12 word straight-news headlines,
  captions of 1-2 short sentences with one sector hashtag and `#TechspireOfficial`. A Bangladesh angle is mentioned
  only when the article contains one. **No financial, investment or career advice**, ever.
- Feed text is **untrusted**: it is sent only as JSON data between markers, the prompt tells the model never to follow
  instructions inside it, and the task is repeated after the data.
- The answer must match a strict JSON schema, and is then checked by code: one of the 5 sectors; the original URL
  copied exactly; headline 5-18 words, English, no URLs/hashtags/emoji/shouting; caption 60-500 characters with 2-7
  hashtags at the end and no URL; **every number and web address in the copy must appear in the source**; no
  advice/hype phrases ("guaranteed returns", "buy now"); no prompt leakage or refusals. A failing answer gets one correction attempt, then the article is marked `FAILED` and
  retried on later runs.
- Transient API errors (timeouts, 429, 5xx) are retried with backoff (honouring `Retry-After`); if the provider is
  down or the key is wrong, AI work stops for that run and resumes next time.

## The news card

1200x630 JPEG on RGB(20, 24, 33): `TECHSPIRE OFFICIAL` wordmark, a tagline at the top right (`CARD_TAGLINE`,
default `BITESIZE NEWS`), the sector badge, and the headline centred as one group. The headline is measured in real pixels (`ImageDraw.textbbox`), split into balanced lines, and shrunk from 76 px
until it fits (up to 3 lines comfortably, 4 at most, never below 34 px). Very long words are hyphenated and, as a last
resort, the final line gets an ellipsis, so text never leaves the card. Brand colours are constants at the top of
`techspire/image_generator.py`; add your logo with `BRAND_LOGO_PATH`.

## Facebook module

`techspire/fb_publisher.py` implements the future Graph API flow (API version `v26.0` by default, configurable with
`FB_GRAPH_API_VERSION`):

1. `POST /{page-id}/photos` with the card and caption -> Facebook returns a photo ID **and** a post ID (they differ);
   both are saved immediately.
2. `POST /{post-id}/comments` with `Read full story: <original URL>`.

The token is sent only in the `Authorization: Bearer` header (never in URLs or logs). Errors are classified as
*refused* (safe to retry), *not sent* (safe to retry) or *outcome unknown* (never auto-retried). If the post succeeds
but the comment fails, the next run retries **only the comment**; the database refuses a second upload for any article
that already has a Facebook ID. All of this is covered by tests against a mocked Facebook; none of it has run live.

## Publishing safety

Live publishing needs **all** of the following at once, and is refused otherwise with a message listing what is off:

1. `DRY_RUN=false` in `.env`
2. `PUBLISH_ENABLED=true` in `.env`
3. the `--publish` flag on that specific command
4. `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN` in `.env`

The publisher checks these again before every request, the dry-run and demo modes never create a publisher, and a
typo in a safety setting (e.g. `DRY_RUN=ture`) stops the program instead of guessing. `.env.example` ships locked.

## Troubleshooting

| Message / symptom | Fix |
|---|---|
| `GEMINI_API_KEY is missing` / `OPENAI_API_KEY is missing` | Add the key to `.env`, or run `--demo` to try the pipeline without one |
| `Configuration error: DRY_RUN must be true or false` | Fix the typo in `.env` (only `true`/`false`, `1`/`0`, `yes`/`no`) |
| `Unable to open data/techspire.db` | Make sure `data/` is writable and the file isn't open in another program |
| `Not started: Another Techspire run ... is in progress` | Wait for it to finish; a crashed run's lock expires by itself after 30 minutes |
| `Feed 'X' skipped: HTTP 403/429` | The site is blocking or rate-limiting; try later, or set `enabled: false` |
| `Feed 'X' skipped: malformed feed` | The URL isn't an RSS/Atom feed; check it in a browser, fix it in `config/feeds.yaml` |
| `AI request failed ... Stopping AI analysis for this run` | Provider outage, rate limit, wrong key or wrong `AI_MODEL`; the articles wait for the next run |
| Many `FAILED (analyze)` items with "AI answer failed validation" | Read `last_error` on the review page; the validator explains which rule the AI broke |
| No new cards, summary shows everything "already known" or "too old" | Normal: only new stories from the last `MAX_ARTICLE_AGE_HOURS` are processed |
| Card text looks wrong / fonts missing | `--doctor` shows which font files are used; restore `assets/fonts/` or set `FONT_*_PATH` |
| `PUBLISHING BLOCKED` | Expected: publishing is disabled. Use `--dry-run` |
| `Feed 'X' skipped: server sent a compressed feed` / `feed declares XML entities` / `Refusing ...` | A safety check refused that feed (see docs/architecture.md, Security measures); use another feed URL |
| `--doctor` warns about folder permissions | See [Secure the project folder](#secure-the-project-folder) |

## Future live publishing

**This section documents a future decision for the page owner. The product is delivered with publishing locked, and
nothing in this build has been posted.** Before switching it on:

1. Run dry runs for a while and review the output daily (the dry-run period works as a shadow deployment).
2. Create a Meta app and a **Page access token** with only the permissions the Graph API documents for this flow:
   `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` (photo posts) and `pages_manage_engagement`
   (comments). Put `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN` in `.env`; restrict who can read that file.
3. Read [docs/runbook.md](docs/runbook.md) (token leak response and the "unknown outcome" procedure).
4. Set `DRY_RUN=false` and `PUBLISH_ENABLED=true`, then run `--doctor`.
5. Publish explicitly chosen, reviewed cards: `python -m techspire.main --publish --ids 12,15`. This is required
   while `PUBLISH_REQUIRE_IDS=true` (the default). Only if you set it to `false` does a plain `--publish` post the
   newest reviewed cards from the last `MAX_ARTICLE_AGE_HOURS` automatically, up to `--limit`.
6. Only then consider adding `--publish` to the scheduled task.

To lock it again at any time, set `PUBLISH_ENABLED=false` (or `DRY_RUN=true`).

## Updating later, and using Gemini

- Step-by-step procedures for later changes (feeds, sectors, prompt, card design, AI model and quota, Graph API
  version, packages, moving computers) and a verification checklist: [docs/updating.md](docs/updating.md).
- To give Gemini (or another assistant) the project: run `.\.venv\Scripts\python.exe scripts\export_for_ai.py` and
  upload the files it writes to `exports\`. They contain no secrets, data or reference books. `GEMINI.md` tells the
  assistant the project's hard safety rules. Re-run the export after each change and replace the old upload.

## Reference material

The project's reference books (AI engineering, prompt engineering, API/OAuth security, incident response, Python
automation, microservices) were read for the parts that matter to this product. How each one shaped the design, plus
the official API documentation used (Graph API v26.0, Gemini Interactions API, OpenAI Responses API), is in
[docs/reference-audit.md](docs/reference-audit.md).
