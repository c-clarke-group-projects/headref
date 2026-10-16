"""ARGUS configuration module.

Defines typed application settings and named constants (NFR-006, NFR-011, NFR-021).
All thresholds, timeouts, and model parameters are defined here in one place.
Secrets are read from environment variables and never defaulted.
"""

from typing import Any

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Secrets (Environment only, never defaulted, fail-fast if missing)
    # -------------------------------------------------------------------------
    database_url: str = Field(
        ...,
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
        description="PostgreSQL connection string with psycopg driver.",
    )
    github_token: str = Field(
        ...,
        validation_alias=AliasChoices("GITHUB_TOKEN", "github_token"),
        description="Read-only GitHub personal access token.",
    )
    jira_base_url: str = Field(
        ...,
        validation_alias=AliasChoices("JIRA_BASE_URL", "jira_base_url"),
        description="Base URL of Jira instance.",
    )
    jira_email: str = Field(
        ...,
        validation_alias=AliasChoices("JIRA_EMAIL", "jira_email"),
        description="Email associated with Jira API token.",
    )
    jira_api_token: str = Field(
        ...,
        validation_alias=AliasChoices("JIRA_API_TOKEN", "jira_api_token"),
        description="Atlassian Jira API token.",
    )
    github_oauth_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_OAUTH_CLIENT_ID", "github_oauth_client_id"),
        description="GitHub OAuth application client ID.",
    )
    github_oauth_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_OAUTH_CLIENT_SECRET", "github_oauth_client_secret"),
        description="GitHub OAuth application client secret.",
    )

    @property
    def github_oauth_enabled(self) -> bool:
        """True if GitHub OAuth credentials are configured."""
        return bool(self.github_oauth_client_id and self.github_oauth_client_secret)


    # -------------------------------------------------------------------------
    # Operational Settings
    # -------------------------------------------------------------------------
    app_env: str = Field(
        default="local",
        validation_alias=AliasChoices("APP_ENV", "app_env"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "log_level"),
    )
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("API_PORT", "api_port"),
    )
    db_statement_timeout_ms: int = Field(
        default=2000,
        validation_alias=AliasChoices("DB_STATEMENT_TIMEOUT_MS", "db_statement_timeout_ms"),
        description="Database statement timeout in milliseconds for read sessions.",
    )

    # -------------------------------------------------------------------------
    # The 10 Threshold Constants (DATA_AND_EVIDENCE.md 6.8 & TASKS.md P1-003)
    # -------------------------------------------------------------------------
    freshness_window_hours: int = Field(
        default=24,
        validation_alias=AliasChoices("FRESHNESS_WINDOW_HOURS", "freshness_window_hours"),
        description="Fresh to stale boundary in hours.",
    )
    recent_activity_days: int = Field(
        default=14,
        validation_alias=AliasChoices(
            "RECENT_ACTIVITY_DAYS", "recent_activity_days", "EVIDENCE_WINDOW_DAYS"
        ),
        description="Default activity window in days for questions.",
    )
    pr_review_wait_days: int = Field(
        default=3,
        validation_alias=AliasChoices("PR_REVIEW_WAIT_DAYS", "pr_review_wait_days"),
        description="Blocker threshold: days an unreviewed pull request waits.",
    )
    draft_pr_stale_days: int = Field(
        default=5,
        validation_alias=AliasChoices("DRAFT_PR_STALE_DAYS", "draft_pr_stale_days"),
        description="Blocker threshold: days a draft pull request remains inactive.",
    )
    issue_no_code_days: int = Field(
        default=3,
        validation_alias=AliasChoices("ISSUE_NO_CODE_DAYS", "issue_no_code_days"),
        description="Blocker threshold: days an in-progress ticket has no linked code.",
    )
    due_soon_days: int = Field(
        default=3,
        validation_alias=AliasChoices("DUE_SOON_DAYS", "due_soon_days"),
        description="Risk threshold: days before due date.",
    )
    status_stuck_days: int = Field(
        default=5,
        validation_alias=AliasChoices("STATUS_STUCK_DAYS", "status_stuck_days"),
        description="Risk threshold: days an issue sits in the same status.",
    )
    no_activity_days: int = Field(
        default=7,
        validation_alias=AliasChoices("NO_ACTIVITY_DAYS", "no_activity_days"),
        description="Risk threshold: days without activity on a high-priority item.",
    )
    excerpt_max_chars: int = Field(
        default=500,
        validation_alias=AliasChoices("EXCERPT_MAX_CHARS", "excerpt_max_chars"),
        description="Maximum characters for untrusted excerpt text.",
    )
    max_evidence_items: int = Field(
        default=40,
        validation_alias=AliasChoices("MAX_EVIDENCE_ITEMS", "max_evidence_items"),
        description="Maximum evidence items presented to reasoning stage.",
    )
    github_repos: str = Field(
        default="keshan-dev/argus",
        validation_alias=AliasChoices("GITHUB_REPOS", "github_repos"),
        description=(
            "Comma separated owner/repo scopes this deployment ingests. "
            "The MVP has 1 team, so these are the team's GitHub scopes."
        ),
    )
    jira_project_keys: str = Field(
        default="ALL",
        validation_alias=AliasChoices("JIRA_PROJECT_KEYS", "jira_project_keys"),
        description=(
            "Comma separated Jira project keys this deployment ingests, or ALL. "
            "The MVP has 1 team, so these are the team's Jira scopes."
        ),
    )
    sync_interval_minutes: int = Field(
        default=5,
        validation_alias=AliasChoices("SYNC_INTERVAL_MINUTES", "sync_interval_minutes"),
        description="Cadence of sync scheduler in minutes.",
    )
    scheduler_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("SCHEDULER_ENABLED", "scheduler_enabled"),
        description="Master switch to enable or disable background sync scheduler.",
    )
    stuck_sync_timeout_minutes: int = Field(
        default=15,
        validation_alias=AliasChoices("STUCK_SYNC_TIMEOUT_MINUTES", "stuck_sync_timeout_minutes"),
        description="Timeout in minutes after which a running sync is considered stuck.",
    )

    # -------------------------------------------------------------------------
    # HTTP and Inference Settings
    # -------------------------------------------------------------------------
    http_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("HTTP_TIMEOUT_SECONDS", "http_timeout_seconds"),
        description="HTTP request timeout in seconds.",
    )
    http_max_attempts: int = Field(
        default=3,
        validation_alias=AliasChoices("HTTP_MAX_ATTEMPTS", "http_max_attempts"),
        description="Maximum retry attempts for HTTP client.",
    )
    model_id: str = Field(
        default="llama3.2",
        validation_alias=AliasChoices("MODEL_ID", "model_id", "OLLAMA_MODEL"),
        description="Ollama model identifier.",
    )
    ollama_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("OLLAMA_URL", "ollama_url", "OLLAMA_BASE_URL"),
        description="Base URL for local Ollama server.",
    )
    ollama_seed: int = Field(
        default=42,
        validation_alias=AliasChoices("OLLAMA_SEED", "ollama_seed"),
        description="Seed for deterministic generation.",
    )
    ollama_num_predict: int = Field(
        default=300,
        validation_alias=AliasChoices("OLLAMA_NUM_PREDICT", "ollama_num_predict"),
        description="Maximum tokens predicted by narrative model.",
    )
    prompt_version: str = Field(
        default="narrative_v1",
        validation_alias=AliasChoices("PROMPT_VERSION", "prompt_version"),
        description="Identifier of prompt template version.",
    )

    # Freshness display warnings (optional helpers for UI state)
    freshness_warn_minutes: int = Field(
        default=30,
        validation_alias=AliasChoices("FRESHNESS_WARN_MINUTES", "freshness_warn_minutes"),
    )
    freshness_stale_minutes: int = Field(
        default=120,
        validation_alias=AliasChoices("FRESHNESS_STALE_MINUTES", "freshness_stale_minutes"),
    )

    def __repr__(self) -> str:
        """Mask sensitive secrets in string representation."""
        redacted_keys = {"token", "secret", "password", "key", "database_url"}
        safe_fields: list[str] = []
        for key, val in self.__dict__.items():
            if any(term in key.lower() for term in redacted_keys):
                safe_fields.append(f"{key}='***'")
            else:
                safe_fields.append(f"{key}={val!r}")
        return f"Settings({', '.join(safe_fields)})"

    def __str__(self) -> str:
        return self.__repr__()


