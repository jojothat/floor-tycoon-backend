"""The calls exactly as the game makes them (frontend/FRONTEND.md and the "backend link"
block in frontend/src/game_tpl.html)."""

import uuid

from sqlalchemy import func, select

from app.models import Event, Floor, GameSession
from tests.conftest import PILOT_TOKEN

ANON = str(uuid.UUID(int=1))


def start_body(client_session_id=None, anon=ANON, role="CE"):
    return {"client_session_id": client_session_id or str(uuid.uuid4()), "anon_player_id": anon,
            "pilot_token": PILOT_TOKEN, "game_version": "dev", "flow": "quick", "role_type": role,
            "language": "en-IN", "device_class": "phone", "viewport_h": 390, "viewport_w": 844}


def game_headers(key=None, anon=ANON):
    return {"X-FT-Player": anon, **({"Authorization": f"Bearer {key}"} if key else {})}


def test_start_as_the_game_sends_it(client, db):
    r = client.post("/v1/sessions", json=start_body(), headers=game_headers())
    assert r.status_code == 201
    body = r.json()
    assert len(body["deal"]) == 7 and body["write_key"]
    gs = db.get(GameSession, body["session_id"])
    assert gs.viewport_height_bucket == "<400" and gs.anon_player_id == ANON


def test_a_retried_start_returns_the_same_session(client, db):
    b = start_body()
    first = client.post("/v1/sessions", json=b, headers=game_headers()).json()
    again = client.post("/v1/sessions", json=b, headers=game_headers())
    assert again.status_code == 201
    again = again.json()
    assert (again["session_id"], again["deal"]) == (first["session_id"], first["deal"])
    assert db.scalar(select(func.count()).select_from(GameSession)) == 1
    # Only the newest key works: the phone that retried never got the first one.
    url = f"/v1/sessions/{again['session_id']}/events"
    ev = {"events": [{"seq": 1, "ev": "answer", "t": 0, "m": "z1"}]}
    assert client.post(url, json=ev, headers=game_headers(first["write_key"])).status_code == 401
    assert client.post(url, json=ev, headers=game_headers(again["write_key"])).status_code == 200


def test_someone_else_cannot_take_over_a_start(client):
    b = start_body()
    client.post("/v1/sessions", json=b, headers=game_headers())
    other = {**b, "anon_player_id": str(uuid.UUID(int=2))}
    assert client.post("/v1/sessions", json=other, headers=game_headers(anon=other["anon_player_id"])).status_code == 409


