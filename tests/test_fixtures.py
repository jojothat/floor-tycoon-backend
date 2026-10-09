"""Synthetic test data must never be mistaken for real data, and real sample data
must stay labelled as sample (CLAUDE.md)."""

import json

import pytest

from app import deal as d
from app.config import ROOT as APP_ROOT, Settings
from tests.conftest import FIXTURES, ROOT


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.name)
def test_synthetic_fixtures_are_labelled(path):
    data = json.loads(path.read_text())
    assert "synthetic" in path.name
    assert data["_synthetic"] is True and data["_note"].startswith("SYNTHETIC")


def test_real_samples_live_in_sample_and_are_not_synthetic():
    for name in ("scenarios.json", "play-log-sample.json"):
        text = (ROOT / "sample" / name).read_text()
        assert "_synthetic" not in text and "SYNTHETIC" not in text


def test_default_scenarios_path_is_the_real_library(monkeypatch):
    monkeypatch.delenv("SCENARIOS_PATH", raising=False)
    assert Settings().scenarios_path == APP_ROOT / "sample" / "scenarios.json"


def test_synthetic_library_still_parses():
    lib = d.load_scenarios(FIXTURES / "scenarios-synthetic.json")
    assert len(lib) == 61 and d.find_team_grows(lib, None) == 62
