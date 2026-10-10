"""Plain-English activity lines on the console, so whoever runs the backend can watch games arrive.

    09:41:07  [game 3fA9xQ]  New game started · employee type CE · quick flow · 7 moments dealt
    09:41:12  [game 3fA9xQ]  Saved 3 events (this game now has 12)
    09:41:12  [game 3fA9xQ]    · 09:00 Morning stand-up has no room: chose "Huddle Corner"

Turn off with LOG_ACTIVITY=off. A game is named by the first 6 characters of its session id.
No player id, no free text and nothing about the character look is ever written here.
"""

import logging
import sys

from app.config import get_settings

log = logging.getLogger("floor_tycoon.activity")
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

OPTION_NAMES = {"0": "left it as it was"}


def on() -> bool:
    return get_settings().log_activity != "off"


def say(game: str | None, text: str) -> None:
    if on():
        log.info(f"[game {game[:6]}]  {text}" if game else text)


def _scenario(lib: dict, zone_to_id: dict, m: str | None):
    sid = zone_to_id.get(m)
    return lib.get(sid) if sid is not None else None


def _fix(sc, k) -> str:
    if k is None:
        return "?"
    if str(k) in OPTION_NAMES:
        return OPTION_NAMES[str(k)]
    name = next((f.get("name") for f in (sc.fixes if sc else ()) if f.get("k") == k), None)
    return f'"{name}"' if name else f"option {k}"


def describe(ev: dict, lib: dict, zone_to_id: dict) -> str | None:
    """One event in plain words, or None for the ones too small to mention."""
    kind, m = ev.get("ev"), ev.get("m")
    sc = _scenario(lib, zone_to_id, m)
    moment = f"{sc.time} {sc.question}" if sc else (m or "")
    match kind:
        case "deal":
            return f"Game dealt {len(ev.get('ids') or [])} moments"
        case "role_set":
            return f"Player picked employee type {ev.get('type') or 'prefer not to say'}"
        case "answer":
            return f"{moment}: player opened this moment"
        case "keep":
            return f"{moment}: chose {_fix(sc, ev.get('opt'))}"
        case "skip":
            note = " (with a written note, kept for review only)" if ev.get("text") else ""
            return f"{moment}: none of these work, skipped{note}"
        case "rethink":
            return f"{moment}: went back to rethink"
        case "edit":
            return f"End of day: changed {moment} from {_fix(sc, ev.get('from'))} to {_fix(sc, ev.get('to'))}"
        case "add":
            where = f" in place of {_fix(sc, ev.get('swap'))} at {moment}" if ev.get("swap") else (f" at {moment}" if m else "")
            return f"End of day: added a {ev.get('item', 'new item')}{where}"
        case "addRemoved":
            return f"End of day: took the {ev.get('item', 'added item')} back out"
        case "addSkip" | "gapLeave":
            return "End of day: added nothing"
        case "palette_set":
            return f"Picked colour palette {ev.get('name') or ev.get('palette') or ''}".rstrip()
        case "sim_done":
            nums = ", ".join(f"{label} {ev[k]:.0f}%" for k, label in (("use", "space use"), ("happy", "happiness"))
                             if isinstance(ev.get(k), (int, float)))
            return f"Watched the simulated day{': ' + nums if nums else ''}"
        case "render_view":
            return "Looked at the finished office"
        case "wall_post":
            return "Shared the office to the wall"
        case "wallOpen":
            return "Opened the wall of offices"
        case "session_end":
            return "Closed the game"
    return None


def events_saved(game: str, inserted: list[dict], duplicates: int, total: int, lib: dict, zone_to_id: dict) -> None:
    if not on():
        return
    extra = f", {duplicates} repeat{'s' if duplicates != 1 else ''} ignored" if duplicates else ""
    if not inserted:
        say(game, f"Batch was a resend: nothing new{extra}")
        return
    say(game, f"Saved {len(inserted)} event{'s' if len(inserted) != 1 else ''}{extra} (this game now has {total})")
    for ev in inserted:
        if (line := describe(ev, lib, zone_to_id)) is not None:
            say(game, f"  · {line}")
