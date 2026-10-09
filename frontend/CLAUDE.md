# Notes for Claude Code: the game frontend

This folder is the game. Read `FRONTEND.md` first: it says exactly which API calls the game makes, when, and with what bodies. `mock-api/server.mjs` is a working reference of that contract. The game passes `e2e/play.mjs` against it.

## Your job with this folder

1. **Put it in the repo as `frontend/`.** It arrives as `floor-tycoon-frontend.zip` uploaded to the repo root. Unzip it, move the contents to `frontend/`, delete the zip, and commit.
2. **Make the real backend match the contract in `FRONTEND.md`.** Where `backend-spec.md` and `FRONTEND.md` differ, `FRONTEND.md` wins for request and response shapes, because it is what the game actually sends. In particular:
   - `client_session_id` makes session creation idempotent;
   - `X-FT-Player` identifies the anon player;
   - the wall post is built on the server from the session;
   - `GET /v1/wall` returns `posts[].mine`, `posts[].views` and `games` with the under-10 merge.
3. **Containerise.**
   - Give the backend a `Dockerfile` at the repo root that listens on 8000 (or change `API_UPSTREAM` in `frontend/docker-compose.yml`).
   - Wire the backend's database settings into the `api` service.
   - Make `docker compose up --build web api db` work.
4. **Test end to end.** Run `docker compose --profile test run --rm e2e` against the real backend until it prints `E2E PASSED`.
   - Then add the e2e run to CI if there is CI.
   - Keep the mock profile working: it is how frontend changes get tested without a database.
5. **Open a PR** with what changed in the backend to meet the contract, and anything in `FRONTEND.md` that turned out to be wrong.

## Don't

- Don't edit `public/index.html` by hand; it is generated. Change `src/game_tpl.html` and run `cd src && python3 build.py`.
- Don't add any field that identifies a person. Don't send or store the character look.
- Don't drop the under-10 rule from `GET /v1/wall` `games`.
- No PPE or gowning content, no cost fields, no study names, and sample data always labelled.

## Facts the tests rely on

- Viewport 844×390 (a phone held sideways). The game is also checked at heights down to 250 px.
- A full scripted play takes about 2–3 minutes, most of it the walks and the 20 s simulated day. Set `FT_SIM_MS` lower to speed up tests.
- Picture and page caching is set in `docker/nginx.conf.template`.
