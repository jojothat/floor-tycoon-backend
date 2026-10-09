# Floor Tycoon backend: notes for Claude Code

Read `backend-spec.md` first. It is the source of truth. `events.md` describes what the game already logs. `sample/` holds a real play log and the scenario list.

## What to build first

Start with step 1 of the build plan (Collect), in this order:

1. The data model in section 4.
2. `POST /v1/sessions`, with the default deal function from section 2.
3. `POST /v1/sessions/{id}/events`.
4. `POST /v1/sessions/{id}/finish`.

It is done when a test replays `sample/play-log-sample.json` through the API and the session, events and derived moments come out complete.

Open a pull request per step. Keep each one small enough to review on a phone.

## Rules that must never be broken

- **No identity.** No names, emails, employee numbers or exact locations. The character look (outfit, hair, skin) is never stored.
- **Group-of-10 rule in the API.** Any management number or wall count from fewer than 10 plays returns "fewer than 10" or is merged into "Other". A filter that would isolate a small group is refused. Test this.
- **No free text** reaches the wall or the management read without review.
- No PPE or gowning content. No cost or money fields. No study names.
- Sample data is always labelled as sample.
- Raw events are append-only: never update or delete them, except through the retention job.

## Choices left open

These are in spec section 10 and are not decided:

- the cloud provider (it must be a Korean region);
- invite link vs SSO;
- the retention period.

Write the code so these are configuration, not code changes. Until hosting is chosen, use Postgres via Docker for local runs and tests.

## Style

- Plain names that match the spec (`sessions`, `events`, `moments`, `edits`, `additions`, `wall_posts`, `wall_views`).
- Every endpoint has tests, including duplicate `seq` retries and the under-10 rule.
- A README section on how to run it locally in under 5 commands.
