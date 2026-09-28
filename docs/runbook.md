# Operations runbook

Short procedures for the situations that need a human. Publishing is disabled today, so sections 2-4 describe
what to do once live publishing has been deliberately enabled in the future.

## 1. Daily review (dry-run mode)

1. Open `output/previews/index.html` in a browser.
2. For each card, check the headline and caption against the original article (link in each card).
3. Look at "Recently rejected" for tech stories that were wrongly rejected, and at "Failed" for repeated errors.
4. `python -m techspire.main --status` shows totals and recent runs; `logs/techspire.log` has the details.

## 2. A photo upload has an unknown outcome (future live mode)

Symptoms: `--status` prints `ATTENTION: ... unknown Facebook outcome`, and the article is `FAILED` with stage
`publish_unknown`. The upload timed out, Facebook answered with a server error, or the run stopped mid-upload, so the
post may or may not exist. Techspire never retries this automatically, because retrying could post it twice.

1. Open the Facebook Page and look for the article's card (use the headline shown by `--status`/the review page).
2. If the post **exists**, copy its ID (`<page-id>_<post-id>`, visible in the post's URL or in Meta Business Suite) and
   run:
   `python -m techspire.main --resolve-unknown <article-id> --post-id <page-id>_<post-id>`
   The next `--publish` run adds only the "Read full story" comment.
3. If the post **does not exist**:
   `python -m techspire.main --resolve-unknown <article-id> --not-posted`
   The article becomes eligible for publishing again (it still needs a `--publish` run).

Comments need no manual action: before every comment request Techspire saves an "in flight" marker, so after a
timeout, a server error or even a crash, the next run first looks (read-only) for the Page's own "Read full story"
comment and only posts one if it is not there.

## 3. Suspected leak of the Page access token, or an unrecognised post on the Page

A leaked token can be used within minutes (Blue Team Handbook), and an unrecognised post is a compromise signal
(Cybersecurity For Dummies). Act first, investigate second.

**Contain (minutes)**
1. Set `PUBLISH_ENABLED=false` and `DRY_RUN=true` in `.env`, and disable the scheduled task / cron job.
2. Revoke the token: in Meta Business Suite remove the app's access to the Page (or reset the app secret in the Meta
   developer dashboard, which invalidates its tokens). Never reuse the old token.
3. Review the Page's roles and remove anyone or any app you don't recognise; make sure every admin uses two-factor
   authentication.

**Investigate**
4. Compare the Page's recent posts with the database (`--status`, `article_events` table): every Techspire post has a
   recorded `facebook_post_id`. Posts that are not in the database were not made by Techspire.
5. Check `logs/techspire.log` for runs you did not start. Log lines carry a token fingerprint (`token=abcd123456`),
   never the token itself, so you can tell which token was used.
6. Check that nobody changed the project files, the virtual environment or the scheduled task (a tampered scheduled job
   runs with that account's rights).

**Recover**
7. Delete any unauthorised posts, create a new Page token with only the permissions listed in the README, put it in
   `.env`, and run `python -m techspire.main --doctor`.
8. Re-enable scheduling in dry-run mode first. Only re-enable publishing as a deliberate decision.

**Communicate and learn**
9. If followers saw unauthorised content, post a short factual notice (no speculation). Write down what happened, when
   it happened and when it was noticed, and what will change.

## 4. Suspected leak of an AI API key

1. Revoke the key in Google AI Studio (Gemini) or the OpenAI dashboard and create a new one.
2. Put the new key in `.env` and run `python -m techspire.main --doctor`.
3. Check the provider's usage page for unexpected spend.

## 5. Backups and data retention

- The only state is `data/techspire.db`. Back it up by copying the file (plus any `-wal`/`-shm` files next to it)
  while no run is active. Keep backups private: they contain article metadata, never secrets.
- Logs rotate automatically (5 files x 2 MB). Generated images and previews can be deleted at any time; the database
  keeps the decisions.
- No personal data is collected beyond the author names published in public feeds.
