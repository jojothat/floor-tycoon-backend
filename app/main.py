"""Ingest API, build step 1 (spec section 5)."""

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import deal as deal_fn
from app.config import Settings, get_settings
from app.db import get_engine, make_sessionmaker
from app.derive import derive
from app.models import (
    ZONES,
    AddDecline,
    Addition,
    Deal,
    Edit,
    Event,
    Floor,
    GameSession,
    Moment,
    Pilot,
)

KNOWN_EVENTS = {
    # during the day
    "deal", "answer", "preview", "choose", "keep", "rethink", "skip",
    # end of day
    "replay", "edit", "addRemoved", "addOpen", "addShuffle", "addSkip", "addNeedsRoom",
    "gap", "gapShuffle", "gapLeave", "add", "wallOpen",
    # to be added once the backend exists
    "role_set", "palette_set", "sim_done", "render_view", "wall_post", "session_end",
}
# Never stored, wherever it turns up: the character look.
FORBIDDEN_KEYS = {"look"}
OPTIONS = {"a", "b", "c", "d", "0"}

app = FastAPI(title="Floor Tycoon backend")


def settings() -> Settings:
    return get_settings()


def db(cfg: Settings = Depends(settings)):
    with make_sessionmaker(get_engine(cfg.database_url))() as s:
        yield s


@lru_cache
def _library(path: str):
    return deal_fn.load_scenarios(path)


def library(cfg: Settings = Depends(settings)):
    return _library(str(cfg.scenarios_path))


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def strip_forbidden(obj):
    if isinstance(obj, dict):
        return {k: strip_forbidden(v) for k, v in obj.items() if k not in FORBIDDEN_KEYS}
    if isinstance(obj, list):
        return [strip_forbidden(v) for v in obj]
    return obj


# ---------------------------------------------------------------- sessions


class SessionIn(BaseModel):
    # Anything else in the body (a name, the look, ...) is dropped, never stored.
    model_config = ConfigDict(extra="ignore")

    pilot_token: str | None = None
    pilot_id: str | None = None  # SSO mode only
    anon_player_id: str = Field(min_length=8, max_length=64)
    role_type: Literal["CE", "PSE", "IE", "Sales", "Support", "Lead", "none"] | None = None
    game_version: str | None = Field(default=None, max_length=32)
    flow: Literal["quick", "classic"] = "quick"
    language: str | None = Field(default=None, max_length=16)
    device_class: str | None = Field(default=None, max_length=16)
    viewport_height: int | None = Field(default=None, ge=0, le=10000)


def viewport_bucket(h: int | None) -> str | None:
    """Bucket the height so short-screen drop-off can be checked without storing exact sizes."""
    if h is None:
        return None
    for top in (300, 400, 500, 700, 900):
        if h < top:
            return f"<{top}"
    return "900+"


def resolve_pilot(body: SessionIn, request: Request, s: Session, cfg: Settings) -> Pilot:
    if cfg.access_mode == "sso":
        if not request.headers.get(cfg.sso_header):
            raise HTTPException(401, "not signed in")
        pilot = s.get(Pilot, body.pilot_id) if body.pilot_id else None
    else:
        pilot = (
            s.scalar(select(Pilot).where(Pilot.invite_token_hash == sha256(body.pilot_token)))
            if body.pilot_token
            else None
        )
    if pilot is None:
        raise HTTPException(401, "unknown pilot")
    today = date.today()
    if (pilot.start_date and today < pilot.start_date) or (pilot.end_date and today > pilot.end_date):
        raise HTTPException(403, "pilot is not running")
    return pilot


@app.post("/v1/sessions", status_code=201)
def create_session(
    body: SessionIn,
    request: Request,
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
    lib=Depends(library),
):
    pilot = resolve_pilot(body, request, s, cfg)
    role = body.role_type or "none"
    dealt = deal_fn.deal(lib, role, team_grows_id=cfg.deal_z7_scenario_id)
    session_id = secrets.token_urlsafe(18)
    write_key = secrets.token_urlsafe(32)
    expires = now() + timedelta(minutes=cfg.write_key_ttl_minutes)
    s.add(
        GameSession(
            session_id=session_id,
            anon_player_id=body.anon_player_id,
            pilot_id=pilot.pilot_id,
            role_type=role,
            device_class=body.device_class,
            viewport_height_bucket=viewport_bucket(body.viewport_height),
            language=body.language,
            game_version=body.game_version,
            flow=body.flow,
            write_key_hash=sha256(write_key),
            write_key_expires_at=expires,
        )
    )
    s.flush()
    s.add(Deal(session_id=session_id, scenario_ids=[d.id for d in dealt], zones=[d.zone for d in dealt]))
    s.commit()
    return {
        "session_id": session_id,
        "deal": [d.id for d in dealt],
        "zones": [d.zone for d in dealt],
        "write_key": write_key,
        "write_key_expires_at": expires.isoformat(),
    }


