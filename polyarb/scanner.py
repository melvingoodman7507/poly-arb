"""Arbitrage detectors.

Two families of guaranteed-payout structure on Polymarket:

1. BINARY COMPLEMENTARY (within one market)
   YES and NO always sum to $1 at resolution. If you can acquire one YES and
   one NO for less than $1 total, you merge them into $1 -> risk-free edge.
     * taker view : best_ask(YES) + best_ask(NO) < 1  (executable now, rare)
     * maker view : rest BUY YES @ qy and BUY NO @ qn with qy+qn < 1, pay 0
                    fees + earn a rebate if BOTH fill.

2. MULTI-OUTCOME MUTUALLY-EXCLUSIVE (a negRisk group -- one question, N
   candidates, exactly one resolves YES). Markets sharing a `negRiskMarketID`
   form the exclusive set. The YES prices should sum to $1.
     * buy-all-YES : sum(best_ask(YES_i)) < 1        -> one YES pays $1
     * buy-all-NO  : sum(best_ask(NO_i))  < (N - 1)  -> (N-1) NOs pay $1 each
                     (negRisk lets you convert/redeem the NO set efficiently)

   IMPORTANT: we group by negRiskMarketID, NOT by event. A Polymarket "event"
   (e.g. "France vs England - More Markets") bundles many *independent* prop
   markets whose YES prices are unrelated -- summing those is meaningless and
   was the source of bogus signals. Grouping by negRiskMarketID and requiring
   every leg to be quotable keeps the mutual-exclusivity guarantee honest.

Every signal carries the taker net (after the exact fee) and the maker net
(0 fee + estimated rebate) so the fee-thesis is visible per row.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from . import fees
from .models import Market, Book


@dataclass
class ScanConfig:
    max_markets: int = 400
    max_events: int = 150
    min_liquidity: float = 2000.0      # USDC of CLOB liquidity to bother with
    min_event_markets: int = 3
    sim_shares: float = 100.0          # shares per leg for P&L illustration
    maker_improve_ticks: int = 1       # ticks we jump ahead of best bid for queue priority
    min_maker_margin: float = 0.004    # only surface maker rows with >=0.4c/pair after improve
    max_realistic_margin: float = 0.05 # margins above this usually = wide/illiquid mirage
    prime_vol24h: float = 20000.0      # volume to call a maker market "prime"
    your_maker_share: float = 0.5      # conservative: assume we win half the rebate pool
    min_leg_size_usdc: float = 20.0    # ignore dust levels


@dataclass
class ArbSignal:
    kind: str                # binary_taker | binary_maker | multi_yes_buy | multi_no_buy
    label: str
    category: str
    cost_per_unit: float     # $ paid per guaranteed $1 payout unit (or per pair)
    gross_edge_per_unit: float
    taker_net_per_unit: float
    maker_net_per_unit: float
    size_shares: float
    notional_usdc: float
    taker_profit_usdc: float
    maker_profit_usdc: float
    executable_now: bool     # true only for hard, guaranteed arbs
    fillability: str = ""    # for maker setups
    fill_tier: int = 0       # 3=prime 2=good 1=moderate 0=mirage
    liquidity: float = 0.0
    volume24hr: float = 0.0
    detail: dict = field(default_factory=dict)

    def rank_key(self):
        # Hard guaranteed arbs first, ranked by FEE-AWARE taker net (so a gross
        # gap that goes negative after taker fees sinks below real ones); then
        # maker setups by fill tier and volume-weighted EV.
        if self.executable_now:
            return (10, 0, self.taker_net_per_unit)
        ev = self.maker_net_per_unit * min(self.volume24hr, 500000.0) / 1e5
        return (self.fill_tier, 0, ev)

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("detail", None)
        return d


def _best(book: Optional[Book], side: str) -> Optional[float]:
    if not book:
        return None
    lvl = book.best_ask() if side == "ask" else book.best_bid()
    return lvl.price if lvl else None


def _size(book: Optional[Book], side: str) -> float:
    if not book:
        return 0.0
    lvl = book.best_ask() if side == "ask" else book.best_bid()
    return lvl.size if lvl else 0.0


# ---------------------------------------------------------------------------
# 1. Binary complementary
# ---------------------------------------------------------------------------
def scan_binary(m: Market, yes: Optional[Book], no: Optional[Book], cfg: ScanConfig) -> List[ArbSignal]:
    out: List[ArbSignal] = []
    tick = m.tick_size or 0.01
    ay, an = _best(yes, "ask"), _best(no, "ask")
    by, bn = _best(yes, "bid"), _best(no, "bid")

    # --- hard taker complementary arb: both asks executable, sum < 1 ---
    if ay is not None and an is not None and (ay + an) < 1.0:
        size = min(_size(yes, "ask"), _size(no, "ask"))
        if size * (ay + an) >= cfg.min_leg_size_usdc:
            gross = 1.0 - (ay + an)
            fee = (fees.taker_fee_per_share(ay, m.category) +
                   fees.taker_fee_per_share(an, m.category)) if m.fees_enabled else 0.0
            out.append(ArbSignal(
                kind="binary_taker", label=m.question, category=m.category,
                cost_per_unit=ay + an, gross_edge_per_unit=gross,
                taker_net_per_unit=gross - fee, maker_net_per_unit=gross,
                size_shares=size, notional_usdc=size * (ay + an),
                taker_profit_usdc=(gross - fee) * size, maker_profit_usdc=gross * size,
                executable_now=True, fillability="executable now",
                liquidity=m.liquidity, volume24hr=m.volume24hr,
                detail={"yes_ask": ay, "no_ask": an, "condition_id": m.condition_id,
                        "slug": m.slug, "taker_fee_per_pair": fee},
            ))

    # --- maker complementary setup: quote a BUY inside both spreads ---
    # Realistic only when the book is two-sided; margin realised only if BOTH fill.
    if by is not None and bn is not None and ay is not None and an is not None:
        qy = round(by + cfg.maker_improve_ticks * tick, 4)
        qn = round(bn + cfg.maker_improve_ticks * tick, 4)
        if 0 < qy < ay and 0 < qn < an:                       # stay a maker on both legs
            margin = 1.0 - (qy + qn)
            combined_spread = (ay + an) - (by + bn)
            if margin >= cfg.min_maker_margin:
                rebate = (fees.rebate_for_fill(cfg.sim_shares, qy, m.category, cfg.your_maker_share) +
                          fees.rebate_for_fill(cfg.sim_shares, qn, m.category, cfg.your_maker_share)) / cfg.sim_shares
                # Liquidity-rewards: our quotes sit below each midpoint by s cents.
                mid_y, mid_n = (ay + by) / 2.0, (an + bn) / 2.0
                s_y, s_n = max(0.0, mid_y - qy) * 100.0, max(0.0, mid_n - qn) * 100.0
                r_elig = (m.rewards_max_spread > 0 and
                          fees.reward_eligible(s_y, cfg.sim_shares, m.rewards_max_spread, m.rewards_min_size) and
                          fees.reward_eligible(s_n, cfg.sim_shares, m.rewards_max_spread, m.rewards_min_size))
                r_score = min(fees.reward_score(s_y, cfg.sim_shares, m.rewards_max_spread),
                              fees.reward_score(s_n, cfg.sim_shares, m.rewards_max_spread)) if r_elig else 0.0
                # Tier: mirage < moderate < good < prime. Reward-eligible + tight + liquid = prime.
                tight = combined_spread <= 3 * tick
                if margin > cfg.max_realistic_margin:
                    tier, fillability = 0, "wide/illiquid — margin likely a mirage (adverse selection)"
                elif tight and m.volume24hr >= cfg.prime_vol24h and r_elig:
                    tier, fillability = 3, "prime (tight + liquid + earns liquidity rewards)"
                elif tight and m.volume24hr >= cfg.prime_vol24h:
                    tier, fillability = 3, "prime (tight spread + liquid)"
                elif r_elig or m.volume24hr >= cfg.prime_vol24h:
                    tier, fillability = 2, ("good (reward-eligible)" if r_elig else "good (liquid)")
                else:
                    tier, fillability = 1, "moderate"
                out.append(ArbSignal(
                    kind="binary_maker", label=m.question, category=m.category,
                    cost_per_unit=qy + qn, gross_edge_per_unit=margin,
                    taker_net_per_unit=margin,   # if you had to take, fees would apply; shown maker-side
                    maker_net_per_unit=margin + rebate,
                    size_shares=cfg.sim_shares, notional_usdc=cfg.sim_shares * (qy + qn),
                    taker_profit_usdc=margin * cfg.sim_shares,
                    maker_profit_usdc=(margin + rebate) * cfg.sim_shares,
                    executable_now=False, fillability=fillability, fill_tier=tier,
                    liquidity=m.liquidity, volume24hr=m.volume24hr,
                    detail={"quote_yes": qy, "quote_no": qn, "best_bid_yes": by, "best_bid_no": bn,
                            "best_ask_yes": ay, "best_ask_no": an, "combined_spread": round(combined_spread, 4),
                            "rebate_per_pair": round(rebate, 5), "reward_eligible": r_elig,
                            "reward_score_qmin": round(r_score, 2), "rewards_max_spread": m.rewards_max_spread,
                            "rewards_min_size": m.rewards_min_size, "yes_token": m.yes_token,
                            "no_token": m.no_token, "condition_id": m.condition_id, "tick": tick,
                            "note": "structural margin; realised only if BOTH legs fill -- see paper sim"},
                ))
    return out


# ---------------------------------------------------------------------------
# 2. Multi-outcome mutually-exclusive (negRisk group)
# ---------------------------------------------------------------------------
def scan_neg_risk_group(legs: List[Market], books: Dict[str, Book], cfg: ScanConfig) -> List[ArbSignal]:
    """`legs` = markets sharing one negRiskMarketID (mutually exclusive outcomes)."""
    n = len(legs)
    if n < cfg.min_event_markets:
        return []
    category = legs[0].category
    label = legs[0].event_title or (legs[0].question[:40] + " ...")

    yes_asks, no_asks = [], []
    yes_ask_ok = no_ask_ok = True
    for m in legs:
        yb = books.get(m.yes_token)
        nb = books.get(m.no_token)
        ya, na = _best(yb, "ask"), _best(nb, "ask")
        if ya is None:
            yes_ask_ok = False
        else:
            yes_asks.append((m, ya, _size(yb, "ask")))
        if na is None:
            no_ask_ok = False
        else:
            no_asks.append((m, na, _size(nb, "ask")))

    out: List[ArbSignal] = []
    liq = sum(m.liquidity for m in legs)
    vol = sum(m.volume24hr for m in legs)

    # Completeness guard: a genuine mutually-exclusive set has YES fair prices
    # summing to ~1. If our fetched legs sum well below that, we're likely
    # MISSING outcomes and the "buy/sell all" guarantee would be unsafe -- so
    # we refuse to flag a guaranteed arb on an incomplete set.
    yes_fair = []
    for m in legs:
        yb = books.get(m.yes_token)
        fp = (yb.mid() if yb else None) or _best(yb, "ask") or _best(yb, "bid")
        if fp is not None:
            yes_fair.append(fp)
    complete = (len(yes_fair) == n) and (0.90 <= sum(yes_fair) <= 1.10)

    # buy-all-YES: need every leg's YES ask; sum < 1
    if complete and yes_ask_ok and len(yes_asks) == n:
        ask_sum = sum(a for _, a, _ in yes_asks)
        if ask_sum < 1.0:
            size = min(s for _, _, s in yes_asks)
            if size > 0:
                gross = 1.0 - ask_sum
                fee = sum(fees.taker_fee_per_share(a, category) for _, a, _ in yes_asks) if legs[0].fees_enabled else 0.0
                out.append(ArbSignal(
                    kind="multi_yes_buy", label=f"[{n}-way] {label}", category=category,
                    cost_per_unit=ask_sum, gross_edge_per_unit=gross,
                    taker_net_per_unit=gross - fee, maker_net_per_unit=gross,
                    size_shares=size, notional_usdc=size * ask_sum,
                    taker_profit_usdc=(gross - fee) * size, maker_profit_usdc=gross * size,
                    executable_now=True, fillability="executable now",
                    liquidity=liq, volume24hr=vol,
                    detail={"n_outcomes": n, "yes_ask_sum": round(ask_sum, 4),
                            "neg_risk_market_id": legs[0].neg_risk_market_id,
                            "capital_locked_until_resolution": True,
                            "note": "buy every YES; capital LOCKS to resolution (YES legs are separate conditions, not mergeable)",
                            "legs": [(m.group_item_title or m.question[:22], round(a, 4)) for m, a, _ in yes_asks]},
                ))

    # buy-all-NO: need every leg's NO ask; sum < (n-1)
    if complete and no_ask_ok and len(no_asks) == n and n >= 3:
        no_sum = sum(a for _, a, _ in no_asks)
        if no_sum < (n - 1.0):
            size = min(s for _, _, s in no_asks)
            if size > 0:
                gross = (n - 1.0) - no_sum
                fee = sum(fees.taker_fee_per_share(a, category) for _, a, _ in no_asks) if legs[0].fees_enabled else 0.0
                out.append(ArbSignal(
                    kind="multi_no_buy", label=f"[{n}-way] {label} (buy-all-NO)", category=category,
                    cost_per_unit=no_sum, gross_edge_per_unit=gross,
                    taker_net_per_unit=gross - fee, maker_net_per_unit=gross,
                    size_shares=size, notional_usdc=size * no_sum,
                    taker_profit_usdc=(gross - fee) * size, maker_profit_usdc=gross * size,
                    executable_now=True, fillability="executable now (negRisk convert)",
                    liquidity=liq, volume24hr=vol,
                    detail={"n_outcomes": n, "no_ask_sum": round(no_sum, 4), "payout": n - 1,
                            "neg_risk_market_id": legs[0].neg_risk_market_id,
                            "note": "buy NO on every outcome; exactly (N-1) resolve YES=false -> pay $1 each"},
                ))
    return out
