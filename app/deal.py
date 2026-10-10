"""The default deal function (spec section 2, step 3).

7 moments, one per zone z1-z7, from the scenario library:

- z7, "The team grows to 14", is always dealt.
- CE and IE always get scenario 30 (three fab days a month) in z6.
- Lead always gets scenario 56 (should leads keep offices) in z6.
- Other zones draw at random, weighted: player's role x3, scenarios for everyone x1.5,
  other roles x0.35, Core tier x2, hand-built x1.3.
- No theme repeats.
- Moments play in time order.
"""

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.models import ZONES

HAND_BUILT_IDS = frozenset({1, 6, 11, 16, 21, 34, 38})
PLACE_TO_ZONE = {
    "entry": "z1",
    "calls": "z2",
    "quiet": "z3",
    "kitchen": "z4",
    "back corner": "z5",
    "collaboration": "z6",
    "team desks": "z7",
}
EVERYONE = {"all", "everyone", "*"}
# Role codes in scenarios.json -> employee types in the game.
ROLE_CODES = {"CE": "CE", "PSE": "PSE", "INS": "IE", "IE": "IE", "SAL": "Sales", "SALES": "Sales",
              "SUP": "Support", "SUPPORT": "Support", "LEAD": "Lead"}

W_OWN_ROLE = 3.0
W_EVERYONE = 1.5
W_OTHER_ROLE = 0.35
W_CORE = 2.0
W_HAND_BUILT = 1.3


@dataclass(frozen=True)
class Scenario:
    id: int
    zone: str
    time: str  # "HH:MM", used for play order
    theme: str
    tier: str
    roles: frozenset[str]  # employee types; empty = for everyone
    core_for: frozenset[str] | None = frozenset()  # None = Core for everyone; empty = not Core
    hand_built: bool = False
    question: str = ""
    fixes: tuple = field(default=(), compare=False)


def _zone(raw: dict) -> str:
    for key in ("zone", "m", "place"):
        v = raw.get(key)
        if isinstance(v, str):
            v = v.strip().lower()
            if v in ZONES:
                return v
            if v in PLACE_TO_ZONE:
                return PLACE_TO_ZONE[v]
    raise ValueError(f"scenario {raw.get('id')}: no zone")


def _role_set(v) -> frozenset[str]:
    """"CE;INS", "CE, INS" or ["CE", "INS"] -> {"CE", "IE"}. "ALL" -> empty (everyone)."""
    if v is None:
        return frozenset()
    parts = re.split(r"[;,]", v) if isinstance(v, str) else v
    codes = [str(p).strip() for p in parts if str(p).strip()]
    if any(c.lower() in EVERYONE for c in codes):
        return frozenset()
    unknown = [c for c in codes if c.upper() not in ROLE_CODES]
    if unknown:
        raise ValueError(f"unknown role codes {unknown}")
    return frozenset(ROLE_CODES[c.upper()] for c in codes)


def _core_for(tier: str) -> frozenset[str] | None:
    """"Core" -> Core for everyone; "Core (CE, INS)" -> Core only for those types."""
    m = re.fullmatch(r"\s*core\s*(?:\((.*)\))?\s*", tier, re.I)
    if not m:
        return frozenset()
    return _role_set(m[1]) if m[1] else None


def _time_key(t: str) -> tuple[int, int]:
    m = re.match(r"(\d{1,2}):(\d{2})", t or "")
    return (int(m[1]), int(m[2])) if m else (99, 99)


def parse_scenario(raw: dict) -> Scenario:
    sid = int(raw["id"])
    tier = str(raw.get("tier", ""))
    return Scenario(
        id=sid,
        zone=_zone(raw),
        time=str(raw.get("time", "")),
        theme=str(raw.get("theme", sid)),
        tier=tier,
        roles=_role_set(raw.get("roles", raw.get("role"))),
        core_for=_core_for(tier),
        hand_built=bool(raw.get("native", sid in HAND_BUILT_IDS)),
        question=str(raw.get("question", "")),
        fixes=tuple(raw.get("options", raw.get("fixes", ()))),
    )


def load_scenarios(path: Path) -> dict[int, Scenario]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("scenarios", data)
    return {s.id: s for s in (parse_scenario(r) for r in data)}


def find_team_grows(library: dict[int, Scenario], configured: int | None) -> int:
    if configured is not None:
        return configured
    z7 = [s for s in library.values() if s.zone == "z7"]
    named = [s for s in z7 if re.search(r"team grows", f"{s.theme} {s.question}", re.I)]
    if len(named) == 1:
        return named[0].id
    if len(z7) == 1:
        return z7[0].id
    raise ValueError("cannot tell which z7 scenario is 'The team grows to 14'; set DEAL_Z7_SCENARIO_ID")


def weight(s: Scenario, role: str | None) -> float:
    if not s.roles:
        w = W_EVERYONE
    elif role and role in s.roles:
        w = W_OWN_ROLE
    else:
        w = W_OTHER_ROLE
    if s.core_for is None or (role and role in s.core_for):
        w *= W_CORE
    if s.hand_built:
        w *= W_HAND_BUILT
    return w


def forced_for(role: str | None, team_grows_id: int) -> dict[str, int]:
    forced = {"z7": team_grows_id}
    if role in ("CE", "IE"):
        forced["z6"] = 30
    elif role == "Lead":
        forced["z6"] = 56
    return forced


def deal(
    library: dict[int, Scenario],
    role: str | None,
    rng: random.Random | None = None,
    team_grows_id: int | None = None,
) -> list[Scenario]:
    """Return 7 scenarios, one per zone, in play (time) order."""
    rng = rng or random.SystemRandom()
    role = None if role in (None, "none") else role
    picked: dict[str, Scenario] = {}
    for zone, sid in forced_for(role, find_team_grows(library, team_grows_id)).items():
        if sid not in library:
            raise ValueError(f"forced scenario {sid} is not in the library")
        picked[zone] = library[sid]

    open_zones = [z for z in ZONES if z not in picked]
    rng.shuffle(open_zones)  # so no zone always gets first pick of the themes
    for zone in open_zones:
        themes = {s.theme for s in picked.values()}
        pool = [s for s in library.values() if s.zone == zone and s.theme not in themes]
        if not pool:
            raise ValueError(f"no scenario left for {zone} without repeating a theme")
        picked[zone] = rng.choices(pool, weights=[weight(s, role) for s in pool])[0]

    return sorted(picked.values(), key=lambda s: (_time_key(s.time), s.zone))
