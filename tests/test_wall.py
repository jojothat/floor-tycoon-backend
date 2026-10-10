"""The wall, as the game calls it (frontend/FRONTEND.md): the player is X-FT-Player, and the
pilot comes from that player's own plays when no invite token is sent."""

import hashlib
import json
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

SAMPLE = load_json(ROOT / "sample" / "play-log-sample.json")


def lib():
    return clean.library(str(get_settings().scenarios_path))


def finished_play(client, new_session, anon, seed=1, role="CE", kind="good"):
    return play_through_api(client, new_session, *script_play(lib(), kind, random.Random(seed), role), anon=anon, role=role)[0]


def share(client, s):
    return client.post("/v1/wall", headers=s["headers"], json={"session_id": s["session_id"]})


def wall(client, player=None, **headers):
    h = {**({"X-FT-Player": player} if player else {}), **headers}
    r = client.get("/v1/wall", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def view(client, post_id, player):
    return client.post(f"/v1/wall/{post_id}/view", headers={"X-FT-Player": player}, json={})


# ---------------------------------------------------------------- POST /v1/wall


def test_share_the_sample_play(client, new_session):
    from tests.test_replay import replay

    s, _ = replay(client, new_session, SAMPLE)
    client.post(f"/v1/sessions/{s['session_id']}/floor", headers=s["headers"],
                json={"palette": 1, "palette_name": SAMPLE["end"]["palette"], "sim": SIM,
                      "addition": {"adds": {}, "deskfit": None, "swaps": {}}})
    r = share(client, s)
    assert r.status_code == 200 and r.json()["ok"] is True
    [post] = wall(client, "anon-player-0001")["posts"]
    assert post["id"] == r.json()["id"]
    assert post["deal"] == SAMPLE["deal"]
    assert post["picks"] == SAMPLE["finalPicks"] and post["skips"] == {"z6": "none"}
    assert (post["adds"], post["deskfit"], post["swaps"]) == ({}, None, {})
    assert (post["pal"], post["use"], post["happy"]) == (1, 14, 33)
    assert (post["views"], post["mine"]) == (0, True)


def test_the_wall_draws_the_addition_the_game_sent(client, new_session):
    s = finished_play(client, new_session, "anon-adder-0001")
    client.post(f"/v1/sessions/{s['session_id']}/floor", headers=s["headers"], json={
        "palette": 3, "sim": SIM, "addition": {"adds": {"z7": "phonebooth", "z5": "training"},
                                               "deskfit": {"mode": "smaller", "add": "phonebooth", "k": "a"},
                                               "swaps": {"z5": "training"}}})
    share(client, s)
    [post] = wall(client, "anon-adder-0001")["posts"]
    assert post["adds"] == {"z7": "phonebooth", "z5": "training"} and post["pal"] == 3
    assert (post["deskfit"], post["swaps"]) == ({"mode": "smaller", "add": "phonebooth", "k": "a"}, {"z5": "training"})


def test_without_one_the_addition_comes_from_the_events(client, new_session):
    s = finished_play(client, new_session, "anon-swapper-01", seed=2)
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [
        {"seq": 200, "ev": "add", "t": 900_000, "m": "z5", "item": "training", "via": "end", "swap": "b"}]})
    share(client, s)
    [post] = wall(client, "anon-swapper-01")["posts"]
    assert (post["adds"], post["deskfit"], post["swaps"]) == ({"z5": "training"}, None, {"z5": "training"})


def test_desks_made_smaller_from_the_events(client, new_session):
    s = finished_play(client, new_session, "anon-smaller-01", seed=5)
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [
        {"seq": 200, "ev": "add", "t": 900_000, "m": "z7", "item": "phonebooth", "via": "end", "desks": "smaller"}]})
    share(client, s)
    [post] = wall(client, "anon-smaller-01")["posts"]
    assert post["deskfit"]["mode"] == "smaller" and post["deskfit"]["add"] == "phonebooth"
    assert post["deskfit"]["k"] == post["picks"]["z7"]


def test_share_needs_the_simulated_day(client, new_session):
    s = new_session()
    client.post(f"/v1/sessions/{s['session_id']}/finish", headers=s["headers"], json={"picks": {"z1": "a"}})
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
    post_id = wall(client, "anon-twice-0001")["posts"][0]["id"]
    finished_play(client, new_session, "anon-viewer-001", seed=3)
    view(client, post_id, "anon-viewer-001")
    assert wall(client, "anon-twice-0001")["posts"][0]["views"] == 1

    second = finished_play(client, new_session, "anon-twice-0001", seed=2)
    share(client, second)
    posts = wall(client, "anon-twice-0001")["posts"]
    assert len(posts) == 1 and posts[0]["views"] == 0, "a new office starts with no views"
    assert db.scalar(select(WallPost.session_id)) == second["session_id"]


# ---------------------------------------------------------------- GET /v1/wall


def test_wall_cards_carry_no_identity_timestamps_or_amenities(client, new_session):
    share(client, finished_play(client, new_session, "anon-cards-0001"))
    out = wall(client, "anon-cards-0001")
    [post] = out["posts"]
    assert set(post) == {"id", "mine", "deal", "picks", "skips", "adds", "deskfit", "swaps",
                         "pal", "use", "happy", "views"}
    assert "anon-cards-0001" not in json.dumps(out), "anon ids never leave the server"


