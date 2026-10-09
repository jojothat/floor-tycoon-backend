#!/bin/sh
# The scheduled jobs: quality scores every 15 minutes, retention once a day.
set -u
last_retention=""
while true; do
  python -m app.clean || echo "clean job failed" >&2
  today=$(date -u +%F)
  if [ "$today" != "$last_retention" ]; then
    python -m app.retention && last_retention="$today" || echo "retention job failed" >&2
  fi
  sleep 900
done
