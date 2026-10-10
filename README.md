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

## Run the whole backend in Docker

The API, the scheduled jobs and Postgres, as containers:

```sh
cp .env.example .env                                     # then change the password and secret
docker compose up -d --build --wait                      # API on :8000, health at /healthz
docker compose exec api python -m app.admin add-pilot test site-a test-invite
docker compose exec api python -m app.admin add-viewer me my-viewer-token
python -m scripts.smoke --url http://localhost:8000 --pilot-token test-invite --viewer-token my-viewer-token
```

The smoke test plays the real sample log over HTTP the way the game would (session, events with retried batches, 18:00, edits, floor, wall, views), plus 11 scripted plays, and reads the management API back. Its plays are labelled sample data (`game_version` `sample-smoke-test`), so use a test pilot.

Postgres only listens on this machine (`127.0.0.1`), with the password from `.env`. Behind a TLS-inspecting proxy, set `EXTRA_CA_FILE` to its CA so the build can install packages.

### On a test server (for example a DigitalOcean droplet)

The same steps work on any Linux server with Docker. Clone the repo, create `.env`, `docker compose up -d --build --wait`, and run the smoke test from your own machine against `http://<server-ip>:8000`. For HTTPS, put Caddy or nginx in front and set `TRUST_PROXY=true`.

Use a test server for sample and synthetic data only. Real pilot data must stay in a Korean cloud region (spec section 8), and DigitalOcean has no Korean region.

## Production on your own machine

`deploy/local/` runs the game, the backend, the Space Readout, the jobs and daily backups on one computer, behind HTTPS with a manager login, and with no cloud account. See `deploy/local/README.md`. It holds real pilot data only if that machine is in Korea and Applied Materials Korea accepts self-hosting (spec section 8).

## CI

GitHub Actions runs the same lint and tests on every pull request (`.github/workflows/test.yml`), against a Postgres 16 service.

Configuration is through environment variables (see `app/config.py`): `DATABASE_URL` (any Postgres, so any Korean-region provider), `ACCESS_MODE` (`invite` or `sso`), `RETENTION_MONTHS`, `WRITE_KEY_TTL_MINUTES`, `MIN_GROUP_SIZE`, `SCENARIOS_PATH` and `DEAL_Z7_SCENARIO_ID`.

The scheduled job (quality scores, repeat plays, derived tables) runs with `python -m app.clean`. Run it every 15 minutes during the pilot. Quality thresholds and penalties are in `app/quality.py`.

The management read API (`/v1/read/...`) needs a viewer: `python -m app.admin add-viewer <id> <token> [--export]`, then `Authorization: Bearer <token>`. Set `READ_AUTH=sso` to use the SSO header instead, with `READ_EXPORTERS` listing who may export. Every call is written to `audit_log`.

The retention job runs daily with `python -m app.retention` (`--dry-run` to count only). It deletes raw events once a pilot ended more than `RETENTION_MONTHS` ago, and keeps derived tables and scores.

Rate limits: `RATE_LIMIT_IP_PER_MIN` (default 600) and `RATE_LIMIT_PILOT_PER_MIN` (default 6000), 0 to turn one off. Set `RATE_LIMIT_SECRET` to the same value on every instance, and `TRUST_PROXY=true` behind a load balancer.

Browser access: set `CORS_ORIGINS` to the game's origin (and the dashboard's, if separate), comma-separated, for example `CORS_ORIGINS=https://play.example.kr`. Empty allows no cross-origin calls.
