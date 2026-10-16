"""Pytest configuration and environment fixtures for headref."""

import os

# Ensure dummy configuration secrets exist so app.config can load in test environments
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://headref:headref@localhost:5432/headref")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("JIRA_BASE_URL", "https://example.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "test@example.com")
os.environ.setdefault("JIRA_API_TOKEN", "test-jira-token")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
