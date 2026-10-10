# Floor Tycoon frontend: how the game talks to the backend

The game is one static web page (`public/index.html`) plus picture folders. There is no build step needed to run it: serve `public/` from any static server.

## Folder map

| Path | What it is |
| --- | --- |
| `public/index.html` | The game: HTML, CSS and JavaScript in one file (about 3 MB). It includes three.js r160 and the floor pictures. |
| `public/config.js` | Runtime settings. The Docker image rewrites it from env vars at start. |
| `public/ll/`, `public/web/`, `public/web2/`, `public/sp/` | Pictures the page loads as it needs them: floor layers, cards, add-something tiles. |
| `src/` | The source to rebuild the page: `game_tpl.html` plus `build.py`. Run `cd src && python3 build.py`, which writes `public/`. |
| `mock-api/server.mjs` | A zero-dependency Node mock of the backend contract, in memory. It is the reference for what the game sends and expects. |
| `e2e/play.mjs` | Plays one full game in a phone-sized browser and checks every API call landed. |
| `docker/`, `docker-compose.yml` | The game image (nginx), mock API image, e2e image, and compose for web + api + db. |

## Switching the backend on

The game reads `window.FT_CONFIG` from `config.js`:

```js
window.FT_CONFIG = { apiBase: '/api', pilotToken: 'dev-pilot', gameVersion: 'dev', simMs: 20000 };
```

- `apiBase` set: the game calls the backend (below).
- `apiBase` empty or missing: the game behaves like the Claude artifact playtest. The log stays in the browser and the wall uses the artifact store. That is why the same `game_tpl.html` builds both.
- `simMs`: length of the simulated day. Tests can shorten it.

In Docker, nginx serves the game and proxies `/api/*` to the backend (`API_UPSTREAM`, default `http://api:8000`), stripping `/api`. The phone talks to one origin, so CORS is not needed in production. The mock still sends CORS headers for local use.

## The calls, in play order

Every request carries `X-FT-Player: <anon_player_id>`, a random UUID kept in the phone's localStorage. It is never a name. Calls after the session is created also carry `Authorization: Bearer <write_key>`.

1. **Start pressed** (after the employee type and look screens): `POST /v1/sessions`
   ```json
   {"client_session_id":"<uuid made on the phone>","anon_player_id":"<uuid>","pilot_token":"dev-pilot","game_version":"dev",
    "flow":"quick","role_type":"CE|PSE|IE|Sales|Support|Lead|none","language":"en-IN","device_class":"phone|tablet|desktop","viewport_h":390,"viewport_w":844}
   ```
   - Expects `201 {"session_id":"…","deal":[7 scenario ids],"write_key":"…"}`.
   - **Must be idempotent on `client_session_id`.** If the call fails or takes over 2.5 s, the game plays offline with its own local deal. It then retries this same body before the first event batch.
   - The game uses the server's `deal` only if it is 7 known scenario ids covering the 7 places once each; otherwise it deals locally. Either way, the `deal` event says which (`src:'server'|'local'`).
   - `session_id` may differ from `client_session_id`; the game switches to it.
2. **Every event** (see `events.md` in the backend pack) is queued with `seq` = 1, 2, 3… per session and sent as `POST /v1/sessions/{id}/events` with `{"events":[…]}`.
   - **When:** every 10 events, every 15 s, when the tab is hidden, and on page close (`keepalive`).
   - **Batch size:** up to 50 events.
   - **Retries:** the game re-sends anything not acknowledged with a 2xx, so **dedupe on (session, seq)**. The queue survives a reload (localStorage `ft_q`) and is re-sent on the next visit.
   - New events added for the backend: `role_set`, `palette_set` (palette, name), `sim_done` (use, happy, deskU, peak), `render_view`, `wall_post`, and `session_end` (reason `pagehide`). `deal` now carries `src`.
3. **First arrival at the 18:00 floor:** `POST /v1/sessions/{id}/finish` with `{"picks":{"z1":"a",…},"skips":{"z4":"none"}}`. This counts as a completed game for the wall's games-by-type strip.
4. **Simulated day done:** `POST /v1/sessions/{id}/floor`:
   ```json
   {"palette":3,"palette_name":"Japandi","picks":{…},"skips":{…},
    "addition":{"adds":{"z1":"focuspod"},"deskfit":null,"swaps":{}},
    "sim":{"use":14,"happy":62,"desk_use":36,"desks":12,"peak":14,"peak_at":"09:15","busy":[…],"occ":[…],"occ_fab":[…],"fab_people":5}}
   ```
   It is only sent for the player's own floor, not when watching an office from the wall.
5. **Share to the wall:** `POST /v1/wall` with `{"session_id":"…"}`. One post per anon player; the latest replaces the last. The server builds the post from the session's deal and floor.
6. **Wall opened:** `GET /v1/wall` returns:
   ```json
   {"posts":[{"id":"…","mine":true,"deal":[…],"picks":{…},"skips":{…},"adds":{…},"deskfit":null,"swaps":{},"pal":3,"use":14,"happy":62,"views":2,"ts":1760000000000}],
    "games":{"CE":34,"PSE":21,"Support":18,"other":27,"total":100}}
   ```
   - `mine` is worked out from `X-FT-Player`. Anon ids never leave the server.
   - `games`: types with fewer than 10 completed games are merged into `other` **on the server**. The game shows labelled sample numbers until `total` reaches 10.
7. **An office opened from the wall:** `POST /v1/wall/{post_id}/view` with `{}`. Unique per viewer; the poster's own views are not counted.

All game code for this is in one block in `src/game_tpl.html`; search for `backend link`. The wall still builds each office picture on the phone from `deal` + `picks` + the addition fields, so the server stores those as-is.

## Testing it

Without Docker (Node 18+):

```sh
cd frontend
STATIC_DIR=$PWD/public node mock-api/server.mjs                 # game + mock API on :8787 (put scenarios.json beside server.mjs or set SCENARIOS)
BASE_URL=http://localhost:8787 node e2e/play.mjs                # needs: npm i playwright && npx playwright install chromium
```

With Docker:

```sh
cd frontend
docker compose --profile mock up -d --build web mock            # game against the mock
docker compose --profile test run --rm e2e                      # scripted full play → "E2E PASSED"
docker compose up -d --build web api db                         # game against the real backend (adjust the api service first)
docker compose --profile test run --rm e2e
```

The e2e test plays as a Customer Engineer:

- answers 6 moments and skips 1;
- declines Add something;
- picks a palette, waits for the simulated day, opens the render and shares to the wall.

It then checks:

- sessions, events, finish, floor, wall post and wall read all returned 2xx;
- the player's office shows as "You";
- `GET /v1/wall` marks it `mine`;
- the response never contains the anon id;
- the page threw no errors.

## Known limits

- The page is one 3 MB file because it inlines three.js and the base floor pictures. In production, move three.js to a pinned ES-module import and lazy-load the picture folders per moment. Both are in the production handoff list, not needed for the pilot.
- Google Fonts load from fonts.googleapis.com. Self-host them if the pilot network blocks Google.
- `#classic` on the URL still gives the old Keep / Rethink flow (`flow:"classic"`).
