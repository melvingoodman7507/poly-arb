"""Read-only client for Polymarket's CLOB API (order books & prices).

Docs: https://clob.polymarket.com  (public read endpoints, unauthenticated).

Only GETs/POSTs against read endpoints are used. This client can NOT place,
sign or cancel orders -- by design. Execution lives only in the paper simulator.
"""

from __future__ import annotations

import time
from typing import Dict, Iterable, List, Optional

import requests

from .models import Book

CLOB = "https://clob.polymarket.com"


class ClobClient:
    def __init__(self, base: str = CLOB, timeout: float = 20.0, pause: float = 0.12):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.pause = pause
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "poly-arb/0.1 (read-only scanner)"})

    def book(self, token_id: str) -> Optional[Book]:
        r = self.s.get(f"{self.base}/book", params={"token_id": token_id}, timeout=self.timeout)
        if r.status_code != 200:
            return None
        return Book.from_api(r.json())

    def books(self, token_ids: Iterable[str], chunk: int = 100) -> Dict[str, Book]:
        """Batch-fetch order books. Returns {token_id: Book}."""
        ids = [t for t in token_ids if t]
        out: Dict[str, Book] = {}
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            r = self.s.post(f"{self.base}/books", json=[{"token_id": t} for t in part], timeout=self.timeout)
            if r.status_code == 200:
                body = r.json()
                for d in (body if isinstance(body, list) else []):
                    if not isinstance(d, dict):
                        continue
                    b = Book.from_api(d)
                    if b.token_id:
                        out[b.token_id] = b
            if i + chunk < len(ids):
                time.sleep(self.pause)
        return out

    def prices(self, token_ids: Iterable[str], chunk: int = 100) -> Dict[str, Dict[str, float]]:
        """Batch best BUY/SELL prices. Returns {token_id: {'BUY': x, 'SELL': y}}."""
        ids = [t for t in token_ids if t]
        out: Dict[str, Dict[str, float]] = {}
        payload = []
        for t in ids:
            payload.append({"token_id": t, "side": "BUY"})
            payload.append({"token_id": t, "side": "SELL"})
        for i in range(0, len(payload), chunk * 2):
            part = payload[i:i + chunk * 2]
            r = self.s.post(f"{self.base}/prices", json=part, timeout=self.timeout)
            if r.status_code == 200:
                for tid, sides in r.json().items():
                    out[tid] = {k: float(v) for k, v in sides.items()}
            if i + chunk * 2 < len(payload):
                time.sleep(self.pause)
        return out

    def midpoint(self, token_id: str) -> Optional[float]:
        r = self.s.get(f"{self.base}/midpoint", params={"token_id": token_id}, timeout=self.timeout)
        if r.status_code != 200:
            return None
        try:
            return float(r.json().get("mid"))
        except (TypeError, ValueError):
            return None

    def tick_size(self, token_id: str) -> Optional[float]:
        r = self.s.get(f"{self.base}/tick-size", params={"token_id": token_id}, timeout=self.timeout)
        if r.status_code != 200:
            return None
        try:
            return float(r.json().get("minimum_tick_size"))
        except (TypeError, ValueError):
            return None
