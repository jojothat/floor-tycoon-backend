"""The plain-English console lines (app/activity.py): readable, and never naming a player."""

import logging

import pytest

from app import activity
from tests.conftest import ROOT, load_json
from tests.test_replay import replay


class Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def console():
    h = Lines()
    activity.log.addHandler(h)
    yield h.lines
    activity.log.removeHandler(h)


def test_a_played_game_reads_as_plain_english(client, new_session, console):
    play = load_json(ROOT / "sample" / "play-log-sample.json")
    s, _ = replay(client, new_session, play)
    text = "\n".join(console)
    game = f"[game {s['session_id'][:6]}]"
    assert f"{game}  New game started" in text
    assert "The team grows to 14: chose" in text, "moments are named by their question and fix"
    assert "none of these work, skipped" in text
    assert "End of day: changed" in text
    assert "Batch was a resend: nothing new, 10 repeats ignored" in text, "retried batches are called out"
    assert "Reached 18:00 · 6 fixes kept, 1 skipped" in text
    assert "events to the database (this game now has" in text, "says where the events went"
    # Never a player id, the session id in full, or the character look.
    assert s["session_id"] not in text and "anon" not in text and "hair" not in text


def test_turned_off(client, new_session, console, monkeypatch):
    monkeypatch.setenv("LOG_ACTIVITY", "off")
    replay(client, new_session, load_json(ROOT / "sample" / "play-log-sample.json"))
    assert console == []


def test_free_text_is_never_written():
    line = activity.describe({"ev": "skip", "m": "z1", "text": "my desk by the window"}, {}, {})
    assert "window" not in line and "written note" in line
