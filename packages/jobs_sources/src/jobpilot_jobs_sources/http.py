"""The polite HTTP client every connector goes through.

- identifiable User-Agent with a contact URL;
- robots.txt checked (and cached for a day) before any scraped (non-API) URL;
- per-source daily request caps and a minimum interval between two requests to the same host,
  stored in Redis so the API, the worker and every run share them;
- response cache (a re-run within the TTL costs the source nothing);
- retries with exponential backoff on 429 / 5xx / network errors, honouring Retry-After;
- bounded response size: a source cannot make us download gigabytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
from protego import Protego

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = logging.getLogger(__name__)

MAX_BYTES = 8 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """`code` is stable (shown on the dashboard): daily_cap, robots_disallow, http_<status>..."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class PoliteClient:
    def __init__(
        self,
        redis: Redis,
        *,
        user_agent: str,
        timeout_s: float = 30.0,
        cache_ttl_s: int = 3 * 3600,
        min_interval_s: float = 6.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._redis = redis
        self._ua = user_agent
        self._ttl = cache_ttl_s
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Language": "fr,en;q=0.8"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ budgets
    async def _take_daily(self, source: str, cap: int) -> None:
        key = f"jobs:calls:{source}:{datetime.now(UTC):%Y%m%d}"
        used = int(await self._redis.incr(key))
        if used == 1:
            await self._redis.expire(key, 2 * 86_400)
        if used > cap:
            raise SourceError("daily_cap", f"{source}: {cap} requests/day reached")

    async def calls_today(self, source: str) -> int:
        raw = await self._redis.get(f"jobs:calls:{source}:{datetime.now(UTC):%Y%m%d}")
        return int(raw or 0)

    async def _pace(self, host: str, interval: float) -> None:
        """At most one request per `interval` seconds to the same host, across processes."""
        if interval <= 0:
            return
        key = f"jobs:pace:{host}"
        while True:
            now = time.time()
            if await self._redis.set(key, now, nx=True, px=int(interval * 1000)):
                return
            ttl_ms = await self._redis.pttl(key)
            await self._sleep(max(0.05, (ttl_ms if ttl_ms and ttl_ms > 0 else 100) / 1000))

    # ------------------------------------------------------------------ robots.txt
    async def robots_allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        key = f"jobs:robots:{parts.netloc}"
        body = await self._redis.get(key)
        if body is None:
            try:
                resp = await self._http.get(f"{origin}/robots.txt")
                text = resp.text if resp.status_code == 200 else ""
                # 4xx: no robots.txt -> everything allowed; 5xx: be safe, disallow for now
                if resp.status_code >= 500:
                    text = "User-agent: *\nDisallow: /"
            except httpx.HTTPError:
                text = "User-agent: *\nDisallow: /"
            await self._redis.set(key, text, ex=86_400)
            body = text.encode()
        # Protego follows Google's spec (wildcards, longest match), unlike urllib.robotparser
        rules = Protego.parse(body.decode() if isinstance(body, bytes) else str(body))
        return bool(rules.can_fetch(url, self._ua))

    # ------------------------------------------------------------------ requests
    async def get(
        self,
        url: str,
        *,
        source: str,
        daily_cap: int,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        scraped: bool = False,
        interval_s: float | None = None,
    ) -> str:
        """GET text, through cache, robots (when `scraped`), budgets, pacing and retries."""
        request = self._http.build_request("GET", url, params=params, headers=headers)
        full_url = str(request.url)
        cache_key = "jobs:cache:" + hashlib.sha256(full_url.encode()).hexdigest()
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return cached.decode() if isinstance(cached, bytes) else str(cached)
        if scraped and not await self.robots_allowed(full_url):
            raise SourceError("robots_disallow", full_url)
        await self._take_daily(source, daily_cap)
        host = urlsplit(full_url).netloc
        interval = self._min_interval if interval_s is None else interval_s

        delay = 2.0
        for attempt in range(4):
            await self._pace(host, interval)
            try:
                async with self._http.stream("GET", url, params=params, headers=headers) as resp:
                    if resp.status_code in RETRY_STATUSES and attempt < 3:
                        retry_after = resp.headers.get("retry-after", "")
                        wait = float(retry_after) if retry_after.isdigit() else delay
                        log.info("%s: HTTP %s, retrying in %.0fs", source, resp.status_code, wait)
                        await self._sleep(min(wait, 60.0))
                        delay *= 2
                        continue
                    if resp.status_code >= 400:
                        raise SourceError(f"http_{resp.status_code}", full_url)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise SourceError("too_large", full_url)
                        chunks.append(chunk)
                    text = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
            except httpx.TransportError as exc:
                if attempt == 3:
                    raise SourceError("network", f"{type(exc).__name__}: {exc}") from exc
                await self._sleep(delay)
                delay *= 2
                continue
            await self._redis.set(cache_key, text, ex=self._ttl)
            return text
        raise SourceError("http_retries_exhausted", full_url)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        text = await self.get(url, **kwargs)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise SourceError("bad_json", url) from exc
