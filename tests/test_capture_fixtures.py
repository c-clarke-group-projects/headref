"""The fixture capture script never writes or reports an email address (P0-002, #2)."""

import json

from scripts.capture_fixtures import REDACTED, REDACTED_EMAIL, a3_counts, sanitize


def test_every_email_is_replaced_and_nothing_else_changes() -> None:
    raw = {
        "fields": {
            "assignee": {"accountId": "5f3a", "emailAddress": "person@company.example"},
            "summary": "Contact person@company.example about it",
        },
        "commit": {"author": {"name": "Keshan", "email": "keshan@company.example"}},
        "list": [{"email": "other@company.example", "login": "octo"}],
        "blank": {"email": ""},
    }

    clean = sanitize(raw)

    text = json.dumps(clean)
    assert "keshan@company.example" not in text
    assert "other@company.example" not in text
    assert clean["fields"]["assignee"]["emailAddress"] == REDACTED_EMAIL
    assert clean["commit"]["author"]["email"] == REDACTED_EMAIL
    assert clean["list"][0] == {"email": REDACTED_EMAIL, "login": "octo"}
    assert clean["blank"]["email"] == "", "an empty value stays empty"
    # Free text keeps everything except the address.
    assert clean["fields"]["summary"] == f"Contact {REDACTED_EMAIL} about it"
    assert raw["commit"]["author"]["email"] == "keshan@company.example", "input untouched"


def test_an_email_inside_raw_commit_text_is_replaced() -> None:
    """GitHub's verification.payload is the raw commit, author and committer lines included."""
    payload = (
        "tree abc\nauthor Keshan <keshan@company.example> 1792000000 +0530\n"
        "committer GitHub <noreply@github.com> 1792000000 +0530\n\nMerge"
    )
    clean = sanitize({"verification": {"payload": payload}, "ssh_url": "git@github.com:o/r.git"})

    text = clean["verification"]["payload"]
    assert "keshan@company.example" not in text
    assert f"author Keshan <{REDACTED_EMAIL}>" in text
    assert "<noreply@github.com>" in text, "GitHub's service address is not a person's"
    assert clean["ssh_url"] == "git@github.com:o/r.git"


def test_a_token_field_is_replaced() -> None:
    """GitHub returns temp_clone_token for a private repository; it is a credential."""
    clean = sanitize({"full_name": "o/r", "temp_clone_token": "CSERVM4A", "other_token": None})

    assert clean["temp_clone_token"] == REDACTED
    assert clean["other_token"] is None
    assert clean["full_name"] == "o/r"


def test_a3_report_is_counts_only() -> None:
    issues = [
        {"fields": {"assignee": {"emailAddress": "a@x.example"}, "reporter": {"accountId": "1"}}},
        {"fields": {"assignee": None, "reporter": {"emailAddress": ""}}},
    ]
    commits = [
        {"commit": {"author": {"email": "123+octo@users.noreply.github.com"}}},
        {"commit": {"author": {"email": "dev@company.example"}}},
        {"commit": {"author": {}}},
    ]

    report = a3_counts(issues, commits)

    assert report == {
        "jira_user_objects": 3,
        "jira_user_objects_with_email": 1,
        "github_commit_author_emails": 2,
        "github_commit_author_noreply": 1,
    }
    assert "@" not in json.dumps(report)
