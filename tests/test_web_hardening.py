"""Tests for the web shell hardening (WP6).

Verifies:
1. The post-login next parameter cannot send a user off this origin.
2. app/main.py performs no filesystem write at import time, and every font the
   stylesheet references exists.
3. A page route renders error.html; an API route still returns JSON.
4. Two different users get two different avatar tint classes.
5. GET / takes no parameters and redirects to /workspaces.
"""

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.web import auth, routes
from app.web.routes import safe_next

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "web" / "static"


@dataclass
class FakeUser:
    id: int
    team_id: int | None
    display_name: str = "Test"
    is_active: bool = True

    @property
    def user_id(self) -> int:
        return self.id


USERS = {
    1: FakeUser(id=1, team_id=10, display_name="Alice"),
    2: FakeUser(id=2, team_id=10, display_name="Bob"),
    3: FakeUser(id=3, team_id=20, display_name="Carol"),
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    app.dependency_overrides[auth.get_db] = fake_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# 1. Open redirect ------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/",
        "http://evil.example/",
        "//evil.example/",
        r"/\evil.example/",
        r"\\evil.example",
        "javascript:alert(1)",
        "/\t//evil.example",
        "/\n//evil.example",
        "/\r//evil.example",
        "evil.example",
        "",
        None,
    ],
)
def test_safe_next_rejects_anything_that_can_leave_this_origin(hostile):
    assert safe_next(hostile) == "/"


@pytest.mark.parametrize("allowed", ["/", "/w/10", "/w/2?member=5"])
def test_safe_next_keeps_a_same_site_path(allowed):
    assert safe_next(allowed) == allowed


def test_github_sign_in_cannot_carry_an_off_site_next(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """next now enters only through the GitHub OAuth start; it must be cleaned there."""
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_oauth_client_id="test-client-id",
        github_oauth_client_secret="test-client-secret",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)

    response = client.get("/auth/github/login?next=https://evil.example/", follow_redirects=False)

    assert response.status_code == 303
    assert response.cookies[routes.OAUTH_NEXT_COOKIE].strip('"') == "/"


def test_login_page_does_not_echo_a_hostile_next(client: TestClient):
    response = client.get("/login?next=https://evil.example/")
    assert response.status_code == 200
    assert "evil.example" not in response.text


# 2. Static assets ------------------------------------------------------------


def test_main_module_writes_nothing_at_import_time():
    """Importing the app must not touch the filesystem.

    app/main.py used to copy fonts out of docs/ with shutil.copytree at import.
    A docs directory is not a runtime asset source and may not exist in an image.
    """
    tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
    names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("copytree", "copy", "copyfile", "mkdir", "write_text", "write_bytes"):
        assert forbidden not in names, f"app/main.py calls {forbidden} at module scope"


def test_every_font_the_stylesheet_references_exists():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    sources = re.findall(r'url\("([^"]+)"\)', css)
    assert sources, "no @font-face sources found in style.css"
    for src in sources:
        assert (STATIC / src).is_file(), f"{src} is referenced but not present"


# 3. Error pages --------------------------------------------------------------


def test_page_route_renders_the_html_error_page(client: TestClient, monkeypatch):
    """A role too low for the route: error.html with fixed copy, never the detail."""
    from fastapi import HTTPException

    from app.web.workspaces import current_account

    def refuse() -> None:
        raise HTTPException(status_code=403, detail="internal detail")

    app.dependency_overrides[current_account] = refuse
    try:
        response = client.get("/workspaces", headers={"accept": "text/html"})
    finally:
        app.dependency_overrides.pop(current_account)

    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert "Error 403" in response.text
    assert "Your role cannot do this" in response.text
    assert "internal detail" not in response.text


def test_api_route_still_returns_json(client: TestClient):
    response = client.get("/api/workspaces", headers={"accept": "text/html"})

    assert response.status_code == 401
    assert "application/json" in response.headers["content-type"]
    assert response.json()["detail"]


# 4. Avatar tints -------------------------------------------------------------


def test_different_users_get_different_avatar_tints():
    from app.web.ui_format import avatar_tint

    assert avatar_tint(USERS[1].user_id) != avatar_tint(USERS[2].user_id)
    assert avatar_tint(USERS[1].user_id) != "avatar--tint-0"


# 5. Route surface ------------------------------------------------------------


def test_home_redirects_to_the_workspaces():
    """GET / takes no parameters and sends everyone to the portfolio (DEC-026)."""
    spec = app.openapi()["paths"]["/"]["get"]
    assert not spec.get("parameters")
    with TestClient(app) as test_client:
        response = test_client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/workspaces"
