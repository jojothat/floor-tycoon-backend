"""Ingest API, build step 1 (spec section 5)."""

import secrets
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import clean
from app import deal as deal_fn
from app import read, wall
from app.config import Settings
from app.deps import db, library, now, resolve_pilot, settings, sha256, writable_session
from app.models import ZONES, Deal, Event, Floor, GameSession

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
app.include_router(wall.router)
app.include_router(read.router)


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


@app.post("/v1/sessions", status_code=201)
def create_session(
    body: SessionIn,
    request: Request,
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
    lib=Depends(library),
):
    pilot = resolve_pilot(body.pilot_token, body.pilot_id, request, s, cfg)
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
        clean.rebuild_derived(s, gs.session_id)
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
    clean.rebuild_derived(s, gs.session_id)
    s.commit()
    return {"ok": True, "first_finish": first}


# ---------------------------------------------------------------- floor

# What the game's simulated day reports (events.md, `stats` and `end`). Anything else
# is dropped, so no free text can arrive this way.


class BusyIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: str | None = Field(default=None, max_length=5)
    name: str = Field(max_length=64)  # the fix's name from scenarios.json, not free text
    pct: float | None = Field(default=None, ge=0, le=100)


class SimIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    use: float | None = Field(default=None, ge=0, le=100)
    happy: float | None = Field(default=None, ge=0, le=100)
    deskU: float | None = Field(default=None, ge=0, le=100)
    desks: int | None = Field(default=None, ge=0, le=100)
    peak: int | None = Field(default=None, ge=0, le=100)
    peakAt: str | None = Field(default=None, max_length=5)
    calls: float | None = Field(default=None, ge=0)
    focusH: float | None = Field(default=None, ge=0)
    lunch: float | None = Field(default=None, ge=0)
    breaks: float | None = Field(default=None, ge=0)
    joint: float | None = Field(default=None, ge=0)
    walk: float | None = Field(default=None, ge=0)
    fabN: int | None = Field(default=None, ge=0)
    fabH: float | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=0)
    occ: list[int] | None = Field(default=None, max_length=96)
    occf: list[int] | None = Field(default=None, max_length=96)
    busy: list[BusyIn] | None = Field(default=None, max_length=7)


class FloorIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    palette: str | None = Field(default=None, max_length=32)
    sim: SimIn


@app.post("/v1/sessions/{session_id}/floor")
def post_floor(body: FloorIn, gs: GameSession = Depends(writable_session), s: Session = Depends(db)):
    floor = s.get(Floor, gs.session_id)
    if floor is None:
        raise HTTPException(409, "finish the day first")
    floor.palette_id = body.palette
    floor.sim = body.sim.model_dump(exclude_none=True)
    # The addition is taken from the events (add / addRemoved), not from this body.
    q = clean.score_session(s, gs)
    s.commit()
    return {"quality": q.score if q else None}  # never shown to the player
