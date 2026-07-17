"""Read-only client for Polymarket's Gamma API (market & event discovery).

Docs: https://gamma-api.polymarket.com  (public, unauthenticated).
"""

from __future__ import annotations

import time
from typing import Dict, Iterator, List, Optional

import requests

from .models import Market, Event

GAMMA = "https://gamma-api.polymarket.com"


class GammaClient:
    def __init__(self, base: str = GAMMA, timeout: float = 20.0, pause: float = 0.15):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.pause = pause
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "poly-arb/0.1 (read-only scanner)"})

    def _get(self, path: str, **params) -> list | dict:
        last = None
        for attempt in range(3):
            try:
                r = self.s.get(f"{self.base}{path}", params=params, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503, 504):
                    last = requests.HTTPError(f"HTTP {r.status_code} on {path}")
                    time.sleep(0.5 * (attempt + 1))
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                last = e
                time.sleep(0.5 * (attempt + 1))
        if last:
            raise last
        return []

    # --- markets --------------------------------------------------------------
    def markets_page(self, limit: int = 100, offset: int = 0, **filters) -> List[dict]:
        params = dict(closed="false", active="true", limit=limit, offset=offset,
                      order="volume24hr", ascending="false")
        params.update(filters)
        out = self._get("/markets", **params)
        return out if isinstance(out, list) else out.get("data", [])

    def iter_markets(self, max_markets: int = 500, page: int = 100,
                     min_liquidity: float = 0.0, **filters) -> Iterator[Market]:
        # Request a FULL page each time and cap on yielded count. (Requesting
        # `max_markets - fetched` would shrink the ask and then trip an early
        # `len(batch) < page` break, silently skipping deeper markets, because
        # `fetched` counts post-filter yields, not raw rows returned.)
        fetched = 0
        offset = 0
        while fetched < max_markets:
            batch = self.markets_page(limit=page, offset=offset, **filters)
            if not batch:
                break
            for d in batch:
                m = Market.from_gamma(d)
                if m is None:
                    continue
                if m.liquidity < min_liquidity:
                    continue
                yield m
                fetched += 1
                if fetched >= max_markets:
                    return
            offset += len(batch)
            if len(batch) < page:   # true end of data (we asked for a full page)
                break
            time.sleep(self.pause)

    def market_by_condition(self, condition_id: str) -> Optional[Market]:
        out = self._get("/markets", condition_ids=condition_id, limit=1)
        arr = out if isinstance(out, list) else out.get("data", [])
        return Market.from_gamma(arr[0]) if arr else None

    # --- events (multi-outcome groups) ---------------------------------------
    def events_page(self, limit: int = 50, offset: int = 0, **filters) -> List[dict]:
        params = dict(closed="false", active="true", limit=limit, offset=offset,
                      order="volume24hr", ascending="false")
        params.update(filters)
        out = self._get("/events", **params)
        return out if isinstance(out, list) else out.get("data", [])

    def iter_events(self, max_events: int = 200, page: int = 50,
                    min_markets: int = 3, **filters) -> Iterator[Event]:
        fetched = 0
        offset = 0
        while fetched < max_events:
            batch = self.events_page(limit=page, offset=offset, **filters)
            if not batch:
                break
            for d in batch:
                if len(d.get("markets", [])) < min_markets:
                    continue
                ev = Event.from_gamma(d)
                if ev is None or len(ev.markets) < min_markets:
                    continue
                yield ev
                fetched += 1
                if fetched >= max_events:
                    return
            offset += len(batch)
            if len(batch) < page:   # true end of data
                break
            time.sleep(self.pause)
