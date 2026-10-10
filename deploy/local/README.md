# Production on your own machine

This runs the whole pilot on one computer you control, with no cloud account:

- the game, for players;
- the backend and its Postgres database;
- the Space Readout, for managers, behind a login;
- the scheduled jobs (quality scores every 15 minutes, retention once a day);
- a daily database backup.

One HTTPS front door (Caddy) is the only thing listening on the network. The database and the backend are never reachable from outside.

**Before real pilot data:** spec section 8 says the database and backups stay in Korea. This setup counts only if the machine (and every copy of the backups) is physically in Korea, and Applied Materials Korea accepts self-hosting instead of a Korean cloud region. Until they confirm, use it for testing and demos.

You need Docker (Docker Desktop on Mac or Windows, or Docker Engine on Linux). On Windows, run `init.sh` from Git Bash or WSL. The machine has to stay on and awake while people play.

## Start

```sh
cd deploy/local
./init.sh <your-name> --export     # creates .env with random secrets; asks for your readout password
docker compose up -d --build
```

- **Game:** https://localhost
- **Readout:** https://localhost:8443 (sign in with the name and password you just set)

The readout shows "Not enough games yet" until 10 people have finished the game. That is the group-of-10 rule working, not a fault.

Your browser will warn about the certificate the first time. See **Certificates** below.

## Let phones on the office network play

1. Find this machine's address on the network, for example `192.168.1.20`.
2. In `.env`, set:
   ```
   GAME_ADDRESS=https://192.168.1.20
   READOUT_ADDRESS=https://192.168.1.20:8443
   ```
3. Run `docker compose up -d`.
4. Share `https://192.168.1.20` with players.

Give the machine a fixed address on your router, so the link keeps working.

## Certificates

- **`CADDY_TLS=internal` (the default).** Caddy runs its own small certificate authority. Browsers and phones show a warning until they trust its root certificate. Get that certificate with:
  ```sh
  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./floor-tycoon-root.crt
  ```
  Then install it as a trusted certificate on each device, or tap through the warning. The authority is kept in a Docker volume, so it survives restarts and updates.
- **Real certificates (no warnings).** You need:
  - two domain names pointing at this machine, for example `play.example.kr` and `readout.example.kr`;
  - ports 80 and 443 reachable from the internet.

  Set `GAME_ADDRESS` and `READOUT_ADDRESS` to the bare names and `CADDY_TLS` to your email address. Caddy then gets free Let's Encrypt certificates and renews them by itself. This puts the game on the public internet, so check it with whoever owns the network first.

## Managers

| To | Run |
| --- | --- |
| Add a manager, or reset their password | `./init.sh <name>` |
| Also let them download the CSV export | `./init.sh <name> --export` |
| Remove a manager | delete their line from `managers.caddy`, and their name from `READ_EXPORTERS` in `.env` |

Then apply the change: `docker compose up -d && docker compose restart caddy`.

Every read and export is recorded in the audit log under the manager's login name. Players are never named anywhere.

## Updates

```sh
git pull
docker compose up -d --build
```

Data, certificates and logins are kept.

## Backups

Every day the `backup` service writes a compressed dump to `deploy/local/backups/` and deletes dumps older than `BACKUP_KEEP_DAYS` (default 14). It also writes one whenever the stack starts.

- **Copy them off this machine regularly**, for example to an encrypted drive. A backup on the same disk does not survive the disk failing.
- Dumps hold raw events. After the retention job deletes old events, copies still sit in the dumps for up to `BACKUP_KEEP_DAYS`, and in any copies you made.

To restore a dump (this replaces the current database):

```sh
docker compose stop api jobs backup readout web
docker compose exec -T db dropdb -U ft floor_tycoon
docker compose exec -T db createdb -U ft floor_tycoon
docker compose exec -T db pg_restore -U ft -d floor_tycoon < backups/floor_tycoon-YYYYMMDD-HHMM.dump
docker compose up -d
```

## Day to day

| To | Run |
| --- | --- |
| See what is running | `docker compose ps` |
| Watch games arrive, in plain English | `docker compose logs -f api` (or `caddy`, `jobs`, `backup`) |
| Stop everything (data is kept) | `docker compose down` |
| Change a setting | edit `.env`, then `docker compose up -d` |

## Don't

- Don't run `readout/tools/seed.mjs` or any e2e test against this stack. They write synthetic or test plays into the real data. Use the dev stack in `frontend/docker-compose.yml` for that.
- Don't publish the database or backend ports, and don't commit `.env`, `managers.caddy` or `backups/`. They are git-ignored.
- Don't set `READ_TOKEN` here. The backend refuses to start in production with a shared viewer token.
