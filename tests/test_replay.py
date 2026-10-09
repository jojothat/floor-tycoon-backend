"""Build step 1 is done when a play log replays through the API and the session,
events and derived moments come out complete (CLAUDE.md).

Runs on sample/play-log-sample.json (with sample/scenarios.json) when those are in
the repo, and always on a synthetic log written from events.md.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import AddDecline, Addition, Edit, Event, Floor, GameSession, Moment
from tests.conftest import FIXTURES, ROOT, load_json

END_OF_DAY = {"replay", "edit", "addRemoved", "addOpen", "addShuffle", "addSkip", "addNeedsRoom",
              "gap", "gapShuffle", "gapLeave", "add", "wallOpen"}

LOGS = [
    pytest.param(FIXTURES / "play-log-synthetic.json", FIXTURES / "scenarios-synthetic.json", id="synthetic"),
    pytest.param(
        ROOT / "sample" / "play-log-sample.json",
        ROOT / "sample" / "scenarios.json",
        id="sample",
        marks=pytest.mark.skipif(
            not (ROOT / "sample" / "play-log-sample.json").exists()
            or not (ROOT / "sample" / "scenarios.json").exists(),
            reason="sample/ files not in the repo yet",
        ),
    ),
]


def events_of(play: dict) -> list[dict]:
    log = play.get("log", play.get("events"))
    # The game adds seq when it starts sending; the current log doesn't have it yet.
    return [{**e, "seq": e.get("seq", i)} for i, e in enumerate(log, start=1)]


def replay(client, new_session, play: dict) -> tuple[dict, list[dict]]:
    profile = play.get("profile") or {}
    s = new_session(profile.get("type") or "none", look={"hair": 1})
    url = f"/v1/sessions/{s['session_id']}"
    events = events_of(play)
    first_end = next((i for i, e in enumerate(events) if e["ev"] in END_OF_DAY), len(events))
    day, end = events[:first_end], events[first_end:]

    def send(batch):
        for i in range(0, len(batch), 10):  # the game sends every 10 events
            chunk = batch[i:i + 10]
            r = client.post(f"{url}/events", headers=s["headers"], json={"events": chunk})
            assert r.status_code == 200, r.text
            # Every batch is retried once, as a flaky phone connection would.
            assert client.post(f"{url}/events", headers=s["headers"], json={"events": chunk}).json()["inserted"] == 0

    send(day)
    final = {z: str(o) for z, o in (play.get("finalPicks") or {}).items()}
    # First arrival at 18:00: picks as they were before any end-of-day edit.
    first_picks = dict(final)
    for e in reversed([e for e in end if e["ev"] == "edit"]):
        first_picks[e["m"]] = str(e["from"])
    r = client.post(f"{url}/finish", headers=s["headers"], json={"final_picks": first_picks})
    assert r.status_code == 200, r.text
    send(end)
    r = client.post(f"{url}/finish", headers=s["headers"], json={"final_picks": final})
    assert r.status_code == 200
    return s, events


@pytest.mark.parametrize("log_path,scenarios_path", LOGS)
def test_replay_lands_complete(client, new_session, db, monkeypatch, log_path: Path, scenarios_path: Path):
    monkeypatch.setenv("SCENARIOS_PATH", str(scenarios_path))
    play = load_json(log_path)
    s, events = replay(client, new_session, play)
    sid = s["session_id"]

    # Session
    gs = db.get(GameSession, sid)
    assert gs.finished_at is not None
    assert gs.role_type == ((play.get("profile") or {}).get("type") or "none")

    # Events: every one, once, in order, as sent
    rows = db.scalars(select(Event).where(Event.session_id == sid).order_by(Event.seq)).all()
    assert [(r.seq, r.ev, r.t) for r in rows] == [(e["seq"], e["ev"], e["t"]) for e in events]
    assert not any(r.unknown_ev for r in rows)

    # Moments: one per dealt zone, each decided, consistent with the final floor
    played_deal = next(e["ids"] for e in events if e["ev"] == "deal")
    moments = db.scalars(select(Moment).where(Moment.session_id == sid).order_by(Moment.play_order)).all()
    assert len(moments) == len(played_deal) == 7
    assert sorted(m.zone for m in moments) == [f"z{i}" for i in range(1, 8)]
    assert sorted(m.scenario_id for m in moments) == sorted(played_deal)
    assert all(m.chosen is not None for m in moments)
    assert all(m.time_to_decide_ms is not None and m.time_to_decide_ms >= 0 for m in moments)

    edits = db.scalars(select(Edit).where(Edit.session_id == sid).order_by(Edit.id)).all()
    assert len(edits) == sum(e["ev"] == "edit" for e in events)
    expected = {m.zone: ("0" if m.chosen == "skip" else m.chosen) for m in moments}
    for e in edits:
        expected[e.zone] = e.to_option
    floor = db.get(Floor, sid)
    assert floor.final_picks == expected
    assert floor.skips == sorted(z for z, o in expected.items() if o == "0")

    adds = db.scalars(select(Addition).where(Addition.session_id == sid)).all()
    assert len(adds) == sum(e["ev"] == "add" for e in events)
    declines = db.scalar(select(func.count()).select_from(AddDecline).where(AddDecline.session_id == sid))
    assert declines == sum(e["ev"] in ("addSkip", "gapLeave") for e in events)


def test_synthetic_replay_exact(client, new_session, db, monkeypatch):
    """Exact values for the synthetic log, so the derivation itself is checked."""
    monkeypatch.setenv("SCENARIOS_PATH", str(FIXTURES / "scenarios-synthetic.json"))
    s, _ = replay(client, new_session, load_json(FIXTURES / "play-log-synthetic.json"))
    sid = s["session_id"]
    moments = {m.zone: m for m in db.scalars(select(Moment).where(Moment.session_id == sid))}
    assert {z: m.chosen for z, m in moments.items()} == {
        "z1": "b", "z2": "a", "z3": "skip", "z4": "c", "z5": "d", "z6": "a", "z7": "b"}
    assert moments["z5"].previewed == ["a", "b", "d"] and moments["z5"].first_preview == "a"
    assert moments["z6"].scenario_id == 30 and moments["z7"].scenario_id == 62
    assert moments["z1"].time_to_decide_ms == 4500  # answer -> choose: 3 steps of 1.5 s
    assert moments["z3"].time_to_decide_ms == 3000  # answer -> skip

    [edit] = db.scalars(select(Edit).where(Edit.session_id == sid)).all()
    assert (edit.zone, edit.from_option, edit.to_option) == ("z4", "c", "a")
    [add] = db.scalars(select(Addition).where(Addition.session_id == sid)).all()
    assert (add.item_id, add.how_placed, add.zone, add.shuffles, add.removed) == ("plants", "end", "z5", 1, False)
    [decline] = db.scalars(select(AddDecline).where(AddDecline.session_id == sid)).all()
    assert (decline.where_, decline.zone) == ("gap", "z2")
    assert db.get(Floor, sid).addition["item_id"] == "plants"
