import pytest

from app.config import Settings
from tests.conftest import PILOT_TOKEN

GAME = "https://game.example"


def preflight(client, path, origin=GAME, method="POST", headers="content-type,authorization"):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": method,
                                         "Access-Control-Request-Headers": headers})


@pytest.mark.parametrize("path,method,headers", [
    ("/v1/sessions", "POST", "content-type"),
    ("/v1/sessions/abc/events", "POST", "content-type,authorization"),
    ("/v1/wall", "GET", "x-pilot-token,x-anon-player"),
    ("/v1/read/summary", "GET", "authorization"),
])
def test_preflight_from_the_game(client, path, method, headers):
    r = preflight(client, path, method=method, headers=headers)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == GAME
    assert "access-control-allow-credentials" not in r.headers


def test_trailing_slash_in_config_is_ignored(client):
    r = preflight(client, "/v1/wall", origin="https://dashboard.example", method="GET", headers="x-pilot-token")
    assert r.headers["access-control-allow-origin"] == "https://dashboard.example"


def test_other_origins_are_not_allowed(client):
    r = preflight(client, "/v1/sessions", origin="https://evil.example")
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers
    r = client.get("/v1/wall", headers={"Origin": "https://evil.example", "X-Pilot-Token": PILOT_TOKEN})
    assert "access-control-allow-origin" not in r.headers


def test_unlisted_methods_and_headers_are_refused(client):
    assert preflight(client, "/v1/sessions", method="DELETE").status_code == 400
    assert preflight(client, "/v1/sessions", headers="x-something-else").status_code == 400


def test_responses_carry_the_origin(client):
    r = client.get("/v1/wall", headers={"Origin": GAME, "X-Pilot-Token": PILOT_TOKEN})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == GAME


def test_rate_limited_responses_are_readable_by_the_game(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "1")
    h = {"Origin": GAME, "X-Pilot-Token": PILOT_TOKEN}
    client.get("/v1/wall", headers=h)
    r = client.get("/v1/wall", headers=h)
    assert r.status_code == 429
    assert r.headers["access-control-allow-origin"] == GAME
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()


def test_cors_origins_setting(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "")
    assert Settings().cors_origins == ()
    monkeypatch.setenv("CORS_ORIGINS", " https://a.kr/ ,https://b.kr")
    assert Settings().cors_origins == ("https://a.kr", "https://b.kr")
