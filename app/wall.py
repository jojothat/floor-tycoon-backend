"""The wall (spec sections 2.7, 5 and 6), replacing the artifact's own store.

Request and response shapes follow the game (frontend/FRONTEND.md). Its public API only
serves floors and aggregates, never events or anon ids. No names, timestamps or amenity
lists. Games by employee type follow the group-of-10 rule: types under 10 merge into "other".
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.deps import admit, db, resolve_pilot, settings, writable_session
from app.models import Addition, Deal, Event, Floor, GameSession, Pilot, WallPost, WallView
from app.privacy import merge_small

router = APIRouter()

# The game's keys for employee types: "Prefer not to say" is "na".
GAME_TYPE_KEYS = {"none": "na"}
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
    """Share to the wall. The post is built here from the session, never from the body."""
    gs = writable_session(body.session_id, authorization, s)
    floor = s.get(Floor, gs.session_id)
    if floor is None or not floor.sim:
        raise HTTPException(409, "finish the simulated day first")
    addition = floor.wall_addition or addition_from_events(s, gs.session_id, floor.final_picks or {})
    fields = dict(
        pilot_id=gs.pilot_id,
        session_id=gs.session_id,
        deal=played_deal(s, gs.session_id),
        picks=floor.final_picks,
        skips=floor.skips,
        adds=addition["adds"],
        deskfit=addition["deskfit"],
        swaps=addition["swaps"],
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
    post_id = s.execute(
        stmt.on_conflict_do_update(index_elements=[WallPost.anon_player_id], set_=fields).returning(WallPost.id)
    ).scalar_one()
    s.commit()
    return {"ok": True, "id": post_id}


def addition_from_events(s: Session, session_id: str, picks: dict) -> dict:
    """The addition in the game's shape, when the game did not send it with the floor."""
    live = s.scalars(
        select(Addition).where(Addition.session_id == session_id, Addition.removed.is_(False)).order_by(Addition.id)
    ).all()
    desks = next((a for a in live if a.how_placed.startswith("desks_")), None)
    return {
        "adds": {a.zone: a.item_id for a in live if a.zone},
        "deskfit": {"mode": desks.how_placed.removeprefix("desks_"), "add": desks.item_id, "k": picks.get("z7")}
        if desks else None,
        "swaps": {a.zone: a.item_id for a in live if a.how_placed == "swap" and a.zone},
    }


def player_pilot(s: Session, anon: str) -> Pilot | None:
    """The pilot of this player's most recent play."""
    return s.scalar(
        select(Pilot).join(GameSession, GameSession.pilot_id == Pilot.pilot_id)
        .where(GameSession.anon_player_id == anon)
        .order_by(GameSession.started_at.desc())
        .limit(1)
    )


def wall_viewer(
    request: Request,
    x_ft_player: str | None = Header(default=None),
    x_anon_player: str | None = Header(default=None),
    x_pilot_token: str | None = Header(default=None),
    x_pilot_id: str | None = Header(default=None),
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
) -> tuple[Pilot | None, str | None]:
    """Who is looking, and at which pilot's wall.

    The player is the phone's anon id (X-FT-Player, as the game sends it; X-Anon-Player also
    works). The pilot comes from the invite token (or SSO) when one is sent, and otherwise
    from that player's own most recent play. A player with no play and no token sees an
    empty wall: the wall is never served to someone with no link to a pilot.
    """
    anon = x_ft_player or x_anon_player
    if anon is not None and not 8 <= len(anon) <= 64:
        raise HTTPException(400, "bad player id")
    if x_pilot_token or x_pilot_id:
        return resolve_pilot(x_pilot_token, x_pilot_id, request, s, cfg), anon
    if cfg.access_mode == "sso" and not request.headers.get(cfg.sso_header):
        raise HTTPException(401, "not signed in")
    pilot = player_pilot(s, anon) if anon else None
    return (admit(pilot, cfg) if pilot else None), anon


def games_by_type(counts: dict[str, int], min_group: int) -> dict:
    """Completed games per employee type, in the game's shape: {"CE": 34, "other": 27, "total": 100}.
    Types under 10 merge into "other" (topped up so no small type shows by subtraction).
    Under 10 games in all, only {"total": null}: the game keeps its labelled sample numbers."""
    groups = merge_small(counts, min_group)
    if groups is None:
        return {"total": None}
    out = {}
    for g, keys in groups.items():
        out["other" if g == "Other" else GAME_TYPE_KEYS.get(g, g)] = sum(counts[k] for k in keys)
    out["total"] = sum(counts.values())
    return out


def palette_number(p: str | None) -> int | None:
    return int(p) if p and p.isdigit() else None


@router.get("/v1/wall")
def get_wall(
    viewer=Depends(wall_viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)
):
    pilot, anon = viewer
    if pilot is None:
        return {"posts": [], "games": {"total": None}}
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
    # Newest first. No timestamps, names or amenity lists (spec section 2), and never an anon id.
    return {
        "posts": [
            {
                "id": p.id,
                "mine": anon is not None and p.anon_player_id == anon,
                "deal": p.deal,
                "picks": p.picks,
                "skips": {z: "none" for z in p.skips or []},
                "adds": p.adds if isinstance(p.adds, dict) else {},
                "deskfit": p.deskfit,
                "swaps": p.swaps or {},
                "pal": palette_number(p.palette),
                "use": p.use_pct,
                "happy": p.happiness_pct,
                "views": n,
            }
            for p, n in rows
        ],
        "games": games_by_type(counts, cfg.min_group_size),
    }


@router.post("/v1/wall/{post_id}/view")
def view_post(post_id: int, viewer=Depends(wall_viewer), s: Session = Depends(db)):
    pilot, anon = viewer
    if anon is None:
        raise HTTPException(400, "X-FT-Player is required")
    post = s.get(WallPost, post_id)
    if pilot is None or post is None or post.pilot_id != pilot.pilot_id:
        raise HTTPException(404, "no such office")
    if post.anon_player_id != anon:  # own office not counted
        s.execute(
            insert(WallView).values(viewer_anon_id=anon, wall_post_id=post_id).on_conflict_do_nothing()
        )
        s.commit()
    return {"ok": True}
