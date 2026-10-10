"""Request dependencies shared by the ingest, wall and read APIs."""

import hashlib
import secrets
from datetime import date, datetime, timezone

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clean
from app.config import Settings, get_settings
from app.db import get_engine, make_sessionmaker
from app.models import GameSession, Pilot
from app.ratelimit import limit_pilot


def settings() -> Settings:
    return get_settings()


def db(cfg: Settings = Depends(settings)):
    with make_sessionmaker(get_engine(cfg.database_url))() as s:
        yield s


def library(cfg: Settings = Depends(settings)):
    return clean.library(str(cfg.scenarios_path))


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_pilot(
    pilot_token: str | None, pilot_id: str | None, request: Request, s: Session, cfg: Settings
) -> Pilot:
    """The pilot a player belongs to: by invite token, or by pilot id behind SSO."""
    if cfg.access_mode == "sso":
        if not request.headers.get(cfg.sso_header):
            raise HTTPException(401, "not signed in")
        pilot = s.get(Pilot, pilot_id) if pilot_id else None
    else:
        pilot = (
            s.scalar(select(Pilot).where(Pilot.invite_token_hash == sha256(pilot_token)))
            if pilot_token
            else None
        )
    if pilot is None:
        raise HTTPException(401, "unknown pilot")
    today = date.today()
    if (pilot.start_date and today < pilot.start_date) or (pilot.end_date and today > pilot.end_date):
        raise HTTPException(403, "pilot is not running")
    limit_pilot(pilot.pilot_id, cfg)
    return pilot


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
    limit_pilot(gs.pilot_id)
    return gs
