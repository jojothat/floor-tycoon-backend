# Notes for Claude Code: the Space Readout (management dashboard)

This folder is the management dashboard (v2, the story readout). It is a static page that reads aggregated game telemetry from the backend's read API and tells it as 13 numbered slides with pictures from the game.

- Read `READOUT.md` first: it defines every `/v1/read/*` endpoint, its response shape, where each number comes from in the game's events, and the group-of-10 rule.
- `READOUT.md` §0 lists the slides, their chart forms and where the pictures come from. Keep the varied chart forms; don't collapse slides into bar charts.
- `mock-api/read.mjs` is a working reference implementation. The dashboard passes `e2e/readout.mjs` against it.

## Your job with this folder

1. **Put it in the repo as `readout/`.**
   - It arrives as `floor-tycoon-readout.zip` uploaded to the repo root.
   - Unzip it, move the contents to `readout/`, delete the zip, and commit.
   - `frontend/` (the game) and the backend at the repo root should already be there.
2. **Implement the read API in the real backend so it matches `mock-api/read.mjs` output for output.**
   - **Routes:** `GET /v1/read/{meta,summary,funnel,picks,skips,edits,additions,sharing,pain-hours,bundles,space-brief,sim,leads-gap,styles,wall,export.csv}`.
   - **Filters:** `type`, `version`, `flow`, `from`, `to`, `synthetic`.
   - **Suppression:** under-10 groups are `null` or `{n, lt10: true}`, applied on the server.
   - **Auth:** viewer token from env `READ_TOKEN` in dev and staging. Refuse to start in production without a real auth setup (see `READOUT.md` §2).
   - **Data:** compute from the backend's derived tables, not by replaying raw events per request.
   - **meta** must carry `palettes` (from `mock-api/palettes.json`, which mirrors the game's palette list), each scenario's `native` flag and each option's `item`; the page uses them for pictures and swatches.
   - **Parity test:** load the same event logs into the mock and the backend and diff every endpoint's JSON, ignoring `updated_at`. Use `frontend/sample/play-log-sample.json` plus a seeded run.
3. **Containerise.**
   - `docker compose --profile mock up --build readout mock` must work as shipped.
   - Then make the readout run against the real backend. Either:
     - add the `readout` service to `frontend/docker-compose.yml`:
       ```yaml
       readout:
         build: { context: ../readout, dockerfile: docker/readout.Dockerfile }
         ports: ["8081:80"]
         environment: { API_UPSTREAM: "http://api:8000", RO_API_BASE: /api, RO_READ_TOKEN: dev-viewer }
         depends_on: [api]
       ```
       plus `READ_TOKEN: dev-viewer` on `api`; or
     - run this folder's compose on the frontend stack's network (`networks: default: external: true, name: frontend_default`).
4. **Fill it with telemetry and test.** Use a dev or staging database only, never production.
   1. `docker compose --profile seed run --rm seed` (or `API=http://localhost:8000 N=300 node tools/seed.mjs`) plays synthetic games through the game API. They are tagged `game_version: synthetic-seed`.
   2. Play at least one real game: run the game's e2e (`cd frontend && docker compose --profile test run --rm e2e`), or play by hand at :8080.
   3. Run `docker compose --profile test run --rm e2e` (or `READOUT_URL=http://localhost:8081 REQUIRE_REAL=1 node e2e/readout.mjs`) until it prints `READOUT E2E PASSED`.
   4. Open http://localhost:8081, tick "Include synthetic test plays", and check that the counts match the database.
5. **Open a PR** with the read API, the compose wiring, the parity test, and anything in `READOUT.md` that turned out to be wrong.

## Keep the pictures in step with the game

When the game's floor art changes, run `python3 tools/assets.py ../frontend/src` then `python3 tools/build.py`, and commit `public/assets/`. The e2e fails if any option picture is missing.

## Don't

- Don't edit `public/index.html` by hand; it is generated. Change `src/app.html` and run `python3 tools/build.py`.
- Don't compute shares in the page or return raw counts behind a suppressed cell. Suppression lives in the backend.
- Don't return or show anything that identifies a person or a single game: no session ids, no player ids, no invite codes, no free text, no per-game rows in exports.
- Don't let the readout fall back to sample numbers in live mode. The container blanks `sample-data.js` unless `RO_SAMPLE=1`.
- Don't run `tools/seed.mjs` against production. Don't run `tools/capture-sample.mjs` against anything with real games (it refuses if it sees one).
- Don't proxy the game's write endpoints through the readout origin. `docker/readout.nginx.conf.template` passes only `/api/v1/read/`.
- No PPE or gowning content, no cost or money angle, no study names, and sample data always labelled.

## Facts the tests rely on

- **Live mode:** the page shows a "● Live data" pill, and synthetic plays are hidden until the checkbox in the Filters drawer is ticked.
- **With enough data:** 13 slides, each with a headline; 11 or more with a "So what"; the floor drawn from 8 or more layers; no chart text under 9 px.
- **Before 10 completed real games:** the page shows one "Not enough games yet" slide. That is correct, not a bug.
- **Checked layouts:** 1280×900 and 400×800, with no sideways scroll.
- **Charts:** every slide with numbers has "See the numbers"; colours were checked for colour-blind separation.
