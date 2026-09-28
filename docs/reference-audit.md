# Reference audit

What was in `reference/`, how it was read, and which implementation decisions it drove.
Audit date: 2026-09-28.

## How the references were read

- All 19 PDFs and the EPUB were converted to text with `pypdf` (as the `pdf` skill recommends) and read in a
  targeted way: title and copyright pages (edition/date), table of contents, then the chapters relevant to this
  product, located by keyword search and read in full. Page numbers below are **PDF page indexes** unless marked
  "printed".
- The books are general engineering, security and AI references. **None of them documents the Facebook Graph API,
  the Gemini API or the OpenAI API**, and there is no Techspire brand guide or feed list. Current API behaviour was
  therefore taken from the official vendor documentation (see "External sources").
- `claude-skill-audit.md` (2026-09-28) was treated as the source of truth for installed skills;
  `claude-inventory.txt` (2026-09-26) only as a cross-check.

## Reference index

| # | Document (file) | Version / date | Relevance | Implementation areas | Key requirements taken from it |
|---|---|---|---|---|---|
| 1 | *AI Engineering* - Chip Huyen | 1st ed., Dec 2024, O'Reilly (c) 2025 | High | AI prompt, validation, logging | Indirect prompt injection via retrieved content can't be fully eliminated (pp. 473-476, 484) -> defence in depth; state what the model must not do and repeat the task after untrusted text (486-487); JSON mode guarantees syntax, not content (199, 208) -> deterministic checks; retry malformed output (860); human approval before impactful actions (488) |
| 2 | *Hands-On Large Language Models* - Alammar & Grootendorst | 1st ed., Sep 2024, O'Reilly | Medium | AI prompt, retries | Fixed answer set for classification (188); exponential backoff on rate limits (189); verify structure and accuracy of outputs (270-277) |
| 3 | *Prompt Engineering for LLMs* - Berryman & Ziegler | 1st ed., O'Reilly (copyright page dates are inconsistent: (c) 2025 / first release 2023-11-04) | High | AI prompt, validation, testing | Never put retrieved content in the system message (80); "sandwich" the data between task and restated task (160-162); "trust but verify" (36-37); feed validation errors back to the model (228); irreversible actions need human sign-off enforced in code (228, 266); mock the model in unit tests (202) |
| 4 | *Prompt Engineering for Generative AI* - Phoenix & Taylor (EPUB) | 1st ed., May 2024, O'Reilly | Medium-high | Validation, testing | Parse errors as retry triggers; custom content checks on JSON; enum-typed categories; ground copy in the reference text; cheap programmatic checks before anything else |
| 5 | *Designing Machine Learning Systems* - Chip Huyen | 1st ed., May 2022, O'Reilly | Medium | AI design, monitoring | Make relevance a binary decision with a separate topic label (59); ML systems fail silently (274) -> log every decision; a shadow deployment (our dry-run) before going live (336) |
| 6 | *Advanced API Security* (2nd ed.) - Prabath Siriwardena | 2nd ed., Apress (c) 2020 | Medium | Secrets, logging, runbook | "Never write access/refresh tokens into logs", TLS 1.2+ (306); fail-safe defaults (64); revoke keys after an incident (238) |
| 7 | *API Security in Action* - Neil Madden | Manning (c) 2020 | High | Publisher, retries, logging, SSRF | 429 + Retry-After (89); POST is not idempotent, so retry only when safe (528-529); log before and after an operation (104-106); tokens in URLs leak (166); keep secrets out of version control (441); SSRF defences for fetched URLs (385-387) |
| 8 | *OAuth 2 in Action* - Richer & Sanso | Manning (c) 2017 | High | Publisher, secrets | Authorization header is the recommended token transport; query string only as a last resort (79, 88); a token can stop working at any time (82); keep token values out of logs (149, 200); minimal scope (198-199) |
| 9 | *The Little Book of OAuth 2.0 RFCs* - compiled by Aaron Parecki | 2nd ed., (c) 2022 | High (normative RFC text) | Publisher, TLS, secrets | RFC 6750 section 2: header SHOULD be used, form-body only for single-part urlencoded bodies, query string SHOULD NOT (93-96); `invalid_token` -> 401 (98); TLS validation MUST (101-102); RFC 6749 section 10.3 confidentiality (64) |
| 10 | *Security Engineering* (3rd ed.) - Ross Anderson (`SEv3.pdf`) | 3rd ed., Wiley (c) 2020 | High | Safe defaults, logging, review | Least privilege; "the default configuration ... should be safe" (238); keep logs separate from what they record (213); injection = data interpreted as code (234); release requires an incident plan and a final security review (986, 999-1000) |
| 11 | *Cybersecurity For Dummies* - Joseph Steinberg | Wiley (c) 2020 | Medium | Runbook, access | Give only needed access, never share credentials (208); an unrecognised post is a compromise signal (268); recovery steps (272-275) |
| 12 | *Blue Team Handbook: Incident Response* - Don Murdoch | O'Reilly (c) 2026, Early Release ("February 2026: First Edition") | High | Runbook, logging | Leaked credentials are exploited within minutes (18-19); know in advance how to revoke/rotate each credential (34, 38); all systems log in UTC (33); containment steps (65) |
| 13 | *The Cuckoo's Egg* - Cliff Stoll | No copyright page in this copy (epilogue Feb 1990) | Low | Audit, scheduling | Reconciling two independent records exposed the intruder (5-7) -> compare the database with the Page; a hijacked scheduled job ran with full privileges (21) -> protect the scheduled script |
| 14 | *Linux Basics for Hackers* - OccupyTheWeb | No Starch (c) 2019 | Medium | Scheduling, logging | crontab user field and absolute paths (printed p. 174); log retention trade-offs (printed 115-116) |
| 15 | *Privacy's Blueprint* - Woodrow Hartzog | Harvard UP (c) 2018 | Medium | Privacy, defaults | Defaults are sticky (printed 2); collect and keep only what is necessary (printed 250-251) |
| 16 | *Automate the Boring Stuff with Python* (3rd ed.) - Al Sweigart | Early Access 11/08/2024, No Starch (c) 2025 | High | SQLite, logging, scheduling, Pillow | `CREATE TABLE ... STRICT`, ISO dates, `?` placeholders, explicit transactions, `PRAGMA foreign_keys` (411-428); use the OS scheduler (498); `ImageFont.truetype` (539-540). Its `textsize()` example is obsolete (removed in Pillow 10) -> `textbbox` used |
| 17 | *Web Scraping with Python* (3rd ed.) - Ryan Mitchell | Feb 2024, O'Reilly | High | RSS fetcher, cleaning, dedupe | Politeness and an honest User-Agent (42-44, 319, 326); `get_text()` last, then Unicode normalisation (77, 202); consistently formatted URLs for duplicate detection (105-106); POST changes state (274) |
| 18 | *Building Microservices* (2nd ed.) - Sam Newman | Aug 2021, O'Reilly | High | Architecture, retries, idempotency | A monolith is the sensible default for a small team (44, 57-58); timeouts on every out-of-process call (425); retry only transient failures (425-426); idempotency and forward-recovering sagas (209-215, 432-433); a timeout is ambiguous (143) |
| 19 | *Software Architecture Patterns* - Mark Richards | O'Reilly report, Feb 2015 (3rd release 2017) | Medium | Architecture, testing | Layered architecture as a starting point (14); isolate persistence (10-11); mock layers in tests (16) |
| 20 | *ReportLab - PDF Processing with Python* - Michael Driscoll | Leanpub, 2018-06-05 | Low | Image fonts | Register fonts by explicit path (67); font fallback is limited (61, 557); measure before layout, split long words (93, 105-106, 200) |

