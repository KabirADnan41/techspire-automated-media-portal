# Techspire Official - progress log

Updated after every milestone. The two sections at the top always show the current state; the milestone log below
them is newest first.

## Current state (2026-09-28)

| Area | State |
|---|---|
| Pipeline | RSS feeds -> duplicate check -> AI editor -> 1200x630 card -> local review page. Works end to end. |
| Facebook | Publishing is built but **locked** (`DRY_RUN=true`, `PUBLISH_ENABLED=false`). Posts made: 0. Comments made: 0. |
| AI editor | Gemini `gemini-3.5-flash-lite` on the free tier, about 5 seconds per article. OpenAI is also supported. |
| Sectors | Technology, Business, Finance, Career Development, Entrepreneurship. Bite-size English copy for a Bangladesh audience. |
| Sources | 19 enabled RSS feeds: 6 TBS News sections and 13 international outlets (`config/feeds.yaml`). |
| Checks | 325 tests pass, ruff clean, the offline demo makes 7 cards across all 5 sectors. |
| Repository | Public on GitHub: [KabirADnan41/techspire-automated-media-portal](https://github.com/KabirADnan41/techspire-automated-media-portal), branch `main`. |
| Instagram | Not built yet. Researched: see the plan below. |
| Plan | [docs/research-report.md](docs/research-report.md): Facebook + Instagram, about 3 posts a day, two-step AI, one 4:5 card for both platforms. |
| Hosting | **Fully in the cloud, with no laptop** (owner's decision). A small VPS runs the program. The Hostinger Cloud Startup plan (which can't run Python) hosts the Instagram images and the link-in-bio page. Approvals come through a Telegram bot. About $5-10 a month on top of the Hostinger plan. |

## Waiting on you

1. **Sectors.** The new brief is technology-only; the current build covers five sectors. Which should the product
   follow?
2. **Your list of technology news sites.** Dhaka Chronicle is still on hold: it has no RSS feed and publishes about one
   article a month.
3. **Approve the plan** in `docs/research-report.md` (Option 2, reached by first running Option 1 on the laptop), so
   Phase 1 can start. Everything is built in dry-run mode, and nothing is published.
4. **Instagram account:** a professional account (Business or Creator), linked to the Techspire Facebook Page in a
   Meta Business portfolio.
5. **Choose the VPS** (needed before deployment, not before building): Hostinger KVM 1 (4 GB RAM; $6.49 a month on a
   2-year term, renewing at $11.99; same account as the Cloud plan) or DigitalOcean ($4 a month, 512 MB, no
   commitment). Also: which domain does the Hostinger plan use? The images and the link-in-bio page will be served from
   it.
6. **Team access:** add teammates as collaborators on GitHub (repository Settings -> Collaborators). Optional: choose
   a licence. Without one, others can read the code but have no right to reuse it.
7. Optional: screenshots of The Front Page BD and Sozoo Today posts, to fine-tune the card style.

## Milestones

### 2026-09-28 - Published to GitHub

- Public repository: https://github.com/KabirADnan41/techspire-automated-media-portal (64 files, branch `main`).
- Checked before pushing:
  - no `.env` or keys (the real key values were scanned for);
  - no database, logs or exports;
  - `reference/` (third-party books and a copied repository) is now in `.gitignore` and stays local.
- Commits use the GitHub noreply address, so no personal email is public. 325 tests pass, ruff clean.

### 2026-09-28 - Architecture: fully in the cloud

- Owner's decision: no laptop. A small VPS runs everything on a schedule; the Hostinger plan hosts the public images and
  the link-in-bio page; approvals and alerts come through a Telegram bot; a free healthchecks.io heartbeat warns if
  runs stop.
- Google Cloud's "free" VM was checked and rejected: its public IPv4 address costs about $3.65 a month, close to a
  plain $4 VPS but with more setup.
- The plan's phases were updated in `docs/research-report.md`: Telegram approvals and cloud deployment (in dry-run
  mode) now come before launch.

### 2026-09-28 - Hosting checked

- The server is a Hostinger Cloud Startup plan: managed web hosting with 4 CPU cores, 4 GB RAM, 100 GB NVMe, SSH, cron
  jobs and free SSL.
- Hostinger supports Python only on its VPS plans, because Web and Cloud plans have no root access. The Techspire
  pipeline therefore can't officially run there.
- Plan: the Hostinger site hosts the Instagram images and the link-in-bio page, replacing Cloudflare. The pipeline runs
  on the laptop, or later on a small VPS.

### 2026-09-28 - Research: Facebook + Instagram, costs and architecture

- Researched the official Meta, AI-pricing and hosting documentation; the full report with sources is in
  `docs/research-report.md`.
- Comments can be created through the API on both Facebook and Instagram, but **neither platform lets the API pin a
  comment**; pinning stays manual.
- Instagram needs the image at a public URL (planned: a short-lived Cloudflare R2 link). Links in Instagram captions
  and comments aren't clickable, so the plan adds a free "link in bio" page.
- One 1080 x 1350 (4:5) card works for both platforms (Meta recommends 4:5 for the Facebook feed).
- AI cost at 3 posts a day: $0 on free tiers, or about $1-3 a month paid. Recommended total, including a small
  server: about $5-10 a month.
- Graph API v26.0 (Techspire's default) is the newest version; v20.0 expired on 24 Sep 2026.
- Nothing was posted and no code changed.

### 2026-09-28 - New source checked, AI options reviewed

- dhakachronicle.net (requested source): English articles, no RSS feed. Its news sitemap lists recent articles, but
  the latest are from 16 Sep, 26 Aug, 9 Jun and 23 May 2026. Not added yet; the finding is noted in
  `config/feeds.yaml`.
- Checked the providers' documentation: Claude Max, ChatGPT Plus and Google AI Plus include no API use (Google AI
  Pro and Ultra come with Cloud credits that can pay for the Gemini API). Anthropic says products should use an API
  key, not a subscription.
- Free options: the Gemini free tier covers 3.5 Flash-Lite, 3.1 Flash-Lite and 3.8 Flash, each with its own daily
  limit. This laptop (RTX 2050 with 4 GB, about 6 GB usable RAM) is too small for a good local model.
- Started this progress log; the Gemini export now includes it. 325 tests pass, ruff clean.

### 2026-09-28 - Live AI test and Gemini export

- Tested the owner's Gemini key live. The `gemini-3.8-flash` free tier allows only 20 requests a day, so the default
  is now `gemini-3.5-flash-lite`. When a limit is hit, the pipeline stops AI work safely and resumes on the next run.
- Added `scripts/export_for_ai.py`, `GEMINI.md` and `docs/updating.md`. The exported files contain no keys, data
  or reference books.
- Copied the LLM-Engineers-Handbook notes into `reference/` (kept local, not exported).

### 2026-09-28 - Five sectors and bite-size style

- Five sectors replaced the tech-only policy. The AI prompt, the validators, the card tagline (`BITESIZE NEWS`) and
  the database categories were updated; the database upgrades itself.
- Style modelled on Nutshell Today and TBS News. The Instagram references need a login, so screenshots were
  requested instead.
- 19 feeds enabled, including 6 TBS News sections, processed round-robin so no single feed takes every slot.

### 2026-09-28 - Reviews and hardening

- Code review, security review and cleanup passes. Every finding was fixed except folder permissions, which the
  owner decided to leave as they are.

### 2026-09-28 - First build

- RSS reader, SQLite state machine, AI editor with validated JSON, Pillow cards, review page, dry-run mode, the
  locked Facebook publisher, tests and documentation.
