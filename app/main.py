"""Ingest API, build step 1 (spec section 5)."""

import secrets
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import clean
from app import deal as deal_fn
from app import ratelimit, read, wall
from app.config import Settings, get_settings
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



def check_production(cfg: Settings) -> None:
    """Refuse to start in production with a shared or default credential (READOUT.md §2)."""
    if cfg.app_env != "production":
        return
    if cfg.read_token:
        raise RuntimeError("READ_TOKEN is for dev and staging only: use named viewers or SSO in production")
    if cfg.rate_limit_secret == "change-me":
        raise RuntimeError("set RATE_LIMIT_SECRET in production")


check_production(get_settings())
app = FastAPI(title="Floor Tycoon backend")
app.middleware("http")(ratelimit.limit_by_ip)
# Added last, so it wraps everything: preflights are answered before the rate limit,
# and a 429 still carries the CORS headers the game needs to read it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(get_settings().cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-FT-Player", "X-Pilot-Token", "X-Pilot-Id", "X-Anon-Player"],
    expose_headers=["Retry-After"],
    allow_credentials=False,  # no cookies: the API uses tokens in headers
    max_age=600,
)
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
    viewport_height: int | None = Field(default=None, ge=0, le=10000,
                                        validation_alias=AliasChoices("viewport_height", "viewport_h"))
    # Made on the phone. The game retries a failed start with the same body, so the
    # same id from the same player returns the same session.
    client_session_id: str | None = Field(default=None, min_length=8, max_length=64)


def viewport_bucket(h: int | None) -> str | None:
    """Bucket the height so short-screen drop-off can be checked without storing exact sizes."""
    if h is None:
        return None
    for top in (300, 400, 500, 700, 900):
        if h < top:
            return f"<{top}"
    return "900+"


@app.get("/healthz")
def healthz(s: Session = Depends(db)):
    """For the container health check and load balancers: the API is up and can reach the database."""
    s.execute(text("SELECT 1"))
    return {"ok": True}


