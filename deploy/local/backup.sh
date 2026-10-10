#!/bin/sh
# A compressed database dump once a day into /backups (./backups on the host), keeping
# BACKUP_KEEP_DAYS days. Restore: see README.md.
set -u
while true; do
  f=/backups/floor_tycoon-$(date -u +%Y%m%d-%H%M).dump
  if pg_dump -Fc -f "$f.part"; then
    mv "$f.part" "$f" && echo "backup written: $f"
  else
    rm -f "$f.part"; echo "backup failed" >&2
  fi
  find /backups -name 'floor_tycoon-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -delete
  sleep 86400
done
