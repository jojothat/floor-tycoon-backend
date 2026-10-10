#!/bin/sh
# Run once before the first start, and again to add or reset a manager's readout login.
#   ./init.sh <manager>            asks for the manager's password
#   ./init.sh <manager> --export   ... and lets them download the CSV export
# Writes .env (random secrets, only if it does not exist yet) and managers.caddy (password hashes).
set -eu
cd "$(dirname "$0")"
CADDY_IMAGE=caddy:2.8-alpine

rand() { od -An -tx1 -N24 /dev/urandom | tr -d ' \n'; }

if [ ! -f .env ]; then
  sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(rand)/" \
      -e "s/^RATE_LIMIT_SECRET=.*/RATE_LIMIT_SECRET=$(rand)/" \
      -e "s/^PILOT_TOKEN=.*/PILOT_TOKEN=$(rand | cut -c1-24)/" .env.example > .env
  chmod 600 .env
  echo "wrote .env with new secrets"
fi
[ -f managers.caddy ] || { : > managers.caddy; chmod 600 managers.caddy; }

if [ $# -eq 0 ]; then
  grep -q . managers.caddy || { echo "add a manager login first: ./init.sh <manager>" >&2; exit 1; }
  exit 0
fi
name=$1
case "$name" in
  *[!A-Za-z0-9._-]*|"") echo "manager names use letters, digits, . _ - only" >&2; exit 1 ;;
esac

quiet() { [ -t 0 ] && stty "$1" || true; }
trap 'quiet echo' EXIT INT
printf "password for %s: " "$name"; quiet -echo; read -r pw; quiet echo; echo
printf "again: "; quiet -echo; read -r pw2; quiet echo; echo
[ "$pw" = "$pw2" ] || { echo "the passwords differ" >&2; exit 1; }
[ ${#pw} -ge 12 ] || { echo "use at least 12 characters" >&2; exit 1; }
hash=$(printf '%s\n' "$pw" | docker run --rm -i "$CADDY_IMAGE" caddy hash-password)
case "$hash" in \$2*) ;; *) echo "could not hash the password" >&2; exit 1 ;; esac

{ awk -v n="$name" '$1 != n' managers.caddy; echo "$name $hash"; } > managers.caddy.new
mv managers.caddy.new managers.caddy; chmod 600 managers.caddy
echo "login $name saved"

if [ "${2:-}" = "--export" ]; then
  current=$(sed -n 's/^READ_EXPORTERS=//p' .env)
  case ",$current," in *",$name,"*) ;; *)
    sed -e "s/^READ_EXPORTERS=.*/READ_EXPORTERS=${current:+$current,}$name/" .env > .env.new
    mv .env.new .env; chmod 600 .env ;;
  esac
  echo "$name may export"
fi
echo "to apply on a running stack: docker compose up -d && docker compose restart caddy"