@app.post("/v1/sessions", status_code=201)
def create_session(
    body: SessionIn,
    request: Request,
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
    lib=Depends(library),
):
    pilot = resolve_pilot(body.pilot_token, body.pilot_id, request, s, cfg)
    if body.client_session_id:
        again = s.scalar(select(GameSession).where(GameSession.pilot_id == pilot.pilot_id,
                                                   GameSession.client_session_id == body.client_session_id))
        if again is not None:
            return retried_start(s, again, body, cfg)
    role = body.role_type or "none"
    dealt = deal_fn.deal(lib, role, team_grows_id=cfg.deal_z7_scenario_id)
    session_id = secrets.token_urlsafe(18)
    write_key = secrets.token_urlsafe(32)
    expires = now() + timedelta(minutes=cfg.write_key_ttl_minutes)
    s.add(
        GameSession(
            session_id=session_id,
            client_session_id=body.client_session_id,
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
    try:
        s.commit()
    except IntegrityError:
        # Two copies of the same start raced; the other one won.
        s.rollback()
        again = s.scalar(select(GameSession).where(GameSession.pilot_id == pilot.pilot_id,
                                                   GameSession.client_session_id == body.client_session_id))
        if again is None:
            raise
        return retried_start(s, again, body, cfg)
    return {
        "session_id": session_id,
        "deal": [d.id for d in dealt],
        "zones": [d.zone for d in dealt],
        "write_key": write_key,
        "write_key_expires_at": expires.isoformat(),
    }


def retried_start(s: Session, gs: GameSession, body: SessionIn, cfg: Settings) -> dict:
    """The same start sent again (the phone lost the first answer). Same session and deal;
    a new write key, because only the key's hash was kept. Refused for anyone else."""
    if gs.anon_player_id != body.anon_player_id:
        raise HTTPException(409, "client_session_id already used")
    write_key = secrets.token_urlsafe(32)
    gs.write_key_hash = sha256(write_key)
    gs.write_key_expires_at = now() + timedelta(minutes=cfg.write_key_ttl_minutes)
    d = s.get(Deal, gs.session_id)
    s.commit()
    return {
        "session_id": gs.session_id,
        "deal": list(d.scenario_ids),
        "zones": list(d.zones),
        "write_key": write_key,
        "write_key_expires_at": gs.write_key_expires_at.isoformat(),
    }


# ---------------------------------------------------------------- events


class EventIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int = Field(ge=1)
    ev: str = Field(min_length=1, max_length=32)
    t: int = Field(ge=0)
    m: str | None = None

    @field_validator("t", mode="before")
    @classmethod
    def whole_ms(cls, v):
        # The browser's clock can give fractions of a millisecond.
        return round(v) if isinstance(v, float) else v

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

    # The game sends "picks"; "final_picks" is the spec's name. Either works.
    final_picks: dict[str, str] = Field(validation_alias=AliasChoices("final_picks", "picks"))
    # Zones ({"z4": "none"} from the game, or a list), or a count. Picks of "0" are the source of truth.
    skips: dict[str, str] | list[str] | int | None = None

    @field_validator("final_picks")
    @classmethod
    def picks(cls, v):
        return check_picks(v)


def check_picks(v: dict) -> dict[str, str]:
    """zone -> a-d, or "0" for as it was."""
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
    # The game's names (desk_use, peak_at, occ_fab, fab_people) and events.md's both work;
    # they are stored under events.md's.
    deskU: float | None = Field(default=None, ge=0, le=100, validation_alias=AliasChoices("deskU", "desk_use"))
    desks: int | None = Field(default=None, ge=0, le=100)
    peak: int | None = Field(default=None, ge=0, le=100)
    peakAt: str | None = Field(default=None, max_length=5, validation_alias=AliasChoices("peakAt", "peak_at"))
    calls: float | None = Field(default=None, ge=0)
    focusH: float | None = Field(default=None, ge=0)
    lunch: float | None = Field(default=None, ge=0)
    breaks: float | None = Field(default=None, ge=0)
    joint: float | None = Field(default=None, ge=0)
    walk: float | None = Field(default=None, ge=0)
    fabN: int | None = Field(default=None, ge=0, validation_alias=AliasChoices("fabN", "fab_people"))
    fabH: float | None = Field(default=None, ge=0)
    seats: int | None = Field(default=None, ge=0)
    # People on the floor and at the fab every 15 min. A missed sample can arrive as null
    # (the game's OCC array can have holes, which JSON writes as null).
    occ: list[int | None] | None = Field(default=None, max_length=96)
    occf: list[int | None] | None = Field(default=None, max_length=96, validation_alias=AliasChoices("occf", "occ_fab"))
    busy: list[BusyIn] | None = Field(default=None, max_length=7)


ITEM_ID = r"^[a-z0-9_-]{1,32}$"  # furniture ids, never free text


ItemId = Annotated[str, Field(pattern=ITEM_ID)]


class DeskFitIn(BaseModel):
    """The team desks changed to make room: {"mode": "smaller"|"share", "add": item, "k": the z7 fix}."""
    model_config = ConfigDict(extra="ignore")

    mode: Literal["smaller", "share"]
    add: ItemId
    k: Literal["a", "b", "c", "d", "0"]  # "0": the team desks were left as they were (z7 skipped)


class AdditionIn(BaseModel):
    """The end-of-day addition as the game draws it on the wall: {"adds": {zone: item},
    "deskfit": DeskFitIn or null, "swaps": {zone: item that replaced the fix}}."""
    model_config = ConfigDict(extra="ignore")

    adds: dict[str, ItemId] = Field(default_factory=dict)
    deskfit: DeskFitIn | None = None
    swaps: dict[str, ItemId] = Field(default_factory=dict)

    @field_validator("adds", "swaps")
    @classmethod
    def zones(cls, v):
        if any(z not in ZONES for z in v):
            raise ValueError("keys must be z1-z7")
        return v


class FloorIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The game sends its palette number (1-10) and its name; a name alone also works.
    palette: int | str | None = None
    palette_name: str | None = Field(default=None, max_length=32)
    # The floor after any end-of-day edits; when sent, it replaces what finish sent.
    picks: dict[str, str] | None = None
    addition: AdditionIn | None = None
    sim: SimIn

    @field_validator("palette")
    @classmethod
    def palette_ok(cls, v):
        if isinstance(v, int) and not 0 <= v <= 99:
            raise ValueError("palette number out of range")
        if isinstance(v, str) and len(v) > 32:
            raise ValueError("palette name too long")
        return v

    @field_validator("picks")
    @classmethod
    def picks_ok(cls, v):
        return check_picks(v) if v is not None else v


@app.post("/v1/sessions/{session_id}/floor")
def post_floor(body: FloorIn, gs: GameSession = Depends(writable_session), s: Session = Depends(db)):
    floor = s.get(Floor, gs.session_id)
    if floor is None:
        raise HTTPException(409, "finish the day first")
    floor.palette_id = None if body.palette is None else str(body.palette)
    floor.palette_name = body.palette_name or (body.palette if isinstance(body.palette, str) else None)
    floor.sim = body.sim.model_dump(exclude_none=True)
    if body.picks:
        floor.final_picks = body.picks
        floor.skips = sorted(z for z, o in body.picks.items() if o == "0")
    # What the wall draws. The additions table is still built from the events.
    if body.addition is not None:
        floor.wall_addition = body.addition.model_dump()
    q = clean.score_session(s, gs)
    s.commit()
    return {"quality": q.score if q else None}  # never shown to the player
