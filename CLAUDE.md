# Techspire Official - project rules for AI assistants

Python automation that curates bite-size news in five sectors (Technology, Business, Finance, Career Development,
Entrepreneurship) from RSS feeds for the Techspire Official Facebook Page (audience mainly in Bangladesh):
RSS -> SQLite dedupe -> AI curator (Gemini/OpenAI, validated JSON) -> 1200x630 Pillow card -> local review.
Facebook publishing is implemented but locked. `GEMINI.md` holds the same rules for other assistants;
`docs/updating.md` holds the change procedures.

## Hard rules

- **Never publish anything to Facebook.** Do not run `--publish`, do not set `DRY_RUN=false` or
  `PUBLISH_ENABLED=true`, do not call any Graph API write endpoint, and do not use a browser to post, comment or
  react. Only the owner can authorise live publishing, explicitly and separately; finished code, passing tests or
  available credentials are not authorisation.
- Never commit or print `.env` or any token/API key. Never weaken `logging_config.redact()`. The GitHub repository
  (github.com/KabirADnan41/techspire-automated-media-portal) is **public**. Never commit runtime data (`data/`, `logs/`,
  `output/`), `exports/` or `reference/`, which holds third-party books that may not be redistributed.
- Tests must never touch the network (`tests/conftest.py` blocks it); mock every external API.
- Keep the publishing interlock intact: `closed_gates()`, `authorize_publishing()`, the `PublishPermit` check,
  the per-request gate re-check, and the `prevent_second_upload` trigger.

## Commands

```bash
.venv\Scripts\python.exe -m pytest -q          # all tests (offline)
.venv\Scripts\python.exe -m ruff check techspire tests
.venv\Scripts\python.exe -m techspire.main --demo     # offline end-to-end demo (mocked AI)
.venv\Scripts\python.exe -m techspire.main --doctor   # read-only preflight
```

## Conventions

- All datetimes are timezone-aware UTC; SQL uses `?` parameters only.
- Every article state change goes through `Storage.transition()` (checked against `enums.ALLOWED_TRANSITIONS`).
- Article text is untrusted: it goes only into the AI user turn via `build_user_prompt()`; AI output goes through
  `validate_decision()` before use.
- See `docs/architecture.md`, `docs/reference-audit.md` and `docs/runbook.md`.
- After each milestone, update `progress.md`: the current-state table, "Waiting on you", and a new dated entry
  at the top of the milestone log.
