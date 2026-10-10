"""Parity: the backend's read API returns what the readout's reference mock returns.

Identical plays go into both: through the backend's game API (as the game sends them),
and as the same event logs into readout/mock-api/read.mjs (tests/parity/mock_read.mjs).
Every endpoint is compared, under several filters, ignoring only updated_at.

The plays: the real sample (sample/play-log-sample.json) plus a seeded run of synthetic
and non-synthetic games (SYNTHETIC test data, never real data) covering skips, edits
(including to a skip), every way of placing an addition, removed additions, declines,
quits before 18:00, finishes without a simulated day, the classic flow, palettes,
wall posts and views.
"""

import json
import random
import shutil
import subprocess
import uuid

import pytest
from sqlalchemy import select

from app import clean
from app import deal as deal_fn
from app.config import get_settings
from app.models import GameSession, WallPost, WallView
from tests.conftest import EXPORT_TOKEN, PILOT_TOKEN, ROOT, TOKEN, load_json

pytestmark = pytest.mark.usefixtures("viewers")

RUNNER = ROOT / "tests" / "parity" / "mock_read.mjs"
NEEDS = pytest.mark.skipif(
    shutil.which("node") is None or not (ROOT / "readout" / "mock-api" / "read.mjs").exists(),
    reason="needs node and readout/mock-api",
)
ENDPOINTS = ["meta", "summary", "funnel", "picks", "skips", "edits", "additions", "sharing", "pain-hours",
             "bundles", "space-brief", "sim", "leads-gap", "styles", "wall"]
PALETTES = ["Warm Minimal", "Scandi Light", "Japandi", "Biophilic Calm", "Soft Greige", "Terracotta Studio",
            "Midnight Brass", "Industrial Loft", "Mid-century Pop", "Colour-block Tech"]
ADDABLE = ["phonebooth", "focuspod", "pantry", "nappod", "greenwall", "lockers", "cafebar", "huddleroom",
           "videoroom", "meditation", "duobooth", "massage"]
TYPES = [("CE", 30), ("PSE", 18), ("IE", 12), ("Sales", 14), ("Support", 16), ("Lead", 22), ("none", 4)]


