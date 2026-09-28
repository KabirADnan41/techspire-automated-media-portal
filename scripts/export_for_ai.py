"""Export a clean snapshot of Techspire for an AI assistant (Gemini, NotebookLM, ...).

    .venv\\Scripts\\python.exe scripts\\export_for_ai.py

Writes to exports/ (git-ignored):
  techspire-source-<date>.zip   source, tests, docs and config (for code/folder upload)
  techspire-context-<date>.md   the same as one Markdown file (for Gem knowledge / NotebookLM)

Only allow-listed text files are included. Never included: .env, API keys, the database, logs,
generated images, the virtual environment, or the reference/ books. The export is refused if any
file contains something that looks like a key or token, or the value of a secret from .env.
"""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "exports"

INCLUDE = (
    "GEMINI.md", "CLAUDE.md", "README.md", "progress.md", ".env.example", ".gitignore", "pyproject.toml",
    "requirements.txt", "requirements-dev.txt", "assets/fonts/OFL.txt",
    "techspire/*.py", "tests/*.py", "config/*.yaml", "docs/*.md", "scripts/*.py", "scripts/*.ps1",
    "scripts/*.sh", "demo/*.yaml", "demo/*.json", "demo/feeds/*.xml",
)
NEVER = (".venv/", "data/", "logs/", "output/", "exports/", "reference/", "__pycache__/")
SECRET_SHAPES = (
    re.compile(r"\bEAA[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)
# Test fixtures contain fake, deliberately token-shaped strings to prove redaction works.
FAKE_TOKEN_FILES = {"tests/test_logging_config.py", "tests/test_fb_publisher.py", "tests/test_pipeline.py",
                    "tests/test_config.py", "tests/test_content_filter.py", "tests/test_storage.py"}
LANGUAGE = {".py": "python", ".md": "markdown", ".yaml": "yaml", ".json": "json", ".xml": "xml",
            ".toml": "toml", ".ps1": "powershell", ".sh": "bash", ".txt": "text", ".example": "dotenv"}


def collect() -> list[Path]:
    files: set[Path] = set()
    for pattern in INCLUDE:
        files.update(p for p in ROOT.glob(pattern) if p.is_file())
    chosen = []
    for path in sorted(files):
        rel = "/" + path.relative_to(ROOT).as_posix()
        if rel == "/.env" or any(part in rel for part in NEVER):
            continue
        chosen.append(path)
    return chosen


def real_secrets() -> list[str]:
    env = ROOT / ".env"
    if not env.is_file():
        return []
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in ("GEMINI_API_KEY", "OPENAI_API_KEY", "FB_PAGE_ACCESS_TOKEN") and len(value.strip()) >= 8:
            values.append(value.strip())
    return values


def check_secrets(files: list[Path]) -> None:
    secrets = real_secrets()
    problems = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if any(secret in text for secret in secrets):
            problems.append(f"{rel}: contains a real secret from .env")
        if rel not in FAKE_TOKEN_FILES and any(p.search(text) for p in SECRET_SHAPES):
            problems.append(f"{rel}: contains something that looks like an API key or token")
    if problems:
        sys.exit("Export refused:\n  " + "\n  ".join(problems))


def main() -> None:
    files = collect()
    check_secrets(files)
    OUT_DIR.mkdir(exist_ok=True)
    stamp = date.today().isoformat()
    zip_path = OUT_DIR / f"techspire-source-{stamp}.zip"
    md_path = OUT_DIR / f"techspire-context-{stamp}.md"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"techspire-project/{path.relative_to(ROOT).as_posix()}")

    rels = [p.relative_to(ROOT).as_posix() for p in files]
    ordered = [ROOT / "GEMINI.md"] + [p for p in files if p.name != "GEMINI.md"]
    parts = [
        f"# Techspire Official - project snapshot ({stamp})\n",
        "Read GEMINI.md (first section below) before anything else: it contains the hard safety rules.\n",
        "## Files\n", "\n".join(f"- `{r}`" for r in rels), "\n",
    ]
    for path in ordered:
        rel = path.relative_to(ROOT).as_posix()
        language = LANGUAGE.get(path.suffix, "text")
        fence = "````" if "```" in path.read_text(encoding="utf-8") else "```"
        parts.append(f"\n## `{rel}`\n\n{fence}{language}\n{path.read_text(encoding='utf-8').rstrip()}\n{fence}\n")
    md_path.write_text("\n".join(parts), encoding="utf-8")

    print(f"Exported {len(files)} files (no secrets, no data, no reference books):")
    print(f"  {zip_path}  ({zip_path.stat().st_size / 1024:.0f} KB)")
    print(f"  {md_path}  ({md_path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
