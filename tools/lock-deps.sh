#!/bin/sh
# Re-lock the app's Python packages with hashes (hardening spec, decision 7). Runs INSIDE a linux/amd64
# python:3.12-slim container (same platform as the app image), never on the PC:
#   docker run --rm --platform linux/amd64 -v "<repo>:/src" -w /src python:3.12-slim sh tools/lock-deps.sh
# Optional: a file of exact versions to keep (e.g. `pip freeze` of the current image) as $KEEP.
set -eu
pip install --quiet --no-cache-dir "pip-tools==7.5.1"
KEEP_ARG=""
if [ -n "${KEEP:-}" ]; then KEEP_ARG="--constraint ${KEEP}"; fi
pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
  ${KEEP_ARG} --output-file requirements.lock requirements.in
pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
  ${KEEP_ARG} --output-file requirements-dev.lock requirements-dev.in
echo "locked: requirements.lock requirements-dev.lock"
