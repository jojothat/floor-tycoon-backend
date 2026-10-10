"""The read API v2 (readout/READOUT.md): auth, the group-of-10 rule, filters and the
production guard. Output shapes are checked against the reference mock in
tests/test_read_parity.py."""

import random

import pytest
from sqlalchemy import select

from app import clean
from app.config import Settings, get_settings
from app.main import check_production
from app.models import AuditLog, Quality
from app.privacy import merge_small
from tests.conftest import EXPORT_TOKEN, TOKEN
from tests.scripted import script_play
from tests.test_clean import play_through_api

pytestmark = pytest.mark.usefixtures("viewers")


def read(client, path, token=TOKEN, **params):
    return client.get(f"/v1/read/{path}", params=params, headers={"Authorization": f"Bearer {token}"} if token else {})


def ok(client, path, **params):
    r = read(client, path, **params)
    assert r.status_code == 200, r.text
    return r.json()


def populate(client, new_session, db, groups, version="pilot-1.0"):
    """groups: [(role, kind, count)], played through the game API."""
    lib = clean.library(str(get_settings().scenarios_path))
    rng = random.Random(11)
    i = 0
    for role, kind, count in groups:
        for _ in range(count):
            i += 1
            events, picks = script_play(lib, kind, rng, role)
            play_through_api(client, new_session, events, picks, anon=f"anon-read-{version}-{i:05d}", role=role,
                             game_version=version)
    clean.run(db)


def team(picks):
    return next(s for s in picks["scenarios"] if s["id"] == 1)  # dealt in every game


# ---------------------------------------------------------------- viewer and audit


def test_read_needs_a_viewer(client):
    assert read(client, "summary", token=None).status_code == 401
    assert read(client, "summary", token="nope").status_code == 401


def test_the_shared_dev_token(client, monkeypatch):
    assert read(client, "summary", token="dev-viewer").status_code == 401, "no default token"
    monkeypatch.setenv("READ_TOKEN", "dev-viewer")
    assert read(client, "summary", token="dev-viewer").status_code == 200
    assert read(client, "export.csv", token="dev-viewer").status_code == 200, "the readout's export button"


def test_read_with_sso(client, monkeypatch):
    monkeypatch.setenv("READ_AUTH", "sso")
    assert client.get("/v1/read/summary").status_code == 401
    assert client.get("/v1/read/summary", headers={"X-Auth-User": "manager@sso"}).status_code == 200


def test_every_call_is_audited(client, db):
    ok(client, "summary")
    ok(client, "picks", type="PSE", synthetic="include")
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(r.viewer_id, r.endpoint, r.params) for r in rows] == [
        ("manager-1", "summary", {}), ("manager-1", "picks", {"type": "PSE", "synthetic": "include"})]


def test_export_needs_permission(client):
    assert read(client, "export.csv").status_code == 403
    r = read(client, "export.csv", token=EXPORT_TOKEN)
    assert r.status_code == 200 and r.text.startswith("scenario_id,question,option,name,share_pct,n")


def test_unknown_endpoint_and_bad_filters(client):
    assert read(client, "nope").status_code == 404
    assert read(client, "summary", type="CEO").status_code == 422
    assert read(client, "summary", **{"from": "yesterday"}).status_code == 422
    assert read(client, "summary", synthetic="all").status_code == 422


# ---------------------------------------------------------------- the group-of-10 rule


def test_no_data_yet(client):
    s = ok(client, "summary")
    assert (s["n"], s["started"], s["completion"], s["median_play_s"]) == (0, 0, None, None)
    funnel = ok(client, "funnel")
    assert {k: v for k, v in funnel.items() if k != "updated_at"} == {"n": 0, "steps": None, "lt10": True}
    assert ok(client, "picks")["scenarios"] == []


def test_under_ten_games_every_number_is_hidden(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 9)])
    row = team(ok(client, "picks"))
    assert row["n"] == 9 and row["lt10"] is True
    assert all(o["share"] is None for o in row["options"])
    assert (row["skip_share"], row["top_share"], row["median_decide_s"], row["edit_share"]) == (None,) * 4
    s = ok(client, "summary")
    assert (s["completion"], s["median_play_s"], s["by_type"]["CE"]) == (None, None, None)
    sharing = ok(client, "sharing")
    assert sharing["team_desks"]["by_type"]["all"] == {"n": 9, "lt10": True}
    assert ok(client, "skips")["rows"] == [] and ok(client, "edits")["rows"] == []


def test_ten_games_show(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10)])
    row = team(ok(client, "picks"))
    assert row["n"] == 10 and "lt10" not in row
    assert abs(sum(o["share"] for o in row["options"]) + row["skip_share"] - 100) < 0.5
    assert ok(client, "summary")["by_type"]["CE"] == 10