def test_the_same_client_id_in_another_pilot_is_another_session(client, db):
    import hashlib
    from app.models import Pilot
    db.add(Pilot(pilot_id="pilot-2", site="b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    b = start_body()
    one = client.post("/v1/sessions", json=b).json()["session_id"]
    two = client.post("/v1/sessions", json={**b, "pilot_token": "other"}).json()["session_id"]
    assert one != two


def test_events_with_fractional_times(client, db):
    s = client.post("/v1/sessions", json=start_body()).json()
    r = client.post(f"/v1/sessions/{s['session_id']}/events", headers=game_headers(s["write_key"]),
                    json={"events": [{"seq": 1, "ev": "deal", "t": 0, "ids": s["deal"], "src": "server"},
                                     {"seq": 2, "ev": "role_set", "t": 1234.56, "type": "CE"}]})
    assert r.status_code == 200
    assert db.scalars(select(Event.t).order_by(Event.seq)).all() == [0, 1235]
    assert not any(db.scalars(select(Event.unknown_ev))), "the game's new events are known"


def test_finish_and_floor_as_the_game_sends_them(client, db):
    s = client.post("/v1/sessions", json=start_body()).json()
    url, h = f"/v1/sessions/{s['session_id']}", game_headers(s["write_key"])
    picks = {"z1": "a", "z2": "b", "z3": "a", "z4": "0", "z5": "c", "z6": "a", "z7": "b"}
    assert client.post(f"{url}/finish", headers=h, json={"picks": picks, "skips": {"z4": "none"}}).status_code == 200
    edited = {**picks, "z5": "a"}
    r = client.post(f"{url}/floor", headers=h, json={
        "palette": 3, "palette_name": "Japandi", "picks": edited, "skips": {"z4": "none"},
        "addition": {"adds": {"z1": "focuspod"}, "deskfit": None, "swaps": {}},
        "sim": {"use": 14, "happy": 62, "desk_use": 36, "desks": 12, "peak": 14, "peak_at": "09:15",
                "busy": [{"t": "07:30", "name": "Satellite hub", "pct": 23}], "occ": [3, 7], "occ_fab": [0, 1],
                "fab_people": 5}})
    assert r.status_code == 200, r.text
    floor = db.get(Floor, s["session_id"])
    assert (floor.palette_id, floor.palette_name) == ("3", "Japandi")
    assert floor.final_picks == edited and floor.skips == ["z4"], "the floor's picks are the latest"
    assert floor.wall_addition == {"adds": {"z1": "focuspod"}, "deskfit": None, "swaps": {}}
    assert {k: floor.sim[k] for k in ("deskU", "peakAt", "occf", "fabN")} == {
        "deskU": 36, "peakAt": "09:15", "occf": [0, 1], "fabN": 5}, "stored under events.md's names"


def test_a_missed_occupancy_sample_is_accepted(client, db):
    """Seen in the e2e run: the game's 15-minute samples can have a hole, sent as null."""
    s = client.post("/v1/sessions", json=start_body()).json()
    url, h = f"/v1/sessions/{s['session_id']}", game_headers(s["write_key"])
    client.post(f"{url}/finish", headers=h, json={"picks": {"z1": "a"}})
    r = client.post(f"{url}/floor", headers=h, json={"palette": 0, "sim": {"occ": [5, 7, None, 11], "occ_fab": [0, 0, None, 0]}})
    assert r.status_code == 200, r.text
    assert db.get(Floor, s["session_id"]).sim["occ"] == [5, 7, None, 11]


def test_the_floor_refuses_free_text_in_the_addition(client):
    s = client.post("/v1/sessions", json=start_body()).json()
    url, h = f"/v1/sessions/{s['session_id']}", game_headers(s["write_key"])
    client.post(f"{url}/finish", headers=h, json={"picks": {"z1": "a"}})
    for bad in ({"adds": {"z1": "Call me on 010-1234"}}, {"adds": {"z9": "desk"}}, {"deskfit": "smaller"},
                {"deskfit": {"mode": "huge", "add": "bench", "k": "a"}}, {"deskfit": {"mode": "share", "add": "Hi there", "k": "a"}},
                {"swaps": {"z1": "Not an item!"}}, {"swaps": {"z8": "bench"}}):
        assert client.post(f"{url}/floor", headers=h, json={"sim": {}, "addition": bad}).status_code == 422, bad


def test_a_whole_play_the_game_way(client):
    """Start, events, 18:00, floor, share, wall: the e2e test's calls, without the browser."""
    s = client.post("/v1/sessions", json=start_body(role="PSE")).json()
    url, h = f"/v1/sessions/{s['session_id']}", game_headers(s["write_key"])
    events = [{"seq": 1, "ev": "deal", "t": 0, "ids": s["deal"], "src": "server"}]
    picks = {}
    for i, zone in enumerate(s["zones"]):
        t = 1000 * (i + 1) * 10
        events += [{"seq": len(events) + 1, "ev": "answer", "m": zone, "t": t},
                   {"seq": len(events) + 2, "ev": "preview", "m": zone, "opt": "a", "t": t + 3000},
                   {"seq": len(events) + 3, "ev": "choose", "m": zone, "opt": "a", "t": t + 5000},
                   {"seq": len(events) + 4, "ev": "keep", "m": zone, "opt": "a", "rethinks": 0, "t": t + 7600}]
        picks[zone] = "a"
    for i in range(0, len(events), 50):
        assert client.post(f"{url}/events", headers=h, json={"events": events[i:i + 50]}).status_code == 200
    assert client.post(f"{url}/finish", headers=h, json={"picks": picks, "skips": {}}).status_code == 200
    assert client.post(f"{url}/floor", headers=h, json={"palette": 2, "palette_name": "Scandi Light", "picks": picks,
                                                        "skips": {}, "addition": {"adds": {}, "deskfit": None, "swaps": {}},
                                                        "sim": {"use": 20, "happy": 55}}).status_code == 200
    assert client.post("/v1/wall", headers=h, json={"session_id": s["session_id"]}).status_code == 200
    got = client.get("/v1/wall", headers=game_headers()).json()
    [post] = got["posts"]
    assert post["mine"] and post["picks"] == picks and post["pal"] == 2
    assert ANON not in str(got)
