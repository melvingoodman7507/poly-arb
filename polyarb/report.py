"""Render a self-contained dark HTML dashboard of live scan + paper-sim results."""

from __future__ import annotations

import html
import time
from typing import List, Optional

from .fees import complementary_arb_pnl
from .scanner import ArbSignal

CSS = """
:root{--bg:#0b0e14;--card:#131823;--card2:#1a2030;--line:#232b3d;--tx:#e6ebf5;--mut:#8a94a8;
--grn:#3ddc84;--red:#ff5c6c;--yel:#ffcf5c;--blu:#5c9dff;--acc:#7c5cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 12px;color:#fff}
.sub{color:var(--mut);margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi .n{font-size:22px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;margin-top:3px}
.thesis{background:linear-gradient(135deg,#151b2b,#0f1420);border:1px solid var(--line)}
.thesis b{color:var(--grn)}.thesis .bad{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
th,td{padding:9px 11px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{background:var(--card2);color:var(--mut);font-weight:600;position:sticky;top:0;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
td.l,th.l{text-align:left}tr:last-child td{border-bottom:none}
tr:hover td{background:#161c2b}
.pos{color:var(--grn)}.neg{color:var(--red)}.mut{color:var(--mut)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600}
.t3{background:rgba(61,220,132,.15);color:var(--grn)}
.t2{background:rgba(92,157,255,.15);color:var(--blu)}
.t1{background:rgba(255,207,92,.13);color:var(--yel)}
.t0{background:rgba(255,92,108,.13);color:var(--red)}
.warn{background:rgba(255,207,92,.08);border:1px solid rgba(255,207,92,.3);border-radius:12px;padding:14px 18px}
.foot{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);padding-top:16px}
code{background:#0d1220;padding:1px 6px;border-radius:5px;color:#b9c4dc;font-size:12px}
"""


def _n(x, d=2, dollar=False, cents=False, pct=False):
    try:
        if cents:
            return f"{x*100:.2f}¢"
        if pct:
            return f"{x*100:.2f}%"
        s = f"{x:,.{d}f}"
        return (("$" + s) if dollar else s)
    except Exception:
        return "-"


def _cls(x):
    return "pos" if x > 0 else ("neg" if x < 0 else "mut")


def _esc(s):
    return html.escape(str(s))


def _guaranteed_table(sigs: List[ArbSignal]) -> str:
    rows = [s for s in sigs if s.executable_now]
    if not rows:
        return ('<div class="warn">No <b>executable-now</b> arbs right now — expected. '
                'Hard taker arbs are rare and, as the thesis shows, usually fee-negative. '
                'The durable edge is the maker table below.</div>')
    body = ""
    for s in rows[:25]:
        body += (f"<tr><td class='l'>{_esc(s.label[:60])}</td><td>{_esc(s.category)}</td>"
                 f"<td>{_n(s.cost_per_unit,4)}</td>"
                 f"<td class='pos'>{_n(s.gross_edge_per_unit,4,dollar=True)}</td>"
                 f"<td class='{_cls(s.taker_net_per_unit)}'>{_n(s.taker_net_per_unit,4,dollar=True)}</td>"
                 f"<td>{_n(s.size_shares,0)}</td>"
                 f"<td class='{_cls(s.taker_profit_usdc)}'>{_n(s.taker_profit_usdc,2,dollar=True)}</td></tr>")
    return (f"<p class=mut>Gross gap is guaranteed (buy the set, merge to $1). "
            f"<b>Taker net</b> subtracts the round-trip fee — often negative, which is the whole point: "
            f"the gap is only worth taking as a maker (0 fee). Rows are ranked by taker net.</p>"
            f"<div class='scroll'><table><thead><tr>"
            f"<th class='l'>Market</th><th>Cat</th><th>Cost/unit</th><th>Gross/unit</th>"
            f"<th>Taker net/unit</th><th>Size</th><th>Taker P&amp;L</th></tr></thead><tbody>{body}"
            f"</tbody></table></div>")


def _maker_table(sigs: List[ArbSignal]) -> str:
    rows = [s for s in sigs if s.kind == "binary_maker"]
    if not rows:
        return "<p class='mut'>No maker setups above threshold this scan.</p>"
    body = ""
    for s in rows[:30]:
        d = s.detail
        rew = "✓" if d.get("reward_eligible") else "·"
        body += (f"<tr><td class='l'>{_esc(s.label[:52])}</td><td>{_esc(s.category)}</td>"
                 f"<td><span class='pill t{s.fill_tier}'>{_esc(s.fillability.split(' (')[0])}</span></td>"
                 f"<td>{_n(s.cost_per_unit,3)}</td>"
                 f"<td class='pos'>{_n(s.gross_edge_per_unit,0,cents=True)}</td>"
                 f"<td>{_n(d.get('rebate_per_pair',0)*s.size_shares,2,dollar=True)}</td>"
                 f"<td>{rew}</td>"
                 f"<td>{_n(s.volume24hr,0,dollar=True)}</td></tr>")
    return (f"<div class='scroll'><table><thead><tr>"
            f"<th class='l'>Market</th><th>Cat</th><th>Fill quality</th><th>Cost/pair</th>"
            f"<th>Margin/pair</th><th>Rebate/lot</th><th>Rwd</th><th>Vol 24h</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>")


