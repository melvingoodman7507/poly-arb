"""Simplified 'Best Bets' layer for a MANUAL taker.

Turns a researched value-bet card (side + entry price + my_prob) into the plain
numbers Dominic asked for -- win chance, $ if win, $ if lose, and an honest
expected value that accounts for the Polymarket taker fee -- and renders a
dead-simple mobile-friendly page.

IMPORTANT HONESTY: a directional bet only makes money on average if your true
probability is better than the market price. The math below uses `my_prob`
(the researcher's estimate) for EV, and always shows the market's own implied
probability next to it so the 'edge' is explicit. No edge -> negative EV -> skip.
"""

from __future__ import annotations

import html
from . import fees


def bet_math(side: str, entry_price: float, my_prob: float, category: str = "other",
             stake: float = 100.0) -> dict:
    """All the money numbers for buying `side` at `entry_price` with `stake` USDC.

    Buying `shares = stake/entry` of a token at price p that pays $1 if it wins:
      win  profit = shares*(1 - entry) = stake*(1-entry)/entry     (gross)
      taker fee   = shares*feeRate*entry*(1-entry) = stake*feeRate*(1-entry)
      lose        = -stake  (plus the fee already paid)
    my_prob is the probability that the CHOSEN side wins.
    """
    p = max(0.01, min(0.99, float(entry_price)))
    shares = stake / p
    win_gross = stake * (1.0 - p) / p
    fee = fees.taker_fee(shares, p, category)          # paid on entry, win or lose
    win_net = win_gross - fee
    lose_net = -(stake + fee)
    q = max(0.0, min(1.0, float(my_prob)))             # my prob the side wins
    ev = q * win_net + (1.0 - q) * lose_net
    # break-even true prob (EV = 0), given fee: q*(win_net) + (1-q)*(lose_net) = 0
    denom = (win_net - lose_net)
    breakeven = (-lose_net / denom) if denom else p
    return {
        "entry": p,
        "market_prob": p,                # implied prob = price
        "my_prob": q,
        "shares_per_stake": shares,
        "win_profit": win_net,           # $ profit per `stake` if you WIN
        "win_pct": win_net / stake * 100.0,
        "lose_amount": lose_net,         # negative
        "fee": fee,
        "ev": ev,                        # expected $ per `stake` using my_prob
        "ev_pct": ev / stake * 100.0,
        "breakeven_prob": breakeven,     # min true win-prob to be +EV
        "edge_pts": (q - p) * 100.0,     # my prob minus market prob, in points
        "stake": stake,
    }


# --------------------------------------------------------------------------- UI
CSS = """
:root{--bg:#0b0e14;--card:#141a26;--card2:#1b2333;--line:#263047;--tx:#eef2fa;--mut:#93a0b8;
--grn:#37e08b;--red:#ff5d6c;--yel:#ffd45c;--blu:#5aa2ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 90px}
h1{font-size:26px;margin:0 0 2px}.sub{color:var(--mut);margin:0 0 20px;font-size:14px}
.note{background:linear-gradient(135deg,#16203a,#0f1526);border:1px solid var(--line);
border-radius:14px;padding:16px 18px;margin:16px 0;font-size:14.5px}
.note b{color:var(--yel)}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;margin:18px 0;
box-shadow:0 6px 24px rgba(0,0,0,.25)}
.card.bet{border-left:5px solid var(--grn)}.card.lean{border-left:5px solid var(--yel)}
.tag{display:inline-block;font-size:12px;font-weight:800;letter-spacing:.05em;padding:4px 10px;border-radius:999px}
.tag.bet{background:rgba(55,224,139,.16);color:var(--grn)}.tag.lean{background:rgba(255,212,92,.16);color:var(--yel)}
.q{font-size:19px;font-weight:700;margin:10px 0 4px}
.dir{font-size:16px;margin:8px 0 14px}.dir b{color:var(--blu)}
.rows{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}
.box{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.box .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.box .v{font-size:22px;font-weight:800;margin-top:2px}
.v.g{color:var(--grn)}.v.r{color:var(--red)}.v.y{color:var(--yel)}
.why{font-size:14.5px;color:#cdd6e6;margin-top:12px}.why a{color:var(--blu)}
.ev{margin-top:12px;font-size:14px;padding:9px 12px;border-radius:10px}
.ev.pos{background:rgba(55,224,139,.10);color:var(--grn)}.ev.neg{background:rgba(255,93,108,.10);color:var(--red)}
.foot{color:var(--mut);font-size:12.5px;margin-top:34px;border-top:1px solid var(--line);padding-top:16px}
.skip{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 15px;margin:8px 0;font-size:13.5px;color:var(--mut)}
.skip b{color:#c2ccdd}
a.btn{color:var(--blu);text-decoration:none}
@media(max-width:520px){.rows{grid-template-columns:1fr 1fr}.wrap{padding:20px 14px 80px}}
"""


def _esc(s): return html.escape(str(s))


