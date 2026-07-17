"""Paper-trading simulator for the complementary MAKER strategy.

No orders are ever placed. We take a real binary market, decide where we would
rest two maker BUY orders (YES and NO), then REPLAY the market's actual recent
taker flow (from the public trade tape) against those resting quotes to estimate
what would have filled -- and, crucially, the one-sided-fill / adverse-selection
risk that the scanner's structural margin ignores.

FILL MODEL (stated assumptions, kept deliberately simple and honest):
  * We rest BUY YES @ qy and BUY NO @ qn, `size` shares each, improved one tick
    above the best bid so we sit at the front of the queue.
  * A resting BUY fills only against a taker SELL on that token (side == SELL,
    which hits the bid) at a price <= our quote. At our improved price we are
    effectively first in queue, so we fill min(trade_size, remaining) each time
    -- never more than the real sell volume that traded through our level.
    (Taker BUYS lift the ask and do NOT fill our resting bid, so they're ignored.)
  * Matched YES+NO pairs are merged to $1 (0 fee), realising margin = 1-qy-qn
    per pair, plus the maker rebate on both legs.
  * Whatever fills on only ONE side is leftover directional INVENTORY. We mark
    it at the current best bid of that token (a conservative, adverse-selection-
    aware mark) -- this is where the strategy can bleed.

This is an ESTIMATE against static quotes, not a re-quoting engine. It is meant
to make the risk visible before any capital is ever committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import fees
from .models import Market, Book
from .data import Trade


@dataclass
class PaperConfig:
    size: float = 200.0            # shares we would rest per leg
    improve_ticks: int = 1         # ticks above best bid (queue priority)
    your_maker_share: float = 0.5  # share of the rebate pool we assume we win
    lookback_hours: float = 6.0    # window of real flow to replay


@dataclass
class PaperResult:
    question: str
    category: str
    quote_yes: float
    quote_no: float
    size: float
    cost_per_pair: float
    margin_per_pair: float
    n_trades_seen: int
    filled_yes: float
    filled_no: float
    matched_pairs: float
    # P&L breakdown (USDC)
    realized_margin: float          # from matched (merged) pairs
    rebates: float                  # maker rebate on all fills
    inventory_shares: float         # leftover one-sided shares
    inventory_side: str
    inventory_cost: float           # what we paid for leftover inventory
    inventory_mark: float           # conservative current value
    inventory_pnl: float            # mark - cost (usually negative => adverse selection)
    net_pnl: float
    reward_eligible: bool
    reward_score_qmin: float
    window_hours: float
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        L = []
        L.append(f"{self.question[:70]}  [{self.category}]")
        L.append(f"  quotes: BUY YES @ {self.quote_yes:.3f} + BUY NO @ {self.quote_no:.3f}"
                 f"  (cost {self.cost_per_pair:.3f}/pair, margin {self.margin_per_pair*100:.2f}c)")
        L.append(f"  replayed {self.n_trades_seen} real trades over ~{self.window_hours:.1f}h")
        L.append(f"  fills: YES {self.filled_yes:.0f} sh, NO {self.filled_no:.0f} sh"
                 f"  -> matched {self.matched_pairs:.0f} pairs")
        L.append(f"  + realized margin (merged pairs) : ${self.realized_margin:+.2f}")
        L.append(f"  + maker rebates                  : ${self.rebates:+.2f}")
        if self.inventory_shares > 0:
            L.append(f"  - leftover {self.inventory_side} inventory {self.inventory_shares:.0f} sh"
                     f"  mark ${self.inventory_mark:.2f} vs cost ${self.inventory_cost:.2f}"
                     f"  = ${self.inventory_pnl:+.2f}  (adverse selection)")
        L.append(f"  = NET (paper)                    : ${self.net_pnl:+.2f}")
        if self.reward_eligible:
            L.append(f"  (also liquidity-reward eligible; two-sided score {self.reward_score_qmin:.0f})")
        for n in self.notes:
            L.append(f"  ! {n}")
        return "\n".join(L)


class PaperMaker:
    def __init__(self, cfg: PaperConfig | None = None):
        self.cfg = cfg or PaperConfig()

    def _quote(self, book: Book, tick: float) -> Optional[float]:
        bb = book.best_bid()
        ba = book.best_ask()
        if bb is None or ba is None:
            return None
        q = round(bb.price + self.cfg.improve_ticks * tick, 4)
        q = min(q, round(ba.price - tick, 4))
        return q if 0 < q < 1 else None

    def backtest(self, m: Market, yes_book: Book, no_book: Book, trades: List[Trade]) -> Optional[PaperResult]:
        tick = m.tick_size or 0.01
        qy = self._quote(yes_book, tick)
        qn = self._quote(no_book, tick)
        if qy is None or qn is None:
            return None
        size = self.cfg.size
        notes: List[str] = []

        filled_y = filled_n = 0.0
        cost_y = cost_n = 0.0
        rebates = 0.0
        matched = 0.0
        realized_margin = 0.0
        n_trades = len(trades)   # trades actually replayed
        n_fills = 0              # how many of them hit our quotes

        for t in trades:
            is_sell = t.side.upper() == "SELL"   # taker hit the bid -> can fill our BUY
            if not is_sell:
                continue
            if t.token_id == m.yes_token and t.price <= qy + 1e-9 and filled_y < size:
                fill = min(t.size, size - filled_y)
                filled_y += fill
                cost_y += fill * qy
                rebates += fees.rebate_for_fill(fill, qy, m.category, self.cfg.your_maker_share)
                n_fills += 1
            elif t.token_id == m.no_token and t.price <= qn + 1e-9 and filled_n < size:
                fill = min(t.size, size - filled_n)
                filled_n += fill
                cost_n += fill * qn
                rebates += fees.rebate_for_fill(fill, qn, m.category, self.cfg.your_maker_share)
                n_fills += 1
            # merge as pairs become available
            new_matched = min(filled_y, filled_n)
            if new_matched > matched:
                realized_margin += (new_matched - matched) * (1.0 - qy - qn)
                matched = new_matched

        # leftover one-sided inventory, conservatively marked at best bid
        inv_shares = abs(filled_y - filled_n)
        if filled_y > filled_n:
            side = "YES"
            unit_cost = qy
            mark_px = (yes_book.best_bid().price if yes_book.best_bid() else max(0.0, qy - tick))
        elif filled_n > filled_y:
            side = "NO"
            unit_cost = qn
            mark_px = (no_book.best_bid().price if no_book.best_bid() else max(0.0, qn - tick))
        else:
            side, unit_cost, mark_px = "-", 0.0, 0.0
        inv_cost = inv_shares * unit_cost
        inv_mark = inv_shares * mark_px
        inv_pnl = inv_mark - inv_cost

        net = realized_margin + rebates + inv_pnl

        if matched == 0 and (filled_y > 0 or filled_n > 0):
            notes.append("only ONE side filled in this window -> pure directional inventory, no arb locked")
        if matched == 0 and filled_y == 0 and filled_n == 0:
            notes.append("no fills through our quotes in the window (thin flow or too-deep quotes)")

        # reward eligibility of these quotes (distance-from-mid clamped >= 0)
        mid_y = yes_book.mid() or qy
        mid_n = no_book.mid() or qn
        sy = max(0.0, mid_y - qy) * 100.0
        sn = max(0.0, mid_n - qn) * 100.0
        r_elig = (m.rewards_max_spread > 0 and
                  fees.reward_eligible(sy, size, m.rewards_max_spread, m.rewards_min_size) and
                  fees.reward_eligible(sn, size, m.rewards_max_spread, m.rewards_min_size))
        r_score = min(fees.reward_score(sy, size, m.rewards_max_spread),
                      fees.reward_score(sn, size, m.rewards_max_spread)) if r_elig else 0.0

        return PaperResult(
            question=m.question, category=m.category, quote_yes=qy, quote_no=qn, size=size,
            cost_per_pair=qy + qn, margin_per_pair=1.0 - qy - qn, n_trades_seen=n_trades,
            filled_yes=filled_y, filled_no=filled_n, matched_pairs=matched,
            realized_margin=realized_margin, rebates=rebates,
            inventory_shares=inv_shares, inventory_side=side, inventory_cost=inv_cost,
            inventory_mark=inv_mark, inventory_pnl=inv_pnl, net_pnl=net,
            reward_eligible=r_elig, reward_score_qmin=r_score,
            window_hours=self.cfg.lookback_hours, notes=notes,
        )
