"""GitHub App JWT, installation tokens and the attach rule (W3, DEC-022). No live calls."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import Response

from app.integrations.github_app import (
    GitHubAppClient,
    Installation,
    make_app_jwt,
    user_can_attach,
)

API = "https://api.github.com"


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, rsa.RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _installation(account_type: str = "User", account_id: int = 101) -> Installation:
    return Installation(
        installation_id=55,
        account_id=account_id,
        account_login="acme" if account_type == "Organization" else "lead-a",
        account_type=account_type,
        repository_selection="selected",
        suspended=False,
    )


def test_the_app_jwt_is_rs256_backdated_and_under_10_minutes(key_pair):
    pem, public_key = key_pair

    token = make_app_jwt("123", pem, now=1_000_000)
    claims = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_exp": False})

    assert claims["iss"] == "123"
    assert claims["iat"] == 1_000_000 - 60
    assert claims["exp"] - claims["iat"] == 540


@respx.mock
def test_an_installation_token_is_reused_until_near_expiry(key_pair):
    pem, _ = key_pair
    clock = Clock(datetime(2026, 10, 19, 12, 0, tzinfo=UTC).timestamp())
    expires = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
    route = respx.post(f"{API}/app/installations/55/access_tokens").mock(
        return_value=Response(201, json={"token": "ghs_first", "expires_at": expires.isoformat()})
    )
    client = GitHubAppClient(app_id="123", private_key_pem=pem, clock=clock)

    assert client.installation_token(55) == "ghs_first"
    clock.now += 30 * 60
    assert client.installation_token(55) == "ghs_first"
    assert route.call_count == 1

    clock.now = (expires - timedelta(minutes=4)).timestamp()
    client.installation_token(55)
    assert route.call_count == 2


@respx.mock
def test_get_installation_reads_the_owner(key_pair):
    pem, _ = key_pair
    respx.get(f"{API}/app/installations/55").mock(
        return_value=Response(
            200,
            json={
                "id": 55,
                "account": {"id": 101, "login": "lead-a", "type": "User"},
                "repository_selection": "selected",
                "suspended_at": None,
            },
        )
    )

    installation = GitHubAppClient(app_id="123", private_key_pem=pem).get_installation(55)

    assert installation == _installation()


@respx.mock
def test_installation_repositories_follow_pages(key_pair):
    pem, _ = key_pair
    respx.post(f"{API}/app/installations/55/access_tokens").mock(
        return_value=Response(201, json={"token": "t", "expires_at": "2099-01-01T00:00:00Z"})
    )
    page_1 = [{"id": i, "full_name": f"o/r{i}", "private": True} for i in range(100)]
    page_2 = [{"id": 100, "full_name": "o/r100", "private": False}]
    respx.get(f"{API}/installation/repositories", params={"page": "1"}).mock(
        return_value=Response(200, json={"total_count": 101, "repositories": page_1})
    )
    respx.get(f"{API}/installation/repositories", params={"page": "2"}).mock(
        return_value=Response(200, json={"total_count": 101, "repositories": page_2})
    )

    repos = GitHubAppClient(app_id="123", private_key_pem=pem).list_installation_repositories(55)

    assert len(repos) == 101
    assert repos[-1] == {"id": 100, "full_name": "o/r100", "private": False}


def test_a_user_installation_attaches_only_for_its_owner():
    assert user_can_attach(_installation(account_id=101), "token", github_user_id=101)
    assert not user_can_attach(_installation(account_id=101), "token", github_user_id=202)


@respx.mock
@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (200, {"state": "active", "role": "admin"}, True),
        (200, {"state": "active", "role": "member"}, False),
        (200, {"state": "pending", "role": "admin"}, False),
        (404, {"message": "Not Found"}, False),
    ],
)
def test_an_org_installation_attaches_only_for_an_active_admin(status_code, body, expected):
    respx.get(f"{API}/user").mock(return_value=Response(200, json={"id": 101}))
    respx.get(f"{API}/user/memberships/orgs/acme").mock(
        return_value=Response(status_code, json=body)
    )

    assert user_can_attach(_installation("Organization"), "user-token", 101) is expected


@respx.mock
def test_an_org_admin_token_of_another_person_is_refused():
    """A token that is not the signed-in account's own cannot vouch for an org."""
    respx.get(f"{API}/user").mock(return_value=Response(200, json={"id": 999}))
    membership = respx.get(f"{API}/user/memberships/orgs/acme").mock(
        return_value=Response(200, json={"state": "active", "role": "admin"})
    )

    assert not user_can_attach(_installation("Organization"), "someone-elses-token", 101)
    assert membership.call_count == 0


def test_an_unknown_account_type_never_attaches():
    assert not user_can_attach(_installation("Enterprise"), "token", github_user_id=101)
