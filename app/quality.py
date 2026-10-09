"""Quality score for a play (spec section 7).

Each play scores 0-100. Below 40 is dropped, 40-69 counts at half weight, 70 and
above counts fully. Thresholds and penalties are starting points to tune in the
pilot, so they all live here.

Scores and flags are stored but never shown to the player.
"""

from dataclasses import dataclass, field

DROP_BELOW = 40
FULL_FROM = 70

SPEEDING_MOMENT_MS = 3000  # bubble tap to Choose
SPEEDING_PLAY_MS = 90_000  # whole play, start to the 18:00 floor
SPEEDING_PER_MOMENT = 10
SPEEDING_MAX = 40

NO_LOOK_PER_MOMENT = 15
NO_LOOK_MAX = 30

POSITION_BIAS = 25
POSITION_BIAS_MIN_PICKS = 5  # "always the same tile" needs enough picks to mean anything

SKIP_EVERYTHING = 30
SKIP_EVERYTHING_FROM = 5

INCONSISTENT_PAIR = 15
# Paired scenarios where two answers contradict each other:
# (scenario_a, option_a, scenario_b, option_b). Not defined in the spec yet; to fill
# in with Zyeta before the pilot. Each matching pair costs INCONSISTENT_PAIR.
INCONSISTENT_PAIRS: list[tuple[int, str, int, str]] = []


@dataclass
class MomentSignal:
    scenario_id: int | None
    chosen: str | None  # a-d, "skip", or None if undecided
    previewed: list[str]
    time_to_decide_ms: int | None


@dataclass
class Score:
    score: int
    flags: list[str] = field(default_factory=list)

    @property
    def weight(self) -> float:
        if self.score < DROP_BELOW:
            return 0.0
        return 1.0 if self.score >= FULL_FROM else 0.5


def score_play(
    moments: list[MomentSignal],
    play_ms: int | None,
    pairs: list[tuple[int, str, int, str]] | None = None,
) -> Score:
    """Score one play from its moments and its length (start to the 18:00 floor)."""
    pairs = INCONSISTENT_PAIRS if pairs is None else pairs
    penalty = 0
    flags: list[str] = []
    picks = [m for m in moments if m.chosen not in (None, "skip")]

    fast = sum(
        1 for m in picks if m.time_to_decide_ms is not None and m.time_to_decide_ms < SPEEDING_MOMENT_MS
    )
    speeding = min(SPEEDING_MAX, fast * SPEEDING_PER_MOMENT)
    if play_ms is not None and play_ms < SPEEDING_PLAY_MS:
        speeding = SPEEDING_MAX
        flags.append("speeding_play")
    if fast:
        flags.append("speeding_moments")
    penalty += speeding

    blind = sum(1 for m in picks if not m.previewed)
    if blind:
        flags.append("no_looking")
        penalty += min(NO_LOOK_MAX, blind * NO_LOOK_PER_MOMENT)

    if len(picks) >= POSITION_BIAS_MIN_PICKS and len({m.chosen for m in picks}) == 1:
        flags.append("position_bias")
        penalty += POSITION_BIAS

    skips = sum(1 for m in moments if m.chosen == "skip")
    if skips >= SKIP_EVERYTHING_FROM:
        flags.append("skip_everything")
        penalty += SKIP_EVERYTHING

    chosen = {m.scenario_id: m.chosen for m in picks}
    clashes = sum(1 for a, oa, b, ob in pairs if chosen.get(a) == oa and chosen.get(b) == ob)
    if clashes:
        flags.append("inconsistent_pair")
        penalty += clashes * INCONSISTENT_PAIR

    return Score(max(0, 100 - penalty), flags)
