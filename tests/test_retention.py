import random
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app import clean, retention
from app.config import get_settings
from app.models import Event, Floor, GameSession, Moment, Pilot, Quality
from tests.scripted import script_play
from tests.test_clean import play_through_api


def played(client, new_session, db, n=2):
    lib = clean.library(str(get_settings().scenarios_path))
    sessions = [play_through_api(client, new_session, *script_play(lib, "good", random.Random(i)), anon=f"anon-keep-{i:05d}")[0]
                for i in range(n)]
    clean.run(db)
    return sessions


def end_pilot(db, end: date | None):
    p = db.get(Pilot, "pilot-1")
    p.end_date = end
    db.commit()


def count(db, model, **where):
    q = select(func.count()).select_from(model)
    for k, v in where.items():
        q = q.where(getattr(model, k) == v)
    return db.scalar(q)


@pytest.mark.parametrize("d,months,expected", [
    (date(2026, 11, 30), 12, date(2027, 11, 30)),
    (date(2027, 1, 31), 1, date(2027, 2, 28)),
    (date(2027, 12, 15), 3, date(2028, 3, 15)),
    (date(2027, 11, 30), 3, date(2028, 2, 29)),
])
def test_add_months(d, months, expected):
    assert retention.add_months(d, months) == expected


def test_nothing_expires_while_the_pilot_runs_or_has_no_end(client, new_session, db):
    played(client, new_session, db)
    before = count(db, Event)
    assert retention.run(db, months=12).events == 0, "no end date"
    end_pilot(db, date.today() - timedelta(days=30))
    assert retention.run(db, months=12).events == 0, "ended a month ago, kept 12"
    assert count(db, Event) == before


def test_events_deleted_after_pilot_plus_retention(client, new_session, db):
    sessions = played(client, new_session, db)
    end = date(2025, 1, 31)
    end_pilot(db, end)
    before = count(db, Event)
    moments, quality = count(db, Moment), [db.get(Quality, s["session_id"]).score for s in sessions]

    dry = retention.run(db, today=date(2026, 2, 1), months=12, dry_run=True)
    assert (dry.pilots, dry.sessions, dry.events) == (["pilot-1"], 2, before)
    assert count(db, Event) == before, "a dry run deletes nothing"
    assert retention.run(db, today=date(2026, 1, 31), months=12).events == 0, "the last day is still kept"

    r = retention.run(db, today=date(2026, 2, 1), months=12)
    assert (r.pilots, r.sessions, r.events) == (["pilot-1"], 2, before)
    assert count(db, Event) == 0
    # Aggregates and derived rows are kept.
    db.expire_all()
    assert count(db, Moment) == moments
    assert [db.get(Quality, s["session_id"]).score for s in sessions] == quality
    assert all(db.get(Floor, s["session_id"]).sim for s in sessions)
    assert all(db.get(GameSession, s["session_id"]).events_deleted_at for s in sessions)


def test_retention_only_touches_expired_pilots(client, new_session, db):
    played(client, new_session, db, n=1)
    db.add(Pilot(pilot_id="pilot-2", site="site-b", end_date=date(2020, 1, 1)))
    db.commit()
    r = retention.run(db, months=12)
    assert r.pilots == ["pilot-2"] and r.events == 0
    assert count(db, Event) > 0, "pilot-1's events are untouched"


def test_derived_rows_and_scores_survive_later_jobs(client, new_session, db):
    sessions = played(client, new_session, db, n=1)
    sid = sessions[0]["session_id"]
    end_pilot(db, date(2020, 1, 1))
    retention.run(db, months=12)
    moments = count(db, Moment, session_id=sid)
    assert clean.run(db) == 0
    assert clean.rebuild_derived(db, sid) is None
    gs = db.get(GameSession, sid)
    assert clean.score_session(db, gs).score == 100
    db.commit()
    assert count(db, Moment, session_id=sid) == moments


def test_second_run_finds_nothing(client, new_session, db):
    played(client, new_session, db, n=1)
    end_pilot(db, date(2020, 1, 1))
    assert retention.run(db, months=12).sessions == 1
    r = retention.run(db, months=12)
    assert (r.sessions, r.events) == (0, 0)


def test_events_still_append_only_outside_the_job(client, new_session, db, engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    played(client, new_session, db, n=1)
    with pytest.raises(DBAPIError, match="append-only"):
        with engine.begin() as c:
            c.execute(text("DELETE FROM events"))
