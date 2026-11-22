# Test copy of CallSight on this PC, BEFORE pushing to main (push = live).
# Builds the code on disk now (with your changes) and runs it on http://localhost:8090, next to the normal local
# copy (port 8080). It uses the same local database: never the live server, never the ERP.
#
#   powershell -ExecutionPolicy Bypass -File tools\local-preview.ps1          # build + start (2-5 min)
#   powershell -ExecutionPolicy Bypass -File tools\local-preview.ps1 -Stop    # stop and remove it
#
# Needs: Docker Desktop running and the normal local copy up (container callsight-api-1).
# Steps to test per role: docs/test-before-live.md
param([switch]$Stop)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$name = "callsight-preview-api"

function Remove-Preview { try { docker rm -f $name 2>$null | Out-Null } catch {} }

if ($Stop) { Remove-Preview; Write-Output "Test copy stopped."; return }

Write-Output "Building the test copy from $root ..."
docker build -q -t callsight:preview $root
if ($LASTEXITCODE -ne 0) { throw "build failed (see the error above)" }

# the same settings as the normal local copy (database, keys), read from its running container
$lines = docker inspect callsight-api-1 --format '{{range .Config.Env}}{{println .}}{{end}}' |
  Where-Object { $_ -and $_ -notmatch '^(PATH|CS_[A-Z_]*)=' }   # no CS_* keys: the test copy never talks to the ERP
if (-not $lines) { throw "the normal local copy (callsight-api-1) is not running: start it with docker compose up -d" }
$envFile = Join-Path $env:TEMP "callsight-preview.env"
[IO.File]::WriteAllLines($envFile, [string[]]$lines)      # UTF-8 without BOM (docker reads it line by line)
try {
  Remove-Preview
  docker run -d --name $name --network callsight_default --env-file $envFile `
    -v "${root}\config:/app/config:ro" -v "${root}\data\audio:/data/audio:ro" `
    -p 127.0.0.1:8090:8080 callsight:preview `
    uvicorn callsight.api.main:app --host 0.0.0.0 --port 8080 --no-server-header --no-access-log | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "could not start the test copy" }
} finally {
  Remove-Item $envFile -ErrorAction SilentlyContinue      # it holds the local passwords: never left on disk
  if (Test-Path $envFile) { Write-Warning "could not delete $envFile (it holds local passwords): delete it by hand" }
}
Write-Output "Ready: open http://localhost:8090  (steps: docs/test-before-live.md)"