def writable_session(
    session_id: str,
    authorization: str | None = Header(default=None),
    s: Session = Depends(db),
) -> GameSession:
    gs = s.get(GameSession, session_id)
    key = (authorization or "").removeprefix("Bearer ").strip()
    # Same answer for unknown session and wrong key, so ids can't be probed.
    if gs is None or not key or not secrets.compare_digest(gs.write_key_hash, sha256(key)):
        raise HTTPException(401, "bad session or write key")
    if gs.write_key_expires_at < now():
        raise HTTPException(401, "write key expired")
    return gs


# ---------------------------------------------------------------- events


class EventIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int = Field(ge=1)
    ev: str = Field(min_length=1, max_length=32)
    t: int = Field(ge=0)
    m: str | None = None

    @field_validator("m")
    @classmethod
    def zone(cls, v):
        if v is not None and v not in ZONES:
            raise ValueError("m must be z1-z7")
        return v


class EventsIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=1000)


@app.post("/v1/sessions/{session_id}/events")
async def post_events(
    request: Request,
    gs: GameSession = Depends(writable_session),
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
):
    raw = await request.body()
    if len(raw) > cfg.max_batch_bytes:
        raise HTTPException(413, f"batch over {cfg.max_batch_bytes} bytes")
    try:
        body = EventsIn.model_validate_json(raw)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))

    rows = []
    for e in body.events:
        payload = strip_forbidden(e.model_extra or {})
        rows.append(
            {
                "session_id": gs.session_id,
                "seq": e.seq,
                "ev": e.ev,
                "m": e.m,
                "t": e.t,
                "payload": payload,
                "unknown_ev": e.ev not in KNOWN_EVENTS,
                "has_text": bool(payload.get("text")),
            }
        )
    stmt = (
        insert(Event)
        .values(rows)
        .on_conflict_do_nothing(constraint="events_session_seq")
        .returning(Event.seq)
    )
    inserted = sorted(s.scalars(stmt).all())
    s.commit()

    # Events can arrive after finish (offline queue, end-of-day edits and additions).
    if gs.finished_at is not None and inserted:
        rebuild_derived(s, gs.session_id)
        s.commit()

    seqs = [e.seq for e in body.events]
    return {
        "accepted": {"from": min(seqs), "to": max(seqs)},
        "inserted": len(inserted),
        "duplicates": len(seqs) - len(inserted),
    }


# ---------------------------------------------------------------- finish


class FinishIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    final_picks: dict[str, str]
    skips: list[str] | int | None = None  # zones, or a count; picks of "0" are the source of truth

    @field_validator("final_picks")
    @classmethod
    def picks(cls, v):
        for zone, opt in v.items():
            if zone not in ZONES or str(opt) not in OPTIONS:
                raise ValueError(f"bad pick {zone}={opt}")
        return {z: str(o) for z, o in v.items()}


def rebuild_derived(s: Session, session_id: str) -> None:
    """Rebuild moments, edits, additions and add_declines from the raw events."""
    events = s.scalars(select(Event).where(Event.session_id == session_id).order_by(Event.seq)).all()
    flat = [{**e.payload, "ev": e.ev, "m": e.m, "t": e.t} for e in events]

    lib = _library(str(get_settings().scenarios_path))
    played = next((f.get("ids") for f in flat if f["ev"] == "deal"), None)
    if played:
        # The deal the player actually played, as logged by the game.
        zone_to_scenario = {lib[int(i)].zone: int(i) for i in played if int(i) in lib}
    else:
        d = s.get(Deal, session_id)
        zone_to_scenario = dict(zip(d.zones, d.scenario_ids)) if d else {}

    out = derive(flat, zone_to_scenario)
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


@app.post("/v1/sessions/{session_id}/finish")
def finish(body: FinishIn, gs: GameSession = Depends(writable_session), s: Session = Depends(db)):
    skips = sorted(z for z, o in body.final_picks.items() if o == "0")
    floor = s.get(Floor, gs.session_id)
    if floor is None:
        s.add(Floor(session_id=gs.session_id, final_picks=body.final_picks, skips=skips))
    else:
        floor.final_picks, floor.skips = body.final_picks, skips
    first = gs.finished_at is None
    if first:
        gs.finished_at = now()  # counts as a completed game for the wall strip
    s.flush()
    rebuild_derived(s, gs.session_id)
    s.commit()
    return {"ok": True, "first_finish": first}
