import random
from collections import Counter

import pytest

from app import deal as d
from app.models import ZONES
from tests.conftest import ROOT

LIB = d.load_scenarios(ROOT / "sample" / "scenarios.json")


@pytest.mark.parametrize("role", ["CE", "PSE", "IE", "Sales", "Support", "Lead", "none", None])
def test_deal_shape(role):
    rng = random.Random(1)
    for _ in range(200):
        dealt = d.deal(LIB, role, rng)
        assert sorted(s.zone for s in dealt) == list(ZONES)
        assert len({s.theme for s in dealt}) == 7, "no theme repeats"
        times = [d._time_key(s.time) for s in dealt]
        assert times == sorted(times), "moments play in time order"
        assert {s.zone: s.id for s in dealt}["z7"] == 1, "team grows to 14 is always dealt"


@pytest.mark.parametrize("role,z6", [("CE", 30), ("IE", 30), ("Lead", 56)])
def test_forced_z6(role, z6):
    rng = random.Random(2)
    for _ in range(50):
        assert {s.zone: s.id for s in d.deal(LIB, role, rng)}["z6"] == z6


def test_other_roles_are_not_forced():
    rng = random.Random(3)
    z6 = Counter({s.zone: s.id for s in d.deal(LIB, "Sales", rng)}["z6"] for _ in range(300))
    assert len(z6) > 1


def test_weights():
    everyone = d.Scenario(100, "z1", "09:00", "x", "Extra", frozenset())
    ce = d.Scenario(101, "z1", "09:00", "y", "Extra", frozenset({"CE"}))
    core_hand = d.Scenario(1, "z1", "09:00", "z", "Core", frozenset({"CE"}), core_for=None, hand_built=True)
    assert d.weight(everyone, "CE") == 1.5
    assert d.weight(ce, "CE") == 3.0
    assert d.weight(ce, "Sales") == 0.35
    assert d.weight(ce, None) == 0.35
    assert d.weight(core_hand, "CE") == pytest.approx(3.0 * 2 * 1.3)


def test_own_role_drawn_more_often():
    rng = random.Random(4)
    counts = Counter()
    for _ in range(2000):
        for s in d.deal(LIB, "Sales", rng):
            if s.zone not in ("z6", "z7") and s.roles:
                counts["own" if "Sales" in s.roles else "other"] += 1
    sales = sum(1 for s in LIB.values() if s.roles and "Sales" in s.roles and s.zone not in ("z6", "z7"))
    other = sum(1 for s in LIB.values() if s.roles and "Sales" not in s.roles and s.zone not in ("z6", "z7"))
    # Per scenario, own-role ones should come up far more often (x3 vs x0.35).
    assert counts["own"] / sales > 3 * counts["other"] / other


def test_team_grows_lookup():
    assert len(LIB) == 61
    assert d.find_team_grows(LIB, None) == 1
    assert d.find_team_grows(LIB, 7) == 7


def test_place_names_map_to_zones():
    s = d.parse_scenario({"id": 5, "place": "Back corner", "time": "14:00", "theme": "t", "roles": ["CE"]})
    assert s.zone == "z5"
    assert d.parse_scenario({"id": 6, "zone": "z2", "roles": "all"}).roles == frozenset()


def test_real_library_formats():
    assert LIB[3].roles == {"CE", "IE"}, "INS is the IE code"
    assert LIB[13].roles == {"Sales"} and LIB[12].roles == {"Lead", "Support"}
    assert LIB[4].roles == frozenset(), "ALL is for everyone"
    assert {s.id for s in LIB.values() if s.hand_built} == d.HAND_BUILT_IDS
    # "Core (CE, INS)" is Core only for those types.
    assert d.weight(LIB[30], "CE") == 3.0 * 2 and d.weight(LIB[30], "PSE") == 0.35
    assert d.weight(LIB[56], "Lead") == 1.5 * 2 and d.weight(LIB[56], "Sales") == 1.5
    assert d.weight(LIB[11], "Sales") == pytest.approx(1.5 * 2 * 1.3)


def test_unknown_role_code_is_an_error():
    with pytest.raises(ValueError):
        d.parse_scenario({"id": 99, "zone": "z1", "roles": "CE;XYZ"})
