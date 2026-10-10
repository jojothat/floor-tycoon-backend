#!/bin/sh
# Records the demo video against a throwaway copy of this stack, with its own empty database.
# The real stack's data is never touched. Needs Docker, Node 18+, Python 3 with Pillow and ffmpeg.
#   MANAGER=<readout login> MANAGER_PASSWORD=<its password> ./demo/demo.sh
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
DEPLOY=$(dirname "$HERE")
: "${MANAGER:?set MANAGER to a readout login from managers.caddy}"
: "${MANAGER_PASSWORD:?set MANAGER_PASSWORD}"
[ -f "$DEPLOY/.env" ] || { echo "run ./init.sh first" >&2; exit 1; }
if [ -n "$(cd "$DEPLOY" && docker compose ps -q caddy 2>/dev/null)" ]; then
  echo "the real stack is running on the same ports: stop it first with 'docker compose stop' (data is kept)" >&2
  exit 1
fi
for tool in node python3 ffmpeg; do command -v $tool >/dev/null || { echo "needs $tool" >&2; exit 1; }; done
python3 -c "import PIL" 2>/dev/null || { echo "needs Pillow: python3 -m pip install pillow" >&2; exit 1; }

# The demo copy: same secrets and logins, its games labelled synthetic, a fast simulated day, no read cache.
sed -e 's/^GAME_VERSION=.*/GAME_VERSION=synthetic-demo-ui/' "$DEPLOY/.env" > "$HERE/demo.env"
printf 'FT_SIM_MS=3000\nREAD_CACHE_SECONDS=1\n' >> "$HERE/demo.env"
PROJ=floor-tycoon-demo
cleanup() { (cd "$DEPLOY" && docker compose -p $PROJ --env-file "$HERE/demo.env" down -v >/dev/null 2>&1) || true; }
trap cleanup EXIT
cleanup  # start from an empty database
(cd "$DEPLOY" && docker compose -p $PROJ --env-file "$HERE/demo.env" up -d --build --wait)

cd "$HERE"
[ -d node_modules/playwright ] || { npm install --no-save --no-package-lock playwright@1.56.1 && npx playwright install chromium; }
OUT="$HERE/out/frames" ENV_FILE="$HERE/demo.env" PROJ=$PROJ node record.mjs
python3 compose.py "$HERE/out/frames" "$HERE/out"
echo "videos in $HERE/out: floor-tycoon-demo.mp4, 1-game.mp4, 2-backend-console.mp4, 3-readout.mp4"
