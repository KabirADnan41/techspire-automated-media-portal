from pathlib import Path

import pytest

from techspire.config import DEFAULT_GRAPH_API_VERSION, PROJECT_ROOT, Settings
from techspire.exceptions import ConfigError


def test_defaults_are_safe():
    s = Settings.from_mapping({})
    assert s.dry_run is True
    assert s.publish_enabled is False
    assert s.ai_provider == "gemini"
    assert s.ai_model == "gemini-3.5-flash-lite"
    assert s.fb_graph_api_version == DEFAULT_GRAPH_API_VERSION == "v26.0"
    assert s.max_articles_per_run == 10
    assert s.max_article_age_hours == 48
    assert s.publish_require_ids is True


def test_env_example_ships_the_safe_configuration():
    s = Settings.load(env_file=PROJECT_ROOT / ".env.example", environ={})
    assert s.dry_run is True
    assert s.publish_enabled is False
    assert s.fb_page_access_token == ""
    assert s.gemini_api_key == ""


@pytest.mark.parametrize("raw,expected", [("true", True), ("TRUE", True), ("1", True), ("yes", True),
                                          ("false", False), ("0", False), ("No", False), ("off", False)])
def test_boolean_parsing(raw, expected):
    assert Settings.from_mapping({"DRY_RUN": raw}).dry_run is expected


@pytest.mark.parametrize("raw", ["ture", "enabled", "2"])
def test_typo_in_safety_flag_fails_closed(raw):
    with pytest.raises(ConfigError, match="DRY_RUN must be true or false"):
        Settings.from_mapping({"DRY_RUN": raw})


def test_invalid_provider_is_reported_clearly():
    with pytest.raises(ConfigError, match="AI_PROVIDER must be one of gemini, openai"):
        Settings.from_mapping({"AI_PROVIDER": "claude"})


def test_openai_default_model():
    assert Settings.from_mapping({"AI_PROVIDER": "openai"}).ai_model == "gpt-6-luna"


@pytest.mark.parametrize("version", ["20.0", "v26", "latest", "v26.0.1"])
def test_graph_version_format(version):
    with pytest.raises(ConfigError, match="FB_GRAPH_API_VERSION"):
        Settings.from_mapping({"FB_GRAPH_API_VERSION": version})


def test_numbers_are_validated():
    with pytest.raises(ConfigError, match="MAX_ARTICLES_PER_RUN must be between"):
        Settings.from_mapping({"MAX_ARTICLES_PER_RUN": "0"})
    with pytest.raises(ConfigError, match="must be a whole number"):
        Settings.from_mapping({"MAX_ARTICLES_PER_RUN": "ten"})


def test_page_id_must_be_numeric():
    with pytest.raises(ConfigError, match="FB_PAGE_ID"):
        Settings.from_mapping({"FB_PAGE_ID": "techspireofficial"})


def test_relative_paths_resolve_against_project_root():
    s = Settings.from_mapping({"DATABASE_PATH": "data/x.db"})
    assert s.database_path == PROJECT_ROOT / "data" / "x.db"


def test_secrets_never_appear_in_repr():
    s = Settings.from_mapping({"GEMINI_API_KEY": "AIzaSECRETSECRETSECRET", "OPENAI_API_KEY": "sk-secretvalue123",
                               "FB_PAGE_ACCESS_TOKEN": "EAAsecrettokenvalue"})
    text = repr(s)
    assert "SECRET" not in text and "secret" not in text
    assert set(s.secret_values()) == {"AIzaSECRETSECRETSECRET", "sk-secretvalue123", "EAAsecrettokenvalue"}


def test_missing_ai_key_message_says_what_to_do():
    with pytest.raises(ConfigError) as info:
        Settings.from_mapping({}).require_ai_key()
    assert "GEMINI_API_KEY is missing" in str(info.value)
    assert "--demo" in str(info.value)


def test_process_environment_overrides_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("DRY_RUN=true\nLOG_LEVEL=DEBUG\n", encoding="utf-8")
    s = Settings.load(env_file=env, environ={"LOG_LEVEL": "WARNING", "UNRELATED": "x"})
    assert s.log_level == "WARNING"
    assert s.env_file == env


def test_missing_explicit_env_file_is_an_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="does not exist"):
        Settings.load(env_file=tmp_path / "nope.env", environ={})


def test_card_tagline_default_and_override():
    assert Settings.from_mapping({}).card_tagline == "BITESIZE NEWS"
    assert Settings.from_mapping({"CARD_TAGLINE": "news in brief"}).card_tagline == "NEWS IN BRIEF"
