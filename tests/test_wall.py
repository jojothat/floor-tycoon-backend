import hashlib
import random

import pytest
from sqlalchemy import select

from app import clean
from app.config import get_settings
from app.models import Pilot, WallPost
from app.wall import games_by_type
from tests.conftest import PILOT_TOKEN, ROOT, load_json
from tests.scripted import script_play
from tests.test_clean import SIM, play_through_api

WALL_HEADERS = {"X-Pilot-Token": PILOT_TOKEN}
SAMPLE = load_json(ROOT / "sample" / "play-log-sample.json")


def lib():
    return clean.library(str(get_settings().scenarios_path))


def finished_play(client, new_session, anon, seed=1, role="CE", kind="good"):
    return play_through_api(client, new_session, *script_play(lib(), kind, random.Random(seed), role), anon=anon, role=role)[0]


def share(client, s):
    return client.post("/v1/wall", headers=s["headers"], json={"session_id": s["session_id"]})


def wall(client, anon=None):
    headers = {**WALL_HEADERS, **({"X-Anon-Player": anon} if anon else {})}
    r = client.get("/v1/wall", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- POST /v1/wall


def test_share_the_sample_play(client, new_session):
    from tests.test_replay import replay

    s, _ = replay(client, new_session, SAMPLE)
    client.post(f"/v1/sessions/{s['session_id']}/floor", headers=s["headers"],
                json={"palette": SAMPLE["end"]["palette"], "sim": SIM})
    assert share(client, s).json() == {"ok": True}
    [post] = wall(client, "anon-player-0001")["posts"]
    assert post["deal"] == SAMPLE["deal"]
    assert post["picks"] == SAMPLE["finalPicks"] and post["skips"] == ["z6"]
    assert (post["adds"], post["deskfit"], post["swaps"]) == ([], None, {}), "the edit removed the addition"
    assert (post["palette"], post["use"], post["happiness"]) == ("Scandi Light", 14, 33)
    assert (post["views"], post["mine"]) == (0, True)


def test_share_with_smaller_desks_and_swap(client, new_session):
    s = finished_play(client, new_session, "anon-adder-0001")
    url = f"/v1/sessions/{s['session_id']}/events"
    client.post(url, headers=s["headers"], json={"events": [
        {"seq": 200, "ev": "add", "t": 900_000, "m": "z7", "item": "phonebooth", "via": "end", "desks": "smaller"}]})
    share(client, s)
    [post] = wall(client)["posts"]
    assert (post["adds"], post["deskfit"], post["swaps"]) == (["phonebooth"], "smaller", {})

    s2 = finished_play(client, new_session, "anon-swapper-01", seed=2)
    client.post(f"/v1/sessions/{s2['session_id']}/events", headers=s2["headers"], json={"events": [
        {"seq": 200, "ev": "add", "t": 900_000, "m": "z5", "item": "training", "via": "end", "swap": "b"}]})
    share(client, s2)
    post = next(p for p in wall(client, "anon-swapper-01")["posts"] if p["mine"])
    assert (post["adds"], post["deskfit"], post["swaps"]) == (["training"], None, {"z5": "b"})


def test_share_needs_the_simulated_day(client, new_session):
    s = new_session()
    client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"final_picks": {"z1": "a"}})
    assert share(client, s).status_code == 409


def test_share_needs_the_write_key(client, new_session):
    s = finished_play(client, new_session, "anon-keyless-01")
    assert client.post("/v1/wall", json={"session_id": s["session_id"]}).status_code == 401
    other = new_session()
    r = client.post("/v1/wall", headers=other["headers"], json={"session_id": s["session_id"]})
    assert r.status_code == 401


def test_latest_post_replaces_the_last(client, new_session, db):
    first = finished_play(client, new_session, "anon-twice-0001", seed=1)
    share(client, first)
    post_id = wall(client)["posts"][0]["id"]
    client.post(f"/v1/wall/{post_id}/view", headers={**WALL_HEADERS, "X-Anon-Player": "anon-viewer-001"})
    assert wall(client)["posts"][0]["views"] == 1

    second = finished_play(client, new_session, "anon-twice-0001", seed=2)
    share(client, second)
    posts = wall(client)["posts"]
    assert len(posts) == 1 and posts[0]["views"] == 0, "a new office starts with no views"
    assert db.scalar(select(WallPost.session_id)) == second["session_id"]


# ---------------------------------------------------------------- GET /v1/wall


