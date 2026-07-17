"""Scan orchestrator: discover markets/events, batch-fetch books, detect arbs."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Tuple

from .gamma import GammaClient
from .clob import ClobClient
from .models import Market, Event, Book
from .scanner import ScanConfig, ArbSignal, scan_binary, scan_neg_risk_group


class ArbEngine:
    def __init__(self, cfg: ScanConfig | None = None):
        self.cfg = cfg or ScanConfig()
        self.gamma = GammaClient()
        self.clob = ClobClient()

    def _fetch_markets(self) -> List[Market]:
        return list(self.gamma.iter_markets(
            max_markets=self.cfg.max_markets, min_liquidity=self.cfg.min_liquidity))

    def _fetch_events(self) -> List[Event]:
        return list(self.gamma.iter_events(
            max_events=self.cfg.max_events, min_markets=self.cfg.min_event_markets))

    def scan(self, do_binary: bool = True, do_multi: bool = True,
             log=lambda *_: None) -> Dict[str, object]:
        t0 = time.time()
        signals: List[ArbSignal] = []
        stats = {"markets": 0, "events": 0, "books": 0}

        if do_binary:
            markets = self._fetch_markets()
            stats["markets"] = len(markets)
            log(f"  fetched {len(markets)} binary markets (liq >= ${self.cfg.min_liquidity:,.0f})")
            tokens = []
            for m in markets:
                tokens += [m.yes_token, m.no_token]
            books = self.clob.books(tokens)
            stats["books"] += len(books)
            log(f"  fetched {len(books)} order books")
            for m in markets:
                signals += scan_binary(m, books.get(m.yes_token), books.get(m.no_token), self.cfg)

        if do_multi:
            events = self._fetch_events()
            stats["events"] = len(events)
            # Regroup ALL markets across events by negRiskMarketID -> true
            # mutually-exclusive sets (splits bundled "More Markets" events).
            groups: Dict[str, List[Market]] = defaultdict(list)
            for ev in events:
                for m in ev.markets:
                    if m.neg_risk and m.neg_risk_market_id:
                        groups[m.neg_risk_market_id].append(m)
            groups = {k: v for k, v in groups.items() if len(v) >= self.cfg.min_event_markets}
            stats["neg_risk_groups"] = len(groups)
            log(f"  fetched {len(events)} events -> {len(groups)} negRisk exclusive groups")
            ev_tokens = []
            for legs in groups.values():
                for m in legs:
                    ev_tokens += [m.yes_token, m.no_token]
            ev_books = self.clob.books(ev_tokens)
            stats["books"] += len(ev_books)
            for legs in groups.values():
                signals += scan_neg_risk_group(legs, ev_books, self.cfg)

        signals.sort(key=lambda s: s.rank_key(), reverse=True)
        return {
            "signals": signals,
            "stats": stats,
            "elapsed_s": round(time.time() - t0, 1),
            "config": self.cfg,
        }
