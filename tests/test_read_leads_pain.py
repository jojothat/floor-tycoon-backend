import statistics

import pytest
from sqlalchemy import select

from app.models import Moment, Quality
from app.read import hour_of, weighted_shares
from tests.test_read import FEW, ok, populate, read, scenario

pytestmark = pytest.mark.usefixtures("viewers")


def shares_from_db(db, sid, lead: bool):
    w = {q.session_id: q.weight for q in db.scalars(select(Quality))}
    from app.models import GameSession
    roles = {g.session_id: g.role_type for g in db.scalars(select(GameSession))}
    ms = [m for m in db.scalars(select(Moment).where(Moment.scenario_id == sid))
          if (roles[m.session_id] == "Lead") == lead and w.get(m.session_id, 0) > 0]
    return weighted_shares([(m.chosen, w[m.session_id]) for m in ms], ["a", "b", "c", "skip"])


# ---------------------------------------------------------------- leads-gap


def test_leads_gap(client, new_session, db):
    populate(client, new_session, db, [("Lead", "good", 12), ("CE", "good", 12)])
    out = ok(client, "leads-gap")
    row = scenario(out["scenarios"], 1)
    lead, staff = shares_from_db(db, 1, True), shares_from_db(db, 1, False)
    assert (row["leads"], row["staff"]) == (12, 12)
    assert row["lead_shares"] == lead and row["staff_shares"] == staff
    assert row["gap"] == {k: round(100 * (lead[k] - staff[k]), 1) for k in lead}
    assert abs(row["biggest_gap"]["points"]) == max(abs(v) for v in row["gap"].values())
    # Scenario 56 is dealt to every lead and to no CE: no staff to compare with.
    assert scenario(out["scenarios"], 56) == {"scenario_id": 56, "zone": "z6", "leads": FEW, "staff": FEW, "gap": FEW}


def test_leads_gap_hides_both_counts_when_one_group_is_small(client, new_session, db):
    populate(client, new_session, db, [("Lead", "good", 5), ("CE", "good", 12)])
    row = scenario(ok(client, "leads-gap")["scenarios"], 1)
    assert row == {"scenario_id": 1, "zone": "z7", "leads": FEW, "staff": FEW, "gap": FEW}


def test_leads_gap_refuses_type_filters(client, new_session, db):
    populate(client, new_session, db, [("Lead", "good", 10), ("CE", "good", 10)])
    assert read(client, "leads-gap", type="Lead").status_code == 422
    assert read(client, "leads-gap", by="type").status_code == 422


def test_leads_gap_needs_a_viewer(client):
    assert client.get("/v1/read/leads-gap").status_code == 401


# ---------------------------------------------------------------- pain-hours


def test_pain_hours(client, new_session, db):
    def extra(i, s):
        client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"],
                    json={"events": [{"seq": 100, "ev": "edit", "t": 900_000, "m": "z7", "from": "a", "to": "b"}]})

    populate(client, new_session, db, [("CE", "good", 8), ("CE", "skip_everything", 4)], extra=extra)
    out = ok(client, "pain-hours")
    h17 = next(h for h in out["hours"] if h["hour"] == "17:00")
    assert 1 in h17["scenarios"] and h17["n"] >= 12  # "The team grows to 14" is at 17:00 in every deal

    ms = [m for m in db.scalars(select(Moment)) if hour_of(m.scenario_id) == 17]
    assert h17["n"] == len({m.session_id for m in ms}), "plays, not moments"
    assert h17["value"]["median_decide_s"] == round(statistics.median(m.time_to_decide_ms for m in ms) / 1000, 1)
    # Every player edited z7 (17:00), so it is the most edited hour.
    assert out["ranked"]["most_edited"][0] == "17:00"
    shown = {h["hour"] for h in out["hours"] if isinstance(h["value"], dict)}
    for ranking in out["ranked"].values():
        assert set(ranking) <= shown, "only hours with 10+ plays are ranked"


def test_pain_hours_by_type(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("PSE", "good", 10)])
    h17 = next(h for h in ok(client, "pain-hours", by="type")["hours"] if h["hour"] == "17:00")
    assert {g["type"] for g in h17["by_type"]} <= {"CE", "PSE", "Other"}
    assert all(g["n"] >= 10 for g in h17["by_type"])


def test_pain_hours_under_ten(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 9)])
    out = ok(client, "pain-hours")
    # One play can have two moments in an hour (17:00 and 17:30); the rule still counts 9 plays.
    assert all(h["n"] == FEW and h["value"] == FEW for h in out["hours"])
    assert out["ranked"] == {"slowest": [], "most_skipped": [], "most_edited": []}


def test_pain_hours_refuses_isolating_filters(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("PSE", "good", 3)])
    assert read(client, "pain-hours", type="PSE").status_code == 403