def test_wall_cards_carry_no_identity_timestamps_or_amenities(client, new_session):
    share(client, finished_play(client, new_session, "anon-cards-0001"))
    [post] = wall(client)["posts"]
    assert set(post) == {"id", "deal", "picks", "skips", "adds", "deskfit", "swaps", "palette",
                         "use", "happiness", "views", "mine"}


def test_wall_mine_flag(client, new_session):
    share(client, finished_play(client, new_session, "anon-mine-00001", seed=1))
    share(client, finished_play(client, new_session, "anon-other-0001", seed=2))
    assert sorted(p["mine"] for p in wall(client, "anon-mine-00001")["posts"]) == [False, True]
    assert not any(p["mine"] for p in wall(client)["posts"])


def test_wall_is_per_pilot(client, new_session, db):
    share(client, finished_play(client, new_session, "anon-pilot1-001"))
    db.add(Pilot(pilot_id="pilot-2", site="site-b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    r = client.get("/v1/wall", headers={"X-Pilot-Token": "other"})
    assert r.json()["posts"] == []


def test_wall_needs_the_invite(client):
    assert client.get("/v1/wall").status_code == 401
    assert client.get("/v1/wall", headers={"X-Pilot-Token": "nope"}).status_code == 401


def test_wall_games_by_type_with_other(client, new_session):
    for i in range(12):
        finished_play(client, new_session, f"anon-ce-{i:07d}", seed=i, role="CE")
    for i in range(3):
        finished_play(client, new_session, f"anon-pse-{i:06d}", seed=100 + i, role="PSE")
    # PSE (3) is under 10, and "Other" alone would show it, so CE joins "Other" too.
    assert wall(client)["games_by_type"] == {
        "total": 15, "enough_data": True, "types": [{"type": "Other", "count": 15, "pct": 100}]}


def test_wall_games_by_type_hidden_until_ten(client, new_session):
    for i in range(9):
        finished_play(client, new_session, f"anon-few-{i:06d}", seed=i)
    assert wall(client)["games_by_type"] == {"total": None, "enough_data": False, "types": []}


# ---------------------------------------------------------------- POST /v1/wall/{id}/view


def test_views_are_unique_and_skip_your_own(client, new_session):
    share(client, finished_play(client, new_session, "anon-owner-0001"))
    post_id = wall(client)["posts"][0]["id"]
    url = f"/v1/wall/{post_id}/view"
    for anon in ("anon-owner-0001", "anon-viewer-001", "anon-viewer-001", "anon-viewer-002"):
        assert client.post(url, headers={**WALL_HEADERS, "X-Anon-Player": anon}).json() == {"ok": True}
    assert wall(client)["posts"][0]["views"] == 2


def test_view_errors(client, new_session, db):
    share(client, finished_play(client, new_session, "anon-owner-0002"))
    post_id = wall(client)["posts"][0]["id"]
    assert client.post(f"/v1/wall/{post_id}/view", headers=WALL_HEADERS).status_code == 400
    h = {**WALL_HEADERS, "X-Anon-Player": "anon-viewer-001"}
    assert client.post("/v1/wall/999999/view", headers=h).status_code == 404
    db.add(Pilot(pilot_id="pilot-2", site="site-b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    r = client.post(f"/v1/wall/{post_id}/view", headers={"X-Pilot-Token": "other", "X-Anon-Player": "anon-viewer-001"})
    assert r.status_code == 404, "another pilot's office"


# ---------------------------------------------------------------- the group-of-10 rule


@pytest.mark.parametrize("counts,expected", [
    ({}, None),
    ({"CE": 9}, None),
    ({"CE": 10}, [("CE", 10, 100)]),
    ({"CE": 12, "PSE": 10, "none": 11}, [("CE", 12, 36), ("Prefer not to say", 11, 33), ("PSE", 10, 30)]),
    ({"CE": 20, "PSE": 4, "IE": 7}, [("CE", 20, 65), ("Other", 11, 35)]),
    ({"CE": 20, "PSE": 12, "IE": 3}, [("CE", 20, 57), ("Other", 15, 43)]),
    ({"CE": 5, "PSE": 4, "IE": 3}, [("Other", 12, 100)]),
])
def test_games_by_type_rule(counts, expected):
    out = games_by_type(counts, 10)
    if expected is None:
        assert out == {"total": None, "enough_data": False, "types": []}
        return
    assert [(r["type"], r["count"], r["pct"]) for r in out["types"]] == expected
    assert all(r["count"] >= 10 for r in out["types"]), "no shown group is under 10"
    assert out["total"] == sum(counts.values())
