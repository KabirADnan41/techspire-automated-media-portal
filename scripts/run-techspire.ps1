# Techspire Official - one scheduled dry run (Windows Task Scheduler).
# Uses absolute paths, so the scheduler's working directory does not matter.
# Results: output\previews\index.html (review page) and logs\techspire.log.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m techspire.main --dry-run
exit $LASTEXITCODE
