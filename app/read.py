"""The management read API (spec section 9).

Every call needs a logged-in viewer and is written to the audit log. Every number
says how many plays are behind it, and the group-of-10 rule is applied here, in the
API (app/privacy.py):

- a value from fewer than 10 plays comes back as "fewer than 10";
- a breakdown by employee type merges types under 10 into "Other";
- a filter combination that leaves fewer than 10 plays, or leaves out fewer than 10
  of them (so the difference would show a small group), is refused.

Preference numbers use plays that count for preferences (finished, first play of
that player, quality score 40 or more), weighted 1 or 0.5 by their score.
"""

import csv
import io
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clean
from app.config import Settings
from app.deps import db, settings, sha256
from app.models import (
    AddDecline,
    Addition,
    AuditLog,
    Edit,
    Event,
    Floor,
    GameSession,
    Moment,
    Pilot,
    Quality,
    Viewer,
    WallPost,
    WallView,
)
from app.privacy import fewer, hide, merge_small, protect

router = APIRouter(prefix="/v1/read")

TYPE_LABELS = {"none": "Prefer not to say"}
SIM_METRICS = ("use", "happy", "deskU")


# ---------------------------------------------------------------- viewer, filters, audit


@dataclass
class ReadViewer:
    viewer_id: str
    can_export: bool


def viewer(
    request: Request,
    authorization: str | None = Header(default=None),
    s: Session = Depends(db),
    cfg: Settings = Depends(settings),
) -> ReadViewer:
    if cfg.read_auth == "sso":
        who = request.headers.get(cfg.sso_header)
        if not who:
            raise HTTPException(401, "not signed in")
        return ReadViewer(who, who in cfg.read_exporters)
    key = (authorization or "").removeprefix("Bearer ").strip()
    v = s.scalar(select(Viewer).where(Viewer.token_hash == sha256(key))) if key else None
    if v is None:
        raise HTTPException(401, "not signed in")
    return ReadViewer(v.viewer_id, v.can_export)


