#!/usr/bin/env bash
# AWS main server: put a new version of the code in place and restart the services.
# Called by .github/workflows/deploy.yml (runner on this server), or by hand:
#   bash deploy/aws/deploy-main.sh /path/to/a/checkout
# Order: copy code (never .env) -> database backup -> build + restart -> check that the web page answers.
# The GPU server is not touched here.
set -euo pipefail
SRC=${1:?usage: deploy-main.sh <folder with the new code>}
DEST=/opt/callsight
[ -f "$SRC/docker-compose.yml" ] && [ -f "$SRC/deploy/aws/compose.main.yml" ] || { echo "$SRC is not the project" >&2; exit 1; }
[ -f "$DEST/.env" ] || { echo "$DEST/.env is missing: set the server up first (docs/aws.md)" >&2; exit 1; }

# --delete: files removed from the project also leave the server. .env and anything not from git stay.
rsync -a --delete --exclude '.git' --exclude '.github' --exclude '.env' --exclude 'data/' "$SRC"/ "$DEST"/
cd "$DEST"
C=(docker compose -f docker-compose.yml -f deploy/aws/compose.main.yml)

# A database dump before every update (a new version may change the database). Skipped only on the very first
# start, when no worker exists yet.
# A worker stuck restarting (e.g. a broken migration from the last update) can't be exec'd: then the backup runs in
# a one-off container of the same image (no migration, no celery).
if [ -n "$("${C[@]}" ps -q worker 2>/dev/null)" ]; then
  "${C[@]}" exec -T worker python -m callsight backup || "${C[@]}" run --rm -T --no-deps worker python -m callsight backup
fi

"${C[@]}" up -d --build
if grep -Eqi '^LIVE_ENABLED=(true|1|yes)' .env; then
  "${C[@]}" --profile live up -d --build live
fi

SITE=$(grep -E '^SITE_NAME=' .env | cut -d= -f2 | tr -d '[:space:]')
for i in $(seq 1 30); do
  if [ "$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve "$SITE:443:127.0.0.1" "https://$SITE/")" = 200 ]; then
    echo "deployed: https://$SITE answers"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 5
done
echo "the web page does not answer after the update" >&2
"${C[@]}" ps
exit 1
