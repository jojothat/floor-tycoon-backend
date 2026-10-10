from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models import Deal, Event, Floor, GameSession, Moment, Pilot
from tests.conftest import PILOT_TOKEN

# ---------------------------------------------------------------- POST /v1/sessions


def test_create_session_returns_deal_and_key(new_session, db):
    out = new_session("CE")
    assert len(out["deal"]) == 7 and sorted(out["zones"]) == [f"z{i}" for i in range(1, 8)]
    assert out["zones"][out["deal"].index(30)] == "z6"
    gs = db.get(GameSession, out["session_id"])
    assert gs.role_type == "CE" and gs.pilot_id == "pilot-1" and gs.viewport_height_bucket == "<400"
    assert gs.write_key_hash != out["write_key"], "only the hash is stored"
    assert db.get(Deal, out["session_id"]).scenario_ids == out["deal"]


def test_prefer_not_to_say(new_session, db):
    out = new_session(None)
    assert db.get(GameSession, out["session_id"]).role_type == "none"


def test_bad_pilot_token(client):
    r = client.post("/v1/sessions", json={"pilot_token": "nope", "anon_player_id": "anon-player-0001"})
    assert r.status_code == 401


def test_pilot_not_running(client, db):
    p = db.get(Pilot, "pilot-1")
    p.end_date = date.today() - timedelta(days=1)
    db.commit()
    r = client.post("/v1/sessions", json={"pilot_token": PILOT_TOKEN, "anon_player_id": "anon-player-0001"})
    assert r.status_code == 403


def test_identity_and_look_are_never_stored(new_session, engine):
    out = new_session("PSE", name="Kim", email="kim@example.com", look={"hair": 3}, employee_no="123")
    with engine.connect() as c:
        row = c.execute(text("SELECT row_to_json(s)::text FROM sessions s WHERE session_id = :i"),
                        {"i": out["session_id"]}).scalar_one()
    for leaked in ("Kim", "kim@example.com", "hair", "123"):
        assert leaked not in row


def test_bad_role_rejected(client):
    r = client.post("/v1/sessions", json={"pilot_token": PILOT_TOKEN, "anon_player_id": "anon-player-0001",
                                          "role_type": "CEO"})
    assert r.status_code == 422


def test_sso_mode(client, monkeypatch):
    monkeypatch.setenv("ACCESS_MODE", "sso")
    body = {"pilot_id": "pilot-1", "anon_player_id": "anon-player-0001"}
    assert client.post("/v1/sessions", json=body).status_code == 401
    assert client.post("/v1/sessions", json=body, headers={"X-Auth-User": "x"}).status_code == 201


# ---------------------------------------------------------------- POST /v1/sessions/{id}/events


def ev(seq, ev="answer", t=None, **kw):
    return {"seq": seq, "ev": ev, "t": seq * 100 if t is None else t, **kw}


def test_events_stored(client, new_session, db):
    s = new_session()
    r = client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                    json={"events": [ev(1, "deal", t=0, ids=s["deal"]), ev(2, m="z1"), ev(3, "preview", m="z1", opt="a")]})
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": {"from": 1, "to": 3}, "inserted": 3, "duplicates": 0}
    rows = db.scalars(select(Event).order_by(Event.seq)).all()
    assert [(e.seq, e.ev, e.m) for e in rows] == [(1, "deal", None), (2, "answer", "z1"), (3, "preview", "z1")]
    assert rows[0].payload == {"ids": s["deal"]} and rows[2].payload == {"opt": "a"}
    assert rows[1].server_time is not None


def test_duplicate_seq_retry_is_safe(client, new_session, db):
    s = new_session()
    url = f"/v1/sessions/{s['session_id']}/events"
    batch = {"events": [ev(1), ev(2), ev(3)]}
    assert client.post(url, headers=s["headers"], json=batch).json()["inserted"] == 3
    r = client.post(url, headers=s["headers"], json=batch)
    assert r.status_code == 200 and r.json() == {"accepted": {"from": 1, "to": 3}, "inserted": 0, "duplicates": 3}
    # A retry overlapping a new batch only adds the new ones.
    r = client.post(url, headers=s["headers"], json={"events": [ev(3), ev(4)]})
    assert r.json()["inserted"] == 1
    # The first copy wins; a retry with different content doesn't change it.
    client.post(url, headers=s["headers"], json={"events": [ev(1, "skip", m="z2")]})
    assert db.scalar(select(Event.ev).where(Event.seq == 1)) == "answer"
    assert db.scalar(select(text("count(*)")).select_from(Event)) == 4


def test_events_need_the_write_key(client, new_session):
    s, other = new_session(), new_session()
    url = f"/v1/sessions/{s['session_id']}/events"
    body = {"events": [ev(1)]}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(url, json=body, headers=other["headers"]).status_code == 401, "one key per session"
    assert client.post("/v1/sessions/nope/events", json=body, headers=s["headers"]).status_code == 401


