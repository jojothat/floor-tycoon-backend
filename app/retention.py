"""The retention job (spec section 8): raw events are kept for the pilot plus
RETENTION_MONTHS, then deleted. Aggregated results are kept.

This is the only code allowed to delete events (the append-only trigger lets a
delete through only with `app.retention = 'on'`). It runs daily:
`python -m app.retention`, or `python -m app.retention --dry-run` to count only.

A pilot with no end date never expires. Once a play's events are gone, its derived
rows (moments, edits, additions, floor) and quality score stay as they were.
"""

import sys
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Event, GameSession, Pilot


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    days = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days))


@dataclass
class Result:
    pilots: list[str]
    sessions: int
    events: int


def expired_pilots(s: Session, today: date, months: int) -> list[str]:
    return [
        p.pilot_id
        for p in s.scalars(select(Pilot).where(Pilot.end_date.is_not(None)).order_by(Pilot.pilot_id))
        if add_months(p.end_date, months) < today
    ]


def run(s: Session, today: date | None = None, months: int | None = None, dry_run: bool = False) -> Result:
    today = today or date.today()
    months = get_settings().retention_months if months is None else months
    pilots = expired_pilots(s, today, months)
    sessions = select(GameSession.session_id).where(GameSession.pilot_id.in_(pilots))
    n_sessions = s.scalar(
        select(func.count()).select_from(GameSession)
        .where(GameSession.pilot_id.in_(pilots), GameSession.events_deleted_at.is_(None))
    ) if pilots else 0
    if dry_run or not pilots:
        n_events = s.scalar(select(func.count()).select_from(Event).where(Event.session_id.in_(sessions))) if pilots else 0
        return Result(pilots, n_sessions, n_events)

    s.execute(text("SET LOCAL app.retention = 'on'"))  # this transaction only
    n_events = s.execute(Event.__table__.delete().where(Event.session_id.in_(sessions))).rowcount
    s.execute(
        update(GameSession)
        .where(GameSession.pilot_id.in_(pilots), GameSession.events_deleted_at.is_(None))
        .values(events_deleted_at=func.now())
    )
    s.commit()
    return Result(pilots, n_sessions, n_events)


if __name__ == "__main__":
    from app.db import get_engine, make_sessionmaker

    dry = sys.argv[1:] == ["--dry-run"]
    if sys.argv[1:] not in ([], ["--dry-run"]):
        sys.exit(__doc__)
    with make_sessionmaker(get_engine())() as session:
        r = run(session, dry_run=dry)
    verb = "would delete" if dry else "deleted"
    print(f"{verb} {r.events} events from {r.sessions} plays in pilots {r.pilots or 'none'}")
