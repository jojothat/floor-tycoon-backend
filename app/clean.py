"""Build step 2 (Clean): derived tables, quality score and repeat-play grouping.

`run()` is the scheduled job (every 15 minutes during the pilot, spec section 3):
`python -m app.clean`. The API calls the same functions for one session at a time.
"""

from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import deal as deal_fn
from app.config import get_settings
from app.derive import Derived, derive
from app.models import AddDecline, Addition, Deal, Edit, Event, Floor, GameSession, Moment, Quality
from app.quality import MomentSignal, score_play


@lru_cache
def library(path: str):
    return deal_fn.load_scenarios(path)


def _derive(s: Session, session_id: str) -> Derived:
    events = s.scalars(select(Event).where(Event.session_id == session_id).order_by(Event.seq)).all()
    flat = [{**e.payload, "ev": e.ev, "m": e.m, "t": e.t} for e in events]

    lib = library(str(get_settings().scenarios_path))
    played = next((f.get("ids") for f in flat if f["ev"] == "deal"), None)
    if played:
        # The deal the player actually played, as logged by the game.
        zone_to_scenario = {lib[int(i)].zone: int(i) for i in played if int(i) in lib}
    else:
        d = s.get(Deal, session_id)
        zone_to_scenario = dict(zip(d.zones, d.scenario_ids)) if d else {}
    return derive(flat, zone_to_scenario)


def rebuild_derived(s: Session, session_id: str) -> Derived | None:
    """Rebuild moments, edits, additions and add_declines from the raw events. Does nothing
    (returns None) once the retention job has deleted the events: what was derived stays."""
    gs = s.get(GameSession, session_id)
    if gs is not None and gs.events_deleted_at is not None:
        return None
    out = _derive(s, session_id)
    for model in (Moment, Edit, Addition, AddDecline):
        s.execute(delete(model).where(model.session_id == session_id))
    s.add_all(Moment(session_id=session_id, **vars(m)) for m in out.moments)
    s.add_all(Edit(session_id=session_id, **e) for e in out.edits)
    s.add_all(Addition(session_id=session_id, **vars(a)) for a in out.additions)
    s.add_all(AddDecline(session_id=session_id, **d) for d in out.add_declines)

    floor = s.get(Floor, session_id)
    if floor is not None:
        live = [a for a in out.additions if not a.removed]
        floor.addition = vars(live[-1]) if live else None
    return out


def is_repeat(s: Session, gs: GameSession) -> bool:
    """Same anon player finished an earlier play in this pilot: only the first counts."""
    if gs.finished_at is None:
        return False
    earlier = s.scalar(
        select(func.count())
        .select_from(GameSession)
        .where(
            GameSession.pilot_id == gs.pilot_id,
            GameSession.anon_player_id == gs.anon_player_id,
            GameSession.session_id != gs.session_id,
            GameSession.finished_at.is_not(None),
            or_(
                GameSession.finished_at < gs.finished_at,
                (GameSession.finished_at == gs.finished_at) & (GameSession.session_id < gs.session_id),
            ),
        )
    )
    return bool(earlier)


def score_session(s: Session, gs: GameSession, derived: Derived | None = None) -> Quality | None:
    if gs.events_deleted_at is not None:
        return s.get(Quality, gs.session_id)  # kept as it was when the events went
    out = derived or rebuild_derived(s, gs.session_id)
    result = score_play(
        [MomentSignal(m.scenario_id, m.chosen, m.previewed, m.time_to_decide_ms) for m in out.moments],
        out.day_ms,
    )
    flags = list(result.flags)
    if gs.finished_at is None:
        flags.append("unfinished")
    if is_repeat(s, gs):
        flags.append("repeat")
    q = s.get(Quality, gs.session_id) or Quality(session_id=gs.session_id)
    q.score, q.flags, q.weight = result.score, flags, result.weight
    q.for_preferences = "unfinished" not in flags and "repeat" not in flags
    q.computed_at = s.scalar(select(func.now()))  # the database clock, same as events.server_time
    s.merge(q)
    return q


def due_sessions(s: Session, now: datetime) -> list[GameSession]:
    """Sessions whose events, finish or floor changed since they were last scored, plus
    unfinished sessions whose write key has run out (they can't change any more)."""
    last_event = (
        select(Event.session_id, func.max(Event.server_time).label("at")).group_by(Event.session_id).subquery()
    )
    rows = s.scalars(
        select(GameSession)
        .outerjoin(Quality, Quality.session_id == GameSession.session_id)
        .outerjoin(last_event, last_event.c.session_id == GameSession.session_id)
        .where(
            or_(
                (GameSession.finished_at.is_not(None))
                & (
                    Quality.session_id.is_(None)
                    | (last_event.c.at > Quality.computed_at)
                    | (GameSession.finished_at > Quality.computed_at)
                ),
                (GameSession.finished_at.is_(None))
                & (GameSession.write_key_expires_at < now)
                & (Quality.session_id.is_(None) | (last_event.c.at > Quality.computed_at)),
            )
        )
    ).all()
    # A new finish can change which play of a player is the first, so rescore their others too.
    players = {(g.pilot_id, g.anon_player_id) for g in rows if g.finished_at is not None}
    seen = {g.session_id for g in rows}
    for pilot_id, anon in players:
        for g in s.scalars(
            select(GameSession).where(GameSession.pilot_id == pilot_id, GameSession.anon_player_id == anon)
        ):
            if g.session_id not in seen and (g.finished_at is not None or g.write_key_expires_at < now):
                rows.append(g)
                seen.add(g.session_id)
    return rows


def run(s: Session, now: datetime | None = None) -> int:
    """The 15-minute job. Returns how many sessions were rescored."""
    now = now or datetime.now(timezone.utc)
    due = due_sessions(s, now)
    for gs in due:
        score_session(s, gs)
    s.commit()
    return len(due)


if __name__ == "__main__":
    from app.db import get_engine, make_sessionmaker

    with make_sessionmaker(get_engine())() as session:
        print(f"rescored {run(session)} sessions")
