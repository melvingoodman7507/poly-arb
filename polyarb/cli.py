"""Command-line interface.

    python -m polyarb scan      # live arb scan, ranked table
    python -m polyarb paper     # paper-trade the top maker setups on real flow
    python -m polyarb report    # scan + paper -> self-contained HTML dashboard
    python -m polyarb fees      # show the maker-vs-taker economics (the thesis)

Everything is READ-ONLY: public endpoints, no wallet, no orders.
"""

from __future__ import annotations

import argparse
import json
import time

from . import __version__
from .engine import ArbEngine
from .scanner import ScanConfig, ArbSignal
from .clob import ClobClient
from .data import DataClient
from .gamma import GammaClient
from .paper import PaperMaker, PaperConfig
from .report import write_report
from .fees import complementary_arb_pnl, taker_fee


def _fmt_ts():
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())


def _print_scan(scan, top=15):
    sigs = scan["signals"]
    hard = [s for s in sigs if s.executable_now]
    makers = [s for s in sigs if s.kind == "binary_maker"]
    print(f"\n  scan: {scan['elapsed_s']}s | {scan['stats']} | {len(sigs)} signals\n")
    print("  COMPLETE-SET GAPS (gross-guaranteed; taker-net after fee shown — usually negative, capture as maker):")
    if hard:
        for s in hard[:top]:
            print(f"    [{s.kind:12}] {s.label[:48]:48} cost {s.cost_per_unit:.4f} "
                  f"gross ${s.gross_edge_per_unit:.4f}/u  takerNet ${s.taker_net_per_unit:+.4f}/u  "
                  f"sz {s.size_shares:.0f}  ${s.taker_profit_usdc:+.2f}")
    else:
        print("    (none — expected; hard taker arbs are rare & usually fee-negative)")
    print("\n  TOP MAKER SETUPS (ranked):")
    for s in makers[:top]:
        rew = "R" if s.detail.get("reward_eligible") else " "
        print(f"    t{s.fill_tier}{rew} {s.label[:44]:44} [{s.category:7}] "
              f"cost {s.cost_per_unit:.3f}  margin {s.gross_edge_per_unit*100:5.2f}c  "
              f"vol ${s.volume24hr:>11,.0f}  {s.fillability}")


def _make_scan_cfg(a) -> ScanConfig:
    return ScanConfig(
        max_markets=a.max_markets, max_events=a.max_events, min_liquidity=a.min_liquidity,
        sim_shares=a.size, your_maker_share=a.maker_share,
    )


def _run_paper(scan, a, verbose=True):
    """Paper-trade the top maker setups on real recent flow."""
    gc, cl, dc = GammaClient(), ClobClient(), DataClient()
    pm = PaperMaker(PaperConfig(size=a.size, lookback_hours=a.hours, your_maker_share=a.maker_share))
    candidates = [s for s in scan["signals"] if s.kind == "binary_maker" and s.fill_tier >= 1]
    candidates = candidates[:a.paper_n]
    results = []
    since = int(time.time() - a.hours * 3600)
    for s in candidates:
        cid = s.detail.get("condition_id")
        m = gc.market_by_condition(cid) if cid else None
        if not m:
            continue
        yb, nb = cl.book(m.yes_token), cl.book(m.no_token)
        if not yb or not nb:
            continue
        trades = dc.trades(cid, limit=500, max_pages=4, since_ts=since)
        r = pm.backtest(m, yb, nb, trades)
        if r:
            results.append(r)
            if verbose:
                print("\n" + r.summary())
    return results


def cmd_scan(a):
    scan = ArbEngine(_make_scan_cfg(a)).scan(do_multi=not a.no_multi, log=lambda *x: print(*x))
    _print_scan(scan, top=a.top)
    if a.json:
        with open(a.json, "w") as f:
            json.dump([s.to_row() for s in scan["signals"]], f, indent=2)
        print(f"\n  wrote {a.json}")
    return scan


def cmd_paper(a):
    print("scanning for maker setups to paper-trade ...")
    scan = ArbEngine(_make_scan_cfg(a)).scan(do_multi=False)
    print(f"  replaying ~{a.hours}h of real taker flow against resting quotes on top {a.paper_n} setups:")
    _run_paper(scan, a, verbose=True)


def cmd_report(a):
    scan = ArbEngine(_make_scan_cfg(a)).scan(do_multi=not a.no_multi, log=lambda *x: print(*x))
    _print_scan(scan, top=a.top)
    paper = None
    if not a.no_paper:
        print("\n  running paper sim for the dashboard ...")
        paper = _run_paper(scan, a, verbose=False)
    path = write_report(a.out, scan, paper, meta={"timestamp": _fmt_ts()})
    print(f"\n  wrote dashboard -> {path}")


def cmd_fees(a):
    print("Maker-vs-taker economics (the whole thesis):\n")
    print(f"  taker fee, 100 crypto shares @ 0.50 = ${taker_fee(100,0.50,'crypto'):.4f}")
    print(f"  taker fee, 100 politics shares @ 0.50 = ${taker_fee(100,0.50,'politics'):.4f}\n")
    for yes, no, cat in [(0.49, 0.49, "crypto"), (0.50, 0.48, "politics"), (0.47, 0.50, "crypto")]:
        r = complementary_arb_pnl(yes, no, shares=100, category=cat)
        print(f"  buy 100 YES@{yes} + 100 NO@{no} [{cat}]  gross ${r['gross_edge']:+.2f}  "
              f"taker ${r['taker_net']:+.2f}  maker ${r['maker_net']:+.2f}")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--max-markets", type=int, default=400)
    common.add_argument("--max-events", type=int, default=150)
    common.add_argument("--min-liquidity", type=float, default=3000.0)
    common.add_argument("--size", type=float, default=200.0, help="shares per leg for sizing/P&L")
    common.add_argument("--maker-share", type=float, default=0.5, help="assumed share of the rebate pool [0-1]")
    common.add_argument("--hours", type=float, default=6.0, help="paper-sim lookback window (hours)")
    common.add_argument("--paper-n", type=int, default=8, help="how many maker setups to paper-trade")
    common.add_argument("--top", type=int, default=15)
    common.add_argument("--no-multi", action="store_true", help="skip multi-outcome scan")

    p = argparse.ArgumentParser(prog="polyarb", description=f"Polymarket maker-arb scanner v{__version__} (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", parents=[common], help="live arb scan")
    s.add_argument("--json", help="also write signals to this JSON path")
    s.set_defaults(func=cmd_scan)

    pp = sub.add_parser("paper", parents=[common], help="paper-trade top maker setups on real flow")
    pp.set_defaults(func=cmd_paper)

    r = sub.add_parser("report", parents=[common], help="scan + paper -> HTML dashboard")
    r.add_argument("--out", default="poly-arb-report.html")
    r.add_argument("--no-paper", action="store_true")
    r.set_defaults(func=cmd_report)

    f = sub.add_parser("fees", parents=[common], help="show maker-vs-taker economics")
    f.set_defaults(func=cmd_fees)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