def scripted_play(lib, rng: random.Random, version: str) -> dict:
    """One game as the phone sends it: start body, events, finish, floor, wall."""
    role = rng.choices([t for t, _ in TYPES], [w for _, w in TYPES])[0]
    flow = "classic" if rng.random() < 0.2 else "quick"
    dealt = deal_fn.deal(lib, role, rng)
    events, t = [], 0

    def E(**e):
        events.append({**e, "seq": len(events) + 1, "t": t})

    E(ev="deal", ids=[s.id for s in dealt], src="server")
    E(ev="role_set", type=role)
    picks, skips = {}, {}
    quit_at = rng.randrange(7) if rng.random() < 0.12 else 99
    for i, s in enumerate(dealt):
        if i >= quit_at:
            break
        opts = [o["k"] for o in s.fixes]
        weights = [1 + (hash((role, s.id, k)) % 5) for k in opts]  # roles lean differently, and stably
        t += rng.randint(6000, 10000)
        E(ev="answer", m=s.zone)
        if rng.random() < 0.08:
            if flow == "classic" and rng.random() < 0.5:  # chose, saw the catch, then skipped
                t += rng.randint(1500, 4000)
                E(ev="choose", m=s.zone, opt=opts[0])
                t += 2000
                E(ev="rethink", m=s.zone, **{"from": opts[0]})
            t += rng.randint(1500, 4000)
            E(ev="skip", m=s.zone, why="none", rethinks=0)
            picks[s.zone], skips[s.zone] = "0", "none"
            continue
        pick = rng.choices(opts, weights)[0]
        for k in dict.fromkeys(rng.choices(opts, k=rng.randint(1, 3)) + [pick]):
            t += rng.randint(900, 2700)
            E(ev="preview", m=s.zone, opt=k)
        t += rng.randint(1500, 12000)
        E(ev="choose", m=s.zone, opt=pick)
        t += 2600
        E(ev="keep", m=s.zone, opt=pick, rethinks=0)
        picks[s.zone] = pick
    play = {"role": role, "flow": flow, "version": version, "day": list(events), "end": [], "finish": None,
            "floor": None, "wall": False}
    if quit_at < 99:
        return play
    day_len = len(events)
    play["finish"] = {"picks": dict(picks), "skips": dict(skips)}

    if rng.random() < 0.35:  # an end-of-day edit: often the team desks to a favourite, sometimes to a skip
        s = next(x for x in dealt if x.zone == "z7") if rng.random() < 0.5 else rng.choice(dealt)
        alts = [o["k"] for o in s.fixes if o["k"] != picks[s.zone]] + ["0"]
        to = alts[0] if s.zone == "z7" else rng.choice(alts)
        t += 4000
        E(ev="replay", m=s.zone)
        t += 9000
        E(ev="edit", m=s.zone, **{"from": picks[s.zone], "to": to})
        picks[s.zone] = to
        if to == "0":
            skips[s.zone] = "none"
        else:
            skips.pop(s.zone, None)
    addition = {"adds": {}, "deskfit": None, "swaps": {}}
    r = rng.random()
    if r < 0.6:
        item = rng.choice(ADDABLE)
        how = rng.choices(["gap", "end", "desks_smaller", "desks_share", "swap"], [35, 35, 12, 8, 10])[0]
        zone = "z7" if how.startswith("desks") else rng.choice(dealt).zone
        t += 3000
        E(ev="addOpen")
        E(ev="addShuffle")
        extra = {} if how == "gap" else {"via": "end"}
        if how.startswith("desks"):
            extra["desks"] = how[6:]
        if how == "swap":
            extra["swap"] = picks.get(zone) or "a"
        E(ev="add", m=zone, item=item, **extra)
        if rng.random() < 0.1:  # removed again by replaying that moment
            t += 2000
            E(ev="addRemoved", m=zone, item=item)
        else:
            addition = {"adds": {zone: item},
                        "deskfit": {"mode": how[6:], "add": item, "k": picks.get("z7", "0")} if how.startswith("desks") else None,
                        "swaps": {zone: item} if how == "swap" else {}}
    elif r < 0.85:
        t += 2000
        E(ev="addOpen")
        E(ev="addSkip" if rng.random() < 0.6 else "gapLeave", **({} if rng.random() < 0.6 else {"m": rng.choice(dealt).zone}))
    if rng.random() < 0.08:  # left at the 18:00 floor: no palette, no simulated day
        play["day"], play["end"] = events[:day_len], events[day_len:]
        return play
    pal = rng.randrange(len(PALETTES))
    t += 5000
    E(ev="palette_set", palette=pal, name=PALETTES[pal])
    sim = {"use": rng.randint(9, 23), "happy": rng.randint(10, 95), "desk_use": rng.randint(25, 85), "desks": 12,
           "peak": rng.randint(12, 14), "peak_at": "09:15", "occ": [rng.randint(0, 14) for _ in range(41)],
           "occ_fab": [rng.randint(0, 5) for _ in range(41)], "fab_people": 5}
    t += 20000
    E(ev="sim_done", use=sim["use"], happy=sim["happy"], deskU=sim["desk_use"], peak=sim["peak"])
    if rng.random() < 0.7:
        t += 4000
        E(ev="render_view")
    if rng.random() < 0.45:
        t += 3000
        E(ev="wall_post")
        play["wall"] = True
    play["day"], play["end"] = events[:day_len], events[day_len:]
    play["floor"] = {"palette": pal, "palette_name": PALETTES[pal], "picks": dict(picks), "skips": dict(skips),
                     "addition": addition, "sim": sim}
    return play


def sample_play() -> dict:
    """The real sample (sample/play-log-sample.json), with its floor rebuilt from end and stats."""
    s = load_json(ROOT / "sample" / "play-log-sample.json")
    events = [{**e, "seq": i} for i, e in enumerate(s["log"], start=1)]
    cut = next(i for i, e in enumerate(events) if e["ev"] in ("addOpen", "replay", "edit", "add", "gap"))
    first = dict(s["finalPicks"])
    for e in events[cut:]:
        if e["ev"] == "edit":
            first[e["m"]] = e["from"]
            break
    st = s["stats"]
    return {"role": s["profile"]["type"], "flow": "quick", "version": "dev", "day": events[:cut], "end": events[cut:],
            "finish": {"picks": first, "skips": {"z6": "none"}}, "wall": True,
            "floor": {"palette": s["end"]["pal"], "palette_name": s["end"]["palette"], "picks": s["finalPicks"],
                      "skips": {"z6": "none"}, "addition": {"adds": {}, "deskfit": None, "swaps": {}},
                      "sim": {"use": s["end"]["use"], "happy": s["end"]["happy"], "desk_use": st["deskU"],
                              "desks": st["desks"], "peak": st["peak"], "peak_at": st["peakAt"]}}}


