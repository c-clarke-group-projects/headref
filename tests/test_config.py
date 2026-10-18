"""Unit tests for the configuration module (P1-003, Issue #9)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    DB_STATEMENT_TIMEOUT_MS,
    DRAFT_PR_STALE_DAYS,
    DUE_SOON_DAYS,
    EXCERPT_MAX_CHARS,
    FRESHNESS_WINDOW_HOURS,
    HTTP_MAX_ATTEMPTS,
    HTTP_TIMEOUT_SECONDS,
    ISSUE_NO_CODE_DAYS,
    MAX_EVIDENCE_ITEMS,
    MODEL_ID,
    NO_ACTIVITY_DAYS,
    OLLAMA_NUM_PREDICT,
    OLLAMA_SEED,
    PR_REVIEW_WAIT_DAYS,
    PROMPT_VERSION,
    RECENT_ACTIVITY_DAYS,
    STATUS_STUCK_DAYS,
    SYNC_INTERVAL_MINUTES,
    Settings,
    load_settings,
)


def test_threshold_constants_match_spec() -> None:
    """The 10 thresholds from DATA_AND_EVIDENCE.md 6.8 must match exact values."""
    assert FRESHNESS_WINDOW_HOURS == 24
    assert RECENT_ACTIVITY_DAYS == 14
    assert PR_REVIEW_WAIT_DAYS == 3
    assert DRAFT_PR_STALE_DAYS == 5
    assert ISSUE_NO_CODE_DAYS == 3
    assert DUE_SOON_DAYS == 3
    assert STATUS_STUCK_DAYS == 5
    assert NO_ACTIVITY_DAYS == 7
    assert EXCERPT_MAX_CHARS == 500
    assert MAX_EVIDENCE_ITEMS == 40
    assert SYNC_INTERVAL_MINUTES == 5


def test_runtime_and_model_settings() -> None:
    """HTTP, model, and database timeout settings must match defaults."""
    assert HTTP_TIMEOUT_SECONDS == 10.0
    assert HTTP_MAX_ATTEMPTS == 3
    assert MODEL_ID == "llama3.2"
    assert OLLAMA_SEED == 42
    assert OLLAMA_NUM_PREDICT == 300
    assert PROMPT_VERSION == "narrative_v1"
    assert DB_STATEMENT_TIMEOUT_MS == 2000


def test_secrets_redacted_in_repr_and_str() -> None:
    """No secret or token appears in __repr__ or __str__."""
    cfg = Settings(
        database_url="postgresql+psycopg://headref:super_secret_pw@localhost:5432/headref",
        github_token="ghp_verysecrettoken12345",
        jira_base_url="https://secret-domain.atlassian.net",
        jira_email="engineer@example.com",
        jira_api_token="jira_top_secret_token_abc",
        _env_file=None,
    )
    repr_text = repr(cfg)
    str_text = str(cfg)

    assert "ghp_verysecrettoken12345" not in repr_text
    assert "super_secret_pw" not in repr_text
    assert "jira_top_secret_token_abc" not in repr_text

    assert "ghp_verysecrettoken12345" not in str_text
    assert "super_secret_pw" not in str_text
    assert "jira_top_secret_token_abc" not in str_text


def test_missing_required_secret_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings cannot be created without required secrets and has no default secrets."""
    for secret in (
        "DATABASE_URL",
        "GITHUB_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(secret, raising=False)
        monkeypatch.delenv(secret.lower(), raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    errors = exc_info.value.errors()
    missing_fields = {str(err["loc"][0]).lower() for err in errors}
    expected_required = {
        "database_url",
        "github_token",
        "jira_base_url",
        "jira_email",
        "jira_api_token",
    }
    assert expected_required.issubset(missing_fields)


def test_load_settings_fails_fast_with_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings raises RuntimeError naming missing secrets when environment lacks them."""
    for secret in (
        "DATABASE_URL",
        "GITHUB_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(secret, raising=False)
        monkeypatch.delenv(secret.lower(), raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        load_settings(_env_file=None)

    error_message = str(exc_info.value)
    assert "Missing required configuration secrets" in error_message
    assert "DATABASE_URL" in error_message


def test_alias_support() -> None:
    """Aliases like OLLAMA_BASE_URL and EVIDENCE_WINDOW_DAYS are properly mapped."""
    cfg = Settings(
        database_url="postgresql+psycopg://headref:headref@localhost:5432/headref",
        github_token="gh_token",
        jira_base_url="https://example.atlassian.net",
        jira_email="user@example.com",
        jira_api_token="jira_tok",
        OLLAMA_BASE_URL="http://ollama-host:11434",
        OLLAMA_MODEL="llama3.2:1b",
        EVIDENCE_WINDOW_DAYS=21,
        _env_file=None,
    )
    assert cfg.ollama_url == "http://ollama-host:11434"
    assert cfg.model_id == "llama3.2:1b"
    assert cfg.recent_activity_days == 21


def test_startup_fails_without_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Starting FastAPI app fails fast when required secrets are missing."""
    from fastapi.testclient import TestClient

    import app.config
    import app.main

    # Settings reads env_file=".env", resolved against the working directory. A
    # developer machine has one and CI does not, so move to an empty directory to
    # make the test assert the same thing in both places.
    monkeypatch.chdir(tmp_path)

    for secret in (
        "DATABASE_URL",
        "GITHUB_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ):
        monkeypatch.delenv(secret, raising=False)
        monkeypatch.delenv(secret.lower(), raising=False)

    monkeypatch.setattr(app.config, "settings", None)

    with pytest.raises(RuntimeError) as exc_info:
        with TestClient(app.main.app):
            pass

    assert "Missing required configuration secrets" in str(exc_info.value)
