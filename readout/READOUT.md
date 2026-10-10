# Space Readout: read API contract and metric definitions

Floor Tycoon · management dashboard · v2 · 10 Oct 2026

The Space Readout is a static page. It reads aggregated numbers from the backend's **read API** (`GET /v1/read/*`) and never sees a single player's game. The reference implementation is `mock-api/read.mjs`. Where this file and the mock differ, the mock's output shapes win and this file should be fixed.

## 0. What the page is

A story readout for management, in the approved editorial look (paper-and-ink slides): a headline that counts the people, fact pills, then numbered slides. Each slide has a kicker, a headline written from the data, a hero number or picture, one chart, a "So what" line, and "See the numbers" (a table, for accessibility and checking).

The 13 slides deliberately use different chart forms, so the page doesn't read as a wall of bar charts:

| # | Slide | Form | Endpoints |
| --- | --- | --- | --- |
| 01 | Desk sharing | hero % + option pictures + dot plot by role against the "everyone" line | sharing, meta |
| 02 | The office most people built | the floor assembled from the top pick per zone (game layers on the base floor) + pick chips + palette swatches | picks, styles, meta |
| 03 | Clear winners and split decisions | quadrant scatter (top-fix share vs median decide time) | picks |
| 04 | Where none of the fixes worked | ring gauges with the fixes that were offered | skips, meta |
| 05 | What people would add | 10×10 waffle + item tiles sized by rank + making-room line | additions, sharing |
| 06 | When in the day it hurts | heat strip on the workday clock with the peak marked | pain-hours |
| 07 | Second thoughts | big number + ranked list + picture → picture switches | edits, meta |
| 08 | Where groups disagree | 100% segmented rows by role (two moments) | sharing |
| 09 | Leads and staff | dumbbell (lead dot vs staff dot) | leads-gap |
| 10 | Fixes that go together | picture pairs with lift | bundles, meta |
| 11 | Kinds of space people built | fill tiles | space-brief |
| 12 | How far people got | milestone numbers + still-playing line | funnel, summary |
| 13 | Every moment | look-up with option pictures | picks, meta |

