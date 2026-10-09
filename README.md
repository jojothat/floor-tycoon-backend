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
| `tests/fixtures/*-synthetic.json` | Synthetic test data, made up for tests and labelled `"_synthetic": true`. Never real data. |

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

Configuration is through environment variables (see `app/config.py`): `DATABASE_URL` (any Postgres, so any Korean-region provider), `ACCESS_MODE` (`invite` or `sso`), `RETENTION_MONTHS`, `WRITE_KEY_TTL_MINUTES`, `MIN_GROUP_SIZE`, `SCENARIOS_PATH` and `DEAL_Z7_SCENARIO_ID`.

The scheduled job (quality scores, repeat plays, derived tables) runs with `python -m app.clean`. Run it every 15 minutes during the pilot. Quality thresholds and penalties are in `app/quality.py`.

The management read API (`/v1/read/...`) needs a viewer: `python -m app.admin add-viewer <id> <token> [--export]`, then `Authorization: Bearer <token>`. Set `READ_AUTH=sso` to use the SSO header instead, with `READ_EXPORTERS` listing who may export. Every call is written to `audit_log`.

The retention job runs daily with `python -m app.retention` (`--dry-run` to count only). It deletes raw events once a pilot ended more than `RETENTION_MONTHS` ago, and keeps derived tables and scores.

Rate limits: `RATE_LIMIT_IP_PER_MIN` (default 600) and `RATE_LIMIT_PILOT_PER_MIN` (default 6000), 0 to turn one off. Set `RATE_LIMIT_SECRET` to the same value on every instance, and `TRUST_PROXY=true` behind a load balancer.

Browser access: set `CORS_ORIGINS` to the game's origin (and the dashboard's, if separate), comma-separated, for example `CORS_ORIGINS=https://play.example.kr`. Empty allows no cross-origin calls.
