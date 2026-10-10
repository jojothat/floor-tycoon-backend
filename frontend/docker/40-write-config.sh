#!/bin/sh
# Writes the game's runtime config from environment variables at container start.
set -e
cat > /usr/share/nginx/html/config.js <<JS
window.FT_CONFIG = {
  apiBase: '${FT_API_BASE}',
  pilotToken: '${FT_PILOT_TOKEN}',
  gameVersion: '${FT_GAME_VERSION}',
  simMs: ${FT_SIM_MS}
};
JS
