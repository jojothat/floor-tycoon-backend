# Floor Tycoon backend spec, v2

9 Oct 2026 · Manoj (WowLabz for Zyeta) · supersedes v1 of 8 Oct

v1 was written before the gameplay changes of 8–9 Oct. Everything below reflects the game as it plays today, in the playtest build on the Claude artifact. Section 11 lists what changed from v1.

## 1. Goal and scope

One backend collects every play of the 2.5D Floor Tycoon game and serves the management read from that same data. Today:

- the game keeps its log in the browser;
- the wall uses the artifact's own small store;
- the dashboards run on sample data.

This backend replaces all three with real, anonymous pilot data.

**In scope**

- Collecting each play:
  - profile: employee type, no name;
  - the scenario deal;
  - every moment's previews, choice and skip;
  - end-of-day edits;
  - the one end-of-day addition;
  - palette;
  - simulation results;
  - wall post;
  - wall views.
- Scoring each play for quality and filtering rushed or bogus plays.
- Computing the insight metrics on the server.
- The wall, served by the backend:
  - offices;
  - per-office use %, happiness % and views;
  - completed games by employee type.
- A management API and a live management read that replaces the sample data.
- Admin: pilot sites, invite links, the scenario deal and exports.

**Out of scope for v1 of the backend**

- AI image renders. The game draws its own model render in the browser; an AI render is a separate, later queue.
- Personal results by name, HR linking, any cost data.
- Real-time multiplayer.

**Standing content rules.** These apply to anything the backend stores or returns:

- no names anywhere;
- no PPE or gowning references;
- no cost or money angle;
- studies are never quoted or named;
- groups under 10 are hidden;
- sample data is always labelled as sample.

## 2. How the game plays now

The backend has to record each of these steps.

1. **Start and rules.** The start screen leads to "How it plays". A "See what others have built · Explore" button opens the wall before playing.
2. **Employee type.** CE Customer Engineer, PSE Process Support Engineer, IE Installation Engineer, Sales, Support (Office & Support), Lead (Team lead), or "Prefer not to say".
   - Only the type is sent.
   - The character look (outfit, hair, skin, extras) is never sent.
3. **The deal.** 7 moments, one per place on the floor (z1–z7), drawn from a library of 61 scenarios. The rules:
   - z7, "The team grows to 14", is always dealt.
   - CE and IE always get scenario 30, three fab days a month, in z6.
   - Lead always gets scenario 56, should leads keep offices, in z6.
   - The other places draw at random with these weights:
     - player's role ×3;
     - scenarios for everyone ×1.5;
     - other roles ×0.35;
     - Core tier ×2;
     - hand-built scenario ×1.3.
   - No theme repeats.
   - Moments play in time order.
4. **Each moment.**
   - A chat bubble sets it up, and the player taps it.
   - The fixes show in a 2×2 grid; tapping one previews it on the floor.
   - One "Choose <fix>" button confirms it.
   - The reply and the catch then play. There is **no Keep / Rethink step** any more: after about 2.6 s the day moves on by itself.
   - The grid's fourth tile is **"None of these work · Skip"**. It skips instantly, with no reason asked, and the place stays as it was.
   - A classic build with Keep / Rethink remains for comparison, reached with `#classic` on the link. Its `rethink` events must still be accepted.
5. **End of day, 18:00.** The floor shows all 7 fixes. The player can:
   - **Edit** any moment with the pencil. This replays only that moment and returns with the new pick.
   - **Add one thing.** There is an always-on "Add something to your floor" card, and "Empty space" flags appear where a fix leaves its place half bare.
     - The picker shows 3 items that fit, with Shuffle for 3 more.
     - "Nothing to add" or "Leave it empty" declines.
     - If no bare space fits, the player is asked to make room:
       - **Smaller desks** (12 compact desks for 14) or **Share desks** (8 bench seats for 14). Only offered while the team desks are the original 6 desks or "Keep My Desk". Compact Desks and Team Nest cannot shrink further.
       - Or **swap out** one of their fixes whose whole place fits the new item.
     - Only one addition per floor. Editing that moment removes it.
6. **Palette, simulation, render.**
   - The player picks one of 10 palettes.
   - A simulated day runs full screen: 14 people, 5 of them field engineers who go out to customer fabs.
   - A short "Your render is ready" panel shows Happy %, Desk use % and Peak.
   - The render is a continuous camera orbit of the 3D floor.
