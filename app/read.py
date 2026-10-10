"""The management read API, v2: what the Space Readout (readout/) reads.

The contract is readout/READOUT.md, and readout/mock-api/read.mjs is its reference:
every endpoint here returns what the mock returns for the same plays
(tests/test_read_parity.py checks it). The numbers come from the derived tables
(sessions, deals, moments, edits, add_declines, floor, wall_posts, wall_views) plus
per-session aggregates of the event table; raw events are never replayed per request.

The group-of-10 rule is applied here, never in the page:
- a share, average or median over fewer than 10 games is null;
- a whole group under 10 comes back as {"n": n, "lt10": true};
- lists drop entries under 10;
- a funnel step under 10, or within 10 of the starts or the finished count, has a null count;
  any other step is rounded to 10 unless it equals the shown starts or finishes;
- the start count is null when fewer than 10 of the started games are unfinished;
- a filter that leaves out fewer than 10 games (so subtracting two views would show
  a small group) suppresses every number in the response.

Every call needs a viewer (READ_TOKEN in dev and staging, named viewers, or SSO) and is
written to the audit log.
"""

import json
import math
import re
import secrets
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity, clean
from app.config import Settings
from app.deps import db, settings, sha256
from app.models import (
    AddDecline,
    AuditLog,
    Deal,
    Edit,
    Event,
    Floor,
    GameSession,
    Moment,
    Quality,
    Viewer,
    WallPost,
    WallView,
)

router = APIRouter(prefix="/v1/read")

TYPES = ["CE", "PSE", "IE", "Sales", "Support", "Lead"]
HOW = ["gap", "end", "desks_smaller", "desks_share", "swap"]
NO_GROUP = 10**9  # the minimum group size when a filter would isolate a small group


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
    if not key:
        raise HTTPException(401, "viewer token required")
    if cfg.read_token and secrets.compare_digest(key, cfg.read_token):
        return ReadViewer("read-token", True)
    v = s.scalar(select(Viewer).where(Viewer.token_hash == sha256(key)))
    if v is None:
        raise HTTPException(401, "viewer token required")
    return ReadViewer(v.viewer_id, v.can_export)


class Filters(BaseModel):
    type: Literal["CE", "PSE", "IE", "Sales", "Support", "Lead"] | None = None
    version: str | None = None
    flow: Literal["quick", "classic"] | None = None
    # "from" is a Python keyword, so the query names are mapped in filters() below.
    from_: date | None = None
    to: date | None = None
    synthetic: Literal["include", "only"] | None = None
    pilot: str | None = None  # not in READOUT.md: one pilot when the store holds several

    @field_validator("version")
    @classmethod
    def short(cls, v):
        if v is not None and len(v) > 64:
            raise ValueError("too long")
        return v

    def narrows(self) -> bool:
        """Filters that pick out part of the plays (not the synthetic or pilot scope)."""
        return any(v is not None for v in (self.type, self.version, self.flow, self.from_, self.to))


def filters(request: Request) -> Filters:
    q = dict(request.query_params)
    if "from" in q:
        q["from_"] = q.pop("from")
    try:
        return Filters.model_validate({k: v for k, v in q.items() if k in Filters.model_fields and v != ""})
    except Exception as e:  # pydantic's message, as a 422
        raise HTTPException(422, str(e).splitlines()[0])


def audit(s: Session, who: ReadViewer, endpoint: str, f: Filters, export=False) -> None:
    params = f.model_dump(mode="json", exclude_none=True)
    s.add(AuditLog(viewer_id=who.viewer_id, endpoint=endpoint, params=params, export=export))
    s.commit()
    if endpoint in ("summary", "export.csv"):  # one line per readout load, not one per chart
        shown = ", ".join(f"{k}={v}" for k, v in params.items()) or "everyone"
        done = s.scalar(select(func.count()).select_from(GameSession).where(GameSession.finished_at.is_not(None))) \
            if activity.on() else 0
        activity.say(None, f"[readout]  {who.viewer_id} {'downloaded the CSV export' if export else 'opened the readout'}"
                           f" ({shown}) · read from the database: {done} finished games")