def test_a_filter_that_leaves_out_a_small_group_hides_everything(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("PSE", "good", 3)])
    # CE alone is 12 games, but "everyone minus CE" would be the 3 PSE games.
    ce = ok(client, "picks", type="CE")
    assert ce["n"] == 12 and team(ce)["lt10"] is True and all(o["share"] is None for o in team(ce)["options"])
    assert ok(client, "summary", type="CE")["completion"] is None
    # Unfiltered, the 15 games show.
    assert "lt10" not in team(ok(client, "picks"))


def test_filters_where_both_sides_are_ten(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("PSE", "good", 10)])
    ce = ok(client, "picks", type="CE")
    assert ce["n"] == 10 and "lt10" not in team(ce)


# ---------------------------------------------------------------- which games count


def test_synthetic_plays_are_left_out_unless_asked(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10)], version="pilot-1.0")
    populate(client, new_session, db, [("CE", "good", 12)], version="synthetic-seed")
    assert ok(client, "summary")["completed"] == 10
    assert ok(client, "summary", synthetic="include")["completed"] == 22
    assert ok(client, "summary", synthetic="only")["completed"] == 12
    meta = ok(client, "meta")
    assert (meta["n_all"], meta["synthetic_in_store"]) == (22, 12)
    assert set(meta["versions"]) == {"pilot-1.0", "synthetic-seed"}


def test_unfinished_games_count_only_as_started(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10)])
    s = new_session(anon_player_id="anon-leaver-0001")
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [
        {"seq": 1, "ev": "deal", "t": 0, "ids": s["deal"]}, {"seq": 2, "ev": "answer", "t": 5000, "m": "z1"},
        {"seq": 3, "ev": "keep", "t": 9000, "m": "z1", "opt": "a", "rethinks": 0}]})
    summary = ok(client, "summary")
    assert (summary["started"], summary["completed"], summary["completion"]) == (11, 10, 90.9)
    steps = {st["key"]: st["n"] for st in ok(client, "funnel")["steps"]}
    assert (steps["started"], steps["m1"], steps["m2"], steps["end"]) == (11, 11, 10, 10)


def test_read_quality_on_leaves_out_low_scores_and_repeats(client, new_session, db, monkeypatch):
    populate(client, new_session, db, [("CE", "good", 10), ("CE", "speeding_blind", 3)])
    assert ok(client, "summary")["completed"] == 13, "by default every completed game counts"
    monkeypatch.setenv("READ_QUALITY", "on")
    assert ok(client, "summary")["completed"] == 10
    assert db.scalar(select(Quality).where(Quality.score < 40)) is not None


# ---------------------------------------------------------------- meta and the game's catalogue


def test_meta_carries_palettes_native_and_items(client):
    meta = ok(client, "meta")
    assert len(meta["palettes"]) == 10 and set(meta["palettes"][0]) == {"name", "kind", "colors", "desc"}
    assert meta["types"] == ["CE", "PSE", "IE", "Sales", "Support", "Lead"]
    s1 = next(s for s in meta["scenarios"] if s["id"] == 1)
    assert s1["native"] is True and s1["question"] == "The team grows to 14"
    assert s1["options"][0] == {"k": "a", "name": "Compact Desks", "item": "bench"}
    assert len(meta["scenarios"]) == 61


# ---------------------------------------------------------------- the production guard


def test_production_refuses_a_shared_token_or_default_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RATE_LIMIT_SECRET", "a-real-secret")
    check_production(Settings())  # named viewers or SSO, a real secret: fine
    monkeypatch.setenv("READ_TOKEN", "dev-viewer")
    with pytest.raises(RuntimeError, match="READ_TOKEN"):
        check_production(Settings())
    monkeypatch.delenv("READ_TOKEN")
    monkeypatch.setenv("RATE_LIMIT_SECRET", "change-me")
    with pytest.raises(RuntimeError, match="RATE_LIMIT_SECRET"):
        check_production(Settings())
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("READ_TOKEN", "dev-viewer")
    check_production(Settings())


def test_merge_small():
    assert merge_small({"a": 9}, 10) is None
    assert merge_small({"a": 12, "b": 11}, 10) == {"a": ["a"], "b": ["b"]}
    assert merge_small({"a": 12, "b": 6, "c": 5}, 10) == {"a": ["a"], "Other": ["b", "c"]}


def test_reads_reuse_the_loaded_plays_briefly(client, new_session, db, monkeypatch):
    from app import read as read_mod
    read_mod._cache.clear()
    monkeypatch.setenv("READ_CACHE_SECONDS", "60")
    populate(client, new_session, db, [("CE", "good", 10)])
    first = ok(client, "summary")
    populate(client, new_session, db, [("PSE", "good", 1)], version="pilot-1.1")
    again = ok(client, "summary")
    assert again["completed"] == first["completed"] == 10 and again["updated_at"] == first["updated_at"]
    monkeypatch.setenv("READ_CACHE_SECONDS", "0")
    assert ok(client, "summary")["completed"] == 11
    read_mod._cache.clear()
