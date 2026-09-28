# Updating Techspire later

Step-by-step procedures for the changes you are most likely to make. Every procedure ends with the same
**verification checklist** (section 10). Publishing stays locked throughout; none of these steps posts anything.

Commands assume Windows PowerShell in the project folder. On macOS/Linux use `.venv/bin/python` instead of
`.\.venv\Scripts\python.exe`.

---

## 1. Refresh the copy you gave Gemini (after any change)

Gemini only knows the snapshot you uploaded, so refresh it after each meaningful change:

1. `.\.venv\Scripts\python.exe scripts\export_for_ai.py`
2. It writes two files to `exports\` (date-stamped):
   - `techspire-source-<date>.zip`: source, tests, docs and config, for Gemini's code/folder upload.
   - `techspire-context-<date>.md`: the same content as one Markdown file, for a Gem's knowledge or NotebookLM.
3. In Gemini, **replace** the old file (delete it from the Gem or chat, then upload the new one) so it never
   mixes two versions.
4. The export never contains `.env`, API keys, the database, logs, generated images or the `reference/` books,
   and it stops with an error if anything that looks like a key is found.

## 2. Add, pause or remove a news source

1. Edit `config\feeds.yaml`: add an entry (unique `name`, `url`, `enabled: true`), or set `enabled: false`.
2. Test it: `.\.venv\Scripts\python.exe -m techspire.main --check-feeds --feed "Feed Name"`
3. If it is refused ("compressed feed", "redirect from https to http", "XML entities"), use the feed's direct
   https URL or choose another source. Don't loosen the safety checks.
4. Run the verification checklist.

## 3. Change the editorial rules (what gets accepted, tone, caption length)

1. Edit `SYSTEM_PROMPT` in `techspire\content_filter.py`.
2. Increase `PROMPT_VERSION` (e.g. `2026-10-05.1`) so logs show which rules produced which copy.
3. If you change hard limits (headline words, caption length, hashtags), edit the constants next to
   `validate_decision()` and the matching tests in `tests\test_content_filter.py`.
4. Run the verification checklist, then a small live test: `--dry-run --limit 3`, and read the results on the
   review page.

## 4. Change the sectors (categories)

1. Edit `Category` and `_CATEGORY_ALIASES` in `techspire\enums.py`. Map every old name to a new one in the
   aliases, so existing articles keep a valid category.
2. Update the `CATEGORY` list in `SYSTEM_PROMPT` and increase `PROMPT_VERSION`.
3. Increase `SCHEMA_VERSION` in `techspire\storage.py` (for example 2 -> 3). On the next run the database
   upgrades itself, keeping every row and mapping old categories through the aliases.
4. Update the demo answers in `demo\ai_responses.json` and the tests that name categories.
5. Back up `data\techspire.db` first (copy the file while no run is active), then run the checklist.

## 5. Change the card design

- **Tagline** (top right): set `CARD_TAGLINE=` in `.env`; no code change needed.
- **Logo**: put a PNG (or JPEG/WebP) with transparency in `assets\brand\` and set `BRAND_LOGO_PATH=` in `.env`.
- **Fonts**: set `FONT_HEADLINE_PATH`, `FONT_BRAND_PATH` or `FONT_LABEL_PATH` to `.ttf` files.
- **Colours, margins, text sizes**: edit the constants at the top of `techspire\image_generator.py`.
  `BACKGROUND` must stay RGB(20, 24, 33) unless the brand brief changes.
- Check the result with `--demo` and open `output\demo\images\`. The image tests must still pass.

## 6. Change the AI model, provider or key

- **Key**: put the new key in `.env` (`GEMINI_API_KEY=` or `OPENAI_API_KEY=`), then run `--doctor`. Rotate keys
  in Google AI Studio or the OpenAI dashboard; a key pasted into any chat should be treated as exposed.
- **Model**: set `AI_MODEL=` in `.env`. The default is `gemini-3.5-flash-lite`. `gemini-3.8-flash` writes
  richer copy but its free tier allows only **20 requests per day**, so use it on a paid tier.
- **Quota**: each article uses 1 AI request (2 if its first answer fails validation), plus retries on errors.
  Keep `MAX_ARTICLES_PER_RUN` x runs per day below your daily quota (check it at https://ai.dev/rate-limit).
  When the quota runs out, Techspire stops AI work for that run and continues next time; nothing is lost.
- **Provider**: `AI_PROVIDER=openai` plus `OPENAI_API_KEY`. Test with `--dry-run --limit 3`.

## 7. Update the Facebook Graph API version (future live use)

Meta retires Graph API versions about two years after release. Check
https://developers.facebook.com/docs/graph-api/changelog, set `FB_GRAPH_API_VERSION=` in `.env` (for example
`v27.0`), and run the tests. The publisher is covered by mocked tests; nothing is posted.

## 8. Update Python packages

1. Back up the project folder.
2. `.\.venv\Scripts\python.exe -m pip install --upgrade <package>` (one at a time, e.g. `google-genai`).
3. Put the new version in `requirements.txt`.
4. Run the checklist. If `tests\test_content_filter.py::test_gemini_errors_are_mapped_without_sdk_retries`
   fails after a `google-genai` upgrade, the SDK changed its retry internals: see the comment in
   `GeminiProvider.__init__`.

## 9. Move to a new computer

1. Copy the project folder **without** `.venv` (recreate it with the Install steps in the README).
2. Copy `.env` separately and securely (never by email or chat); restrict who can read it.
3. Copy `data\techspire.db` if you want to keep the duplicate history; otherwise recent articles will be
   analysed again.
4. Run `--doctor`, then set up the scheduled task again (README, Scheduling).

## 10. Verification checklist (after every change)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check techspire tests
.\.venv\Scripts\python.exe -m compileall -q techspire
.\.venv\Scripts\python.exe -m techspire.main --doctor
.\.venv\Scripts\python.exe -m techspire.main --demo
.\.venv\Scripts\python.exe -m techspire.main --check-feeds
```

Then open `output\demo\previews\index.html` (and `output\previews\index.html` after a real dry run) and look at
the cards. `--doctor` must still say `LIVE FACEBOOK PUBLISHING: DISABLED` unless you have deliberately decided
otherwise (README, Future live publishing). Finally, refresh the Gemini copy (section 1).
