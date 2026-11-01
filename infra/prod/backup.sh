#!/bin/sh
# Back up the PostgreSQL database to a dated, compressed dump and delete old dumps.
#
#   ./backup.sh [backup-dir] [keep-days]      (defaults: ./backups and 14)
#
# Run it from the folder that holds the compose file (set COMPOSE_FILE to use another one).
# The database user and name are read from the db container, so no password is needed here.
# Schedule it with cron, for example:  0 3 * * * cd /opt/abm && ./backup.sh >> backup.log 2>&1
set -eu

dir="${1:-./backups}"
keep="${2:-14}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$dir/abm-$stamp.dump"

mkdir -p "$dir"
tmp="$file.partial"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$tmp"
# A dump that is empty is a failed dump: never keep it as a backup.
if [ ! -s "$tmp" ]; then
  rm -f "$tmp"
  echo "backup failed: the dump is empty" >&2
  exit 1
fi
mv "$tmp" "$file"
find "$dir" -name 'abm-*.dump' -mtime +"$keep" -delete
echo "backup written: $file ($(wc -c < "$file") bytes)"