# ---------------------------------------------------------------- numbers, as the mock writes them


def js_round(x: float) -> int:
    """JavaScript's Math.round (halves go up), so shares match the mock to the last digit."""
    return math.floor(x + 0.5)


def num(x):
    """1.0 -> 1, so integral numbers print as the mock prints them."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def jsum(xs) -> float:
    """Left-to-right float addition, as JavaScript's reduce does. Python 3.12's sum() compensates
    for rounding, which can tip a share across a .05 boundary away from the mock's."""
    total = 0
    for x in xs:
        total += x
    return total


def median(xs):
    return num(statistics.median(xs)) if xs else None


@dataclass
class Rules:
    mg: int  # the minimum group size for this response

    def pct(self, a, b):
        return num(js_round(a / b * 1000) / 10) if b >= self.mg else None

    def sup(self, n, obj):
        return obj if n >= self.mg else {"n": n, "lt10": True}


# ---------------------------------------------------------------- one record per play


@dataclass
class Rec:
    id: str
    type: str
    version: str
    flow: str
    created_ms: int
    completed: bool
    synthetic: bool
    play_s: int
    played: int
    reached: dict
    by_zone: dict  # zone -> scenario (deal order)
    picks: dict
    edits: list = field(default_factory=list)  # [(zone, from, to)] in order
    add: dict | None = None
    declined: bool = False
    sim: dict | None = None
    palette: str | None = None
    decide: dict = field(default_factory=dict)  # zone -> choose_ms
    counts_for_preferences: bool = True

    def final_of(self, zone: str) -> str:
        p = self.picks.get(zone)
        return p if p and p != "0" else "skip"


@lru_cache
def _json(path: str):
    return json.loads(open(path, encoding="utf-8").read())


def catalogue(cfg: Settings):
    lib = clean.library(str(cfg.scenarios_path))
    items = {i["id"]: i for i in _json(str(cfg.items_path))}
    return lib, items, _json(str(cfg.palettes_path))


