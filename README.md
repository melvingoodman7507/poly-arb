# poly-arb

A **read-only** Polymarket arbitrage scanner + paper-trading simulator.

No wallet, no private key, no funds are used anywhere. Every network call is an
unauthenticated read of Polymarket's public **Gamma**, **CLOB**, and **Data**
APIs. It **never places an order** — execution is *simulated* against real
market flow so you can see the economics (and the risk) before risking a cent.

Built from a study of the viral "*Polymarket arb with 0 fees / $50k-a-month*"
posts, then fact-checked against the official docs and the live API. Short
version of what's real and what's marketing is in **[Honest feasibility](#honest-feasibility)**.

---

## The one idea: be the maker, not the taker

Polymarket takers pay a fee; **makers pay zero and get a rebate.** The fee is

```
fee = shares × feeRate × p × (1 − p)      # p = price, feeRate by category
```

so the *same* price gap flips from a loss to a profit depending on how you
execute it. A 2¢ gap on a 49/49 crypto market, 100 shares:

| | value |
|---|---|
| gross edge (buy YES+NO for $0.98, merge to $1.00) | **+$2.00** |
| net as a **taker** (fee on both legs) | **−$1.50** |
| net as a **maker** (0 fee + rebate) | **+$2.70** |

The math is easy. The hard part — which this tool makes visible — is getting
**both legs filled without adverse selection**.

`python -m polyarb fees` prints this live.

---

## What it detects

1. **Binary complementary** (one market): `best_ask(YES) + best_ask(NO) < 1`
   is an executable taker arb; the resting-maker version (`qYES + qNO < 1`) pays
   0 fees + a rebate. Each row is annotated with taker-net vs maker-net.
2. **Multi-outcome mutually-exclusive** (a negRisk group — one question, N
   candidates, exactly one resolves YES). Grouped by `negRiskMarketID`
   (**not** by event — bundled "More Markets" events are split correctly):
   - **buy-all-YES**: `Σ best_ask(YES_i) < 1` → one YES pays $1 (capital locks to resolution)
   - **buy-all-NO**: `Σ best_ask(NO_i) < N−1` → convert returns `(N−1)` pUSD instantly
3. **Liquidity rewards**: flags markets where your two-sided quote would also
   earn Polymarket's daily maker-reward pool (`S = ((v−s)/v)²·b`).

## Install & run

```bash
pip install -r requirements.txt          # just `requests`

python -m polyarb scan                    # live ranked arb table
python -m polyarb paper                   # paper-trade top maker setups on real flow
python -m polyarb report --out out.html   # scan + paper -> HTML dashboard
python -m polyarb fees                     # the maker-vs-taker economics

# useful flags (any position):
#   --min-liquidity 5000 --max-markets 400 --max-events 150
#   --size 300 --hours 12 --paper-n 8 --maker-share 0.5 --no-multi
```

## How the paper simulator works

For a market it decides where it would rest two maker BUY orders (one tick above
each best bid, for queue priority), then **replays the market's real recent
taker trades** against those quotes:

- a resting BUY fills only against a taker **SELL** at a price ≤ its quote
  (a taker buy lifts the ask and is ignored);
- matched YES+NO pairs merge to $1 → risk-free margin + maker rebate;
- whatever fills on **one side only** is directional **inventory**, marked at the
  current best bid — that number is the adverse-selection cost.

In practice most windows fill only one side, which is the whole point: the
"guaranteed arb" rarely locks, and you are really running a rebate/reward
market-making operation with inventory risk.

## Package layout

```
polyarb/
  fees.py      exact fee, rebate & liquidity-reward math (self-tests: python -m polyarb.fees)
  models.py    Market / Event / Book / Level dataclasses + Gamma parsing
  gamma.py     market & event discovery (read-only)
  clob.py      order books & prices, batched (read-only)
  data.py      public trade tape, for the paper simulator (read-only)
  scanner.py   the arb detectors
  engine.py    scan orchestrator (discover -> batch books -> detect -> rank)
  paper.py     real-flow maker fill simulation
  report.py    self-contained dark HTML dashboard
  cli.py       scan / paper / report / fees
```

## Honest feasibility

- **"$50k/month" is marketing.** The viral post it traces to is actually a
  *taker* speed-arb with unverifiable numbers (and a fictional "Claude Fable 5").
- Peer-reviewed measurement (arXiv 2508.03474, 2605.00864) shows real Polymarket
  arb is **liquidity-bounded to retail scale**: opportunities live ~3–16s and
  ~77% cap near ~15 shares. Cross-platform (Kalshi) arb adds resolution-divergence
  and jurisdiction risk.
- What's **real**: makers pay 0 fees, earn 15–25% rebates plus a separate daily
  liquidity-reward pool, and CTF split/merge/convert give guaranteed $1 exits.
  A realistic outcome is a **capital- and vigilance-intensive market-making
  operation returning low-single-digit % monthly** on deployed capital — not a
  passive money machine.
- Top risks: adverse selection (worsened by the Feb 18 2026 removal of the 500 ms
  free-cancel window), one-sided fills, capital lockup to resolution, and Polygon
  gas per CTF operation.

## If you later want live execution (not built here, by design)

The scanner is the signal layer. A live maker bot would add a **separate**,
gated execution module — confirmed details from the research pass:

- **CLOB V2** (live Apr 28 2026). Use `py-clob-client-v2`; V1-signed orders are
  rejected. `signature_type=3` (deposit wallet) for new API users.
- **Do NOT set `feeRateBps`** — V2 removed it; the protocol sets fees at match time.
- Collateral is **pUSD**, not USDC.e — wrap via the Collateral Onramp first.
- One-sided fills are neutralized via **CTF merge** (binary) or **negRisk
  convert** (multi-outcome), never by crossing the book as a fee-paying taker.
- Sub-second **WebSocket** requoting is effectively mandatory; batch ≤15 orders.
- Reference implementation to study: `warproxxx/poly-maker` (open source).

**This repo does none of that.** It scans and simulates. That was the deliberate scope.

---

*Research & simulation tool. Not financial advice. It never places an order.*
