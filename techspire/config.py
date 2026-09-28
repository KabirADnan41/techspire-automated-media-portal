"""Central configuration, loaded from `.env` plus real environment variables.

Real environment variables win over `.env`. Relative paths resolve against the
project root, so scheduled runs work no matter which directory they start in.
Secrets are excluded from repr() so they never end up in logs or tracebacks.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from techspire.exceptions import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

AI_PROVIDERS = ("gemini", "openai")
DEFAULT_AI_MODELS = {
    # Current models per the official docs (checked 2026-09-28); see docs/reference-audit.md.
    # gemini-3.5-flash-lite is the default: fast, cheap, and not capped like gemini-3.8-flash,
    # whose free tier allows only 20 requests per day (measured 2026-09-28). On a paid tier,
    # set AI_MODEL=gemini-3.8-flash for richer copy.
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-6-luna",
}
# Latest Graph API version per Meta's changelog (v26.0, released 2026-07-29).
# v20.0 became unavailable on 2026-09-24.
DEFAULT_GRAPH_API_VERSION = "v26.0"
GRAPH_HOST = "https://graph.facebook.com"
DEFAULT_USER_AGENT = "TechspireNewsBot/1.0 (+https://www.facebook.com/techspireofficial)"
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}


@dataclass(frozen=True)
class Settings:
    # AI
    ai_provider: str = "gemini"
    ai_model: str = DEFAULT_AI_MODELS["gemini"]
    ai_reasoning_effort: str | None = "low"
    ai_temperature: float | None = None
    ai_request_timeout: float = 60.0
    gemini_api_key: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    # Facebook (future live publishing only)
    fb_page_id: str = ""
    fb_page_access_token: str = field(default="", repr=False)
    fb_graph_api_version: str = DEFAULT_GRAPH_API_VERSION
    # Hard safety locks
    dry_run: bool = True
    publish_enabled: bool = False
    publish_require_ids: bool = True  # live runs must name the reviewed articles (--ids)
    # Runtime
    log_level: str = "INFO"
    database_path: Path = PROJECT_ROOT / "data" / "techspire.db"
    output_dir: Path = PROJECT_ROOT / "output"
    log_dir: Path = PROJECT_ROOT / "logs"
    feeds_file: Path = PROJECT_ROOT / "config" / "feeds.yaml"
    max_articles_per_run: int = 10
    max_article_age_hours: float = 48.0
    max_retries_per_article: int = 3
    # Networking
    rss_request_timeout: float = 20.0
    facebook_request_timeout: float = 30.0
    rss_user_agent: str = DEFAULT_USER_AGENT
    # Branding (all optional; bundled Montserrat fonts are used by default)
    font_headline_path: Path | None = None
    font_brand_path: Path | None = None
    font_label_path: Path | None = None
    brand_logo_path: Path | None = None
    card_tagline: str = "BITESIZE NEWS"  # top-right text on every card
    # Where settings came from (for --doctor)
    env_file: Path | None = None

    @property
    def images_dir(self) -> Path:
        return self.output_dir / "images"

    @property
    def previews_dir(self) -> Path:
        return self.output_dir / "previews"

    @property
    def review_page(self) -> Path:
        return self.previews_dir / "index.html"

    @property
    def temperature_ignored(self) -> bool:
        """Current Gemini models reject `temperature`, so AI_TEMPERATURE only applies to OpenAI."""
        return self.ai_provider == "gemini" and self.ai_temperature is not None

    @property
    def ai_api_key(self) -> str:
        return self.gemini_api_key if self.ai_provider == "gemini" else self.openai_api_key

    @property
    def ai_key_name(self) -> str:
        return "GEMINI_API_KEY" if self.ai_provider == "gemini" else "OPENAI_API_KEY"

    def secret_values(self) -> list[str]:
        """Every configured secret, for log redaction."""
        return [s for s in (self.gemini_api_key, self.openai_api_key, self.fb_page_access_token) if s]

    def require_ai_key(self) -> str:
        key = self.ai_api_key
        if not key:
            raise ConfigError(
                f"{self.ai_key_name} is missing (AI_PROVIDER={self.ai_provider}).\n"
                f"Add it to your .env file, or run the offline demo instead:\n"
                f"    python -m techspire.main --demo"
            )
        return key

    @classmethod
    def load(cls, env_file: Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings from `.env` (if present) overlaid with the process environment."""
        env_path = env_file or PROJECT_ROOT / ".env"
        values: dict[str, str] = {}
        if env_path.is_file():
            try:
                values.update({k: v for k, v in dotenv_values(env_path).items() if v is not None})
            except (OSError, UnicodeDecodeError) as exc:
                raise ConfigError(f"Could not read {env_path}: {exc}. Check the file is plain UTF-8 text.") from exc
        elif env_file is not None:
            raise ConfigError(f"Env file {env_path} does not exist.")
        values.update(os.environ if environ is None else environ)
        return cls.from_mapping(values, env_file=env_path if env_path.is_file() else None)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str], env_file: Path | None = None) -> Settings:
        get = _Reader(values)
        d = cls()  # the dataclass defaults are the single source of truth (including the safety locks)
        provider = get.text("AI_PROVIDER", d.ai_provider).lower()
        if provider not in AI_PROVIDERS:
            raise ConfigError(
                f"AI_PROVIDER must be one of {', '.join(AI_PROVIDERS)} (got '{provider}'). Fix it in your .env file."
            )
        reasoning = get.text("AI_REASONING_EFFORT", d.ai_reasoning_effort or "").lower() or None
        if reasoning is not None and reasoning not in REASONING_EFFORTS:
            raise ConfigError(
                f"AI_REASONING_EFFORT must be one of {', '.join(REASONING_EFFORTS)} or empty (got '{reasoning}')."
            )
        version = get.text("FB_GRAPH_API_VERSION", "") or d.fb_graph_api_version
        if not re.fullmatch(r"v\d{1,3}\.\d{1,2}", version):
            raise ConfigError(f"FB_GRAPH_API_VERSION must look like 'v26.0' (got '{version}').")
        page_id = get.text("FB_PAGE_ID", "")
        if page_id and not page_id.isdigit():
            raise ConfigError("FB_PAGE_ID must be the numeric Facebook Page ID (digits only).")
        log_level = get.text("LOG_LEVEL", d.log_level).upper()
        if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ConfigError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR (got '{log_level}').")

        return cls(
            ai_provider=provider,
            ai_model=get.text("AI_MODEL", "") or DEFAULT_AI_MODELS[provider],
            ai_reasoning_effort=reasoning,
            ai_temperature=get.optional_float("AI_TEMPERATURE", 0.0, 2.0),
            ai_request_timeout=get.number("AI_REQUEST_TIMEOUT", d.ai_request_timeout, 5.0, 600.0),
            gemini_api_key=get.text("GEMINI_API_KEY", ""),
            openai_api_key=get.text("OPENAI_API_KEY", ""),
            fb_page_id=page_id,
            fb_page_access_token=get.text("FB_PAGE_ACCESS_TOKEN", ""),
            fb_graph_api_version=version,
            dry_run=get.boolean("DRY_RUN", d.dry_run),
            publish_enabled=get.boolean("PUBLISH_ENABLED", d.publish_enabled),
            publish_require_ids=get.boolean("PUBLISH_REQUIRE_IDS", d.publish_require_ids),
            log_level=log_level,
            database_path=get.path("DATABASE_PATH", d.database_path),
            output_dir=get.path("OUTPUT_DIR", d.output_dir),
            log_dir=get.path("LOG_DIR", d.log_dir),
            feeds_file=get.path("FEEDS_FILE", d.feeds_file),
            max_articles_per_run=int(get.number("MAX_ARTICLES_PER_RUN", d.max_articles_per_run, 1, 200, integer=True)),
            max_article_age_hours=get.number("MAX_ARTICLE_AGE_HOURS", d.max_article_age_hours, 1.0, 24.0 * 60),
            max_retries_per_article=int(get.number("MAX_RETRIES_PER_ARTICLE", d.max_retries_per_article, 1, 20,
                                                   integer=True)),
            rss_request_timeout=get.number("RSS_REQUEST_TIMEOUT", d.rss_request_timeout, 1.0, 300.0),
            facebook_request_timeout=get.number("FACEBOOK_REQUEST_TIMEOUT", d.facebook_request_timeout, 1.0, 300.0),
            rss_user_agent=get.text("RSS_USER_AGENT", "") or d.rss_user_agent,
            font_headline_path=get.optional_path("FONT_HEADLINE_PATH"),
            font_brand_path=get.optional_path("FONT_BRAND_PATH"),
            font_label_path=get.optional_path("FONT_LABEL_PATH"),
            brand_logo_path=get.optional_path("BRAND_LOGO_PATH"),
            card_tagline=(get.text("CARD_TAGLINE", "") or d.card_tagline).upper()[:28],
            env_file=env_file,
        )


