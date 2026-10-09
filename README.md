# Floor Tycoon backend

The backend for Floor Tycoon, a 2.5D workplace-strategy game for Applied Materials Korea (WowLabz for Zyeta). Employees play one workday on a phone. Their anonymous choices feed a management read about how the office should work.

## What's here

| File | What it is |
| --- | --- |
| `backend-spec.md` | The spec, v2 (9 Oct 2026): data model, APIs, wall numbers, quality scoring, privacy, management read, build plan. |
| `events.md` | Every event the current game logs, with fields and meaning. |
| `CLAUDE.md` | Instructions and hard rules for Claude Code. |
| `sample/play-log-sample.json` | A real play from the current build, including a skip, the add-something flow, an end-of-day edit, palette and the simulated-day results. |
| `sample/scenarios.json` | The 61 scenarios: id, place, time, theme, tier, which roles, and the fixes with their furniture ids. |

## Playtest

The game itself: https://claude.ai/artifact/RpuzqVhRsSVaAFCmqZUGuU (open it on a phone held sideways).

## Status

Spec agreed in draft. Open questions are at the end of `backend-spec.md`: hosting provider, access method, retention, viewers, role codes and pilot size.

## Run it locally

You need Docker and Python 3.11+.

```sh
docker compose up -d db                                  # Postgres 16, plus the test database
pip install -r requirements.txt
python -m app.db && python -m app.admin add-pilot local site-a local-invite
uvicorn app.main:app --reload                            # http://localhost:8000/docs
pytest                                                   # the tests use floor_tycoon_test
```

`POST /v1/sessions` needs the scenario library at `sample/scenarios.json`. To try it before that file lands, set `SCENARIOS_PATH=tests/fixtures/scenarios-synthetic.json`.

Configuration is through environment variables (see `app/config.py`): `DATABASE_URL` (any Postgres, so any Korean-region provider), `ACCESS_MODE` (`invite` or `sso`), `RETENTION_MONTHS`, `WRITE_KEY_TTL_MINUTES`, `MIN_GROUP_SIZE`, `SCENARIOS_PATH` and `DEAL_Z7_SCENARIO_ID`.