def test_write_key_expires(client, new_session, db):
    s = new_session()
    gs = db.get(GameSession, s["session_id"])
    gs.write_key_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    r = client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [ev(1)]})
    assert r.status_code == 401


def test_unknown_event_stored_but_flagged(client, new_session, db):
    s = new_session()
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                json={"events": [ev(1, "somethingNew", x=1), ev(2)]})
    rows = db.scalars(select(Event).order_by(Event.seq)).all()
    assert [e.unknown_ev for e in rows] == [True, False]
    assert rows[0].payload == {"x": 1}


def test_free_text_flagged(client, new_session, db):
    s = new_session(flow="classic")
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                json={"events": [ev(1, "skip", m="z3", why="else", text="a bigger kitchen", rethinks=0)]})
    assert db.scalar(select(Event.has_text)) is True


def test_look_never_stored_in_events(client, new_session, db):
    s = new_session()
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                json={"events": [ev(1, "role_set", type="CE", look={"hair": 2}, extra={"look": 1, "ok": 2})]})
    assert db.scalar(select(Event.payload)) == {"type": "CE", "extra": {"ok": 2}}


def test_batch_cap_64kb(client, new_session):
    s = new_session()
    big = {"events": [ev(i, "preview", m="z1", opt="a", pad="x" * 700) for i in range(1, 101)]}
    r = client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json=big)
    assert r.status_code == 413


@pytest.mark.parametrize("bad", [
    {"events": []},
    {"events": [{"seq": 0, "ev": "answer", "t": 0}]},
    {"events": [{"seq": 1, "ev": "answer", "t": -1}]},
    {"events": [{"seq": 1, "ev": "answer", "t": 0, "m": "z9"}]},
    {"events": [{"ev": "answer", "t": 0}]},
])
def test_bad_events_rejected(client, new_session, bad):
    s = new_session()
    r = client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json=bad)
    assert r.status_code == 422


def test_events_are_append_only(client, new_session, engine):
    s = new_session()
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [ev(1)]})
    for sql in ("UPDATE events SET ev = 'x'", "DELETE FROM events"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as c:
                c.execute(text(sql))
    # Only the retention job may delete.
    with engine.begin() as c:
        c.execute(text("SET LOCAL app.retention = 'on'"))
        assert c.execute(text("DELETE FROM events")).rowcount == 1


# ---------------------------------------------------------------- POST /v1/sessions/{id}/finish

PICKS = {"z1": "a", "z2": "b", "z3": "0", "z4": "c", "z5": "d", "z6": "a", "z7": "b"}


def test_finish(client, new_session, db):
    s = new_session()
    r = client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"],
                    json={"final_picks": PICKS, "skips": 1})
    assert r.status_code == 200 and r.json() == {"ok": True, "first_finish": True}
    floor = db.get(Floor, s["session_id"])
    assert floor.final_picks == PICKS and floor.skips == ["z3"]
    first = db.get(GameSession, s["session_id"]).finished_at
    assert first is not None

    r = client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"final_picks": PICKS})
    assert r.json()["first_finish"] is False
    db.expire_all()
    assert db.get(GameSession, s["session_id"]).finished_at == first, "first arrival at 18:00 counts"


@pytest.mark.parametrize("picks", [{"z1": "e"}, {"z8": "a"}])
def test_finish_bad_picks(client, new_session, picks):
    s = new_session()
    r = client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"final_picks": picks})
    assert r.status_code == 422


def test_finish_needs_the_write_key(client, new_session):
    s = new_session()
    assert client.post(f"/v1/sessions/{s['session_id']}/finish", json={"final_picks": PICKS}).status_code == 401


def test_unfinished_moment_has_no_choice(client, new_session, db):
    s = new_session()
    url = f"/v1/sessions/{s['session_id']}/events"
    client.post(url, headers=s["headers"], json={"events": [ev(1, m="z1"), ev(2, "preview", m="z1", opt="b")]})
    client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"final_picks": {}})
    m = db.scalar(select(Moment))
    assert m.chosen is None and m.previewed == ["b"]


def test_classic_rethink(client, new_session, db):
    s = new_session(flow="classic")
    events = [
        ev(1, "answer", t=1000, m="z2"),
        ev(2, "preview", t=2000, m="z2", opt="a"),
        ev(3, "choose", t=3000, m="z2", opt="a"),
        ev(4, "rethink", t=5000, m="z2", **{"from": "a"}),
        ev(5, "preview", t=6000, m="z2", opt="c"),
        ev(6, "choose", t=7000, m="z2", opt="c"),
        ev(7, "keep", t=9000, m="z2", opt="c", rethinks=1),
    ]
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": events})
    client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"final_picks": {"z2": "c"}})
    m = db.scalar(select(Moment))
    assert (m.previewed, m.first_preview, m.chosen, m.rethinks, m.time_to_decide_ms) == (["a", "c"], "a", "c", 1, 6000)
