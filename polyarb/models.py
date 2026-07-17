"""Typed data models for markets, events and order books."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Any

from . import fees


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _loads(x, default):
    if x is None:
        return default
    if isinstance(x, (list, dict)):
        return x
    try:
        return json.loads(x)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


@dataclass
class Level:
    price: float
    size: float


@dataclass
class Book:
    """A CLOB order book for one outcome token.

    bids are sorted best-first (highest price first); asks best-first (lowest
    price first). The raw API does not guarantee ordering, so we sort here.
    """

    token_id: str
    bids: List[Level] = field(default_factory=list)
    asks: List[Level] = field(default_factory=list)
    tick_size: float = 0.01
    neg_risk: bool = False
    min_order_size: float = 5.0
    last_trade_price: Optional[float] = None

    @classmethod
    def from_api(cls, d: dict) -> "Book":
        def _levels(raw):
            out = []
            for x in raw or []:
                p, s = _f(x.get("price")), _f(x.get("size"))
                if p is not None and s is not None:
                    out.append(Level(p, s))
            return out
        bids = _levels(d.get("bids"))
        asks = _levels(d.get("asks"))
        bids.sort(key=lambda l: l.price, reverse=True)   # best bid = highest
        asks.sort(key=lambda l: l.price)                 # best ask = lowest
        return cls(
            token_id=str(d.get("asset_id") or d.get("token_id") or ""),
            bids=bids,
            asks=asks,
            tick_size=_f(d.get("tick_size"), 0.01),
            neg_risk=bool(d.get("neg_risk", False)),
            min_order_size=_f(d.get("min_order_size"), 5.0),
            last_trade_price=_f(d.get("last_trade_price")),
        )

    def best_bid(self) -> Optional[Level]:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[Level]:
        return self.asks[0] if self.asks else None

    def mid(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb and ba:
            return (bb.price + ba.price) / 2.0
        if ba:
            return ba.price
        if bb:
            return bb.price
        return None

    def ask_depth_usdc(self, max_price: float) -> float:
        """USDC of liquidity available to BUY at or below `max_price`."""
        return sum(l.size * l.price for l in self.asks if l.price <= max_price + 1e-9)

    def ask_shares(self, max_price: float) -> float:
        return sum(l.size for l in self.asks if l.price <= max_price + 1e-9)


@dataclass
class Market:
    """A single binary (Yes/No) market."""

    condition_id: str
    question: str
    yes_token: str
    no_token: str
    neg_risk: bool = False
    neg_risk_market_id: Optional[str] = None
    tick_size: float = 0.01
    min_size: float = 5.0
    category: str = fees.DEFAULT_CATEGORY
    fees_enabled: bool = True
    active: bool = True
    accepting_orders: bool = True
    volume24hr: float = 0.0
    liquidity: float = 0.0
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread: Optional[float] = None
    rewards_max_spread: float = 0.0   # cents from midpoint to be reward-eligible
    rewards_min_size: float = 0.0     # min shares to earn liquidity rewards
    event_id: Optional[str] = None
    event_title: Optional[str] = None
    group_item_title: Optional[str] = None
    slug: Optional[str] = None
    end_date: Optional[str] = None

    @staticmethod
    def _category_from(d: dict) -> str:
        # Gamma markets carry category signal on the parent event via tags/series.
        ev = (d.get("events") or [{}])
        ev0 = ev[0] if ev else {}
        for key in ("category",):
            if d.get(key):
                return fees.normalize_category(d[key])
        # events[].series[].title or ticker often encode the category/topic
        for tag in (ev0.get("ticker"), ev0.get("slug"), d.get("slug")):
            cat = fees.normalize_category(tag)
            if cat != fees.DEFAULT_CATEGORY:
                return cat
        # fall back to a keyword scan of the human-readable text
        return fees.category_from_text(d.get("question"), ev0.get("title"),
                                       d.get("groupItemTitle"), ev0.get("ticker"), d.get("slug"))

    @classmethod
    def from_gamma(cls, d: dict) -> Optional["Market"]:
        toks = _loads(d.get("clobTokenIds"), [])
        if not toks or len(toks) < 2:
            return None
        ev = (d.get("events") or [{}])
        ev0 = ev[0] if ev else {}
        return cls(
            condition_id=str(d.get("conditionId") or ""),
            question=str(d.get("question") or d.get("groupItemTitle") or ""),
            yes_token=str(toks[0]),
            no_token=str(toks[1]),
            neg_risk=bool(d.get("negRisk", False)),
            neg_risk_market_id=(str(d.get("negRiskMarketID")) if d.get("negRiskMarketID") else None),
            tick_size=_f(d.get("orderPriceMinTickSize"), 0.01),
            min_size=_f(d.get("orderMinSize"), 5.0),
            category=cls._category_from(d),
            fees_enabled=bool(d.get("feesEnabled", True)),
            active=bool(d.get("active", True)),
            accepting_orders=bool(d.get("acceptingOrders", True)),
            volume24hr=_f(d.get("volume24hr"), 0.0) or 0.0,
            liquidity=_f(d.get("liquidityClob") or d.get("liquidityNum") or d.get("liquidity"), 0.0) or 0.0,
            best_bid=_f(d.get("bestBid")),
            best_ask=_f(d.get("bestAsk")),
            spread=_f(d.get("spread")),
            rewards_max_spread=_f(d.get("rewardsMaxSpread"), 0.0) or 0.0,
            rewards_min_size=_f(d.get("rewardsMinSize"), 0.0) or 0.0,
            event_id=str(ev0.get("id")) if ev0.get("id") else None,
            event_title=ev0.get("title"),
            group_item_title=d.get("groupItemTitle"),
            slug=d.get("slug"),
            end_date=d.get("endDate"),
        )


@dataclass
class Event:
    """A group of mutually-exclusive markets (multi-candidate / negRisk)."""

    event_id: str
    title: str
    neg_risk: bool
    markets: List[Market] = field(default_factory=list)
    slug: Optional[str] = None
    category: str = fees.DEFAULT_CATEGORY

    @classmethod
    def from_gamma(cls, d: dict) -> Optional["Event"]:
        mkts = []
        for md in d.get("markets", []):
            md.setdefault("events", [{"id": d.get("id"), "title": d.get("title"),
                                      "ticker": d.get("ticker"), "slug": d.get("slug")}])
            m = Market.from_gamma(md)
            if m:
                mkts.append(m)
        if not mkts:
            return None
        cat = fees.normalize_category(d.get("ticker") or d.get("slug"))
        return cls(
            event_id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            neg_risk=bool(d.get("negRisk", False)),
            markets=mkts,
            slug=d.get("slug"),
            category=cat if cat != fees.DEFAULT_CATEGORY else (mkts[0].category if mkts else fees.DEFAULT_CATEGORY),
        )
