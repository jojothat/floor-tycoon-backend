import random

import pytest
from sqlalchemy import select

from app import clean
from app.config import get_settings
from app.models import AuditLog, Moment
from app.privacy import merge_small, protect
from app.read import weighted_shares
from tests.conftest import EXPORT_TOKEN, TOKEN
from tests.scripted import script_play
from tests.test_clean import play_through_api

FEW = "fewer than 10"

pytestmark = pytest.mark.usefixtures("viewers")


def read(client, path, token=TOKEN, **params):
    return client.get(f"/v1/read/{path}", params=params, headers={"Authorization": f"Bearer {token}"})


def ok(client, path, **params):
    r = read(client, path, **params)
    assert r.status_code == 200, r.text
    return r.json()


def populate(client, new_session, db, groups, extra=None):
    """groups: [(role, kind, count)]. `extra(i, session)` can send more events."""
    lib = clean.library(str(get_settings().scenarios_path))
    rng = random.Random(11)
    i = 0
    for role, kind, count in groups:
        for _ in range(count):
            i += 1
            s, _ = play_through_api(client, new_session, *script_play(lib, kind, rng, role), anon=f"anon-read-{i:05d}", role=role)
            if extra:
                extra(i, s)
    clean.run(db)


def scenario(rows, sid):
    return next(r for r in rows if r["scenario_id"] == sid)


# ---------------------------------------------------------------- viewer and audit


def test_read_needs_a_viewer(client):
    assert client.get("/v1/read/summary").status_code == 401
    assert read(client, "summary", token="nope").status_code == 401


def test_read_with_sso(client, monkeypatch):
    monkeypatch.setenv("READ_AUTH", "sso")
    assert client.get("/v1/read/summary").status_code == 401
    assert client.get("/v1/read/summary", headers={"X-Auth-User": "manager@sso"}).status_code == 200


def test_every_call_is_audited(client, db):
    ok(client, "summary")
    read(client, "summary", type="PSE")
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(r.viewer_id, r.endpoint, r.params, r.refused) for r in rows] == [
        ("manager-1", "summary", {}, False), ("manager-1", "summary", {"type": "PSE"}, True)]


# ---------------------------------------------------------------- the group-of-10 rule


def test_no_data_yet_hides_everything_but_refuses_nothing(client):
    out = ok(client, "summary")
    assert out["plays"] == 0 and out["finished_pct"] == FEW and out["median_play_s"] == FEW
    assert ok(client, "picks")["scenarios"] == []


def test_under_ten_plays_every_cell_is_hidden(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 9)])
    out = ok(client, "picks", by="type")
    row = scenario(out["scenarios"], 1)  # "The team grows to 14" is in every deal
    assert row == {"scenario_id": 1, "zone": "z7", "n": FEW, "value": FEW, "by_type": FEW}
    assert ok(client, "summary")["finished_pct"] == FEW


def test_small_types_merge_into_other(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("PSE", "good", 3)])
    row = scenario(ok(client, "picks", by="type")["scenarios"], 1)
    assert row["n"] == 15
    # PSE (3) would be "Other" on its own, and CE minus All would show it, so all merge.
    assert [(g["type"], g["n"]) for g in row["by_type"]] == [("Other", 15)]


def test_types_of_ten_show_on_their_own(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("PSE", "good", 10)])
    row = scenario(ok(client, "picks", by="type")["scenarios"], 1)
    assert sorted((g["type"], g["n"]) for g in row["by_type"]) == [("CE", 10), ("PSE", 10)]
    assert all(abs(sum(g["value"]["shares"].values()) - 1) < 0.01 for g in row["by_type"])


def test_filter_that_isolates_a_small_group_is_refused(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("PSE", "good", 3)])
    assert read(client, "picks", type="PSE").status_code == 403, "3 plays"
    assert read(client, "picks", type="CE").status_code == 403, "everyone minus CE is 3 plays"
    assert read(client, "summary", type="PSE").status_code == 403
    assert read(client, "picks", pilot="pilot-1").status_code == 200, "the whole pilot"


def test_filter_allowed_when_both_sides_are_ten(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("PSE", "good", 10)])
    out = ok(client, "picks", type="CE")
    assert out["plays"] == 10 and scenario(out["scenarios"], 1)["n"] == 10


def test_date_and_version_filters(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10)])
    assert read(client, "summary", date_from="2000-01-01").status_code == 200, "everyone"
    assert read(client, "summary", date_from="2999-01-01").status_code == 403, "nobody"
    assert read(client, "summary", game_version="other").status_code == 403


def test_quality_mix_hides_small_buckets_and_their_difference(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("CE", "speeding", 4)])
    out = ok(client, "summary")
    assert out["plays"] == 16 and out["finished_pct"] == 100
    # half (4) is hidden, and so is full (12), or plays - full would give half away.
    assert out["quality_mix"] == {"full": "hidden", "half": FEW, "dropped": FEW, "unscored": FEW}


