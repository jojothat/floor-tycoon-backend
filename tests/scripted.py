"""Scripted plays for build step 2: good, speeding, position-biased and skip-everything.

SYNTHETIC: generated for tests, never real data. Each play is a list of events as in
events.md, on a deal drawn from the real scenario library.
"""

import random

from app import deal as d

KINDS = ("good", "speeding", "speeding_blind", "position_biased", "skip_everything")


def script_play(lib: dict, kind: str, rng: random.Random, role: str = "CE") -> tuple[list[dict], dict]:
    """Return (events, final_picks) for one play of the given kind."""
    dealt = d.deal(lib, role, rng)
    events: list[dict] = [{"ev": "deal", "t": 0, "ids": [s.id for s in dealt]}]
    t = 0
    picks: dict[str, str] = {}

    if kind == "skip_everything":
        n_skips = rng.randint(5, 7)
    elif kind == "good":
        n_skips = rng.randint(0, 1)
    else:
        n_skips = 0
    skip_zones = set(rng.sample([s.zone for s in dealt], n_skips))
    # Only the position-biased player picks the same tile every time.
    good_opts = ["a", "b", "c"] * 3
    rng.shuffle(good_opts)

    for i, s in enumerate(dealt):
        z = s.zone
        opts = [o["k"] for o in s.fixes] or ["a", "b", "c"]
        fast = kind.startswith("speeding")
        t += rng.randint(600, 1500) if fast else rng.randint(8000, 14000)  # bubble shows, player taps
        events.append({"ev": "answer", "m": z, "t": t})
        if z in skip_zones:
            t += rng.randint(5000, 10000)
            events.append({"ev": "skip", "m": z, "why": "none", "rethinks": 0, "t": t})
            picks[z] = "0"
            continue
        if kind == "position_biased":
            pick = "a"
        else:
            pick = good_opts[i] if good_opts[i] in opts else opts[0]
        if kind != "speeding_blind":
            looked = rng.sample(opts, rng.randint(1, len(opts)))
            if pick not in looked:
                looked.append(pick)
            for o in looked:
                t += rng.randint(200, 500) if fast else rng.randint(1500, 4000)
                events.append({"ev": "preview", "m": z, "opt": o, "t": t})
        t += rng.randint(200, 900) if fast else rng.randint(1500, 3000)
        events.append({"ev": "choose", "m": z, "opt": pick, "t": t})
        t += 2600  # the reply and catch play, then the day moves on
        events.append({"ev": "keep", "m": z, "opt": pick, "rethinks": 0, "t": t})
        picks[z] = pick

    for i, e in enumerate(events, start=1):
        e["seq"] = i
    return events, picks
