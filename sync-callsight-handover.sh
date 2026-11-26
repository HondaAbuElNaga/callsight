#!/usr/bin/env bash
# One-way copy: D:\claude\data_analsis_call  ->  D:\ERB_Team\callsight (Ghanem-Abdelaziz/sstli-handover2)
# Copies exactly what is committed on GitHub (no .git, no .env, no node_modules, no untracked files).
# Run:  bash /d/claude/data_analsis_call/sync-callsight-handover.sh
set -euo pipefail

SRC="/d/claude/data_analsis_call"
HANDOVER="/d/ERB_Team"
DST="$HANDOVER/callsight"

cd "$SRC"

# 1) Only hand over code that is committed and pushed
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "STOP: you have uncommitted changes in $SRC. Commit and push first."; exit 1
fi
git fetch -q origin
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "STOP: local main and GitHub are not the same. Push (or pull) first."; exit 1
fi

# 2) Safety checks on the target
[ -d "$HANDOVER/.git" ] || { echo "STOP: $HANDOVER is not a git repo"; exit 1; }
[ -d "$DST/.git" ] && { echo "STOP: $DST has its own .git, not touching it"; exit 1; }
mkdir -p "$DST"

# 3) Export committed files to a temp folder, then mirror into the target
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive HEAD | tar -x -C "$TMP"

find "$DST" -mindepth 1 -delete
cp -a "$TMP"/. "$DST"/

echo "Synced commit $(git log -1 --format='%h %s')"
echo "--- changes in handover repo (callsight only):"
cd "$HANDOVER"
git status --short -- callsight | head -50
echo "(total: $(git status --short -- callsight | wc -l) files)"