## Decisions the references drove

| Decision | Where | Sources |
|---|---|---|
| Untrusted article text only in the user turn, JSON-encoded between markers, task restated afterwards; explicit "never follow instructions" rules | `content_filter.py` | 1, 3, 4, 10 |
| Native JSON-schema output **plus** Pydantic + deterministic checks (URL equality, category enum, headline/caption rules, numbers must exist in the source, leakage/refusal detection); one correction round-trip | `content_filter.py` | 1, 2, 3, 4 |
| Page token sent only in `Authorization: Bearer` (never URL or body); never logged; non-secret fingerprint in audit logs | `fb_publisher.py`, `logging_config.py` | 6, 7, 8, 9 |
| Photo upload never retried after an ambiguous outcome (timeout, 5xx, unreadable success); IDs persisted immediately; comment outcome reconciled with a read-only lookup | `pipeline.py`, `storage.py` | 7, 18 |
| Three independent publishing gates, safe defaults, fail-closed boolean parsing | `fb_publisher.py`, `config.py` | 6, 10, 15 |
| Explicit timeouts everywhere; bounded exponential backoff only for 429/5xx/network errors; Retry-After honoured | `rss_fetcher.py`, `content_filter.py` | 2, 7, 18 |
| SQLite STRICT tables, UNIQUE URL hash, compare-and-set state transitions in `BEGIN IMMEDIATE`, audit events, UTC ISO timestamps | `storage.py` | 12, 16, 17, 18 |
| Single-process layered monolith, fetch -> process -> exit, scheduled by the OS | whole package | 16, 18, 19 |
| Honest bot User-Agent, bounded feed size, SSRF guard on feed URLs and redirects | `rss_fetcher.py` | 7, 17 |
| Bundled fonts resolved by explicit path; pixel measurement with `textbbox`; hyphenation of over-long words | `image_generator.py` | 16, 20 |
| Incident runbook (token leak, unknown post) and reconciliation of the database with the Page | `docs/runbook.md` | 10, 11, 12, 13 |