def send(client, play: dict, anon: str) -> dict:
    """Play it through the backend's game API, as the game does."""
    h = {"X-FT-Player": anon}
    s = client.post("/v1/sessions", headers=h, json={
        "client_session_id": str(uuid.uuid4()), "anon_player_id": anon, "pilot_token": PILOT_TOKEN,
        "game_version": play["version"], "flow": play["flow"], "role_type": play["role"], "language": "ko-KR",
        "device_class": "phone", "viewport_h": 390, "viewport_w": 844}).json()
    url, h = f"/v1/sessions/{s['session_id']}", {**h, "Authorization": f"Bearer {s['write_key']}"}

    def events(evs):
        for i in range(0, len(evs), 50):
            assert client.post(f"{url}/events", headers=h, json={"events": evs[i:i + 50]}).status_code == 200

    events(play["day"])
    if play["finish"]:
        assert client.post(f"{url}/finish", headers=h, json=play["finish"]).status_code == 200
        events(play["end"])
    if play["floor"]:
        r = client.post(f"{url}/floor", headers=h, json=play["floor"])
        assert r.status_code == 200, r.text
    if play["wall"] and play["floor"]:
        assert client.post("/v1/wall", headers=h, json={"session_id": s["session_id"]}).status_code == 200
    return {"session_id": s["session_id"], "anon": anon}


@pytest.fixture(scope="module")
def plays():
    lib = clean.library(str(get_settings().scenarios_path))
    rng = random.Random(20261010)
    out = [scripted_play(lib, rng, "synthetic-seed" if i % 3 else "pilot-1.0") for i in range(330)]
    return [sample_play()] + out


def mock_store(db, plays, sent) -> dict:
    sessions = []
    for play, s in zip(plays, sent):
        g = db.get(GameSession, s["session_id"])
        sessions.append({
            "id": s["session_id"], "deal": play["day"][0]["ids"],
            "body": {"role_type": play["role"], "game_version": play["version"], "flow": play["flow"]},
            "createdAt": int(g.started_at.timestamp() * 1000), "completedAt": 1 if play["finish"] else None,
            "finish": play["finish"], "floor": play["floor"], "_posted": bool(play["wall"] and play["floor"]),
            "events": play["day"] + play["end"],
        })
    sessions.sort(key=lambda x: x["createdAt"])
    anon_of = {s["session_id"]: s["anon"] for s in sent}
    wall = [{"id": str(p.id), "anon": anon_of[p.session_id], "session_id": p.session_id} for p in db.scalars(select(WallPost))]
    views = [f"{v.viewer_anon_id}|{v.wall_post_id}" for v in db.scalars(select(WallView))]
    return {"sessions": sessions, "wall": wall, "views": views}


QUERIES = ["synthetic=include", "", "synthetic=only", "synthetic=include&type=CE", "synthetic=include&type=Lead",
           "synthetic=include&flow=classic", "synthetic=include&version=synthetic-seed"]


def strip(x):
    if isinstance(x, dict):
        return {k: strip(v) for k, v in x.items() if k != "updated_at"}
    if isinstance(x, list):
        return [strip(v) for v in x]
    return x


@NEEDS
def test_every_endpoint_matches_the_mock(client, db, plays):
    sent = [send(client, p, f"anon-parity-{i:05d}") for i, p in enumerate(plays)]
    # some players open other offices on the wall
    posts = client.get("/v1/wall", headers={"X-FT-Player": sent[0]["anon"]}).json()["posts"]
    rng = random.Random(7)
    for s in sent[::4]:
        for p in rng.sample(posts, min(6, len(posts))):
            client.post(f"/v1/wall/{p['id']}/view", headers={"X-FT-Player": s["anon"]}, json={})

    store = mock_store(db, plays, sent)
    queries = [(f"/v1/read/{e}", q) for q in QUERIES for e in ENDPOINTS] + [("/v1/read/export.csv", q) for q in QUERIES]
    from_mock = json.loads(subprocess.run(["node", str(RUNNER)], input=json.dumps({**store, "queries": queries}),
                                          capture_output=True, text=True, check=True).stdout)
    h = {"Authorization": f"Bearer {TOKEN}"}
    checked = 0
    for (path, q), want in zip(queries, from_mock):
        r = client.get(path + (f"?{q}" if q else ""),
                       headers={"Authorization": f"Bearer {EXPORT_TOKEN}"} if path.endswith(".csv") else h)
        assert r.status_code == 200, (path, q, r.text)
        got = r.text if path.endswith(".csv") else strip(r.json())
        want = want if path.endswith(".csv") else strip(want)
        assert got == want, f"{path}?{q} differs from the mock"
        checked += 1
    assert checked == len(queries)

    # The run must exercise what it claims to: enough games, and the lists filled.
    summary = client.get("/v1/read/summary?synthetic=include", headers=h).json()
    edits = client.get("/v1/read/edits?synthetic=include", headers=h).json()
    adds = client.get("/v1/read/additions?synthetic=include", headers=h).json()
    gap = client.get("/v1/read/leads-gap?synthetic=include", headers=h).json()
    assert summary["completed"] >= 250 and summary["started"] > summary["completed"]
    assert edits["switches"] and any(r["top_switch"] for r in edits["rows"])
    assert adds["items"] and all(v is not None for v in adds["how"].values())
    assert gap["rows"], "leads and staff both reach 10 on some scenarios"