7. **Wall.**
   - Share to the wall. One office per person; the latest replaces the last.
   - The wall shows each office's floor with Use %, Happiness % and views. The player's own office is tagged "You". No names, timestamps or amenity lists.
   - Opening an office plays its day first, then its render, then returns to the wall.
   - The top strip shows completed games by employee type: count and %, with types under 10 games grouped as "Other".

## 3. Architecture

The steps, in order:

- **Phone.** The game is a static web page. It queues events while offline.
- **Ingest API.** Append-only; dedupes on `session_id + seq`.
- **Raw events.** Postgres in a Korean region.
- **Scheduled jobs**, every 15 minutes:
  - quality score;
  - derived tables;
  - wall aggregates.
- **Read API.** The group-of-10 rule is enforced here.
- **Management read.**

The wall has its own small public read API (section 6) that only serves aggregates and floors. It never serves events.

The game files load from a CDN with a Seoul edge. Nothing in the game waits on the network except session start, the wall and the share.

## 4. Data model

Every play is one **session**; everything else hangs off it. Raw events are stored as sent and never edited; derived tables are rebuilt from them.

| Table | One row per | Key fields |
| --- | --- | --- |
| pilots | pilot wave | pilot_id, site, start/end dates, deal version, invite token hash |
| sessions | play | session_id (random), anon_player_id, pilot_id, role_type (CE, PSE, IE, Sales, Support, Lead, none), device class, viewport height bucket, language, game version, flow (`quick` or `classic`), started_at, finished_at |
| deals | play | session_id, scenario ids in play order, zone of each |
| events | raw event | session_id, seq, ev, m (zone), payload JSON, client t (ms since start), server time |
| moments | moment played | session_id, scenario_id, zone, options previewed in order, first preview, chosen option or `skip`, time to decide (ms), rethinks (classic only) |
| edits | end-of-day edit | session_id, zone, from option, to option (`0` means changed to a skip) |
| additions | end-of-day addition | session_id, item id, how placed (`gap`, `end`, `desks_smaller`, `desks_share`, `swap`), zone, swapped-out option if any, shuffles before picking, removed (bool) |
| add_declines | decline | session_id, where (`end` card or `gap` flag), zone if gap |
| floor | finished floor | session_id, final pick per zone, skips per zone, addition, palette id, sim results (use %, happiness %, desk use %, peak, busy % per place, seats, fab trips) |
| wall_posts | person (latest only) | anon_player_id, session_id, deal, picks, skips, addition (`adds`, `deskfit`, `swaps`), palette, use %, happiness %, posted_at |
| wall_views | viewer × office | viewer anon id, wall post id, first viewed_at (unique viewers; own office not counted) |
| quality | play | session_id, score 0–100, flags |

**Option codes.** In each zone, `a`, `b`, `c` and `d` are the fixes as listed in `scenarios.json`. `0` is "as it was", which is what a skip leaves. Hand-built scenarios (ids 1, 6, 11, 16, 21, 34, 38) use `a`–`c` plus an optional `d`.

## 5. Ingest API and event contract

| Endpoint | Called when | Body | Returns |
| --- | --- | --- | --- |
| POST /v1/sessions | Start pressed | pilot token, game version, flow, language, device class, viewport height | session_id, scenario deal (7 ids), short-lived write key |
| POST /v1/sessions/{id}/events | every 10 events or 15 s, and on leaving the page | events[] with seq | accepted seq range |
| POST /v1/sessions/{id}/finish | first arrival at the 18:00 floor | final picks, skips | ok (also counts as a completed game for the wall strip) |
| POST /v1/sessions/{id}/floor | after the simulated day | palette, sim results, addition | quality score (never shown to the player) |
| POST /v1/wall | Share to the wall | session_id | ok; replaces this player's previous post |
| GET /v1/wall | wall opened | — | latest posts (floor, use %, happiness %, views, `mine` flag), games by employee type with the under-10 rule applied |
| POST /v1/wall/{post_id}/view | an office opened from the wall | — | ok (idempotent per viewer) |

**Rules**