def test_wall_mine_flag(client, new_session):
    share(client, finished_play(client, new_session, "anon-mine-00001", seed=1))
    share(client, finished_play(client, new_session, "anon-other-0001", seed=2))
    assert sorted(p["mine"] for p in wall(client, "anon-mine-00001")["posts"]) == [False, True]
    assert not any(p["mine"] for p in wall(client, **{"X-Pilot-Token": PILOT_TOKEN})["posts"]), "no player, nothing is mine"


def test_the_pilot_comes_from_the_players_own_plays(client, new_session, db):
    share(client, finished_play(client, new_session, "anon-pilot1-001"))
    db.add(Pilot(pilot_id="pilot-2", site="site-b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    # A player of pilot-2 sees pilot-2's wall, which is empty; pilot-1's player sees theirs.
    s2 = new_session(pilot_token="other", anon_player_id="anon-pilot2-001")
    assert s2["session_id"]
    assert wall(client, "anon-pilot2-001")["posts"] == []
    assert len(wall(client, "anon-pilot1-001")["posts"]) == 1
    # The invite token, when sent, says which wall.
    assert wall(client, "anon-pilot1-001", **{"X-Pilot-Token": "other"})["posts"] == []


def test_a_player_with_no_play_and_no_token_sees_an_empty_wall(client, new_session):
    share(client, finished_play(client, new_session, "anon-shared-001"))
    assert wall(client, "anon-stranger-1") == {"posts": [], "games": {"total": None}}
    assert wall(client) == {"posts": [], "games": {"total": None}}


def test_wall_errors(client):
    assert client.get("/v1/wall", headers={"X-Pilot-Token": "nope"}).status_code == 401
    assert client.get("/v1/wall", headers={"X-FT-Player": "short"}).status_code == 400


def test_wall_from_a_pilot_that_ended(client, new_session, db):
    from datetime import date, timedelta
    share(client, finished_play(client, new_session, "anon-ended-0001"))
    db.get(Pilot, "pilot-1").end_date = date.today() - timedelta(days=1)
    db.commit()
    assert client.get("/v1/wall", headers={"X-FT-Player": "anon-ended-0001"}).status_code == 403


def test_wall_games_with_other(client, new_session):
    for i in range(12):
        finished_play(client, new_session, f"anon-ce-{i:07d}", seed=i, role="CE")
    for i in range(3):
        finished_play(client, new_session, f"anon-pse-{i:06d}", seed=100 + i, role="PSE")
    # PSE (3) is under 10, and "other" alone would show it, so CE joins "other" too.
    assert wall(client, "anon-ce-0000000")["games"] == {"other": 15, "total": 15}


def test_wall_games_hidden_until_ten(client, new_session):
    for i in range(9):
        finished_play(client, new_session, f"anon-few-{i:06d}", seed=i)
    assert wall(client, "anon-few-000000")["games"] == {"total": None}


# ---------------------------------------------------------------- POST /v1/wall/{id}/view


def test_views_are_unique_and_skip_your_own(client, new_session):
    share(client, finished_play(client, new_session, "anon-owner-0001"))
    for i in (1, 2):
        finished_play(client, new_session, f"anon-viewer-00{i}", seed=10 + i)
    post_id = wall(client, "anon-owner-0001")["posts"][0]["id"]
    for anon in ("anon-owner-0001", "anon-viewer-001", "anon-viewer-001", "anon-viewer-002"):
        assert view(client, post_id, anon).json() == {"ok": True}
    assert wall(client, "anon-owner-0001")["posts"][0]["views"] == 2


def test_view_errors(client, new_session, db):
    share(client, finished_play(client, new_session, "anon-owner-0002"))
    post_id = wall(client, "anon-owner-0002")["posts"][0]["id"]
    assert client.post(f"/v1/wall/{post_id}/view", json={}).status_code == 400, "no player"
    finished_play(client, new_session, "anon-viewer-001", seed=4)
    assert view(client, 999999, "anon-viewer-001").status_code == 404
    assert view(client, post_id, "anon-stranger-1").status_code == 404, "no play, no pilot"
    db.add(Pilot(pilot_id="pilot-2", site="site-b", invite_token_hash=hashlib.sha256(b"other").hexdigest()))
    db.commit()
    new_session(pilot_token="other", anon_player_id="anon-pilot2-001")
    assert view(client, post_id, "anon-pilot2-001").status_code == 404, "another pilot's office"


# ---------------------------------------------------------------- the group-of-10 rule


@pytest.mark.parametrize("counts,expected", [
    ({}, {"total": None}),
    ({"CE": 9}, {"total": None}),
    ({"CE": 10}, {"CE": 10, "total": 10}),
    ({"CE": 12, "PSE": 10, "none": 11}, {"CE": 12, "PSE": 10, "na": 11, "total": 33}),
    ({"CE": 20, "PSE": 4, "IE": 7}, {"CE": 20, "other": 11, "total": 31}),
    ({"CE": 20, "PSE": 12, "IE": 3}, {"CE": 20, "other": 15, "total": 35}),
    ({"CE": 5, "PSE": 4, "IE": 3}, {"other": 12, "total": 12}),
])
def test_games_rule(counts, expected):
    out = games_by_type(counts, 10)
    assert out == expected
    assert all(v >= 10 for k, v in out.items() if k != "total" and v is not None), "no shown group is under 10"