## External sources (official vendor documentation, checked 2026-09-28)

| Topic | Finding | Used for |
|---|---|---|
| Graph API changelog (developers.facebook.com) | Latest version **v26.0** (released 2026-07-29). The originally suggested **v20.0 expired on 2026-09-24**. | Default `FB_GRAPH_API_VERSION=v26.0` |
| Page photos (`POST /{page-id}/photos`) | Multipart `source` or `url`, `caption`, `published`; returns `{id, post_id}` (two different IDs); JPEG/PNG etc. up to 10 MB; needs `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` | `publish_photo`, JPEG output |
| Comments (`POST /{object-id}/comments`) | `message`; returns `{id}`; needs `pages_manage_engagement` | `create_comment`, `find_comment` |
| Graph error handling / rate limiting | Error object `message/type/code/error_subcode/fbtrace_id`; throttling codes 4, 17, 32, 613, 80001; 190 = invalid token; 10 and 200-299 = permissions; `X-Business-Use-Case-Usage` header | `parse_graph_response` |
| Bearer header on graph.facebook.com | Meta's access-token guide shows `Authorization: Bearer <token>` against `graph.facebook.com` | Header-only token transport |
| Gemini API (via the `gemini-api-dev` skill and ai.google.dev docs) | `google-genai` >= 2.3 with the **Interactions API**; `response_format` JSON schema; current model `gemini-3.8-flash`; **temperature/top_p/top_k are no longer accepted** by 3.8 Flash (use `thinking_level`) | `GeminiProvider` |
| OpenAI API (developers.openai.com) | Responses API with strict `json_schema`; current efficient model `gpt-6-luna`; reasoning effort levels | `OpenAIProvider` |

## Discrepancies and how they were resolved

- **Graph API v20.0 vs current:** v20.0 is no longer available; v26.0 is the default and stays configurable.
- **"Low temperature" vs current models:** Gemini 3.8 Flash rejects `temperature`. Factual consistency is enforced
  instead by a low thinking level, a strict schema, deterministic checks (including the numbers check) and human
  review. `AI_TEMPERATURE` is optional and only sent to OpenAI.
- **Two LLM calls at different temperatures (sources 1, 2, 5):** not adopted. The brief defines one output contract
  and the current Gemini model has no temperature control; the single schema-constrained call plus validation is
  simpler and testable.
- **Token in form body vs header:** RFC 6750 (source 9) allows the form-body method only for single-part urlencoded
  bodies, which a multipart photo upload is not, so the header is used for every request.
