"""The wall (spec sections 2.7, 5 and 6), replacing the artifact's own store.

Its public API only serves floors and aggregates, never events. No names,
timestamps or amenity lists. Games by employee type follow the group-of-10 rule:
types under 10 are merged into "Other".
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import db, resolve_pilot, settings, writable_session
from app.models import Addition, Deal, Event, Floor, GameSession, Pilot, WallPost, WallView
from app.privacy import merge_small

router = APIRouter()

TYPE_LABELS = {"none": "Prefer not to say"}
WALL_LIMIT = 200


class WallPostIn(BaseModel):
    session_id: str


def played_deal(s: Session, session_id: str) -> list[int]:
    ev = s.scalar(select(Event).where(Event.session_id == session_id, Event.ev == "deal").order_by(Event.seq))
    if ev is not None and ev.payload.get("ids"):
        return [int(i) for i in ev.payload["ids"]]
    d = s.get(Deal, session_id)
    return list(d.scenario_ids) if d else []


@router.post("/v1/wall")
def post_wall(body: WallPostIn, authorization: str | None = Header(default=None), s: Session = Depends(db)):
    gs = writable_session(body.session_id, authorization, s)
    floor = s.get(Floor, gs.session_id)
    if floor is None or not floor.sim:
        raise HTTPException(409, "finish the simulated day first")
    live = s.scalars(
        select(Addition).where(Addition.session_id == gs.session_id, Addition.removed.is_(False)).order_by(Addition.id)
    ).all()
    desks = next((a.how_placed.removeprefix("desks_") for a in live if a.how_placed.startswith("desks_")), None)
    fields = dict(
        pilot_id=gs.pilot_id,
        session_id=gs.session_id,
        deal=played_deal(s, gs.session_id),
        picks=floor.final_picks,
        skips=floor.skips,
        adds=[a.item_id for a in live],
        deskfit=desks,
        swaps={a.zone: a.swapped_out_option for a in live if a.how_placed == "swap"},
        palette=floor.palette_id,
        # Use % and Happiness % as the game's simulated day reported them (section 6).
        use_pct=floor.sim.get("use"),
        happiness_pct=floor.sim.get("happy"),
        posted_at=func.now(),
    )
    # One office per person: the latest replaces the last. Views of the old one go with it.
    old = s.scalar(select(WallPost).where(WallPost.anon_player_id == gs.anon_player_id))
    if old is not None and old.session_id != gs.session_id:
        s.query(WallView).filter(WallView.wall_post_id == old.id).delete()
    stmt = insert(WallPost).values(anon_player_id=gs.anon_player_id, **fields)
    s.execute(stmt.on_conflict_do_update(index_elements=[WallPost.anon_player_id], set_=fields))
    s.commit()
    return {"ok": True}


def wall_viewer(
    request: Request,
    x_pilot_token: str | None = Header(default=None),
    x_pilot_id: str | None = Header(default=None),
    x_anon_player: str | None = Header(default=None, min_length=8, max_length=64),
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
) -> tuple[Pilot, str | None]:
    """The wall can be opened before playing, so it is reached with the pilot's invite
    link (or SSO) and the phone's anon id, not a session."""
    return resolve_pilot(x_pilot_token, x_pilot_id, request, s, cfg), x_anon_player


def games_by_type(counts: dict[str, int], min_group: int) -> dict:
    """Completed games per employee type, count and %, with types under 10 merged into "Other"."""
    groups = merge_small(counts, min_group)
    if groups is None:
        return {"total": None, "enough_data": False, "types": []}
    total = sum(counts.values())
    rows = [(TYPE_LABELS.get(g, g), sum(counts[k] for k in keys)) for g, keys in groups.items()]
    rows.sort(key=lambda r: (r[0] == "Other", -r[1], r[0]))
    return {
        "total": total,
        "enough_data": True,
        "types": [{"type": t, "count": n, "pct": round(100 * n / total)} for t, n in rows],
    }


@router.get("/v1/wall")
def get_wall(
    viewer=Depends(wall_viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)
):
    pilot, anon = viewer
    views = (
        select(WallView.wall_post_id, func.count().label("n")).group_by(WallView.wall_post_id).subquery()
    )
    rows = s.execute(
        select(WallPost, func.coalesce(views.c.n, 0))
        .outerjoin(views, views.c.wall_post_id == WallPost.id)
        .where(WallPost.pilot_id == pilot.pilot_id)
        .order_by(WallPost.posted_at.desc(), WallPost.id.desc())
        .limit(WALL_LIMIT)
    ).all()
    counts = dict(
        s.execute(
            select(GameSession.role_type, func.count())
            .where(GameSession.pilot_id == pilot.pilot_id, GameSession.finished_at.is_not(None))
            .group_by(GameSession.role_type)
        ).all()
    )
    return {
        "posts": [
            {
                "id": p.id,
                "deal": p.deal,
                "picks": p.picks,
                "skips": p.skips,
                "adds": p.adds,
                "deskfit": p.deskfit,
                "swaps": p.swaps,
                "palette": p.palette,
                "use": p.use_pct,
                "happiness": p.happiness_pct,
                "views": n,
                "mine": anon is not None and p.anon_player_id == anon,
            }
            for p, n in rows
        ],
        "games_by_type": games_by_type(counts, cfg.min_group_size),
    }


@router.post("/v1/wall/{post_id}/view")
def view_post(post_id: int, viewer=Depends(wall_viewer), s: Session = Depends(db)):
    pilot, anon = viewer
    if anon is None:
        raise HTTPException(400, "X-Anon-Player is required")
    post = s.get(WallPost, post_id)
    if post is None or post.pilot_id != pilot.pilot_id:
        raise HTTPException(404, "no such office")
    if post.anon_player_id != anon:  # own office not counted
        s.execute(
            insert(WallView).values(viewer_anon_id=anon, wall_post_id=post_id).on_conflict_do_nothing()
        )
        s.commit()
    return {"ok": True}
