"""Read-only client for Polymarket's Data API (public trade tape & positions).

Docs base: https://data-api.polymarket.com  (public, unauthenticated).
Used only to REPLAY real taker flow in the paper simulator -- never to trade.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

import requests

DATA = "https://data-api.polymarket.com"


@dataclass
class Trade:
    token_id: str        # `asset`
    condition_id: str
    side: str            # BUY / SELL (taker perspective as reported)
    price: float
    size: float
    timestamp: int
    outcome: Optional[str] = None
    outcome_index: Optional[int] = None


class DataClient:
    def __init__(self, base: str = DATA, timeout: float = 20.0, pause: float = 0.2):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.pause = pause
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "poly-arb/0.1 (read-only scanner)"})

    def trades(self, condition_id: str, limit: int = 500, max_pages: int = 4,
               since_ts: Optional[int] = None) -> List[Trade]:
        """Recent trades for a market (condition), newest-first from the API;
        returned sorted oldest-first for replay."""
        out: List[Trade] = []
        offset = 0
        for _ in range(max_pages):
            r = self.s.get(f"{self.base}/trades",
                           params={"market": condition_id, "limit": limit, "offset": offset},
                           timeout=self.timeout)
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch:
                break
            stop = False
            for d in batch:
                ts = int(d.get("timestamp") or 0)
                if since_ts and ts < since_ts:
                    stop = True
                    continue
                try:
                    out.append(Trade(
                        token_id=str(d.get("asset") or ""),
                        condition_id=str(d.get("conditionId") or condition_id),
                        side=str(d.get("side") or ""),
                        price=float(d.get("price")),
                        size=float(d.get("size")),
                        timestamp=ts,
                        outcome=d.get("outcome"),
                        outcome_index=d.get("outcomeIndex"),
                    ))
                except (TypeError, ValueError):
                    continue
            if stop or len(batch) < limit:
                break
            offset += len(batch)
            time.sleep(self.pause)
        out.sort(key=lambda t: t.timestamp)
        return out
