"""Tests for the web shell hardening (WP6).

Verifies:
1. The post-login next parameter cannot send a user off this origin.
2. app/main.py performs no filesystem write at import time, and every font the
   stylesheet references exists.
3. A page route renders error.html; an API route still returns JSON.
4. Two different users get two different avatar tint classes.
5. GET / does not accept a team_id parameter.
"""

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
    monkeypatch.setattr(routes, "load_user", lambda db, user_id: USERS.get(user_id))

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


@pytest.mark.parametrize("allowed", ["/", "/teams/10", "/members/2?question=risks"])
def test_safe_next_keeps_a_same_site_path(allowed):
    assert safe_next(allowed) == allowed


def test_login_form_cannot_redirect_off_site(client: TestClient):
    response = client.post(
        "/login",
        data={"user_id": "1", "next": "https://evil.example/"},
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_page_does_not_echo_a_hostile_next(client: TestClient):
    response = client.get("/login?next=https://evil.example/")
    assert response.status_code == 200
    assert "evil.example" not in response.text


def test_error_handler_survives_a_non_decimal_member_id(client: TestClient):
    """str.isdigit() is True for characters int() rejects, such as the superscript 2.

    The 404 handler parses the member id out of the path, so a path like
    /members/<superscript 2>/x would have raised inside the exception handler.
    """
    client.post("/api/auth/login", json={"user_id": 1})
    response = client.get("/members/²/panel", headers={"accept": "text/html"})

    # FastAPI rejects the path parameter first, so this is a 422. What matters is
    # that the error page renders instead of the handler raising a 500.
    assert response.status_code == 422
    assert "text/html" in response.headers["content-type"]


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


def test_page_route_renders_the_html_error_page(client: TestClient):
    client.post("/api/auth/login", json={"user_id": 3})  # Carol is on team 20
    response = client.get("/teams/10", headers={"accept": "text/html"})

    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert "Error 403" in response.text
    # A team 403 must not borrow the member copy.
    assert "You cannot view this page" in response.text
    assert "You cannot view this member" not in response.text
    # The exception detail never reaches the page.
    assert "Not allowed to view overview" not in response.text


def test_member_403_keeps_the_member_copy(client: TestClient):
    client.post("/api/auth/login", json={"user_id": 3})  # Carol is on team 20
    response = client.get("/members/1", headers={"accept": "text/html"})

    assert response.status_code == 403
    assert "You cannot view this member" in response.text


def test_api_route_still_returns_json(client: TestClient):
    client.post("/api/auth/login", json={"user_id": 3})
    response = client.get("/api/teams/10/overview", headers={"accept": "text/html"})

    assert response.status_code == 403
    assert "application/json" in response.headers["content-type"]
    assert response.json()["detail"]


# 4. Avatar tints -------------------------------------------------------------


def test_different_users_get_different_avatar_tints():
    from app.web.ui_format import avatar_tint

    assert avatar_tint(USERS[1].user_id) != avatar_tint(USERS[2].user_id)
    assert avatar_tint(USERS[1].user_id) != "avatar--tint-0"


# 5. Route surface ------------------------------------------------------------


def test_home_route_declares_no_team_parameter():
    """GET / must not accept team_id.

    Stacking / and /teams/{team_id} on 1 handler with team_id defaulting to None
    made team_id a query parameter on / as well.
    """
    spec = app.openapi()["paths"]["/"]["get"]
    names = {p["name"] for p in spec.get("parameters", [])}
    assert "team_id" not in names
