"""Redis layout shared by the live service (writer) and the API (reader).

- live:snapshot   JSON {seq, status, calls, published_at}; expires after SNAPSHOT_TTL, so a dead service = no key
                  = "no live data" on the page. Re-written at least every heartbeat (30 s).
- live:last_seen  epoch seconds of the last publish (no expiry): "last live data at ..." on the page.
- live:events     pub/sub channel: JSON {seq, status, deltas, published_at}. seq + 1 per message; a client that
                  sees a gap asks for the snapshot again.
Everything here is already masked (caller numbers: last 4 digits only).
"""
from __future__ import annotations

import json
import time

SNAPSHOT_KEY = "live:snapshot"
LAST_SEEN_KEY = "live:last_seen"
CHANNEL = "live:events"
SNAPSHOT_TTL = 90

# status values: syncing (connected, state just cleared), connected, disconnected (PBX unreachable, retrying),
# auth_blocked (pbx:auth_blocked set: waiting for a person)
STATUSES = ("syncing", "connected", "disconnected", "auth_blocked")


class Publisher:
    def __init__(self, redis, clock=time.time):
        self.redis, self._clock = redis, clock
        self.seq = int(clock() * 1000)       # keeps growing across restarts, so old and new numbers never match

    def publish(self, calls: list[dict], deltas: list[dict], status: str) -> int:
        """Snapshot first, then the delta: a client that gets delta n and asks for the snapshot sees >= n."""
        self.seq += 1
        now = round(self._clock(), 3)
        snap = {"seq": self.seq, "status": status, "calls": calls, "published_at": now}
        pipe = self.redis.pipeline()
        pipe.set(SNAPSHOT_KEY, json.dumps(snap, ensure_ascii=False), ex=SNAPSHOT_TTL)
        pipe.set(LAST_SEEN_KEY, str(now))
        pipe.publish(CHANNEL, json.dumps({"seq": self.seq, "status": status, "deltas": deltas, "published_at": now},
                                         ensure_ascii=False))
        pipe.execute()
        return self.seq

    def clear(self) -> None:
        """Clean stop: the page shows "no live data" at once instead of after SNAPSHOT_TTL."""
        self.seq += 1
        pipe = self.redis.pipeline()
        pipe.delete(SNAPSHOT_KEY)
        pipe.publish(CHANNEL, json.dumps({"seq": self.seq, "status": "stopped", "deltas": [{"op": "reset"}],
                                          "published_at": round(self._clock(), 3)}))
        pipe.execute()
