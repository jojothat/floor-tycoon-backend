"""The group-of-10 rule (spec section 8), enforced in the API, not the dashboard.

Every number states how many plays are behind it. A number from fewer than
`min_group` plays is returned as "fewer than 10", or merged into "Other". Small
groups are also kept from showing up by subtraction: whatever is hidden or merged
always adds up to 0 or at least `min_group` plays.
"""

def fewer(min_group: int) -> str:
    return f"fewer than {min_group}"


def hide(n: int, value, min_group: int):
    return value if n >= min_group else fewer(min_group)


def merge_small(counts: dict[str, int], min_group: int) -> dict[str, list[str]] | None:
    """Group keys so every group has at least `min_group` plays: keys under it are merged
    into "Other", topped up with the smallest other keys while "Other" is still under it.
    Returns {group label: [keys]}, or None when the total itself is under `min_group`."""
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


def protect(counts: dict[str, int], min_group: int) -> dict[str, int | str]:
    """Counts with every one under `min_group` hidden, and more hidden (smallest first)
    while the hidden ones add up to between 1 and `min_group` - 1."""
    hidden = {k for k, n in counts.items() if n < min_group}
    shown = sorted((k for k in counts if k not in hidden), key=lambda k: (counts[k], k))
    while 0 < sum(counts[k] for k in hidden) < min_group and shown:
        hidden.add(shown.pop(0))
    return {k: (fewer(min_group) if counts[k] < min_group else "hidden") if k in hidden else n for k, n in counts.items()}