@pytest.mark.parametrize("counts,expected", [
    ({"a": 12, "b": 4, "c": 0}, {"a": "hidden", "b": FEW, "c": FEW}),
    ({"a": 12, "b": 10, "c": 4}, {"a": 12, "b": "hidden", "c": FEW}),
    ({"a": 12, "b": 10, "c": 0}, {"a": 12, "b": 10, "c": FEW}),
    ({"a": 3, "b": 4, "c": 5}, {"a": FEW, "b": FEW, "c": FEW}),
])
def test_protect(counts, expected):
    assert protect(counts, 10) == expected


def test_merge_small():
    assert merge_small({"a": 9}, 10) is None
    assert merge_small({"a": 12, "b": 11}, 10) == {"a": ["a"], "b": ["b"]}
    assert merge_small({"a": 12, "b": 6, "c": 5}, 10) == {"a": ["a"], "Other": ["b", "c"]}


# ---------------------------------------------------------------- which plays count


def test_dropped_repeat_and_unfinished_plays_do_not_count_for_preferences(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("CE", "speeding_blind", 3)])
    # A repeat play of an existing player, and a play left before 18:00.
    lib = clean.library(str(get_settings().scenarios_path))
    play_through_api(client, new_session, *script_play(lib, "good", random.Random(1)), anon="anon-read-00001")
    s = new_session(anon_player_id="anon-leaver-0001")
    client.post(f"/v1/sessions/{s['session_id']}/events", headers=s["headers"], json={"events": [{"seq": 1, "ev": "deal", "t": 0, "ids": [1]}]})
    clean.run(db)
    assert ok(client, "picks")["plays"] == 10
    assert ok(client, "summary")["plays"] == 15


def test_half_weight_plays_count_half(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10), ("CE", "speeding", 10)])
    out = ok(client, "picks")
    row = scenario(out["scenarios"], 1)
    ms = db.scalars(select(Moment).where(Moment.scenario_id == 1)).all()
    from app.models import Quality
    w = {q.session_id: q.weight for q in db.scalars(select(Quality))}
    expected = weighted_shares([(m.chosen, w[m.session_id]) for m in ms], row["value"]["shares"].keys())
    assert row["n"] == 20 and row["value"]["shares"] == expected


def test_weighted_shares():
    assert weighted_shares([("a", 1), ("b", 0.5), ("b", 0.5)], "ab") == {"a": 0.5, "b": 0.5}


# ---------------------------------------------------------------- the other endpoints


def test_funnel(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12)])
    steps = ok(client, "funnel")["steps"]
    assert steps["started"] == 12 and steps["moment_7"] == 12 and steps["reached_18"] == 12
    assert steps["palette"] == 12 and steps["render"] == FEW and steps["wall"] == FEW


def test_skips(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 5), ("CE", "skip_everything", 6)])
    out = ok(client, "skips")
    z7 = next(z for z in out["zones"] if z["zone"] == "z7")
    assert z7["n"] == 11 and 0 <= z7["value"] <= 1
    assert scenario(out["scenarios"], 1)["n"] == 11


def test_edits_and_additions(client, new_session, db):
    def extra(i, s):
        url = f"/v1/sessions/{s['session_id']}/events"
        evs = [{"seq": 100, "ev": "edit", "t": 900_000, "m": "z7", "from": "a", "to": "b"}]
        if i <= 11:
            evs.append({"seq": 101, "ev": "add", "t": 901_000, "m": "z5", "item": "plants", "via": "end"})
        else:
            evs.append({"seq": 101, "ev": "addSkip", "t": 901_000})
        client.post(url, headers=s["headers"], json={"events": evs})

    populate(client, new_session, db, [("CE", "good", 12)], extra=extra)
    e = scenario(ok(client, "edits")["scenarios"], 1)
    assert e["n"] == 12 and e["value"] == 1.0 and e["edits"] == 12 and e["from_to"] == {"a->b": 1.0}

    a = ok(client, "additions")
    assert a["top_items"]["value"] == [{"item": "plants", "count": 11}]
    assert a["how_placed"]["value"]["end"] == 1.0
    assert a["decline_rate"]["n"] == 12 and a["decline_rate"]["value"] == round(1 / 12, 3)


def test_sim(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 10)])
    d = ok(client, "sim")["distributions"]
    assert d["n"] == 10 and d["value"]["use"] == {"p25": 14, "median": 14, "p75": 14}


# ---------------------------------------------------------------- export


def test_export_needs_permission_and_is_audited(client, new_session, db):
    populate(client, new_session, db, [("CE", "good", 12), ("PSE", "good", 3)])
    assert read(client, "export.csv").status_code == 403
    r = read(client, "export.csv", token=EXPORT_TOKEN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0] == "scenario_id,zone,group,plays,option,share"
    assert any(line.startswith("1,z7,All,15,") for line in lines)
    assert any(line.startswith("1,z7,Other,15,") for line in lines)
    assert not any(",PSE," in line for line in lines), "no group under 10"
    audits = [(a.viewer_id, a.export, a.refused) for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert audits == [("manager-1", True, True), ("analyst-1", True, False)]
