"""Container healthcheck: the worker is healthy while its heartbeat (refreshed every 15 s) is fresh.

Replaces `arq --check`, whose health key expires one second after its 30 s refresh, so a probe
landing in that gap reported a healthy worker as unhealthy.
"""

from __future__ import annotations

import sys
import time
from typing import cast

from redis import Redis

from jobpilot_core.config import get_settings
from jobpilot_core.health import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL_S


def main() -> int:
    try:
        client: Redis = Redis.from_url(get_settings().redis_url, socket_timeout=3)
        raw = cast("bytes | None", client.get(WORKER_HEARTBEAT_KEY))
    except Exception:
        return 1
    if raw is None:
        return 1
    return 0 if time.time() - float(str(raw, "ascii")) < WORKER_HEARTBEAT_TTL_S else 1


if __name__ == "__main__":
    sys.exit(main())