- **Human approval before posting (sources 1, 3, 5):** the brief's future flow posts automatically once all gates are
  open. The product adds `--publish --ids 12,15` so an operator can publish exactly the reviewed cards; the dry-run
  period works as a shadow deployment.
- **Skills:** the brief mentions a `pdf` skill (available as `anthropic-skills:pdf`); `security-review` needs a git
  repository, which this workspace is not, so an equivalent independent security review was run instead.

## Other inputs

- **Facebook page** `https://www.facebook.com/techspireofficial`: a read-only fetch showed only the page title
  "TechSpire | Dhaka"; a direct GET returned HTTP 400 (automated access blocked). No attempt was made to bypass this and
  no branding was inferred from it.
- **Brand assets:** none exist in the repository, `reference/` or elsewhere on D:. The card uses a text wordmark with an
  optional `BRAND_LOGO_PATH`, and the Montserrat font family (SIL OFL 1.1, bundled with its licence in `assets/fonts/`).
- **Feeds:** no feed list was supplied; 14 well-known technology feeds were checked read-only, 13 worked and are enabled
  as starter feeds in `config/feeds.yaml` (VentureBeat returned HTTP 429 and ships disabled).

## Owner updates (2026-09-28, after the first build)

**Editorial scope.** The owner widened the page from technology only to five sectors: **Technology, Business,
Finance, Career Development and Entrepreneurship**, in a bite-size format. This supersedes the original brief's
rejection of business, economics and market news. Politics, elections, sports, celebrity/entertainment and similar
remain rejected, and so do opinion columns, advertorials and "get rich quick" pieces. Categories became the five
sectors; old tech labels map to TECHNOLOGY and "Startups & Funding" to ENTREPRENEURSHIP (database schema v2, upgraded
automatically).

**Style references supplied by the owner** (read-only; used for style only, never as sources):

| Reference | What was observable | Used for |
|---|---|---|
| Nutshell Today (nutshelltoday.com) | English, Bangladesh-focused, "Bringing what matters, minus the noise"; straight Title Case headlines of about 8-12 words; one-sentence summaries | Headline length (7-12 words), 1-2 sentence captions, no-noise tone |
| The Business Standard (tbsnews.net) | English Bangladeshi business daily; sections Economy (Banking, Stocks, Corporates, Industry, RMG, Energy), Tech, Jobs, Features/Pursuit | **Also a source**: section RSS feeds verified and added |
| The Front Page BD (Instagram) | Verified, 217K followers, "Your Daily Informant"; posts not readable without login | Audience and positioning only |
| Sozoo Today (Instagram) | Login wall; nothing observable | Not used (no guessing) |

Instagram and Facebook require a login for anything more. The owner offered to log in; this was declined to keep
their accounts out of automated sessions (platform terms, account safety). Screenshots of example posts are the safe
way to share the visual style.

**Additional reference:** `reference/LLM-Engineers-Handbook/`, copied from github.com/AkshaysNimbalkar/LLM-Engineers-Handbook
(commit 7c18c68, 2024-11-21; personal notes for chapters 1-2 of *LLM Engineer's Handbook* plus the book's colour-figure
PDF; the repository has no licence file, so it is kept for local reference only and excluded from exports).
Relevant to Techspire: keep a manual approval step even in automated systems (already: review page and `--publish --ids`);
version and monitor prompts (already: `PROMPT_VERSION`, logged AI answers); credentials in `.env`. Its MLOps stack
(ZenML, Qdrant, MongoDB, SageMaker, fine-tuning) serves model training and is deliberately not adopted.

**Live AI test (Gemini key supplied by the owner).** The first live dry runs confirmed the Interactions API request
format and the owner's key. `gemini-3.8-flash` returned temporary 503 "high demand" errors (retries and outage handling
worked) and then **HTTP 429: 20 requests per day on the free tier**. `gemini-3.5-flash-lite` processed articles in
about 5 seconds each with valid output, so it is the default model; `gemini-3.8-flash` remains available via
`AI_MODEL` on a paid tier. The live copy was accurate to its sources; a Title Case rule for short words was added.