- **Headlines and "So what" lines** are generated in the page from the numbers by fixed rules (thresholds in `src/app.html`, one function per slide). The footer says so.
- **The simulated day is not shown.** It is a demo inside the game. `sim` stays in the API but the page doesn't call it.
- **Pictures** come from the game: `public/assets/zl` (base floor and native zone layers), `ll` (library layers), `sp` (item sprites), each with the game's `meta.json` placements. `tools/assets.py` rebuilds them from `frontend/src`. An option's picture is `ll/L<id><k>` if it exists and the moment is not native, else `zl/<zone><k>` (the game's own rule). `zl/<zone>0` is an empty zone.
- **Charts are drawn at the width they get** (re-drawn on resize), so chart text stays 12–14 px from a phone to a monitor.
- **Look:** one deliberate light theme (paper and ink), Archivo and IBM Plex Mono with system fallbacks. Option colours `#1F5FBF` `#D98A1C` `#2E9E86` `#9B4FB8` were checked for colour-blind separation; "none work" is neutral `#CFCAC0`.

## 1. How the page talks to the backend

- **Config.** `config.js` sets `window.RO_CONFIG = { apiBase, readToken }`. The container writes it from env at start (`docker/40-write-config.sh`).
- **Calls.** Each call is `GET {apiBase}/v1/read/{name}?{filters}`.
  - Header `Authorization: Bearer {readToken}` is sent only when a token is set.
  - `credentials: 'same-origin'`, so an SSO cookie on the readout's own origin is sent.
- **Behind nginx.** `apiBase` is `/api`, and nginx proxies only `/api/v1/read/*` to the backend. The game's write endpoints can't be reached from the readout origin.
- **Errors.**
  - **401 or 403:** the page shows "Sign in to view the readout" and draws nothing.
  - **Any other failure on `summary`:** the page shows "Could not reach the game backend" and draws nothing. It never falls back to sample numbers in live mode.
  - **A single card's endpoint fails:** that card shows the "Fewer than 10" placeholder.
- **Sample mode.**
  - When `apiBase` is empty, the page draws `window.RO_SAMPLE` (from `sample-data.js`). It also shows a "Sample data" chip and banner, and hides the filters.
  - The container ships sample data only with `RO_SAMPLE=1`. Otherwise it blanks `sample-data.js`.

## 2. Auth

| Environment | How a viewer gets in |
| --- | --- |
| dev, CI, staging | Viewer token. Backend env `READ_TOKEN` (default `dev-viewer` in the mock only), readout env `RO_READ_TOKEN`. |
| production | Company SSO in front of the readout origin (oauth2-proxy or the load balancer's identity layer). Leave `RO_READ_TOKEN` empty. The backend accepts read calls only from the readout proxy, either by a shared secret header the proxy adds or by network policy. |

- **No default token.** The backend must refuse every `/v1/read/*` call without valid viewer auth and must not start in production with a default token.
- **Viewer list.** A short allow-list, e.g. Zyeta and the Applied Materials Korea workplace team. Not every employee.

## 3. Filters (query string, all optional, same on every endpoint)

| Param | Values | Meaning |
| --- | --- | --- |
| `type` | `CE` `PSE` `IE` `Sales` `Support` `Lead` | Employee type picked at the start of the game |
| `version` | a `game_version` string | One build only |
| `flow` | `quick` or `classic` | Quick is the default (no Keep/Rethink); classic is `#classic` |
| `from`, `to` | `YYYY-MM-DD` | Session start date, inclusive |
| `synthetic` | absent, `include` or `only` | Synthetic plays (`game_version` starting `synthetic`) are **left out unless asked** |

Once invite codes ship (`docs/invite-codes-spec.md`), every preference number uses only `counted = true` sessions. Test-code sessions are left out everywhere. The funnel shows counted and uncounted starts separately (add `counted` / `uncounted` to each funnel step).

## 4. Rules every endpoint follows

1. **Completed games only for preferences.** A game is complete when `/finish` was called (reached 18:00). Only `funnel` and `summary.started` count unfinished games.
2. **The final pick counts.** That is the floor after any end-of-day edit. A skipped moment counts as `skip`.
3. **The group-of-10 rule, enforced on the server.**
   - A share, average or median over fewer than 10 games is `null`.
   - A whole group under 10 comes back as `{ "n": <n>, "lt10": true }` with no other numbers.
   - Lists (additions, bundles, leads-gap, edits' `top_switch`) drop entries under 10. `additions.other_items_lt10` says how many games went to items that were dropped.
   - The page never computes a share from raw counts. It only draws what the server returns.
4. **No identifiers.** No session ids, player ids, timestamps finer than the response's `updated_at`, invite codes or free text.
5. Every response has `n` (completed games in the filter) and `updated_at` (ISO time).
6. Percentages are 0–100 with one decimal. Seconds are numbers.
7. **Freshness.** The backend may cache reads for up to 5 minutes. `updated_at` is when the numbers were computed.

## 5. Endpoints

All are under `/v1/read/`. The table gives the response shape, and each endpoint's source events follow it.

| Endpoint | Response shape | Card |
| --- | --- | --- |
| `meta` | `{n, n_all, versions[], synthetic_in_store, types[], palettes:[{name, kind: Neutral or Edgy, colors[5], desc}], scenarios:[{id, zone, time, question, theme, native, options:[{k, name, item}]}]}` | filter lists, pictures, palette swatches |
| `summary` | `{n, started, completed, completion, median_play_s, by_type:{CE..Lead, none: n or null}, synthetic}` | KPI tiles, status chips |
| `funnel` | `{n: starts, steps:[{key, label, n, lt10?, dropped_lt10?}] or null, lt10?}`; a step's `n` is null when under 10 (`lt10`) or fewer than 10 short of the starts (`dropped_lt10`) | Did people actually play? |
| `picks` | `{n, scenarios:[{id, zone, time, question, theme, n, lt10?, options:[{k, name, share}], skip_share, top_share, median_decide_s, edit_share}]}` | Agree vs argue, explorer, CSV |
| `skips` | `{n, rows:[{id, question, time, n, skip_share}]}`, sorted by skip_share descending | Where none of the fixes worked |
| `edits` | `{n, any_share, rows:[{id, question, time, n, edit_share, top_switch:{from, to, from_k, to_k, n} or null}], switches:[{id, question, from:{k, name}, to:{k, name}, n, share}] (n≥10, top 4)}` | Second thoughts |
| `additions` | `{n, added_share, declined_share, items:[{id, name, cat, n, share}] (n≥10, top 15), other_items_lt10, how:{gap, end, desks_smaller, desks_share, swap}}` | What people would add |
| `sharing` | `{n, team_desks:{options[], by_type:{CE..Lead, all:{n, a, b, c, skip} or {n, lt10}}}, fab_days:{…same for CE, IE, all} or null, making_room:{n, desks_smaller, desks_share, swap} or {n, lt10}}` | Team desks by type, making room |
| `pain-hours` | `{n, hours:[{h, scenarios, decide_s, skip_share, edit_share, top[], index}]}` | When in the day it hurts |
| `bundles` | `{n, pairs:[{a:{id, k, name}, b:{…}, n_both, lift}]}` (top 12, n_both ≥10) | Fixes that travel together |
| `space-brief` | `{n, categories:[{cat, share_of_floors}]}` | Kinds of space people built |
| `sim` | `{n, all:{n, use, happy, desk_use}, by_type:{CE..Lead: same or lt10}, happy_hist:[10 bins] or null}` | not shown (the simulated day is a demo) |
| `leads-gap` | `{n, n_lead, n_staff, rows:[{id, question, k, name, lead, staff, gap, n_lead, n_staff}]}` (top 10 by size of gap) | Leads vs staff |
| `styles` | `{n, palettes:[{name, share}]}` | Palette swatches on slide 02 |
| `wall` | `{n, posts, share_posted, views, median_views}` | not shown on the page (kept for the funnel's wall step and exports) |
| `export.csv` | `scenario_id,question,option,name,share_pct,n`, one row per option plus a `skip` row, scenarios with n≥10 only | Export CSV button |

### Where each number comes from (events: `events.md`)

- **started:** sessions created. **completed:** sessions with `/finish`.
- **completion:** completed ÷ started.
- **median_play_s:** last event `t` ÷ 1000 per completed session, median.
- **funnel steps:**
  - `role` = `role_set` event, or `role_type` sent at session create;
  - `m1…m7` = moments with a first `keep` or `skip`;
  - `end` = `/finish`;
  - `palette` = `palette_set`;
  - `render` = `render_view`;
  - `wall` = `wall_post` event or a wall post row.
- **Per scenario:**
  - **final pick:** `floor.picks[zone]` (else `finish.picks`). `'0'` or missing means skip.
  - **top_share:** share of the most-picked option (not skip).
  - **median_decide_s:** `choose.t − answer.t` for the first decision.
  - **edit_share:** completed games with an `edit` event on that zone.
- **additions:**
  - the last `add` event not undone by `addRemoved` on its zone;
  - **how:** `desks` → `desks_smaller` or `desks_share`; `swap` → `swap`; `via: 'end'` → `end`; otherwise `gap`;
  - **declined:** `addSkip` or `gapLeave` with no add.
- **sharing.team_desks:** final pick on scenario 1 (z7, always dealt). **fab_days:** scenario 30 (dealt to CE and IE).
- **pain-hours.index:**
  - first, per hour of the scenario's `time`, take the median decide time, the mean skip share and the mean edit share;
  - then compute 100 × (0.40 × decide ÷ max decide + 0.35 × skip ÷ max skip + 0.25 × edit ÷ max edit).
- **bundles.lift:** P(both picks on one floor) ÷ (P(a) × P(b)), over non-skip final picks.
- **space-brief:** a floor "has" a category if any picked option's `items` or the addition belongs to it (`items.json` `cat`).
- **edits.any_share:** completed games with at least one `edit`. **switches:** `edit` events counted by scenario and from→to across all completed games; `share` is of all completed games.
- **palettes:** from `palettes.json` (the game's `PALETTES` list); `styles` shares come from `floor.palette_name`.
- **sim:** `floor.sim` (`use`, `happy`, `desk_use`), averaged over completed sessions that sent a floor.
- **leads-gap:** for scenarios with at least 10 leads and 10 staff:
  - `lead` and `staff` are the final-pick shares per option;
  - `gap = staff − lead`.
  - Staff means every type except Lead and none.
- **wall:**
  - `posts` counts posts from sessions in the filter;
  - `views` counts distinct viewer→post views on those posts;
  - `median_views` is per post.

## 6. Implementation notes for the backend

- Compute from the derived tables in `backend-spec.md` (sessions, moments, additions, floors), not by replaying raw events per request. The mock replays events only because it is in memory.
- One SQL view per card is fine. Add an index on `(game_version, role_type, created_at)` for the filters.
- **Apply suppression last,** after filters. **Never return the raw counts behind a suppressed cell.** A viewer could subtract two filtered views to recover a small group, so the type filter rejects any combination whose `n` is under 10 by returning the `lt10` shape for the whole response body's groups.
- Keep `synthetic` out by default in every query, including `games` on the wall strip.