def load(s: Session, cfg: Settings, pilot: str | None) -> list[Rec]:
    """Every play in the store (or one pilot), oldest first, from the derived tables."""
    lib, _, _ = catalogue(cfg)
    q = select(GameSession).order_by(GameSession.started_at, GameSession.session_id)
    if pilot:
        q = q.where(GameSession.pilot_id == pilot)
    sessions = s.scalars(q).all()
    ids = [g.session_id for g in sessions]
    if not ids:
        return []

    agg = {
        r.session_id: r
        for r in s.execute(
            select(
                Event.session_id,
                func.bool_or(Event.ev == "role_set").label("role"),
                func.bool_or(Event.ev == "palette_set").label("palette"),
                func.bool_or(Event.ev == "render_view").label("render"),
                func.bool_or(Event.ev == "wall_post").label("wall"),
                func.count(func.distinct(Event.m)).filter(Event.ev.in_(("keep", "skip"))).label("played"),
            ).where(Event.session_id.in_(ids)).group_by(Event.session_id)
        )
    }
    # The client time of each play's last event (by seq): its play length.
    last_t = dict(s.execute(
        select(Event.session_id, Event.t).where(Event.session_id.in_(ids))
        .distinct(Event.session_id).order_by(Event.session_id, Event.seq.desc())
    ).all())
    played_deal = {
        sid: [int(i) for i in (ids_ or [])]
        for sid, ids_ in s.execute(
            select(Event.session_id, Event.payload["ids"]).where(Event.session_id.in_(ids), Event.ev == "deal")
            .distinct(Event.session_id).order_by(Event.session_id, Event.seq)
        )
    }
    server_deal = {d.session_id: list(d.scenario_ids) for d in s.scalars(select(Deal).where(Deal.session_id.in_(ids)))}
    floors = {f.session_id: f for f in s.scalars(select(Floor).where(Floor.session_id.in_(ids)))}
    edits: dict[str, list] = {}
    for e in s.scalars(select(Edit).where(Edit.session_id.in_(ids)).order_by(Edit.id)):
        edits.setdefault(e.session_id, []).append((e.zone, e.from_option, e.to_option))
    decide: dict[str, dict] = {}
    for sid, zone, ms in s.execute(select(Moment.session_id, Moment.zone, Moment.choose_ms).where(Moment.session_id.in_(ids))):
        decide.setdefault(sid, {})[zone] = ms
    declined = set(s.scalars(select(AddDecline.session_id).where(AddDecline.session_id.in_(ids))))
    excluded = set()
    if cfg.read_quality == "on":
        good = {q.session_id for q in s.scalars(select(Quality).where(Quality.session_id.in_(ids)))
                if q.for_preferences and q.weight > 0}
        excluded = set(ids) - good

    recs = []
    for g in sessions:
        sid = g.session_id
        a = agg.get(sid)
        deal_ids = played_deal.get(sid) or server_deal.get(sid) or []
        by_zone = {}
        for i in deal_ids:
            sc = lib.get(i)
            if sc is not None:
                by_zone[sc.zone] = sc
        fl = floors.get(sid)
        version = g.game_version or "dev"
        recs.append(Rec(
            id=sid,
            type=g.role_type if g.role_type in TYPES else "none",
            version=version,
            flow=g.flow or "quick",
            created_ms=int(g.started_at.timestamp() * 1000),
            completed=g.finished_at is not None,
            synthetic=version.startswith("synthetic"),
            play_s=js_round(last_t.get(sid, 0) / 1000),
            played=a.played if a else 0,
            reached={
                "started": True,
                "role": bool(a and a.role) or bool(g.role_type),
                "end": g.finished_at is not None,
                "palette": bool(a and a.palette),
                "render": bool(a and a.render),
                "wall": bool(g.posted_to_wall) or bool(a and a.wall),
            },
            by_zone=by_zone,
            picks=(fl.final_picks if fl else None) or {},
            edits=edits.get(sid, []),
            add=fl.addition if fl else None,
            declined=sid in declined,
            sim=fl.sim if fl else None,
            palette=fl.palette_name if fl else None,
            decide=decide.get(sid, {}),
            counts_for_preferences=sid not in excluded,
        ))
    return recs


_cache: dict[str | None, tuple[float, datetime, list[Rec]]] = {}
_cache_lock = threading.Lock()


def cached_load(s: Session, cfg: Settings, pilot: str | None) -> tuple[datetime, list[Rec]]:
    """The plays, reused for READ_CACHE_SECONDS; returns when they were loaded too."""
    if cfg.read_cache_seconds <= 0:
        return datetime.now(timezone.utc), load(s, cfg, pilot)
    with _cache_lock:  # a burst of calls (the readout's 13) waits for one load
        hit = _cache.get(pilot)
        if hit and time.monotonic() - hit[0] < cfg.read_cache_seconds:
            return hit[1], hit[2]
        loaded_at, recs = datetime.now(timezone.utc), load(s, cfg, pilot)
        _cache[pilot] = (time.monotonic(), loaded_at, recs)
        return loaded_at, recs


def keep(r: Rec, f: Filters) -> bool:
    if f.type and r.type != f.type:
        return False
    if f.flow and r.flow != f.flow:
        return False
    if f.version and r.version != f.version:
        return False
    if f.from_ and r.created_ms < _ms(f.from_):
        return False
    if f.to and r.created_ms > _ms(f.to) + 86_400_000:
        return False
    return in_synthetic_scope(r, f)


