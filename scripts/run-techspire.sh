#!/usr/bin/env sh
# Techspire Official - one scheduled dry run (cron / systemd timer).
# Uses absolute paths, so the scheduler's working directory does not matter.
# Results: output/previews/index.html (review page) and logs/techspire.log.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" -m techspire.main --dry-run
