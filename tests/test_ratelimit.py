import hashlib

import pytest
from sqlalchemy import select

from app import ratelimit
from app.models import Pilot, RateCounter
from tests.conftest import PILOT_TOKEN

T0 = 1_000_000 * 60 + 10  # 10 s into a window


@pytest.fixture
def clock(monkeypatch):
    t = {"now": T0}
    monkeypatch.setattr(ratelimit, "now", lambda: t["now"])
    return t


def wall(client, **headers):
    return client.get("/v1/wall", headers={"X-Pilot-Token": PILOT_TOKEN, **headers})


def test_ip_limit(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "3")
    assert [wall(client).status_code for _ in range(3)] == [200, 200, 200]
    r = wall(client)
    assert r.status_code == 429 and r.headers["Retry-After"] == "50"
    # Refused requests don't reach the endpoint, whatever it is.
    assert client.post("/v1/sessions", json={}).status_code == 429
    clock["now"] += 60
    assert wall(client).status_code == 200, "a new window"


def test_ip_limit_only_on_the_api(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "1")
    wall(client)
    assert wall(client).status_code == 429
    assert client.get("/docs").status_code == 200


def test_forwarded_for_only_behind_a_trusted_proxy(client, clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "1")
    assert wall(client, **{"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert wall(client, **{"X-Forwarded-For": "10.0.0.2"}).status_code == 429, "header ignored: same client"
    monkeypatch.setenv("TRUST_PROXY", "true")
    assert wall(client, **{"X-Forwarded-For": "10.0.0.3, 172.16.0.1"}).status_code == 200
    assert wall(client, **{"X-Forwarded-For": "10.0.0.3"}).status_code == 429
    assert wall(client, **{"X-Forwarded-For": "10.0.0.4"}).status_code == 200


def test_pilot_limit(client, clock, monkeypatch, new_session, db):
    monkeypatch.setenv("RATE_LIMIT_PILOT_PER_MIN", "2")
    s = new_session()  # 1
    url = f"/v1/sessions/{s['session_id']}/events"
    ev = {"events": [{"seq": 1, "ev": "answer", "t": 0, "m": "z1"}]}
    assert client.post(url, headers=s["headers"], json=ev).status_code == 200  # 2
    r = client.post(url, headers=s["headers"], json=ev)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 50
    assert wall(client).status_code == 429, "the wall shares the pilot's limit"
    # Another pilot has its own.
    db.add(Pilot(pilot_id="pilot-2", site="site-b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    assert client.get("/v1/wall", headers={"X-Pilot-Token": "other"}).status_code == 200


def test_no_ip_address_is_stored(client, clock, monkeypatch, db):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "10")
    monkeypatch.setenv("TRUST_PROXY", "true")
    wall(client, **{"X-Forwarded-For": "203.0.113.7"})
    keys = db.scalars(select(RateCounter.key)).all()
    assert keys and not any("203.0.113.7" in k or "testclient" in k for k in keys)


def test_old_windows_are_pruned(client, clock, monkeypatch, db):
    monkeypatch.setenv("RATE_LIMIT_IP_PER_MIN", "10")
    wall(client)
    clock["now"] += 60
    wall(client)
    clock["now"] += 60
    ratelimit.prune(db)
    db.commit()
    windows = db.scalars(select(RateCounter.window)).all()
    assert windows == [int((T0 + 60) // 60)], "only the last finished window is kept"
