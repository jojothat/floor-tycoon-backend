"""The group-of-10 rule for the wall's games-by-type strip (spec section 8).

The read API applies the same rule in its own shapes (app/read.py): null shares and
{"n": n, "lt10": true} groups, as readout/READOUT.md defines.
"""


def merge_small(counts: dict[str, int], min_group: int) -> dict[str, list[str]] | None:
    """Group keys so every group has at least `min_group` plays: keys under it are merged
    into "Other", topped up with the smallest other keys while "Other" is still under it,
    so no small group shows by subtraction. Returns {group label: [keys]}, or None when the
    total itself is under `min_group`."""
    total = sum(counts.values())
    if total < min_group:
        return None
    groups = {k: [k] for k, n in counts.items() if n >= min_group}
    other = [k for k, n in counts.items() if n < min_group]
    while 0 < sum(counts[k] for k in other) < min_group:
        smallest = min(groups, key=lambda g: (counts[g], g))
        other += groups.pop(smallest)
    if other:
        groups["Other"] = sorted(other)
    return groups
