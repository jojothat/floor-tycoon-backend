"""Derive moments, edits, additions and add_declines from a session's raw events.

Pure functions over the event list (as in events.md), so they can be rebuilt at
any time. Events must be in `seq` order.
"""

from dataclasses import dataclass, field

DAY_EVENTS = {"answer", "preview", "choose", "keep", "rethink", "skip"}


@dataclass
class MomentRow:
    zone: str
    scenario_id: int | None
    play_order: int
    previewed: list[str] = field(default_factory=list)
    first_preview: str | None = None
    chosen: str | None = None
    time_to_decide_ms: int | None = None
    rethinks: int = 0


@dataclass
class AdditionRow:
    item_id: str
    how_placed: str
    zone: str | None
    swapped_out_option: str | None
    shuffles: int
    removed: bool = False


@dataclass
class Derived:
    moments: list[MomentRow]
    edits: list[dict]
    additions: list[AdditionRow]
    add_declines: list[dict]
    played_deal: list[int] | None
    day_ms: int | None = None  # client t of the last decision of the day: start to the 18:00 floor


def _how_placed(e: dict) -> str:
    desks = e.get("desks")
    if desks in ("smaller", "share"):
        return f"desks_{desks}"
    if e.get("swap"):
        return "swap"
    if e.get("via") == "end":
        return "end"
    return "gap"


def derive(events: list[dict], zone_to_scenario: dict[str, int]) -> Derived:
    moments: dict[str, MomentRow] = {}
    order: list[str] = []
    answered_at: dict[str, int] = {}
    last_choose_at: dict[str, int] = {}
    edits: list[dict] = []
    additions: list[AdditionRow] = []
    declines: list[dict] = []
    played_deal = None
    replaying: set[str] = set()  # zones being replayed at the 18:00 floor
    shuffles = 0
    day_ms = None

    def moment(zone: str) -> MomentRow:
        if zone not in moments:
            order.append(zone)
            moments[zone] = MomentRow(zone, zone_to_scenario.get(zone), len(order))
        return moments[zone]

    for e in events:
        ev, m, t = e.get("ev"), e.get("m"), e.get("t", 0)

        if ev == "deal":
            played_deal = [int(i) for i in e.get("ids", [])]
            continue

        if ev in DAY_EVENTS and m:
            if m in replaying:
                # The replay of an edit; its result arrives as the `edit` event.
                continue
            row = moment(m)
            if row.chosen is not None:
                continue
            if ev == "answer":
                answered_at.setdefault(m, t)
            elif ev == "preview":
                opt = e.get("opt")
                row.previewed.append(opt)
                row.first_preview = row.first_preview or opt
            elif ev == "choose":
                last_choose_at[m] = t
            elif ev in ("keep", "skip"):
                row.chosen = e.get("opt") if ev == "keep" else "skip"
                row.rethinks = int(e.get("rethinks") or 0)
                day_ms = t
                decided = last_choose_at.get(m, t) if ev == "keep" else t
                if m in answered_at:
                    row.time_to_decide_ms = max(0, decided - answered_at[m])
            continue

        if ev == "replay" and m:
            replaying.add(m)
        elif ev == "edit" and m:
            replaying.discard(m)
            edits.append({"zone": m, "from_option": str(e.get("from")), "to_option": str(e.get("to"))})
        elif ev in ("addOpen", "gap"):
            shuffles = 0
        elif ev in ("addShuffle", "gapShuffle"):
            shuffles += 1
        elif ev == "add":
            additions.append(
                AdditionRow(
                    item_id=str(e.get("item")),
                    how_placed=_how_placed(e),
                    zone=m,
                    swapped_out_option=e.get("swap"),
                    shuffles=shuffles,
                )
            )
            shuffles = 0
        elif ev == "addRemoved":
            for a in reversed(additions):
                if not a.removed and a.zone == m and a.item_id == str(e.get("item")):
                    a.removed = True
                    break
        elif ev == "addSkip":
            declines.append({"where_": "end", "zone": None})
        elif ev == "gapLeave":
            declines.append({"where_": "gap", "zone": m})

    return Derived(
        moments=[moments[z] for z in order],
        edits=edits,
        additions=additions,
        add_declines=declines,
        played_deal=played_deal,
        day_ms=day_ms,
    )