- Each event carries `seq`. The server drops duplicates, so retries are safe.
- `t` is ms since session start; the server stamps its own time as well.
- Payload cap is 64 KB per batch. Unknown `ev` types are stored but flagged.
- `anon_player_id` is a random id kept on the phone, so one person's repeat plays can be grouped. It is never linked to a name or employee number.
- The deal comes from the server, so the mix can change during the pilot without a new game build. The weighting in section 2 is the default deal function.
- The character look is never sent.

The full event list, with payloads, is in `events.md`. A real play log from the current build is in `sample/play-log-sample.json`.

## 6. Wall numbers

These are computed on the server from the simulated day of each floor and stored with the wall post:

- **Use %**: booked minutes ÷ (number of places × 540 working minutes), across the shared places, excluding team desks. The same demand on more places reads as lower use.
- **Happiness %**: happy remarks ÷ all remarks during the simulated day. Fixes that took something away get fewer happy remarks.
- **Desk use %**: average share of desks occupied, sampled every 15 minutes from 09:00 to 18:00, capped at the number of desks. Shown on the ready panel and in Day in numbers, not on the wall.
- **Views**: unique viewers of that office, own views excluded.
- **Games by employee type**: completed games (reached 18:00) per type, as count and %. Types under 10 are merged into "Other". Until 10 real games exist, the game shows labelled sample numbers.

## 7. Data quality and bogus plays

Each finished play gets a quality score from 0 to 100. Below 40 is dropped, 40–69 counts at half weight, and 70 and above counts fully. These thresholds are starting points to tune in the pilot.

| Check | Signal | Penalty |
| --- | --- | --- |
| Speeding | under 3 s from bubble tap to Choose, or whole play under 90 s | up to −40 |
| No looking | chooses without previewing any fix | −15 per moment, max −30 |
| Position bias | always picks the same grid tile (top-left …) | −25 |
| Skip everything | 5 or more of 7 moments skipped | −30 (kept for the skip analysis) |
| Inconsistent pair | opposite answers on two paired scenarios | −15 |
| Repeat play | same anon_player_id finished before in this pilot | only the first play counts for preferences |
| Unfinished | left before 18:00 | kept for drop-off analysis, not for preferences |

Scores and flags are stored but never shown to the player. Every management number states how many plays it uses.

## 8. Privacy and the group-of-10 rule

- **No identity collected:** no name, email, employee number or exact location. Pilot access is a shared invite link per site.
- **Group-of-10 rule in the API, not the dashboard.** Every management query counts the plays behind each cell and returns "fewer than 10" instead of a number. A filter combination that would isolate a small group is refused. The wall's games-by-type strip applies the same rule: "Other".
- **No free text.** Skips carry no reason in the current build. The classic build's optional "What would work instead?" text (80 characters) is accepted but never shown on the wall or in the management read without review.
- **Data stays in Korea:** the database and backups are in a Korean cloud region.
- **Retention:** raw events are kept for the pilot plus 3 months (about 90 days), then deleted; aggregated results are kept.
- **Consent line** on the start screen: what is collected, that it is anonymous, and how long it is kept.

## 9. Management read

Filters on every call: pilot, site, employee type, game version, flow, date range. The minimum group size of 10 applies after filtering.

| Endpoint | Powers | Main numbers |
| --- | --- | --- |
| GET /v1/read/summary | Overview | plays, finished %, median play time, quality mix |
| GET /v1/read/funnel | Engagement funnel | started, type set, each moment, 18:00, palette, render, wall, wall views |
| GET /v1/read/picks | What people choose; contested vs consensus | share choosing each fix per scenario, by type |
| GET /v1/read/skips | Where none of the fixes fit | skip rate per scenario and per place, by type |
| GET /v1/read/edits | Second thoughts at the end | edit rate per scenario, from → to |
| GET /v1/read/additions | What people want outside the scenarios | top items added, by type; how placed; what was given up (smaller desks, shared desks, swapped fix); decline rate |
| GET /v1/read/sharing | The price of sharing; fab days vs keeping a desk | where sharing is accepted, by type; smaller vs shared desks when making room |
| GET /v1/read/desk-jobs | The desk's five jobs | ranking of storage, calls, focus, landing, belonging |
| GET /v1/read/me-we | Me vs we | personal vs shared choices, by type |
| GET /v1/read/pain-hours | Pain by hour | moments ranked by time to decide, skips and edits |
| GET /v1/read/bundles | Fixes that travel together | pairs of picks chosen together (lift) |
| GET /v1/read/space-brief | Implied space brief per 100 people | floor area by space type from final picks and additions |
| GET /v1/read/styles | Work-style map; taste | clusters and palette choice |
| GET /v1/read/leads-gap | Leads vs staff gap | difference in pick shares |
| GET /v1/read/sim | Simulated outcomes | use %, happiness %, desk use % distributions by type |
| GET /v1/read/export.csv | Analysts | aggregated table only, never raw events |