class Filters(BaseModel):
    pilot: str | None = None
    site: str | None = None
    type: Literal["CE", "PSE", "IE", "Sales", "Support", "Lead", "none"] | None = None
    game_version: str | None = None
    flow: Literal["quick", "classic"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    by: Literal["type"] | None = None  # breakdown, not a filter

    def is_filtered(self) -> bool:
        return any(v is not None for k, v in self.model_dump().items() if k != "by")


def audit(s: Session, who: ReadViewer, endpoint: str, f: Filters, export=False, refused=False) -> None:
    s.add(AuditLog(viewer_id=who.viewer_id, endpoint=endpoint, params=f.model_dump(mode="json", exclude_none=True),
                   export=export, refused=refused))
    s.commit()


# ---------------------------------------------------------------- plays in scope


@dataclass
class Play:
    session_id: str
    anon_player_id: str
    role_type: str
    started_at: datetime
    finished_at: datetime | None
    score: int | None
    weight: float
    for_preferences: bool

    @property
    def counts_for_preferences(self) -> bool:
        return self.finished_at is not None and self.for_preferences and self.weight > 0


def load_plays(s: Session, f: Filters) -> list[Play]:
    q = (
        select(GameSession, Quality)
        .join(Pilot, Pilot.pilot_id == GameSession.pilot_id)
        .outerjoin(Quality, Quality.session_id == GameSession.session_id)
    )
    if f.pilot:
        q = q.where(GameSession.pilot_id == f.pilot)
    if f.site:
        q = q.where(Pilot.site == f.site)
    if f.type:
        q = q.where(GameSession.role_type == f.type)
    if f.game_version:
        q = q.where(GameSession.game_version == f.game_version)
    if f.flow:
        q = q.where(GameSession.flow == f.flow)
    if f.date_from:
        q = q.where(func.date(GameSession.started_at) >= f.date_from)
    if f.date_to:
        q = q.where(func.date(GameSession.started_at) <= f.date_to)
    return [
        Play(g.session_id, g.anon_player_id, g.role_type, g.started_at, g.finished_at,
             q_.score if q_ else None, q_.weight if q_ else 0.0, q_.for_preferences if q_ else False)
        for g, q_ in s.execute(q).all()
    ]


def scope(s: Session, who: ReadViewer, endpoint: str, f: Filters, cfg: Settings, preferences: bool,
          export: bool = False) -> list[Play]:
    """The plays behind this call, after the isolation check."""
    def pick(plays):
        return [p for p in plays if p.counts_for_preferences] if preferences else plays

    plays = pick(load_plays(s, f))
    if f.is_filtered():
        everyone = len(pick(load_plays(s, Filters())))
        left_out = everyone - len(plays)
        if len(plays) < cfg.min_group_size or 0 < left_out < cfg.min_group_size:
            audit(s, who, endpoint, f, export=export, refused=True)
            raise HTTPException(403, f"this filter would isolate a group of {fewer(cfg.min_group_size)} plays")
    audit(s, who, endpoint, f, export=export)
    return plays


def meta(s: Session, plays: list[Play], cfg: Settings) -> dict:
    return {
        "plays": len(plays),
        "updated_at": s.scalar(select(func.max(Quality.computed_at))),
        "sample": False,
        "min_group": cfg.min_group_size,
    }


# ---------------------------------------------------------------- helpers


def weighted_shares(items: list[tuple[str, float]], keys) -> dict[str, float]:
    total = sum(w for _, w in items)
    return {k: round(sum(w for v, w in items if v == k) / total, 3) if total else 0.0 for k in keys}


def weighted_rate(items: list[tuple[bool, float]]) -> float:
    total = sum(w for _, w in items)
    return round(sum(w for hit, w in items if hit) / total, 3) if total else 0.0


def breakdown(rows: list, type_of: Callable, compute: Callable, f: Filters, mg: int) -> dict:
    """`compute(rows)` for all rows, and per employee type when `by=type`, with the rule
    applied: n is the number of plays behind each value."""
    n = len(rows)
    out = {"n": hide(n, n, mg), "value": hide(n, compute(rows) if n else None, mg)}
    if f.by == "type":
        groups = merge_small(Counter(type_of(r) for r in rows), mg)
        if groups is None:
            out["by_type"] = fewer(mg)
        else:
            out["by_type"] = []
            for label, keys in groups.items():
                sub = [r for r in rows if type_of(r) in keys]
                out["by_type"].append({"type": TYPE_LABELS.get(label, label), "n": len(sub), "value": compute(sub)})
    return out


def agreement(shares: dict[str, float]) -> str:
    top = sorted(shares.values(), reverse=True)
    if top and top[0] >= 0.6:
        return "consensus"
    if len(top) > 1 and top[0] - top[1] <= 0.1:
        return "contested"
    return "mixed"


def moments_of(s: Session, plays: list[Play]) -> list[Moment]:
    ids = [p.session_id for p in plays]
    return s.scalars(select(Moment).where(Moment.session_id.in_(ids), Moment.chosen.is_not(None))).all() if ids else []


def options_of(scenario_id: int | None) -> list[str]:
    lib = clean.library(str(settings().scenarios_path))
    sc = lib.get(scenario_id)
    return [o.get("k") for o in sc.fixes if o.get("k")] if sc and sc.fixes else ["a", "b", "c", "d"]


def percentiles(values: list[float]) -> dict:
    values = sorted(values)
    if len(values) == 1:
        return {"p25": values[0], "median": values[0], "p75": values[0]}
    q = statistics.quantiles(values, n=4, method="inclusive")
    return {"p25": round(q[0], 1), "median": round(statistics.median(values), 1), "p75": round(q[2], 1)}


# ---------------------------------------------------------------- endpoints


@router.get("/summary")
def summary(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "summary", f, cfg, preferences=False)
    mg, n = cfg.min_group_size, len(plays)
    finished = [p for p in plays if p.finished_at]
    durations = [(p.finished_at - p.started_at).total_seconds() for p in finished]
    mix = Counter(
        "unscored" if p.score is None else "full" if p.weight == 1 else "half" if p.weight == 0.5 else "dropped"
        for p in finished
    )
    return {
        **meta(s, plays, cfg),
        "finished_pct": hide(n, round(100 * len(finished) / n) if n else None, mg),
        "median_play_s": hide(len(finished), round(statistics.median(durations)) if durations else None, mg),
        "quality_mix": protect({k: mix.get(k, 0) for k in ("full", "half", "dropped", "unscored")}, mg),
    }


@router.get("/funnel")
def funnel(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "funnel", f, cfg, preferences=False)
    mg = cfg.min_group_size
    ids = [p.session_id for p in plays]
    decided = Counter(m.session_id for m in moments_of(s, plays))
    floors = s.scalars(select(Floor).where(Floor.session_id.in_(ids))).all() if ids else []
    rendered = set(s.scalars(select(Event.session_id).where(Event.session_id.in_(ids), Event.ev == "render_view"))) if ids else set()
    shared = set(s.scalars(select(Event.session_id).where(Event.session_id.in_(ids), Event.ev == "wall_post"))) if ids else set()
    shared |= set(s.scalars(select(WallPost.session_id).where(WallPost.session_id.in_(ids)))) if ids else set()
    anons = {p.anon_player_id for p in plays}
    viewers = set(s.scalars(select(WallView.viewer_anon_id).where(WallView.viewer_anon_id.in_(anons)))) if anons else set()
    steps = {
        "started": len(plays),
        # The employee type is chosen before the deal, at session start.
        "type_set": len(plays),
        **{f"moment_{k}": sum(1 for p in plays if decided[p.session_id] >= k) for k in range(1, 8)},
        "reached_18": sum(1 for p in plays if p.finished_at),
        "palette": sum(1 for fl in floors if fl.palette_id),
        "render": len(rendered),
        "wall": len(shared),
        "wall_views": sum(1 for p in plays if p.anon_player_id in viewers),
    }
    return {**meta(s, plays, cfg), "steps": {k: hide(v, v, mg) for k, v in steps.items()}}


def picks_table(s: Session, plays: list[Play], f: Filters, mg: int) -> list[dict]:
    by_id = {p.session_id: p for p in plays}
    rows = []
    by_scenario: dict[int, list[Moment]] = {}
    for m in moments_of(s, plays):
        by_scenario.setdefault(m.scenario_id, []).append(m)
    for sid, ms in sorted(by_scenario.items(), key=lambda kv: (kv[0] is None, kv[0])):
        keys = options_of(sid) + ["skip"]

        def compute(sub, keys=keys):
            shares = weighted_shares([(m.chosen, by_id[m.session_id].weight) for m in sub], keys)
            return {"shares": shares, "agreement": agreement({k: v for k, v in shares.items() if k != "skip"})}

        rows.append({"scenario_id": sid, "zone": ms[0].zone,
                     **breakdown(ms, lambda m: by_id[m.session_id].role_type, compute, f, mg)})
    return rows


@router.get("/picks")
def picks(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "picks", f, cfg, preferences=True)
    return {**meta(s, plays, cfg), "scenarios": picks_table(s, plays, f, cfg.min_group_size)}


@router.get("/skips")
def skips(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "skips", f, cfg, preferences=True)
    mg = cfg.min_group_size
    by_id = {p.session_id: p for p in plays}
    ms = moments_of(s, plays)

    def rate(sub):
        return weighted_rate([(m.chosen == "skip", by_id[m.session_id].weight) for m in sub])

    def type_of(m):
        return by_id[m.session_id].role_type

    def group(key):
        out: dict = {}
        for m in ms:
            out.setdefault(key(m), []).append(m)
        return sorted(out.items(), key=lambda kv: (kv[0] is None, kv[0]))

    return {
        **meta(s, plays, cfg),
        "scenarios": [{"scenario_id": k, **breakdown(v, type_of, rate, f, mg)} for k, v in group(lambda m: m.scenario_id)],
        "zones": [{"zone": k, **breakdown(v, type_of, rate, f, mg)} for k, v in group(lambda m: m.zone)],
    }


@router.get("/edits")
def edits(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "edits", f, cfg, preferences=True)
    mg = cfg.min_group_size
    by_id = {p.session_id: p for p in plays}
    ids = list(by_id)
    edit_rows = s.scalars(select(Edit).where(Edit.session_id.in_(ids))).all() if ids else []
    edited = {(e.session_id, e.zone): e for e in edit_rows}  # the last edit of a zone wins
    by_scenario: dict[int, list[Moment]] = {}
    for m in moments_of(s, plays):
        by_scenario.setdefault(m.scenario_id, []).append(m)
    out = []
    for sid, ms in sorted(by_scenario.items(), key=lambda kv: (kv[0] is None, kv[0])):
        changes = [edited[(m.session_id, m.zone)] for m in ms if (m.session_id, m.zone) in edited]
        moves = Counter(f"{e.from_option}->{e.to_option}" for e in changes)
        out.append({
            "scenario_id": sid,
            **breakdown(ms, lambda m: by_id[m.session_id].role_type,
                        lambda sub: weighted_rate([((m.session_id, m.zone) in edited, by_id[m.session_id].weight)
                                                   for m in sub]), f, mg),
            "edits": len(changes) if len(changes) >= mg else fewer(mg),
            "from_to": hide(len(changes), {k: round(v / len(changes), 3) for k, v in moves.items()} if changes else None, mg),
        })
    return {**meta(s, plays, cfg), "scenarios": out}


@router.get("/additions")
def additions(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "additions", f, cfg, preferences=True)
    mg = cfg.min_group_size
    by_id = {p.session_id: p for p in plays}
    ids = list(by_id)
    adds = s.scalars(select(Addition).where(Addition.session_id.in_(ids), Addition.removed.is_(False))).all() if ids else []
    declined = set(s.scalars(select(AddDecline.session_id).where(AddDecline.session_id.in_(ids)))) if ids else set()
    added = {a.session_id for a in adds}

    def type_of(r):
        return by_id[r.session_id].role_type

    def top_items(sub):
        counts = Counter(a.item_id for a in sub)
        groups = merge_small(counts, mg) or {"Other": list(counts)}
        return sorted(({"item": g, "count": sum(counts[k] for k in ks)} for g, ks in groups.items()),
                      key=lambda r: (r["item"] == "Other", -r["count"], r["item"]))

    return {
        **meta(s, plays, cfg),
        "top_items": breakdown(adds, type_of, top_items, f, mg),
        # How the room was made: an empty space, the end card, smaller or shared desks, or a swapped fix.
        "how_placed": breakdown(adds, type_of, lambda sub: weighted_shares(
            [(a.how_placed, by_id[a.session_id].weight) for a in sub],
            ("gap", "end", "desks_smaller", "desks_share", "swap")), f, mg),
        "decline_rate": breakdown(plays, lambda p: p.role_type, lambda sub: weighted_rate(
            [(p.session_id in declined and p.session_id not in added, p.weight) for p in sub]), f, mg),
    }


@router.get("/sim")
def sim(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    plays = scope(s, who, "sim", f, cfg, preferences=True)
    by_id = {p.session_id: p for p in plays}
    ids = list(by_id)
    floors = [fl for fl in (s.scalars(select(Floor).where(Floor.session_id.in_(ids))).all() if ids else []) if fl.sim]

    def dist(sub):
        return {k: percentiles(vals) if (vals := [fl.sim[k] for fl in sub if fl.sim.get(k) is not None]) else None
                for k in SIM_METRICS}

    return {**meta(s, plays, cfg),
            "distributions": breakdown(floors, lambda fl: by_id[fl.session_id].role_type, dist, f, cfg.min_group_size)}


@router.get("/export.csv")
def export_csv(f: Filters = Depends(), who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    """The aggregated picks table. Never raw events."""
    if not who.can_export:
        audit(s, who, "export.csv", f, export=True, refused=True)
        raise HTTPException(403, "this viewer may not export")
    plays = scope(s, who, "export.csv", f, cfg, preferences=True, export=True)
    f = f.model_copy(update={"by": "type"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["scenario_id", "zone", "group", "plays", "option", "share"])
    for row in picks_table(s, plays, f, cfg.min_group_size):
        groups = [{"type": "All", "n": row["n"], "value": row["value"]}]
        groups += row["by_type"] if isinstance(row["by_type"], list) else []
        for g in groups:
            if isinstance(g["value"], dict):
                for opt, share in g["value"]["shares"].items():
                    w.writerow([row["scenario_id"], row["zone"], g["type"], g["n"], opt, share])
            else:
                w.writerow([row["scenario_id"], row["zone"], g["type"], g["n"], "", g["value"]])
    return Response(buf.getvalue(), media_type="text/csv")
