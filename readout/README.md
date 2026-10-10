# Space Readout

The management dashboard for Floor Tycoon. It shows what Applied Materials Korea employees chose when they fixed their own workday on the floor:

- where they agree;
- where none of the fixes worked;
- what they would add;
- how leads and staff differ;
- what kinds of space their floors add up to.

It reads anonymous, aggregated game telemetry from the backend and tells it as 13 numbered slides: a headline written from the data, a picture or hero number, one chart (each slide a different form), and a "So what". Any group under 10 games is hidden by the server.

## Run it

```sh
# against the mock, with synthetic plays (no backend needed)
docker compose --profile mock up --build -d readout mock
docker compose --profile mock --profile seed run --rm seed
open http://localhost:8081            # tick "Include synthetic test plays"
docker compose --profile mock --profile test run --rm e2e

# just look at it with labelled sample data
RO_SAMPLE=1 docker compose up --build readout

# without Docker
STATIC_DIR=public node mock-api/server.mjs &               # API + dashboard on :8787 (read token dev-viewer)
API=http://localhost:8787 N=300 node tools/seed.mjs
open http://localhost:8787
READOUT_URL=http://localhost:8787 node e2e/readout.mjs      # needs playwright installed
```

`space-readout-standalone.html` is the whole page with the sample data inlined. Open it from a file to preview it.

## What's here

| Path | What |
| --- | --- |
| `src/app.html` | The dashboard: markup, styles and script |
| `tools/build.py` | Writes `public/index.html` and the standalone file from `src/app.html` |
| `tools/assets.py` | Copies the game's floor pictures into `public/assets/` at half size |
| `public/assets/` | Base floor, zone and library layers, item sprites, with the game's placement metadata |
| `public/` | What the container serves: `index.html`, `config.js` (written at start), `sample-data.js`, `assets/` |
| `READOUT.md` | The read API contract and metric definitions |
| `CLAUDE.md` | Instructions for Claude Code |
| `mock-api/` | Reference mock of the game API and read API (same as `frontend/mock-api/`), with `palettes.json` |
| `tools/seed.mjs` | Plays synthetic games through the game API (dev and staging only) |
| `tools/capture-sample.mjs` | Rebuilds `sample-data.js` from a synthetic-only store |
| `e2e/readout.mjs` | API and browser checks, desktop and phone |
| `docker/`, `docker-compose.yml` | nginx image (proxies only `/api/v1/read/`), mock, seed, e2e |

## Config (container env)

| Var | Default | |
| --- | --- | --- |
| `API_UPSTREAM` | `http://api:8000` | the backend |
| `RO_API_BASE` | `/api` | where the page calls |
| `RO_READ_TOKEN` | empty | viewer token for dev and staging; empty in production behind SSO |
| `RO_SAMPLE` | `0` | `1` shows the labelled sample data and ignores the backend |
