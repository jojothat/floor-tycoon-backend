"""Rate limits per IP and per pilot token (spec section 10).

Fixed one-minute windows counted in Postgres, so the limit holds across both API
instances. IP addresses are never stored: the key is an HMAC of the address with
RATE_LIMIT_SECRET, and counter rows are deleted by the 15-minute job.

Everyone at a site shares one invite link and often one office IP (NAT), so the
defaults are generous; 0 turns a limit off.
"""

import hashlib
import hmac
import math
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_engine
from app.models import RateCounter

WINDOW_S = 60


def now() -> float:
    return time.time()


def _hit(key: str, limit: int, cfg: Settings) -> int | None:
    """Count one request against `key`. Returns seconds to wait if over the limit."""
    if limit <= 0:
        return None
    t = now()
    window = int(t // WINDOW_S)
    stmt = (
        insert(RateCounter)
        .values(key=key, window=window, count=1)
        .on_conflict_do_update(index_elements=[RateCounter.key, RateCounter.window],
                               set_={"count": RateCounter.count + 1})
        .returning(RateCounter.count)
    )
    with get_engine(cfg.database_url).begin() as conn:
        count = conn.execute(stmt).scalar_one()
    return max(1, math.ceil((window + 1) * WINDOW_S - t)) if count > limit else None


def client_ip(request: Request, cfg: Settings) -> str:
    if cfg.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def ip_key(ip: str, cfg: Settings) -> str:
    return "ip:" + hmac.new(cfg.rate_limit_secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32]


async def limit_by_ip(request: Request, call_next):
    """Middleware: every /v1 request counts against its IP."""
    if request.url.path.startswith("/v1/"):
        cfg = get_settings()
        wait = _hit(ip_key(client_ip(request, cfg), cfg), cfg.rate_limit_ip_per_min, cfg)
        if wait is not None:
            return JSONResponse({"detail": "too many requests"}, status_code=429, headers={"Retry-After": str(wait)})
    return await call_next(request)


def limit_pilot(pilot_id: str, cfg: Settings | None = None) -> None:
    """Count one request against the pilot (its shared invite link)."""
    cfg = cfg or get_settings()
    wait = _hit(f"pilot:{pilot_id}", cfg.rate_limit_pilot_per_min, cfg)
    if wait is not None:
        raise HTTPException(429, "too many requests for this pilot", headers={"Retry-After": str(wait)})


def prune(s: Session) -> None:
    """Drop finished windows (called by the 15-minute job)."""
    s.execute(delete(RateCounter).where(RateCounter.window < int(now() // WINDOW_S) - 1))