class _Reader:
    """Typed accessors with operator-friendly error messages."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = values

    def text(self, name: str, default: str) -> str:
        raw = self._values.get(name)
        return default if raw is None else raw.strip()

    def boolean(self, name: str, default: bool) -> bool:
        raw = self.text(name, "")
        if raw == "":
            return default
        lowered = raw.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        # Fail closed: a typo in a safety flag must never be guessed at.
        raise ConfigError(f"{name} must be true or false (got '{raw}'). Fix it in your .env file.")

    def number(self, name: str, default: float, low: float, high: float, integer: bool = False) -> float:
        raw = self.text(name, "")
        if raw == "":
            return default
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            kind = "a whole number" if integer else "a number"
            raise ConfigError(f"{name} must be {kind} (got '{raw}').") from None
        if not low <= value <= high:
            raise ConfigError(f"{name} must be between {low:g} and {high:g} (got {raw}).")
        return value

    def optional_float(self, name: str, low: float, high: float) -> float | None:
        if self.text(name, "") == "":
            return None
        return self.number(name, 0.0, low, high)

    def path(self, name: str, default: Path) -> Path:
        raw = self.text(name, "")
        return _resolve(raw) if raw else default

    def optional_path(self, name: str) -> Path | None:
        raw = self.text(name, "")
        return _resolve(raw) if raw else None


def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)
