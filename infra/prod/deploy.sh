#!/bin/sh
# Deploy one version on this server, and go back if it does not come up healthy.
#
#   API_IMAGE=ghcr.io/org/repo/api:<tag> WEB_IMAGE=ghcr.io/org/repo/web:<tag> ./deploy.sh <tag>
#   ./deploy.sh <tag> --no-pull       use the images already on this server (abm-api:<tag>, abm-web:<tag>)
#   ./deploy.sh --rollback            switch back to the tag that ran before the last deploy
#
# What it does, in order: pulls the two images and names them abm-api:<tag> and abm-web:<tag> (the
# names the compose file uses), backs the database up, records the current tag, sets IMAGE_TAG in
# .env, starts the stack (the api runs the migrations as it starts), waits until the database,
# api, worker and web are healthy and /readyz answers, then removes old images (the newest three
# stay). If the stack is not healthy within WAIT_SECONDS (default 240) it puts the previous tag
# back and exits with an error.
#
# Run it from the folder that holds docker-compose.prod.yml and .env (the CI workflow does). The
# registry login, if the images are private, is done before the call.
set -eu

cd "$(dirname "$0")"
export COMPOSE_FILE=docker-compose.prod.yml
COMPOSE="docker compose"
WAIT_SECONDS="${WAIT_SECONDS:-240}"
KEEP_IMAGES=3

say() { printf '%s\n' "$*"; }
fail() { printf 'deploy failed: %s\n' "$*" >&2; exit 1; }

current_tag() { grep '^IMAGE_TAG=' .env | head -n 1 | cut -d= -f2-; }

set_tag() {
  if grep -q '^IMAGE_TAG=' .env; then
    sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=$1/" .env
  else
    printf 'IMAGE_TAG=%s\n' "$1" >> .env
  fi
}

public_host() { grep '^PUBLIC_HOST=' .env | head -n 1 | cut -d= -f2-; }

# True when the database, api, worker and web containers are all healthy and /readyz answers.
is_healthy() {
  for service in db api worker web; do
    id="$($COMPOSE ps -q "$service" 2>/dev/null || true)"
    [ -n "$id" ] || return 1
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || true)" = "healthy" ] || return 1
  done
  host="$(public_host)"
  curl -ksf --max-time 10 --resolve "$host:443:127.0.0.1" "https://$host/readyz" > /dev/null
}

wait_healthy() {
  waited=0
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    if is_healthy; then
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  return 1
}

start_stack() {
  $COMPOSE up -d --no-build --remove-orphans
  # A changed Caddyfile only takes effect on a reload; harmless when nothing changed.
  $COMPOSE exec -T caddy caddy reload --config /etc/caddy/Caddyfile > /dev/null 2>&1 || true
}

remove_old_images() {
  keep="$(current_tag) $(cat .previous_tag 2>/dev/null || true)"
  for repo in abm-api abm-web; do
    # Newest first; the newest KEEP_IMAGES stay, and so do the running and the previous tag.
    docker images "$repo" --format '{{.Tag}}' | tail -n "+$((KEEP_IMAGES + 1))" | while read -r tag; do
      case " $keep " in *" $tag "*) continue ;; esac
      docker rmi "$repo:$tag" > /dev/null 2>&1 || true
    done
  done
  docker image prune -f > /dev/null 2>&1 || true
}

rollback() {
  previous="$(cat .previous_tag 2>/dev/null || true)"
  [ -n "$previous" ] || fail "there is no previous tag to go back to"
  say "rolling back to $previous"
  set_tag "$previous"
  start_stack
  if wait_healthy; then
    say "rolled back to $previous and healthy"
  else
    fail "rolled back to $previous but it is not healthy; check: docker compose logs --tail 100"
  fi
}

if [ "${1:-}" = "--rollback" ]; then
  rollback
  say "The database was not changed back. If the old version cannot use it, restore a backup (./restore.sh)."
  exit 0
fi

TAG="${1:-}"
[ -n "$TAG" ] || fail "usage: deploy.sh <tag> [--no-pull] | --rollback"
case "$TAG" in *[!A-Za-z0-9._-]*) fail "the tag may only use letters, digits, dot, dash and underscore" ;; esac
[ -f .env ] || fail ".env is missing next to this script"

if [ "${2:-}" != "--no-pull" ]; then
  [ -n "${API_IMAGE:-}" ] && [ -n "${WEB_IMAGE:-}" ] || fail "set API_IMAGE and WEB_IMAGE, or use --no-pull"
  say "pulling $API_IMAGE and $WEB_IMAGE"
  docker pull "$API_IMAGE"
  docker pull "$WEB_IMAGE"
  docker tag "$API_IMAGE" "abm-api:$TAG"
  docker tag "$WEB_IMAGE" "abm-web:$TAG"
fi
docker image inspect "abm-api:$TAG" > /dev/null 2>&1 || fail "image abm-api:$TAG is not on this server"
docker image inspect "abm-web:$TAG" > /dev/null 2>&1 || fail "image abm-web:$TAG is not on this server"

if [ -x ./backup.sh ]; then
  say "backing up the database"
  ./backup.sh ./backups 14
fi
backup="$(ls -t backups/*.dump 2>/dev/null | head -n 1 || true)"

previous="$(current_tag)"
if [ -n "$previous" ] && [ "$previous" != "$TAG" ]; then
  printf '%s\n' "$previous" > .previous_tag
fi

say "switching $previous -> $TAG"
set_tag "$TAG"
start_stack

if wait_healthy; then
  say "deployed $TAG and healthy"
  remove_old_images
  exit 0
fi

$COMPOSE logs --tail 40 api >&2 || true
say "$TAG did not become healthy within ${WAIT_SECONDS}s" >&2
say "The api may already have migrated the database. If the previous version cannot use it, restore ${backup:-the latest backup in ./backups} with ./restore.sh." >&2
if [ -n "$previous" ] && [ "$previous" != "$TAG" ]; then
  rollback || true
fi
exit 1
