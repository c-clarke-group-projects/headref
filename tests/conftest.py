"""Pytest configuration and environment fixtures for headref."""

import os
from collections.abc import Generator

import pytest

# Ensure dummy configuration secrets exist so app.config can load in test environments.
# An empty value counts as unset: docker compose passes the blank lines of .env through
# as empty variables, which would otherwise make a local run differ from CI.
_TEST_ENV = {
    "DATABASE_URL": "postgresql+psycopg://headref:headref@localhost:5432/headref",
    "GITHUB_TOKEN": "test-github-token",
    "JIRA_BASE_URL": "https://example.atlassian.net",
    "JIRA_EMAIL": "test@example.com",
    "JIRA_API_TOKEN": "test-jira-token",
    "SCHEDULER_ENABLED": "false",
}
for _key, _value in _TEST_ENV.items():
    if not os.environ.get(_key):
        os.environ[_key] = _value


@pytest.fixture(autouse=True)
def _restore_configuration() -> Generator[None, None, None]:
    """Undo any change a test makes to the environment or the settings singleton."""
    import app.config

    saved_env = dict(os.environ)
    saved_settings = app.config.settings
    yield
    os.environ.clear()
    os.environ.update(saved_env)
    app.config.settings = saved_settings