**Refresh:** derived tables update every 15 minutes during the pilot, and the dashboard shows "updated at". Each chart shows how many plays it uses. The sample note disappears once real data exists.

## 10. Hosting, security, performance and build plan

| Part | Proposal |
| --- | --- |
| Game files | static hosting + CDN with a Seoul edge; images load per moment, not all up front |
| Ingest + wall + read API | one small container service, two instances |
| Database | managed Postgres, Korean region, daily backups |
| Jobs | scheduled job every 15 min |
| Management login | SSO or named accounts for a short list of viewers |

**Security**

- HTTPS only.
- Write keys expire after 2 hours and work for one session.
- Rate limits per IP and per pilot token.
- The read API needs a logged-in viewer.
- An audit log records who viewed and who exported.

**Targets**

- Event write under 150 ms at the 95th percentile.
- Wall GET under 500 ms.
- Read endpoints under 1 s.

**Build plan** (durations are estimates)

1. **Collect** (about 1 week): sessions, deal and events API; database; the game sending its existing log with an offline queue. Done when a scripted play (see `sample/`) lands complete in the database.
2. **Clean** (about 1 week): quality score, repeat-play grouping, and the derived moments, edits, additions and floor tables. Done when 50 scripted plays (good, speeding, position-biased, skip-everything) score as expected.
3. **Wall and read** (about 2 weeks): the wall API replacing the artifact store, then the read API with the group-of-10 rule, then the dashboard switched from sample data. Done when every chart shows live numbers and its play count.
4. **Pilot** (2–3 weeks): three sites at once, about 1500 people, one invite link per site, daily quality check, threshold tuning on live data, then the readout to Zyeta and Applied Materials Korea.

**Decisions** (10 Oct 2026; these were the open questions)

- [x] Hosting: AWS Seoul (ap-northeast-2).
- [x] Access: a shared invite link per site (`ACCESS_MODE=invite`). No per-person invite codes for the pilot.
- [x] Retention: raw events are deleted 3 months (about 90 days) after the pilot ends (`RETENTION_MONTHS=3`).
- [x] Viewers: a short named list. Only 1–2 of them can export.
- [x] Employee type codes: CE, PSE, IE, Sales, Support and Lead, kept as they are. Labels can change later; the codes stay.
- [x] Pilot size and sites: about 1500 people across 3 sites, all launched at once.
- [x] Flow: quick.
- [x] Funnel counts are rounded to 10, so subtracting two steps never gives an exact small group (to build).

## 11. What changed since v1 (8 Oct)

- Keep / Rethink removed from the default flow. The day auto-advances after the catch. `keep` is still logged automatically and `rethink` only exists in `#classic`.
- **Skip** added: the 4th grid tile, instant, no reason asked. It is a new `moments` outcome and a new `skips` read.
- **Edits at the end:** the pencil replays one moment (`replay`, `edit` events). New `edits` table and read.
- **Add something:** new `additions` and `add_declines` tables, an `additions` read and new events. Placement can be a gap, smaller desks, shared desks or a swap. One addition per floor; an edit removes it.
- **Wall:**
  - moved from the artifact store into the backend;
  - shows Use %, Happiness % and views per office, plus games by employee type with the "Other" rule;
  - no names, timestamps or amenity lists on cards;
  - opening an office plays its day, then its render.
- **Simulation numbers:** Desk use % replaces "desks empty N h". Use % and Happiness % are defined in section 6.
- **Render:** a continuous orbit in the browser. Still no AI render in scope.
- **Deal:** 61 scenarios with the weighting and forced picks in section 2. PPE scenarios 9 and 61 are removed from the library.
- **Employee types:** CE, PSE, IE, Sales, Support and Lead, plus "Prefer not to say".
- **Layout:** the game is tested at phone heights down to 250 px. Log the viewport height bucket so short-screen drop-off can be checked.
