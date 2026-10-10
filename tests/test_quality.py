import pytest

from app.quality import MomentSignal as M
from app.quality import Score, score_play


def good(n=7, chosen=("a", "b", "c", "a", "b", "c", "a")):
    return [M(i, chosen[i], ["a", "b"], 6000) for i in range(n)]


def test_good_play_scores_full():
    s = score_play(good(), play_ms=180_000)
    assert (s.score, s.flags, s.weight) == (100, [], 1.0)


def test_speeding_moments_cost_10_each_up_to_40():
    ms = good()
    for m in ms[:2]:
        m.time_to_decide_ms = 2999
    assert score_play(ms, 180_000).score == 80
    for m in ms:
        m.time_to_decide_ms = 1000
    s = score_play(ms, 180_000)
    assert s.score == 60 and s.flags == ["speeding_moments"]


def test_whole_play_under_90s_is_speeding():
    s = score_play(good(), play_ms=89_999)
    assert s.score == 60 and s.flags == ["speeding_play"]
    assert score_play(good(), play_ms=90_000).score == 100


def test_no_looking_costs_15_each_up_to_30():
    ms = good()
    ms[0].previewed = []
    assert score_play(ms, 180_000).score == 85
    for m in ms:
        m.previewed = []
    s = score_play(ms, 180_000)
    assert s.score == 70 and s.flags == ["no_looking"]


def test_skipped_moments_are_not_blind_or_fast():
    ms = good()
    ms[0] = M(0, "skip", [], 500)
    assert score_play(ms, 180_000).score == 100


def test_position_bias():
    s = score_play(good(chosen=("b",) * 7), 180_000)
    assert s.score == 75 and s.flags == ["position_bias"]
    # Too few picks to call it bias.
    ms = good(chosen=("b",) * 7)
    for m in ms[:3]:
        m.chosen = "skip"
    assert "position_bias" not in score_play(ms, 180_000).flags


def test_skip_everything():
    ms = good()
    for m in ms[:5]:
        m.chosen = "skip"
    s = score_play(ms, 180_000)
    assert s.score == 70 and s.flags == ["skip_everything"]
    for m in ms[:4]:
        m.chosen = "a"
    ms[0].chosen, ms[1].chosen = "b", "c"
    assert score_play(ms, 180_000).score == 100


def test_inconsistent_pair():
    ms = good()
    pairs = [(0, "a", 1, "b"), (2, "c", 3, "c")]
    s = score_play(ms, 180_000, pairs=pairs)
    assert s.score == 85 and s.flags == ["inconsistent_pair"]


def test_penalties_stack_and_floor_at_zero():
    ms = [M(i, "a", [], 500) for i in range(7)]
    s = score_play(ms, 20_000)
    assert s.score == 100 - 40 - 30 - 25
    assert s.flags == ["speeding_play", "speeding_moments", "no_looking", "position_bias"]
    ms = [M(i, "skip", [], 500) for i in range(7)]
    assert score_play(ms, 20_000, pairs=[]).score == 100 - 40 - 30


@pytest.mark.parametrize("score,weight", [(0, 0.0), (39, 0.0), (40, 0.5), (69, 0.5), (70, 1.0), (100, 1.0)])
def test_weights(score, weight):
    assert Score(score).weight == weight