def in_synthetic_scope(r: Rec, f: Filters) -> bool:
    if f.synthetic == "only":
        return r.synthetic
    return f.synthetic == "include" or not r.synthetic


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------- the endpoints


@dataclass
class Ctx:
    all: list[Rec]
    R: list[Rec]  # filtered plays
    done: list[Rec]  # filtered completed plays (that count)
    rules: Rules
    lib: dict
    items: dict
    palettes: list
    s: Session
    computed_at: datetime

    @property
    def N(self):
        return len(self.done)

    @property
    def started(self):
        # started minus completed would show the unfinished games, so a gap under 10 hides it
        return len(self.R) if not 0 < len(self.R) - self.N < self.rules.mg else None

    def base(self):
        # updated_at: when these numbers were computed (READOUT.md §4.7)
        return {"n": self.N, "updated_at": self.computed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")}

    def name(self, sid: int, k: str) -> str:
        if k == "skip":
            return "Skipped"
        sc = self.lib.get(sid)
        o = next((o for o in (sc.fixes if sc else ()) if o.get("k") == k), None)
        return (o and o.get("name")) or k


def context(s: Session, cfg: Settings, f: Filters) -> Ctx:
    lib, items, palettes = catalogue(cfg)
    computed_at, every = cached_load(s, cfg, f.pilot)
    R = [r for r in every if keep(r, f)]
    done = [r for r in R if r.completed and r.counts_for_preferences]
    mg = cfg.min_group_size
    if f.narrows():
        # Left-out plays under 10 would show up as the difference between two views.
        scope = [r for r in every if in_synthetic_scope(r, f)]
        scope_done = [r for r in scope if r.completed and r.counts_for_preferences]
        if 0 < len(scope) - len(R) < mg or 0 < len(scope_done) - len(done) < mg:
            mg = NO_GROUP
    return Ctx(every, R, done, Rules(mg), lib, items, palettes, s, computed_at)


def per_scenario(c: Ctx) -> list[dict]:
    out = []
    for sid, sc in c.lib.items():
        rows = [r for r in c.done if (z := r.by_zone.get(sc.zone)) is not None and z.id == sid]
        n = len(rows)
        if not n:
            continue
        counts: dict[str, int] = {}
        for r in rows:
            k = r.final_of(sc.zone)
            counts[k] = counts.get(k, 0) + 1
        opts = [o for o in sc.fixes]
        top = max([0] + [counts.get(o["k"], 0) for o in opts])
        times = [r.decide[sc.zone] / 1000 for r in rows if r.decide.get(sc.zone) is not None]
        edited = [r for r in rows if any(e[0] == sc.zone for e in r.edits)]
        row = {"id": sid, "zone": sc.zone, "time": sc.time, "question": sc.question, "theme": sc.theme, "n": n}
        if n < c.rules.mg:
            row["lt10"] = True
        row.update({
            "options": [{"k": o["k"], "name": o.get("name"), "share": c.rules.pct(counts.get(o["k"], 0), n)} for o in opts],
            "skip_share": c.rules.pct(counts.get("skip", 0), n),
            "top_share": c.rules.pct(top, n),
            "median_decide_s": median(times) if n >= c.rules.mg else None,
            "edit_share": c.rules.pct(len(edited), n),
            "_rows": rows,
        })
        out.append(row)
    return out


def ep_meta(c: Ctx):
    versions = list(dict.fromkeys(r.version for r in c.all))
    return {**c.base(), "n_all": len(c.all), "versions": versions,
            "synthetic_in_store": sum(r.synthetic for r in c.all), "types": TYPES, "palettes": c.palettes,
            "scenarios": [{"id": sid, "zone": sc.zone, "time": sc.time, "question": sc.question, "theme": sc.theme,
                           "native": bool(sc.hand_built),
                           "options": [{"k": o["k"], "name": o.get("name"), "item": o.get("item") or None} for o in sc.fixes]}
                          for sid, sc in c.lib.items()]}


def ep_summary(c: Ctx):
    mg = c.rules.mg
    by_type = {t: (n if (n := sum(r.type == t for r in c.done)) >= mg else None) for t in TYPES + ["none"]}
    return {**c.base(), "started": c.started, "completed": c.N,
            "completion": num(js_round(c.N / len(c.R) * 1000) / 10) if len(c.R) >= mg and c.started is not None else None,
            "median_play_s": median([r.play_s for r in c.done]) if c.N >= mg else None,
            "by_type": by_type, "synthetic": sum(r.synthetic for r in c.done)}


FUNNEL = [("started", "Started a game"), ("role", "Chose a job type")] + \
         [(f"m{k}", f"Played moment {k}") for k in range(1, 8)] + \
         [("end", "Reached 18:00"), ("palette", "Picked a palette"), ("render", "Opened the render"),
          ("wall", "Shared to the wall")]


def ep_funnel(c: Ctx):
    def count(key):
        if key.startswith("m"):
            return sum(r.played >= int(key[1:]) for r in c.R)
        return sum(bool(r.reached[key]) for r in c.R)

    def step(key, label):
        # a step under 10, or within 10 of the starts or of the finished count (n on every
        # response), hides its count: subtracting the two would show a small group
        n = count(key)
        if n < c.rules.mg:
            return {"key": key, "label": label, "n": None, "lt10": True}
        if 0 < len(c.R) - n < c.rules.mg:
            return {"key": key, "label": label, "n": None, "dropped_lt10": True}
        if 0 < abs(n - c.N) < c.rules.mg:
            return {"key": key, "label": label, "n": None, "near_lt10": True}
        # rounded to 10 so two neighbouring steps never subtract to an exact small group;
        # a step equal to the shown starts or finishes is already public, so it stays exact
        return {"key": key, "label": label, "n": n if n in (c.started, c.N) else js_round(n / 10) * 10}

    out = {"n": c.started, "updated_at": c.base()["updated_at"],
           "steps": [step(k, label) for k, label in FUNNEL] if len(c.R) >= c.rules.mg else None}
    if len(c.R) < c.rules.mg:
        out["lt10"] = True
    return out


def ep_picks(c: Ctx):
    return {**c.base(), "scenarios": [{k: v for k, v in row.items() if k != "_rows"} for row in per_scenario(c)]}


def ep_skips(c: Ctx):
    rows = [{"id": s["id"], "question": s["question"], "time": s["time"], "n": s["n"], "skip_share": s["skip_share"]}
            for s in per_scenario(c) if not s.get("lt10")]
    return {**c.base(), "rows": sorted(rows, key=lambda r: -r["skip_share"])}


def ep_edits(c: Ctx):
    mg = c.rules.mg
    rows = []
    for s in per_scenario(c):
        if s.get("lt10"):
            continue
        sw: dict[str, int] = {}
        for r in s["_rows"]:
            for zone, a, b in r.edits:
                if zone == s["zone"]:
                    sw[f"{a}>{b}"] = sw.get(f"{a}>{b}", 0) + 1
        top = max(sw.items(), key=lambda kv: kv[1], default=None)  # first of the largest, as the mock's stable sort
        top_switch = None
        if top and top[1] >= mg:
            f, t = top[0].split(">")
            top_switch = {"from": c.name(s["id"], f), "to": c.name(s["id"], t), "from_k": f, "to_k": t, "n": top[1]}
        rows.append({"id": s["id"], "question": s["question"], "time": s["time"], "n": s["n"],
                     "edit_share": s["edit_share"], "top_switch": top_switch})
    rows.sort(key=lambda r: -r["edit_share"])
    counts: dict[tuple, int] = {}
    for r in c.done:
        for zone, a, b in r.edits:
            sc = r.by_zone.get(zone)
            if sc is not None:
                counts[(sc.id, a, b)] = counts.get((sc.id, a, b), 0) + 1
    switches = [{"id": sid, "question": c.lib[sid].question, "from": {"k": a, "name": c.name(sid, a)},
                 "to": {"k": b, "name": c.name(sid, b)}, "n": n, "share": c.rules.pct(n, c.N)}
                for (sid, a, b), n in sorted(((k, n) for k, n in counts.items() if n >= mg), key=lambda kv: -kv[1])[:4]]
    return {**c.base(), "rows": rows, "any_share": c.rules.pct(sum(bool(r.edits) for r in c.done), c.N),
            "switches": switches}


def ep_additions(c: Ctx):
    mg = c.rules.mg
    with_add = [r for r in c.done if r.add]
    cnt: dict[str, int] = {}
    how: dict[str, int] = {}
    for r in with_add:
        cnt[r.add["item_id"]] = cnt.get(r.add["item_id"], 0) + 1
        how[r.add["how_placed"]] = how.get(r.add["how_placed"], 0) + 1
    items = sorted(((i, n) for i, n in cnt.items() if n >= mg), key=lambda kv: -kv[1])[:15]
    return {**c.base(), "added_share": c.rules.pct(len(with_add), c.N),
            "declined_share": c.rules.pct(sum(r.declined and not r.add for r in c.done), c.N),
            "items": [{"id": i, "name": c.items.get(i, {}).get("name") or i, "cat": c.items.get(i, {}).get("cat"),
                       "n": n, "share": c.rules.pct(n, c.N)} for i, n in items],
            "other_items_lt10": sum(n for n in cnt.values() if n < mg),
            "how": {k: c.rules.pct(how.get(k, 0), len(with_add)) for k in HOW}}


def _shares_for(c: Ctx, rows: list[Rec], zone: str, keys: list[str]):
    n = len(rows)
    return c.rules.sup(n, {"n": n, **{k: c.rules.pct(sum(r.final_of(zone) == k for r in rows), n) for k in keys}})


def ep_sharing(c: Ctx):
    d1 = c.lib.get(1)
    keys1 = [o["k"] for o in d1.fixes] + ["skip"]
    by = {t: _shares_for(c, [r for r in c.done if t == "all" or r.type == t], "z7", keys1) for t in TYPES + ["all"]}
    fab = c.lib.get(30)
    fab_out = None
    if fab is not None:
        keys30 = [o["k"] for o in fab.fixes] + ["skip"]
        fab_out = {"options": [{"k": o["k"], "name": o.get("name")} for o in fab.fixes],
                   "by_type": {t: _shares_for(c, [r for r in c.done if (t == "all" or r.type == t)
                                                  and (z := r.by_zone.get("z6")) is not None and z.id == 30], "z6", keys30)
                               for t in ("CE", "IE", "all")}}
    room = [r for r in c.done if r.add and re.match(r"^desks_|swap", r.add["how_placed"])]
    rn = len(room)
    return {**c.base(),
            "team_desks": {"options": [{"k": o["k"], "name": o.get("name")} for o in d1.fixes], "by_type": by},
            "fab_days": fab_out,
            "making_room": c.rules.sup(rn, {"n": rn, **{k: c.rules.pct(sum(r.add["how_placed"] == k for r in room), rn)
                                                        for k in ("desks_smaller", "desks_share", "swap")}})}


def ep_pain_hours(c: Ctx):
    hours: dict[int, list] = {}
    for s in per_scenario(c):
        if not s.get("lt10"):
            hours.setdefault(int(str(s["time"] or "0").split(":")[0]), []).append(s)
    rows = []
    for h in sorted(hours):
        ss = hours[h]
        dec = median([s["median_decide_s"] for s in ss if s["median_decide_s"] is not None]) or 0
        sk = jsum(s["skip_share"] or 0 for s in ss) / len(ss)
        ed = jsum(s["edit_share"] or 0 for s in ss) / len(ss)
        rows.append({"h": h, "scenarios": len(ss), "decide_s": dec, "skip_share": num(js_round(sk * 10) / 10),
                     "edit_share": num(js_round(ed * 10) / 10), "top": [s["question"] for s in ss][:2]})

    def mx(k):
        return max([1] + [r[k] for r in rows])

    for r in rows:
        r["index"] = js_round(100 * (r["decide_s"] / mx("decide_s") * .4 + r["skip_share"] / mx("skip_share") * .35
                                     + r["edit_share"] / mx("edit_share") * .25))
    return {**c.base(), "hours": rows}


def ep_bundles(c: Ctx):
    single: dict[str, int] = {}
    pair: dict[str, int] = {}
    for r in c.done:
        ks = sorted(f"{sc.id}:{r.final_of(z)}" for z, sc in r.by_zone.items() if r.final_of(z) != "skip")
        for k in ks:
            single[k] = single.get(k, 0) + 1
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                p = ks[i] + "|" + ks[j]
                pair[p] = pair.get(p, 0) + 1
    N = c.N

    def lab(k):
        sid, opt = k.split(":")
        return {"id": int(sid), "k": opt, "name": c.name(int(sid), opt)}

    pairs = []
    for p, n in pair.items():
        if n >= c.rules.mg:
            a, b = p.split("|")
            lift = (n / N) / ((single[a] / N) * (single[b] / N))
            pairs.append({"a": lab(a), "b": lab(b), "n_both": n, "lift": num(js_round(lift * 100) / 100)})
    pairs.sort(key=lambda x: -x["lift"])
    return {**c.base(), "pairs": pairs[:12]}


def ep_space_brief(c: Ctx):
    cats: dict[str, int] = {}
    for r in c.done:
        have: dict[str, None] = {}
        for z, sc in r.by_zone.items():
            k = r.final_of(z)
            o = next((o for o in sc.fixes if o.get("k") == k), None)
            for i in (o or {}).get("items") or []:
                if i in c.items:
                    have[c.items[i]["cat"]] = None
        if r.add and r.add["item_id"] in c.items:
            have[c.items[r.add["item_id"]]["cat"]] = None
        for cat in have:
            cats[cat] = cats.get(cat, 0) + 1
    rows = [{"cat": cat, "share_of_floors": c.rules.pct(n, c.N)} for cat, n in cats.items()]
    rows.sort(key=lambda x: -(x["share_of_floors"] or 0))
    return {**c.base(), "categories": rows}


def _sim_value(sim: dict, k: str):
    # The floor stores events.md's name (deskU) for what the game sends as desk_use.
    v = sim.get("deskU" if k == "desk_use" else k)
    try:
        return float(v) or 0
    except (TypeError, ValueError):
        return 0


def ep_sim(c: Ctx):
    def agg(rows):
        s = [r for r in rows if r.sim is not None]
        n = len(s)

        def av(k):
            return js_round(jsum(_sim_value(r.sim, k) for r in s) / n) if n >= c.rules.mg else None

        return c.rules.sup(n, {"n": n, "use": av("use"), "happy": av("happy"), "desk_use": av("desk_use")})

    bins = [0] * 10
    for r in c.done:
        if r.sim is not None:
            bins[min(9, math.floor(_sim_value(r.sim, "happy") / 10))] += 1
    return {**c.base(), "all": agg(c.done), "by_type": {t: agg([r for r in c.done if r.type == t]) for t in TYPES},
            "happy_hist": bins if c.N >= c.rules.mg else None}


def ep_leads_gap(c: Ctx):
    mg = c.rules.mg
    L = [r for r in c.done if r.type == "Lead"]
    O = [r for r in c.done if r.type not in ("Lead", "none")]
    rows = []
    for sid, sc in c.lib.items():
        def dealt(r):
            return (z := r.by_zone.get(sc.zone)) is not None and z.id == sid
        lr = [r for r in L if dealt(r)]
        orr = [r for r in O if dealt(r)]
        if len(lr) < mg or len(orr) < mg:
            continue
        for op in sc.fixes:
            a = c.rules.pct(sum(r.final_of(sc.zone) == op["k"] for r in lr), len(lr))
            b = c.rules.pct(sum(r.final_of(sc.zone) == op["k"] for r in orr), len(orr))
            rows.append({"id": sid, "question": sc.question, "k": op["k"], "name": op.get("name"), "lead": a, "staff": b,
                         "gap": num(js_round((b - a) * 10) / 10), "n_lead": len(lr), "n_staff": len(orr)})
    rows.sort(key=lambda x: -abs(x["gap"]))
    return {**c.base(), "n_lead": len(L), "n_staff": len(O), "rows": rows[:10]}


def ep_styles(c: Ctx):
    cnt: dict[str, int] = {}
    for r in c.done:
        if r.palette:
            cnt[r.palette] = cnt.get(r.palette, 0) + 1
    rows = [{"name": name, "share": c.rules.pct(n, c.N)} for name, n in cnt.items()]
    rows.sort(key=lambda x: -(x["share"] or 0))
    return {**c.base(), "palettes": rows}


def ep_wall(c: Ctx):
    ids = [r.id for r in c.done]
    posts = c.s.scalars(select(WallPost).where(WallPost.session_id.in_(ids))).all() if ids else []
    pids = [p.id for p in posts]
    per_post = dict(c.s.execute(select(WallView.wall_post_id, func.count()).where(WallView.wall_post_id.in_(pids))
                                .group_by(WallView.wall_post_id)).all()) if pids else {}
    return {**c.base(), "posts": len(posts), "share_posted": c.rules.pct(len(posts), c.N),
            "views": sum(per_post.values()),
            "median_views": median([per_post.get(p, 0) for p in pids]) if len(posts) >= c.rules.mg else None}


ENDPOINTS = {
    "meta": ep_meta, "summary": ep_summary, "funnel": ep_funnel, "picks": ep_picks, "skips": ep_skips,
    "edits": ep_edits, "additions": ep_additions, "sharing": ep_sharing, "pain-hours": ep_pain_hours,
    "bundles": ep_bundles, "space-brief": ep_space_brief, "sim": ep_sim, "leads-gap": ep_leads_gap,
    "styles": ep_styles, "wall": ep_wall,
}


def export_csv_text(c: Ctx) -> str:
    def cell(x):
        return "" if x is None else str(num(x))

    lines = ["scenario_id,question,option,name,share_pct,n"]
    for s in ep_picks(c)["scenarios"]:
        if s.get("lt10"):
            continue
        q = json.dumps(s["question"], ensure_ascii=False)
        for o in s["options"]:
            lines.append(",".join([str(s["id"]), q, o["k"], json.dumps(o["name"], ensure_ascii=False), cell(o["share"]), str(s["n"])]))
        lines.append(",".join([str(s["id"]), q, "skip", "Skipped", cell(s["skip_share"]), str(s["n"])]))
    return "\n".join(lines) + "\n"


@router.get("/export.csv")
def export_csv(request: Request, who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    """The aggregated picks table. Never raw events or per-game rows."""
    f = filters(request)
    if not who.can_export:
        raise HTTPException(403, "this viewer may not export")
    audit(s, who, "export.csv", f, export=True)
    return Response(export_csv_text(context(s, cfg, f)), media_type="text/csv")


@router.get("/{name}")
def read(name: str, request: Request, who=Depends(viewer), s: Session = Depends(db), cfg: Settings = Depends(settings)):
    if name not in ENDPOINTS:
        raise HTTPException(404, "not found")
    f = filters(request)
    audit(s, who, name, f)
    return ENDPOINTS[name](context(s, cfg, f))