def _paper_section(paper_results) -> str:
    if not paper_results:
        return ""
    body = ""
    for r in paper_results:
        inv = (f"<td class='neg'>{_n(r.inventory_pnl,2,dollar=True)}</td>"
               if r.inventory_shares > 0 else "<td class='mut'>—</td>")
        body += (f"<tr><td class='l'>{_esc(r.question[:48])}</td>"
                 f"<td>{_n(r.quote_yes,3)}+{_n(r.quote_no,3)}</td>"
                 f"<td>{_n(r.margin_per_pair,0,cents=True)}</td>"
                 f"<td>{_n(r.filled_yes,0)}/{_n(r.filled_no,0)}</td>"
                 f"<td>{_n(r.matched_pairs,0)}</td>"
                 f"<td class='pos'>{_n(r.realized_margin,2,dollar=True)}</td>"
                 f"<td class='pos'>{_n(r.rebates,2,dollar=True)}</td>"
                 f"{inv}"
                 f"<td class='{_cls(r.net_pnl)}'>{_n(r.net_pnl,2,dollar=True)}</td></tr>")
    return (f"<h2>Paper-trade simulation <span class='mut' style='font-size:13px'>"
            f"(resting maker quotes replayed against real taker flow)</span></h2>"
            f"<div class='scroll'><table><thead><tr>"
            f"<th class='l'>Market</th><th>Quotes Y+N</th><th>Margin</th><th>Fill Y/N</th>"
            f"<th>Pairs</th><th>Merge P&amp;L</th><th>Rebates</th><th>Inv. P&amp;L</th><th>Net</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>"
            f"<p class='mut'>Matched pairs merge to $1 risk-free; leftover one-sided fills are "
            f"directional inventory marked at best bid — that column is the adverse-selection cost.</p>")


def render_report(scan, paper_results=None, meta=None) -> str:
    sigs: List[ArbSignal] = scan["signals"]
    stats = scan["stats"]
    ex = complementary_arb_pnl(0.49, 0.49, shares=100, category="crypto")
    n_hard = sum(1 for s in sigs if s.executable_now)
    n_maker = sum(1 for s in sigs if s.kind == "binary_maker")
    n_prime = sum(1 for s in sigs if s.fill_tier >= 2 and not s.executable_now)
    ts = meta.get("timestamp") if meta else "(live)"

    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>poly-arb — live Polymarket maker-arb scanner</title><style>{CSS}</style></head><body><div class=wrap>
<h1>poly-arb <span class='mut' style='font-size:15px'>· live Polymarket maker-arb scanner</span></h1>
<p class=sub>Read-only. No wallet, no funds. Scanned {ts} · {scan['elapsed_s']}s ·
{stats.get('markets',0)} markets, {stats.get('neg_risk_groups',0)} negRisk groups, {stats.get('books',0)} order books.</p>

<div class="card thesis">
<h2 style='margin-top:0'>The whole edge: be the maker, not the taker</h2>
<p>Polymarket takers pay <code>fee = shares × feeRate × p × (1−p)</code>; makers pay <b>zero</b> and earn a daily rebate.
So the <i>same</i> price gap flips sign depending on how you execute it. Worked example — a 2¢ gap on a 49/49 crypto market, 100 shares:</p>
<div class=grid>
<div class=kpi><div class=n>{_n(ex['gross_edge'],2,dollar=True)}</div><div class=l>gross edge (buy YES+NO, merge to $1)</div></div>
<div class=kpi><div class="n bad">{_n(ex['taker_net'],2,dollar=True)}</div><div class=l>net as a <b>taker</b> (fees eat it)</div></div>
<div class=kpi><div class="n" style='color:var(--grn)'>{_n(ex['maker_net'],2,dollar=True)}</div><div class=l>net as a <b>maker</b> (0 fee + rebate)</div></div>
<div class=kpi><div class=n>${_n(ex['maker_minus_taker'],2)}</div><div class=l>maker advantage per 100 sh</div></div>
</div>
<p class=mut style='margin-bottom:0'>The catch isn't the math — it's <b>getting both legs filled without adverse selection</b>. That risk is what the paper sim measures.</p>
</div>

<div class=grid>
<div class=kpi><div class=n>{n_hard}</div><div class=l>guaranteed arbs executable now</div></div>
<div class=kpi><div class=n>{n_maker}</div><div class=l>complementary maker setups</div></div>
<div class=kpi><div class=n>{n_prime}</div><div class=l>prime / good fill quality</div></div>
</div>

<h2>Guaranteed arbs (executable now)</h2>
{_guaranteed_table(sigs)}

<h2>Maker setups — ranked by fill quality × liquidity</h2>
{_maker_table(sigs)}

{_paper_section(paper_results)}

<h2>Honest feasibility</h2>
<div class=warn>
<p style='margin-top:0'><b>The "$50k/month" is marketing.</b> The viral post it traces to is actually a <i>taker</i> speed-arb with unverifiable numbers.
Peer-reviewed data (arXiv 2508.03474 / 2605.00864) shows real Polymarket arb is <b>liquidity-bounded to retail scale</b>: opportunities live ~3–16s and ~77% cap near ~15 shares.</p>
<p style='margin-bottom:0'>What's <b>real</b>: makers pay 0 fees, earn 15–25% rebates + separate liquidity rewards, and CTF split/merge/convert give guaranteed $1 exits.
Realistic outcome is a capital- and vigilance-intensive market-making operation on liquid non-crypto books returning <b>low-single-digit % monthly</b> on deployed capital — not a passive money machine.
Top risks: adverse selection (worsened by the Feb 2026 cancel-window removal), one-sided fills, capital lockup, and Polygon gas per CTF op.</p>
</div>

<div class=foot>
Data: Polymarket Gamma + CLOB + Data public APIs (read-only). Fee/rebate/CTF math verified against docs.polymarket.com &amp; on-chain contracts.
This is a research & simulation tool — not financial advice, and it never places an order.
</div>
</div></body></html>"""


def write_report(path: str, scan, paper_results=None, meta=None) -> str:
    with open(path, "w") as f:
        f.write(render_report(scan, paper_results, meta))
    return path
