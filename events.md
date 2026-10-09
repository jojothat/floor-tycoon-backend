# Floor Tycoon game events

These are the events the current game build writes to its in-browser log, `window.__playLog.log`. The backend's ingest API should accept them as they are. The game will add `seq` (an integer starting at 1 per session) when it starts sending.

Every event has:

- `ev`: the event type;
- `t`: ms since the session started.

`m` is the place on the floor (zone), `z1` to `z7`:

| Zone | Place |
| --- | --- |
| z1 | entry |
| z2 | calls |
| z3 | quiet |
| z4 | kitchen |
| z5 | back corner |
| z6 | collaboration |
| z7 | team desks |

Option codes are `a`–`d`. `0` means "as it was", which is what a skip leaves.

## During the day

| ev | Fields | Meaning |
| --- | --- | --- |
| deal | ids[] | The 7 scenario ids dealt, in play order. Always first, `t` = 0. |
| answer | m | The player tapped the moment's chat bubble. |
| preview | m, opt | The player tapped a fix in the grid to preview it on the floor. |
| choose | m, opt | The player pressed "Choose <fix>". |
| keep | m, opt, rethinks | The pick is final. In the quick flow it is logged automatically about 2.6 s after `choose`, and `rethinks` is 0. |
| rethink | m, from | Classic flow only (`#classic`): the player rejected the pick after seeing the catch. |
| skip | m, why, rethinks | "None of these work · Skip". `why` is `none` in the quick flow. The classic flow may send `asis`, `else` or `notme`, plus optional `text` of up to 80 characters. |

## End of day (18:00 floor)

| ev | Fields | Meaning |
| --- | --- | --- |
| replay | m | The pencil was tapped: the player replays this moment. |
| edit | m, from, to | The result of a replay. `to` = `0` means they changed it to a skip. |
| addRemoved | m, item | A replay removed the addition that sat at this place. |
| addOpen | — | The "Add something to your floor" picker was opened. |
| addShuffle | — | Shuffle in that picker. |
| addSkip | — | "Nothing to add". |
| addNeedsRoom | item | The chosen item didn't fit any bare space, so the game asked to make room. |
| gap | m, item | An "Empty space" flag was opened beside the fix at `m` (`item` is that fix's furniture id). |
| gapShuffle | m | Shuffle in the empty-space picker. |
| gapLeave | m | "Leave it empty". |
| add | m, item, via?, desks?, swap? | One thing was added. With no `via`, it came from an empty-space flag. `via:'end'` means from the end card. `desks:'smaller'` or `'share'` means the team desks were changed to make room (`m` = `z7`). `swap:<opt>` means it replaced the fix `<opt>` at `m`. |
| wallOpen | id | An office on the wall was opened. `id` is a sample id, or `w` for a real post. |

## Not in the log yet (to add when the backend exists)

| ev | Fields | When |
| --- | --- | --- |
| role_set | type | Employee type chosen (today this is in `profile.type`, not the log) |
| palette_set | palette | Palette chosen |
| sim_done | use, happy, deskU, peak | Simulated day finished |
| render_view | — | Render opened |
| wall_post | — | Shared to the wall |
| session_end | reason | Page hidden or closed |

## Other data the game holds at the end

These are in `sample/play-log-sample.json`:

- `profile`:
  - `type`: CE, PSE, IE, Sales, Support, Lead, or null for "Prefer not to say";
  - `role`: an internal group id;
  - `deal`.

  The character `look` is deliberately left out and must not be sent.
- `finalPicks`: zone → option.
- `end`:
  - `use`, `happy`: wall numbers, see spec section 6;
  - `picks`, `skips`;
  - `adds`, `deskfit`, `swaps`: the addition;
  - `palette`, `pal`;
  - `amen`: names of what is on the floor.
- `stats`: the simulated day.
  - `peak`, `peakAt`;
  - `calls`, `focusH`, `lunch`, `breaks`, `joint`, `walk`;
  - `occ` and `occf`: people on the floor and at the fab every 15 min from 08:00;
  - `fabN`, `fabH`;
  - `deskU`, `desks`;
  - `busy`: per fix, the share of 09:00–18:00 that place was busy.

## The wall today (to be replaced)

The playtest stores the wall in the Claude artifact's own document store, in three collections. The backend replaces these with `/v1/wall`.

| Collection | Document id | Fields |
| --- | --- | --- |
| `gallery` | player id | deal, picks, adds, skips, deskfit, swaps, pal, palette, use, happy, amen, type, ts |
| `plays` | player id + time | type, ts (one per completed game, for the games-by-type strip) |
| `views` | viewer id + post id | layout, ts (unique views) |