def _card_html(c: dict) -> str:
    m = c["_math"]
    side = c.get("side", "YES")
    verdict = c.get("verdict", "LEAN").lower()
    vlabel = "STRONG BET" if verdict == "bet" else "WORTH A SHOT"
    ev_cls = "pos" if m["ev"] > 0 else "neg"
    ev_sign = "+" if m["ev"] >= 0 else ""
    evid = ""
    ke = c.get("key_evidence") or []
    if ke:
        src = ke[0].get("source_url", "")
        evid = f" &nbsp;<a href='{_esc(src)}' target='_blank' rel='noopener'>source ↗</a>" if src else ""
    return f"""
<div class="card {verdict}">
  <span class="tag {verdict}">{'🟢' if verdict=='bet' else '🟡'} {vlabel}</span>
  <div class="q">{_esc(c.get('question',''))}</div>
  <div class="dir">👉 <b>BUY {_esc(side)}</b> at <b>{m['entry']*100:.0f}¢</b>
     &nbsp;·&nbsp; resolves {_esc(c.get('resolves','') or 'see market')}</div>
  <div class="rows">
    <div class="box"><div class="l">Win chance — my research</div><div class="v g">{m['my_prob']*100:.0f}%</div></div>
    <div class="box"><div class="l">Market says (the odds)</div><div class="v">{m['market_prob']*100:.0f}%</div></div>
    <div class="box"><div class="l">If you WIN (+per $100)</div><div class="v g">+${m['win_profit']:.0f}</div></div>
    <div class="box"><div class="l">If you LOSE (per $100)</div><div class="v r">−$100</div></div>
  </div>
  <div class="ev {ev_cls}">Expected value using my odds: <b>{ev_sign}${m['ev']:.0f} per $100</b>
     &nbsp;({ev_sign}{m['ev_pct']:.0f}%). Edge vs market: {m['edge_pts']:+.0f} points.</div>
  <div class="why">💡 {_esc(c.get('rationale',''))}{evid}</div>
</div>"""


def _closest_html(cc: dict) -> str:
    return f"""
<div class="card lean" style="border-left-color:#ff5d6c">
  <span class="tag lean" style="background:rgba(255,93,108,.14);color:#ff5d6c">🔪 TEMPTED — THEN KILLED IT</span>
  <div class="q">{_esc(cc.get('question',''))}</div>
  <div class="dir">Looked like: <b>BUY {_esc(cc.get('side','YES'))}</b> at {cc.get('price',0)*100:.0f}¢ ·
     my first read {cc.get('my_prob',0)*100:.0f}% vs market {cc.get('price',0)*100:.0f}%
     (<span style='color:#5aa2ff'>{cc.get('edge',0):.0f}pt "edge"</span>)</div>
  <div class="why" style="color:#ffb3ba"><b>Why I killed it:</b> {_esc(cc.get('why_killed',''))}</div>
</div>"""


def render_bets_page(survivors, all_researched=None, meta=None, closest_calls=None,
                     markets_checked=0, lesson=None) -> str:
    meta = meta or {}
    ts = meta.get("timestamp", "")
    cc_html = "".join(_closest_html(c) for c in (closest_calls or []))
    cc_section = (f"<h2 style='font-size:17px;margin-top:30px'>The ones that <i>tempted</i> me — and why I still said no</h2>"
                  f"<p class='sub' style='margin-top:0'>This is the important part: apparent edges that fell apart on a hard second look.</p>{cc_html}"
                  if closest_calls else "")
    lesson_html = (f"<div class='note'>🧠 <b>The lesson from today.</b> {lesson}</div>" if lesson else "")
    cards_html = "".join(_card_html(c) for c in survivors) if survivors else (
        f"<div class='note'>🔴 <b>No bet worth making right now.</b> I deep-researched "
        f"{markets_checked or 'the live'} markets and <b>not one</b> had an edge that survived a hard, "
        f"adversarial double-check. Per the discipline rule (and your guy's own tweet), the move is to "
        f"<b>skip</b> — a trade you don't lose is money kept.</div>")

    skips = ""
    if all_researched:
        survivor_ids = {c.get("condition_id") for c in (survivors or [])}
        others = [c for c in all_researched if c.get("condition_id") not in survivor_ids]
        others.sort(key=lambda c: abs(c.get("edge_pct", 0) or 0), reverse=True)
        others = others[:10]
        if others:
            rows = ""
            for i, c in enumerate(others):
                tag = "CLOSEST CALL — still skipped" if i == 0 else "SKIP"
                edge = abs(c.get("edge_pct", 0) or 0)
                rows += (f"<div class='skip'><b>{tag}</b> &nbsp;<span style='color:#5aa2ff'>{edge:.0f}pt gap</span> · "
                         f"{_esc(c.get('question','')[:78])} — market {c.get('market_yes_price',0)*100:.0f}%, "
                         f"my read {c.get('my_yes_prob',0)*100:.0f}%<br><span style='color:#93a0b8'>{_esc(c.get('rationale','')[:180])}…</span></div>")
            skips = (f"<h2 style='font-size:16px;color:#93a0b8;margin-top:30px'>"
                     f"Everything I analyzed today — and why none were worth a bet</h2>{rows}")

    n = len(survivors or [])
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>poly-arb — best bets</title><style>{CSS}</style></head><body><div class=wrap>
<h1>🎯 Best Bets <span style='font-size:14px;color:#93a0b8'>· Polymarket</span></h1>
<p class=sub>Researched {_esc(ts)} · {n} bet{'s' if n!=1 else ''} with a real edge today ·
<a class=btn href="scanner.html">nerd view (arb scanner) →</a></p>

<div class="note">
<b>How to read this.</b> On Polymarket the <b>price = the odds</b> (70¢ ≈ 70% chance). You only make money
long-run when your <b>win chance beats the market's</b> — that gap is the "edge". Each card shows my
researched win chance vs the market's, and exactly what you win or lose per $100. 🔴 If a day has no edge,
the honest answer is <b>don't bet</b>. Only stake what you can afford to lose — this is win-some, lose-some.
</div>

{cards_html}
{lesson_html}
{cc_section}
{skips}

<div class=foot>
Not financial advice — these are research estimates, not guarantees. The market can be right and I can be wrong;
size small. Money math is exact (includes the Polymarket taker fee); the win-chance is my analytical estimate,
double-checked by an adversarial pass. You place the trades yourself.
</div>
</div></body></html>"""
