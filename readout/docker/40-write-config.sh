#!/bin/sh
# Writes the readout's runtime config from env at container start.
#   RO_API_BASE   where /v1/read/* is, normally /api (the nginx proxy)
#   RO_READ_TOKEN viewer token for dev/staging; leave empty in production (SSO at the proxy instead)
#   RO_SAMPLE     1 = ignore the backend and show the bundled, labelled sample data
set -e
H=/usr/share/nginx/html
if [ "${RO_SAMPLE}" = "1" ]; then BASE=""; else BASE="${RO_API_BASE}"; fi
cat > $H/config.js <<JS
window.RO_CONFIG = { apiBase: '${BASE}', readToken: '${RO_READ_TOKEN}' };
JS
# outside sample mode the sample numbers are not shipped at all, so they can never be mistaken for live ones
if [ "${RO_SAMPLE}" != "1" ]; then echo "window.RO_SAMPLE=null;" > $H/sample-data.js; fi