def load_settings(**kwargs: Any) -> Settings:
    """Load settings, failing fast with a clear message on missing secrets."""
    try:
        return Settings(**kwargs)
    except ValidationError as exc:
        missing = [
            str(err["loc"][0]).upper()
            for err in exc.errors()
            if err.get("type") in ("missing", "value_error.missing")
        ]
        if missing:
            msg = (
                f"Missing required configuration secrets: {', '.join(missing)}. "
                "Set them in environment variables or .env file."
            )
            raise RuntimeError(msg) from exc
        raise


# -----------------------------------------------------------------------------
# Named Constants Export
# Both developers import thresholds from here, never hardcoding them.
# -----------------------------------------------------------------------------
FRESHNESS_WINDOW_HOURS: int = 24
RECENT_ACTIVITY_DAYS: int = 14
PR_REVIEW_WAIT_DAYS: int = 3
DRAFT_PR_STALE_DAYS: int = 5
ISSUE_NO_CODE_DAYS: int = 3
DUE_SOON_DAYS: int = 3
STATUS_STUCK_DAYS: int = 5
NO_ACTIVITY_DAYS: int = 7
EXCERPT_MAX_CHARS: int = 500
MAX_EVIDENCE_ITEMS: int = 40
SYNC_INTERVAL_MINUTES: int = 5
SCHEDULER_ENABLED: bool = True
STUCK_SYNC_TIMEOUT_MINUTES: int = 15

HTTP_TIMEOUT_SECONDS: float = 10.0
HTTP_MAX_ATTEMPTS: int = 3
MODEL_ID: str = "llama3.2"
OLLAMA_URL: str = "http://localhost:11434"
OLLAMA_SEED: int = 42
OLLAMA_NUM_PREDICT: int = 300
PROMPT_VERSION: str = "narrative_v1"
DB_STATEMENT_TIMEOUT_MS: int = 2000

# Instantiate global settings singleton if environment permits
try:
    settings: Settings = load_settings()
except Exception:
    # Allow imports in environments like Alembic migrations or unit tests
    # where only a subset of environment variables is provided.
    settings = None  # type: ignore[assignment]


def get_settings() -> Settings:
    """Return settings instance, failing fast if required secrets are missing."""
    global settings
    if settings is None:
        settings = load_settings()
    return settings


def _split_scopes(raw: str) -> list[str]:
    """Split a comma separated scope setting, dropping blanks."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def github_scopes() -> list[str]:
    """GitHub owner/repo scopes this deployment ingests, from GITHUB_REPOS."""
    return _split_scopes(get_settings().github_repos)


def jira_scopes() -> list[str]:
    """Jira project key scopes this deployment ingests, from JIRA_PROJECT_KEYS."""
    return _split_scopes(get_settings().jira_project_keys)
