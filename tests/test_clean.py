"""Build step 2 is done when 50 scripted plays (good, speeding, position-biased,
skip-everything) score as expected (spec section 10)."""

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import clean
from app.config import get_settings
from app.models import Floor, GameSession, Quality
from tests.conftest import ROOT, load_json
from tests.scripted import script_play

SAMPLE = load_json(ROOT / "sample" / "play-log-sample.json")
SIM = {**SAMPLE["stats"], "use": SAMPLE["end"]["use"], "happy": SAMPLE["end"]["happy"]}

# kind -> (how many, score, flags, weight)
EXPECTED = {
    "good": (20, 100, [], 1.0),
    "speeding": (8, 60, ["speeding_play", "speeding_moments"], 0.5),
    "speeding_blind": (7, 30, ["speeding_play", "speeding_moments", "no_looking"], 0.0),
    "position_biased": (8, 75, ["position_bias"], 1.0),
    "skip_everything": (7, 70, ["skip_everything"], 1.0),
}


def play_through_api(client, new_session, events, picks, anon, role="CE", **start):
    s = new_session(role, anon_player_id=anon, **start)
    url = f"/v1/sessions/{s['session_id']}"
    for i in range(0, len(events), 10):
        assert client.post(f"{url}/events", headers=s["headers"], json={"events": events[i:i + 10]}).status_code == 200
    assert client.post(f"{url}/finish", headers=s["headers"], json={"final_picks": picks}).status_code == 200
    r = client.post(f"{url}/floor", headers=s["headers"], json={"palette": "Scandi Light", "sim": SIM})
    assert r.status_code == 200, r.text
    return s, r.json()["quality"]


def test_fifty_scripted_plays_score_as_expected(client, new_session, db):
    lib = clean.library(str(get_settings().scenarios_path))
    rng = random.Random(2026)
    plays = []
    n = 0
    for kind, (count, score, flags, weight) in EXPECTED.items():
        for _ in range(count):
            n += 1
            events, picks = script_play(lib, kind, rng)
            s, returned = play_through_api(client, new_session, events, picks, f"anon-scripted-{n:04d}")
            plays.append((kind, s["session_id"], returned))
    assert len(plays) == 50

    # The 15-minute job agrees with what /floor returned, and has nothing left to do after.
    clean.run(db)
    assert clean.run(db) == 0
    db.expire_all()
    for kind, sid, returned in plays:
        _, score, flags, weight = EXPECTED[kind]
        q = db.get(Quality, sid)
        assert (q.score, q.flags, q.weight, q.for_preferences) == (score, flags, weight, True), kind
        assert returned == score


def test_sample_play_scores(client, new_session, db):
    """The real sample was played fast: every pick under 3 s and the day in 59 s."""
    from tests.test_replay import replay

    s, _ = replay(client, new_session, SAMPLE)
    r = client.post(f"/v1/sessions/{s['session_id']}/floor", headers=s["headers"],
                    json={"palette": SAMPLE["end"]["palette"], "sim": SIM})
    assert r.json() == {"quality": 60}
    q = db.get(Quality, s["session_id"])
    assert q.flags == ["speeding_play", "speeding_moments"] and q.weight == 0.5


def test_repeat_play_only_first_counts(client, new_session, db):
    lib = clean.library(str(get_settings().scenarios_path))
    rng = random.Random(7)
    first, _ = play_through_api(client, new_session, *script_play(lib, "good", rng), anon="anon-repeater-1")
    second, _ = play_through_api(client, new_session, *script_play(lib, "good", rng), anon="anon-repeater-1")
    other, _ = play_through_api(client, new_session, *script_play(lib, "good", rng), anon="anon-someone-2")
    clean.run(db)
    db.expire_all()
    q1, q2, q3 = (db.get(Quality, s["session_id"]) for s in (first, second, other))
    assert (q1.for_preferences, q1.flags) == (True, [])
    assert (q2.for_preferences, q2.flags, q2.score) == (False, ["repeat"], 100), "scored, but not for preferences"
    assert q3.for_preferences is True


def test_unfinished_play_scored_once_its_key_runs_out(client, new_session, db):
    lib = clean.library(str(get_settings().scenarios_path))
    events, _ = script_play(lib, "good", random.Random(3))
    s = new_session(anon_player_id="anon-leaver-0001")
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": events[:9]})
    assert clean.run(db) == 0, "still playing"
    later = datetime.now(timezone.utc) + timedelta(hours=3)
    assert clean.run(db, now=later) == 1
    q = db.get(Quality, s["session_id"])
    assert "unfinished" in q.flags and q.for_preferences is False
    assert clean.run(db, now=later) == 0


def test_job_rescores_after_new_events(client, new_session, db):
    lib = clean.library(str(get_settings().scenarios_path))
    s, _ = play_through_api(client, new_session, *script_play(lib, "good", random.Random(5)), anon="anon-editor-0001")
    assert clean.run(db) == 0, "/floor already scored it"
    # An end-of-day edit arrives later from the offline queue.
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                json={"events": [{"seq": 100, "ev": "addOpen", "t": 999_000}, {"seq": 101, "ev": "addSkip", "t": 999_500}]})
    assert clean.run(db) == 1


# ---------------------------------------------------------------- POST /v1/sessions/{id}/floor


def test_floor_needs_finish_first(client, new_session):
    s = new_session()
    r = client.post(f"/v1/sessions/{s['session_id']}/floor", headers=s["headers"], json={"sim": SIM})
    assert r.status_code == 409


def test_floor_needs_the_write_key(client, new_session):
    s = new_session()
    assert client.post(f"/v1/sessions/{s['session_id']}/floor", json={"sim": SIM}).status_code == 401


def test_floor_stores_sim_and_drops_anything_else(client, new_session, db):
    s = new_session()
    url = f"/v1/sessions/{s['session_id']}"
    client.post(f"{url}/finish", headers=s["headers"], json={"final_picks": {"z1": "a"}})
    sim = {**SIM, "note": "free text", "cost": 100, "look": {"hair": 1},
           "busy": [{**SIM["busy"][0], "text": "free text"}, *SIM["busy"][1:]]}
    r = client.post(f"{url}/floor", headers=s["headers"], json={"palette": "Scandi Light", "sim": sim, "name": "Kim"})
    assert r.status_code == 200
    floor = db.get(Floor, s["session_id"])
    assert floor.palette_id == "Scandi Light"
    assert floor.sim["use"] == 14 and floor.sim["happy"] == 33 and floor.sim["deskU"] == 23
    assert floor.sim["busy"][0] == {"t": "07:30", "name": "Satellite hub", "pct": 23}
    assert floor.sim["busy"][5] == {"t": "17:00", "name": "Compact Desks"}, "a null pct is left out"
    assert not {"note", "cost", "look"} & set(floor.sim)


@pytest.mark.parametrize("bad", [{"use": 140}, {"happy": -1}, {"occ": [1] * 200}])
def test_floor_rejects_out_of_range_numbers(client, new_session, bad):
    s = new_session()
    url = f"/v1/sessions/{s['session_id']}"
    client.post(f"{url}/finish", headers=s["headers"], json={"final_picks": {"z1": "a"}})
    assert client.post(f"{url}/floor", headers=s["headers"], json={"sim": {**SIM, **bad}}).status_code == 422


def test_quality_is_never_in_the_session_row(new_session, db):
    s = new_session()
    assert not hasattr(db.get(GameSession, s["session_id"]), "score")
    assert db.scalar(select(Quality)) is None
