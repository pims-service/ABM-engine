#!/bin/sh
# Restore a dump made by backup.sh into a database that does not exist yet.
#
#   ./restore.sh <dump-file> [target-database]      (default target: abm_restore_test)
#
# It never overwrites an existing database, including the live one: pick a new name, check the
# data, then switch POSTGRES_DB in .env to it (or drop the live database yourself) when you mean
# to. Run it from the folder that holds the compose file (set COMPOSE_FILE to use another one).
set -eu

file="${1:?usage: restore.sh <dump-file> [target-database]}"
target="${2:-abm_restore_test}"

case "$target" in
  *[!a-zA-Z0-9_]*) echo "the database name may only use letters, digits and _" >&2; exit 1 ;;
esac
[ -s "$file" ] || { echo "no such dump (or it is empty): $file" >&2; exit 1; }

exists="$(docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='$target'\"")"
if [ -n "$exists" ]; then
  echo "database $target already exists; choose another name" >&2
  exit 1
fi

docker compose exec -T db sh -c "createdb -U \"\$POSTGRES_USER\" \"$target\""
docker compose exec -T db sh -c "pg_restore -U \"\$POSTGRES_USER\" -d \"$target\" --no-owner --exit-on-error" < "$file"
tables="$(docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d \"$target\" -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\"")"
echo "restored into $target: $tables tables"
