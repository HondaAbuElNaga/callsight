#!/usr/bin/env python3
"""Boot service of the AWS GPU server (spec 2026-11-15-aws-deploy): start the three containers, work until nothing
is left, then power the server off. Started by systemd at every boot (callsight-gpu.service); the server itself is
started by the EventBridge schedule. Needs only Python 3 from the system (no packages).

Rules:
  - every 30 s ask the main database "how many calls still wait?" (python -m callsight gpu-work --queue)
  - power off after 2 minutes in a row with the answer 0
  - a failed question is never "0": the server stays on; after 10 minutes of failed questions in a row it powers
    off (without the main server nothing can be done; the next wake-up tries again)
  - power off after 3 hours in any case (safety cap)
  - the file /etc/callsight/stay-on stops every power-off (for setup and repairs):
        sudo touch /etc/callsight/stay-on      ...work...      sudo rm /etc/callsight/stay-on

"instance shutdown behaviour = stop" is set in CloudFormation, so a power-off stops the server (disk kept, no
hourly cost) and does not delete it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

POLL_S = 30
QUIET_S = 120
CAP_S = 3 * 3600
ERROR_S = 600
REPO = Path(os.environ.get("CALLSIGHT_DIR", "/opt/callsight"))
COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy/aws/compose.gpu.yml"), "--env-file", str(REPO / ".env")]
STAY_ON = Path(os.environ.get("CALLSIGHT_STAY_ON", "/etc/callsight/stay-on"))


def run(work: Callable[[], int | None], start: Callable[[], object], stop: Callable[[], object],
        poweroff: Callable[[], object], *, sleep: Callable[[float], object] = time.sleep,
        clock: Callable[[], float] = time.monotonic, stay_on: Callable[[], bool] = lambda: False,
        poll: float = POLL_S, quiet: float = QUIET_S, cap: float = CAP_S, error_limit: float = ERROR_S,
        log: Callable[[str], object] = print) -> str:
    """Returns why the server was powered off: "idle", "cap" or "no-answer". `work` returns the number of calls
    still waiting, or None when the question failed."""
    began = clock()
    try:
        start()
    except Exception as exc:                      # noqa: BLE001  the loop below then ends with "no-answer"
        log(f"start failed: {type(exc).__name__}")
    quiet_since: float | None = None
    error_since: float | None = None
    while True:
        now = clock()
        reason = None
        if now - began >= cap:
            reason = "cap"
        else:
            try:
                left = work()
            except Exception as exc:              # noqa: BLE001
                log(f"work check failed: {type(exc).__name__}")
                left = None
            if left is None:
                quiet_since = None
                error_since = now if error_since is None else error_since
                if now - error_since >= error_limit:
                    reason = "no-answer"
            elif left > 0:
                quiet_since = error_since = None
            else:
                error_since = None
                quiet_since = now if quiet_since is None else quiet_since
                if now - quiet_since >= quiet:
                    reason = "idle"
        if reason and not stay_on():
            break
        sleep(poll)
    log(f"powering off: {reason} after {round((clock() - began) / 60)} min")
    for step in (stop, poweroff):
        try:
            step()
        except Exception as exc:                  # noqa: BLE001  a failed stop must not keep the server on
            log(f"{getattr(step, '__name__', 'step')} failed: {type(exc).__name__}")
    return reason


# ---------- the real commands ----------

def _start() -> None:
    subprocess.run([*COMPOSE, "up", "-d"], check=True, timeout=900)


def _work() -> int | None:
    """The last line of stdout is the number; anything else (exit code, timeout, text) = unknown."""
    try:
        r = subprocess.run([*COMPOSE, "exec", "-T", "nlp-worker", "python", "-m", "callsight", "gpu-work", "--queue"],
                           capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return None
    lines = r.stdout.strip().splitlines()
    if r.returncode != 0 or not lines or not lines[-1].strip().isdigit():
        return None
    return int(lines[-1])


def _stop() -> None:
    subprocess.run([*COMPOSE, "stop", "-t", "60"], check=False, timeout=300)   # warm shutdown of the workers


def _poweroff() -> None:
    subprocess.run(["systemctl", "poweroff"], check=False, timeout=60)


def main() -> int:
    reason = run(_work, _start, _stop, _poweroff, stay_on=STAY_ON.exists,
                 log=lambda m: print(m, flush=True))
    return 0 if reason == "idle" else 1


if __name__ == "__main__":
    sys.exit(main())
