"""End-to-end smoke test against a running backend, over HTTP, the way the game calls it.

    python -m scripts.smoke --url http://localhost:8000 --pilot-token <invite> [--viewer-token <t>] [--plays 11]

Plays the real sample log (sample/play-log-sample.json) start to finish, with every
event batch sent twice as a flaky phone would, shares it to the wall and views it.
Then plays --plays scripted games so the read API has 10+ plays, and reads it back.

Everything it creates is sample data, tagged game_version "sample-smoke-test" so it
can be told apart from real plays. Use a test pilot, never a live one.
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import deal as deal_fn  # noqa: E402
from tests.scripted import script_play  # noqa: E402

VERSION = "sample-smoke-test"
END_OF_DAY = {"replay", "edit", "addRemoved", "addOpen", "addShuffle", "addSkip", "addNeedsRoom",
              "gap", "gapShuffle", "gapLeave", "add", "wallOpen"}
failures: list[str] = []


def check(ok: bool, what: str) -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures.append(what)
    return ok


def start(c: httpx.Client, token: str, anon: str, role: str) -> tuple[str, dict]:
    r = c.post("/v1/sessions", json={"pilot_token": token, "anon_player_id": anon, "role_type": role,
                                      "game_version": VERSION, "flow": "quick", "language": "ko",
                                      "device_class": "phone", "viewport_height": 390})
    r.raise_for_status()
    body = r.json()
    return body["session_id"], {"Authorization": f"Bearer {body['write_key']}"}


def send(c: httpx.Client, sid: str, h: dict, events: list[dict], retry: bool) -> bool:
    good = True
    for i in range(0, len(events), 10):
        batch = {"events": events[i:i + 10]}
        r = c.post(f"/v1/sessions/{sid}/events", headers=h, json=batch)
        good &= r.status_code == 200 and r.json()["inserted"] == len(batch["events"])
        if retry:
            again = c.post(f"/v1/sessions/{sid}/events", headers=h, json=batch)
            good &= again.status_code == 200 and again.json()["inserted"] == 0
    return good


def play_sample(c: httpx.Client, token: str) -> str:
    print("Sample play (sample/play-log-sample.json):")
    play = json.loads((ROOT / "sample" / "play-log-sample.json").read_text())
    events = [{**e, "seq": i} for i, e in enumerate(play["log"], start=1)]
    cut = next(i for i, e in enumerate(events) if e["ev"] in END_OF_DAY)
    final = play["finalPicks"]
    first = dict(final)
    for e in events[cut:]:
        if e["ev"] == "edit":
            first[e["m"]] = e["from"]
            break

    anon = f"smoke-{uuid.uuid4().hex[:12]}"
    sid, h = start(c, token, anon, play["profile"]["type"])
    check(True, f"session started ({sid[:8]}...)")
    check(send(c, sid, h, events[:cut], retry=True), f"{cut} day events stored, retried batches dropped as duplicates")
    check(c.post(f"/v1/sessions/{sid}/finish", headers=h, json={"final_picks": first}).status_code == 200, "reached 18:00")
    check(send(c, sid, h, events[cut:], retry=True), f"{len(events) - cut} end-of-day events stored")
    check(c.post(f"/v1/sessions/{sid}/finish", headers=h, json={"final_picks": final}).status_code == 200, "final picks")
    sim = {**play["stats"], "use": play["end"]["use"], "happy": play["end"]["happy"]}
    r = c.post(f"/v1/sessions/{sid}/floor", headers=h, json={"palette": play["end"]["palette"], "sim": sim})
    check(r.status_code == 200 and r.json().get("quality") == 60, f"simulated day stored, quality {r.json().get('quality')} (expected 60)")
    check(c.post("/v1/wall", headers=h, json={"session_id": sid}).status_code == 200, "shared to the wall")

    w = c.get("/v1/wall", headers={"X-Pilot-Token": token, "X-Anon-Player": anon})
    mine = next((p for p in w.json().get("posts", []) if p["mine"]), None) if w.status_code == 200 else None
    check(mine is not None and mine["picks"] == final and mine["skips"] == ["z6"], "the wall shows this office as mine, with its picks")
    if mine:
        c.post(f"/v1/wall/{mine['id']}/view", headers={"X-Pilot-Token": token, "X-Anon-Player": anon})
        w = c.get("/v1/wall", headers={"X-Pilot-Token": token, "X-Anon-Player": anon}).json()
        views = next(p["views"] for p in w["posts"] if p["id"] == mine["id"])
        check(views == 0, "opening your own office doesn't count as a view")
    return sid


def play_scripted(c: httpx.Client, token: str, n: int) -> None:
    print(f"{n} scripted plays (synthetic, from tests/scripted.py):")
    lib = deal_fn.load_scenarios(ROOT / "sample" / "scenarios.json")
    rng = random.Random()
    ok = True
    for _ in range(n):
        events, picks = script_play(lib, "good", rng, rng.choice(["CE", "PSE", "Sales", "Lead"]))
        sid, h = start(c, token, f"smoke-{uuid.uuid4().hex[:12]}", "CE")
        ok &= send(c, sid, h, events, retry=False)
        ok &= c.post(f"/v1/sessions/{sid}/finish", headers=h, json={"final_picks": picks}).status_code == 200
        ok &= c.post(f"/v1/sessions/{sid}/floor", headers=h, json={"palette": "Scandi Light", "sim": {"use": 20, "happy": 50}}).status_code == 200
        ok &= c.post("/v1/wall", headers=h, json={"session_id": sid}).status_code == 200
    check(ok, f"{n} plays started, finished, scored and shared")


def read_back(c: httpx.Client, viewer: str) -> None:
    print("Management read:")
    h = {"Authorization": f"Bearer {viewer}"}
    s = c.get("/v1/read/summary", headers=h)
    check(s.status_code == 200, f"summary: {s.json().get('plays')} plays, finished {s.json().get('finished_pct')}%")
    p = c.get("/v1/read/picks", headers=h).json()
    team = next((r for r in p.get("scenarios", []) if r["scenario_id"] == 1), None)
    check(team is not None, f"picks: 'The team grows to 14' from {team and team['n']} plays")
    check(c.get("/v1/read/summary").status_code == 401, "read API refuses a request with no viewer")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--pilot-token", required=True)
    ap.add_argument("--viewer-token")
    ap.add_argument("--plays", type=int, default=11)
    a = ap.parse_args()

    with httpx.Client(base_url=a.url.rstrip("/"), timeout=20) as c:
        print(f"Backend at {a.url}")
        check(c.get("/healthz").status_code == 200, "health check")
        play_sample(c, a.pilot_token)
        if a.plays:
            play_scripted(c, a.pilot_token, a.plays)
        if a.viewer_token:
            read_back(c, a.viewer_token)
    print("PASSED" if not failures else f"FAILED: {len(failures)} check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
