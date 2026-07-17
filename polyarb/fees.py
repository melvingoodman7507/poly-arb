"""
Polymarket fee & maker-rebate math (2026 CLOB V2 rules).

This module is the heart of the whole strategy. The one insight that makes a
Polymarket arb bot viable in 2026 is:

    Takers pay a fee.  Makers pay ZERO and are paid a rebate.

So a price gap that *loses* money when you cross the spread (taker) can *make*
money when you capture it with resting limit orders (maker).

------------------------------------------------------------------------------
GENERAL TAKER FEE (verified against docs.polymarket.com/trading/fees)

    fee_usdc = shares * feeRate * p * (1 - p)

  * shares  = number of outcome shares traded
  * p       = share price in USDC, 0.01 .. 0.99
  * feeRate = category multiplier (see CATEGORY_FEE_RATE)

  The fee (in USDC) is symmetric around p = 0.50 and shrinks to ~0 at the
  extremes.  A $0.30 share and a $0.70 share incur the same dollar fee.
  Makers are never charged.

  Sanity check (crypto, feeRate 0.07):
    100 shares @ 0.50 -> 100 * 0.07 * 0.5 * 0.5 = $1.75   <- matches docs
    100 shares @ 0.30 -> 100 * 0.07 * 0.3 * 0.7 = $1.47   <- matches docs

------------------------------------------------------------------------------
SHORT-TERM CRYPTO (5-min / 15-min markets) -- CORRECTED.

  An earlier draft hardcoded a "squared" curve, fee = shares*0.25*(p(1-p))**2,
  attributed to community/theblockbeats posts. The verification pass REFUTED
  that: docs.polymarket.com/trading/fees publishes ONLY the linear p(1-p) form
  for ALL markets. What is actually true about 5-min/15-min crypto is narrower:
  those markets had taker fees newly *enabled* (they used to be fee-free) at a
  higher tier, and they also lost the 500ms free-cancel window (Feb 18 2026).
  The exact peak is reported inconsistently across sources (~1.56% / 1.80% /
  3.15%) and is NOT reliably documented, so we do not hardcode it.

  The authoritative per-market fee source is the CLOB market-info endpoint
  (GET /markets/{condition_id}) and the SDK's getClobMarketInfo -> fd={r,e,to}.
  A general nonlinear form is supported via `taker_fee_dynamic(...)` with an
  exponent; the linear category table below is the documented default and the
  safe fallback. NOTE: the raw `taker_base_fee`/`maker_base_fee` bps fields the
  endpoint returns are inconsistent (0 vs 30 vs 1000 across markets; see
  Polymarket/py-clob-client#326) and must NOT be read as a literal rate -- use
  them only as an on/off signal (0 => fees disabled on that market).

  Practical guidance from the research pass: AVOID 5-/15-min crypto for maker-
  arb -- highest anti-latency fees AND worst adverse selection. Prefer liquid
  politics/finance/tech (0.04 fee, 25% rebate) and geopolitics (0 fee).

------------------------------------------------------------------------------
MAKER REBATES (verified against docs.polymarket.com/market-makers/maker-rebates)

  A pool equal to REBATE_POOL_FRACTION of the taker fees collected in a market
  is paid back DAILY, in pUSD, pro-rata to each maker's share of filled maker
  liquidity in that same market:

    your_rebate = (your_fee_equiv / total_fee_equiv_in_market) * pool

  where fee_equiv uses the same shares*feeRate*p*(1-p) form on YOUR filled
  maker volume.  Minimum payout $1 pUSD.  Geopolitics markets are fee-free
  (no rebate).
"""

from __future__ import annotations

# --- Category multipliers -----------------------------------------------------
# feeRate used in fee = shares * feeRate * p * (1-p)
CATEGORY_FEE_RATE = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "tech": 0.04,
    "mentions": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "geopolitics": 0.0,
    "world": 0.0,
}

# Fraction of collected taker fees returned to makers as a daily rebate pool.
CATEGORY_REBATE_FRACTION = {
    "crypto": 0.20,
    "sports": 0.15,
    "finance": 0.25,
    "politics": 0.25,
    "tech": 0.25,
    "mentions": 0.25,
    "economics": 0.25,
    "culture": 0.25,
    "weather": 0.25,
    "other": 0.25,
    "geopolitics": 0.0,
    "world": 0.0,
}

# When we cannot determine the category we assume a mid-range rate so estimates
# are neither wildly optimistic nor pessimistic.  Fee only matters for the
# TAKER comparison and for sizing the rebate; our own maker legs pay 0.
DEFAULT_CATEGORY = "other"


def normalize_category(raw) -> str:
    """Map a Gamma tag / label to one of our fee categories (best effort)."""
    if not raw:
        return DEFAULT_CATEGORY
    s = str(raw).strip().lower()
    aliases = {
        "crypto": "crypto", "cryptocurrency": "crypto", "bitcoin": "crypto", "btc": "crypto",
        "eth": "crypto", "ethereum": "crypto",
        "sports": "sports", "nba": "sports", "nfl": "sports", "soccer": "sports",
        "football": "sports", "esports": "sports", "mlb": "sports", "tennis": "sports",
        "politics": "politics", "election": "politics", "elections": "politics",
        "finance": "finance", "fed": "finance", "stocks": "finance", "markets": "finance",
        "tech": "tech", "ai": "tech", "technology": "tech",
        "mentions": "mentions",
        "economics": "economics", "econ": "economics", "inflation": "economics",
        "culture": "culture", "pop-culture": "culture", "entertainment": "culture",
        "weather": "weather", "climate": "weather",
        "geopolitics": "geopolitics", "world": "world", "war": "geopolitics",
    }
    if s in CATEGORY_FEE_RATE:
        return s
    return aliases.get(s, DEFAULT_CATEGORY)


_TEXT_HINTS = [
    ("crypto", ("bitcoin", "btc", "ethereum", " eth ", "crypto", "solana", " sol ", "dogecoin",
                "xrp", "altcoin", "updown")),
    ("sports", ("nba", "nfl", "mlb", "nhl", "premier league", "la liga", "serie a", "champions league",
                "super bowl", "world cup", "open:", " vs ", " vs.", "cricket", "ipl", "tennis", "atp",
                "wta", "ufc", "boxing", "home run", "playoff", "yankees", "lakers", "warriors",
                "red sox", "76ers", "heat ", "t20", "test match", "golf", "pga")),
    ("finance", ("fed ", "rate cut", "interest rate", "cpi", "inflation", "gdp", "s&p", "nasdaq",
                 "stock", "crude oil", "wti", "brent", "gold ", "recession", "treasury", "earnings")),
    ("politics", ("election", "president", "prime minister", "senate", "congress", "governor",
                  "parliament", "nominee", "primary", "referendum", "cabinet", "impeach")),
    ("geopolitics", ("ceasefire", "invade", "war ", "military", "nato", "sanction", "missile",
                     "nuclear", "hostage", "airstrike", "annex")),
]


def category_from_text(*parts) -> str:
    """Best-effort category from a market's question / event title / slug."""
    blob = " ".join(str(p) for p in parts if p).lower()
    if not blob:
        return DEFAULT_CATEGORY
    for cat, hints in _TEXT_HINTS:
        if any(h in blob for h in hints):
            return cat
    return DEFAULT_CATEGORY


def fee_rate_for(category: str) -> float:
    return CATEGORY_FEE_RATE.get(normalize_category(category), CATEGORY_FEE_RATE[DEFAULT_CATEGORY])


def rebate_fraction_for(category: str) -> float:
    return CATEGORY_REBATE_FRACTION.get(normalize_category(category), CATEGORY_REBATE_FRACTION[DEFAULT_CATEGORY])


# --- Taker fees ---------------------------------------------------------------
def taker_fee_dynamic(shares: float, price: float, rate: float, exponent: float = 1.0) -> float:
    """General taker fee: shares * rate * (p*(1-p))**exponent.

    Drives fee math from live per-market params (getClobMarketInfo -> fd={r,e}).
    exponent defaults to 1.0 (the documented linear curve for every market).
    """
    p = max(0.0, min(1.0, float(price)))
    return float(shares) * float(rate) * (p * (1.0 - p)) ** float(exponent)


def taker_fee(shares: float, price: float, category: str = DEFAULT_CATEGORY) -> float:
    """Taker fee in USDC using the documented linear category curve (fallback
    when live fd is unavailable)."""
    return taker_fee_dynamic(shares, price, fee_rate_for(category), 1.0)


def taker_fee_per_share(price: float, category: str = DEFAULT_CATEGORY) -> float:
    return taker_fee(1.0, price, category)


def taker_fee_pct_of_notional(price: float, category: str = DEFAULT_CATEGORY) -> float:
    """Fee as a fraction of the USDC you actually spend (shares*price)."""
    p = max(1e-9, min(1.0, float(price)))
    return taker_fee_per_share(p, category) / p  # = feeRate*(1-p)


# --- Rebates ------------------------------------------------------------------
def rebate_for_fill(
    your_shares: float,
    price: float,
    category: str,
    your_maker_share: float = 1.0,
) -> float:
    """
    Estimate the daily maker rebate credited to YOU for a filled maker order.

    The pool for a fill of `your_shares` at `price` is:
        pool = rebate_fraction * (taker_fee the counterparty paid)
    and you receive your pro-rata share of the market's pool.  If you are the
    only relevant maker (your_maker_share ~ 1.0) you get essentially the whole
    pool generated by trades against your quotes.

    This is an ESTIMATE: the true rebate depends on the whole market's daily
    maker volume, which we cannot know ex-ante.  `your_maker_share` in [0,1]
    lets callers stress-test the assumption.
    """
    counterparty_fee = taker_fee(your_shares, price, category)
    pool = rebate_fraction_for(category) * counterparty_fee
    return pool * max(0.0, min(1.0, your_maker_share))


# --- Strategy economics: the whole point --------------------------------------
def complementary_arb_pnl(
    yes_price: float,
    no_price: float,
    shares: float = 100.0,
    category: str = DEFAULT_CATEGORY,
    your_maker_share: float = 1.0,
):
    """
    Compare TAKER vs MAKER economics of a complementary arb: buy `shares` of YES
    at `yes_price` and `shares` of NO at `no_price`.  If both fill you can merge
    each YES+NO pair into $1 (guaranteed), so gross edge = shares*(1 - yes - no).

    Returns a dict with gross edge and the net P&L under each execution mode.
    """
    yes_price = float(yes_price); no_price = float(no_price); shares = float(shares)
    gross = shares * (1.0 - yes_price - no_price)

    # TAKER: cross the spread on both legs -> pay taker fee on both.
    taker_fees = taker_fee(shares, yes_price, category) + taker_fee(shares, no_price, category)
    taker_net = gross - taker_fees

    # MAKER: rest both legs -> pay 0 fees, and collect a rebate on each fill.
    maker_rebate = (
        rebate_for_fill(shares, yes_price, category, your_maker_share)
        + rebate_for_fill(shares, no_price, category, your_maker_share)
    )
    maker_net = gross + maker_rebate  # fees are zero for makers

    return {
        "shares": shares,
        "yes_price": yes_price,
        "no_price": no_price,
        "cost_per_pair": yes_price + no_price,
        "gross_edge": gross,
        "taker_fees": taker_fees,
        "taker_net": taker_net,
        "maker_rebate": maker_rebate,
        "maker_net": maker_net,
        "maker_minus_taker": maker_net - taker_net,
    }


# --- Liquidity Rewards (a SEPARATE maker income stream from rebates) ----------
# Polymarket pays a daily USDC pool to makers who quote two-sided inside an
# incentive band. Score per order: S(v, s) = ((v - s) / v)**2 * b
#   v = rewardsMaxSpread (cents from midpoint that still qualifies)
#   s = your order's distance from midpoint (cents)
#   b = order size (shares)
# Two-sided quotes are scored on the MIN of the two sides (Q_min). Your daily
# payout = (your_score / total_score_in_market) * daily_pool. We can compute the
# score (relative competitiveness) even when the absolute daily pool isn't
# exposed by the API.
def reward_eligible(spread_from_mid_cents: float, size: float,
                    max_spread_cents: float, min_size: float) -> bool:
    if max_spread_cents <= 0:
        return False
    return spread_from_mid_cents <= max_spread_cents + 1e-9 and size >= min_size


def reward_score(spread_from_mid_cents: float, size: float, max_spread_cents: float) -> float:
    if max_spread_cents <= 0 or spread_from_mid_cents > max_spread_cents:
        return 0.0
    return ((max_spread_cents - spread_from_mid_cents) / max_spread_cents) ** 2 * size


def _selftest():
    # Known docs examples (crypto feeRate 0.07)
    assert abs(taker_fee(100, 0.50, "crypto") - 1.75) < 1e-9, taker_fee(100, 0.50, "crypto")
    assert abs(taker_fee(100, 0.30, "crypto") - 1.47) < 1e-9, taker_fee(100, 0.30, "crypto")
    assert abs(taker_fee(100, 0.70, "crypto") - 1.47) < 1e-9  # symmetric
    # Politics max ~ $1.00 per 100 shares at 0.5
    assert abs(taker_fee(100, 0.50, "politics") - 1.00) < 1e-9
    # Dynamic form with exponent=1 must equal the linear category fee
    assert abs(taker_fee_dynamic(100, 0.50, 0.07, 1.0) - taker_fee(100, 0.50, "crypto")) < 1e-9
    # Geopolitics fee-free
    assert taker_fee(100, 0.50, "geopolitics") == 0.0
    # Thesis: a 2c gap on a 0.49/0.49 crypto market -- taker LOSES, maker WINS
    r = complementary_arb_pnl(0.49, 0.49, shares=100, category="crypto")
    assert r["gross_edge"] > 0 and r["taker_net"] < 0 < r["maker_net"], r
    print("fees.py self-test OK")
    print("  crypto 100@0.50 taker fee = $%.4f" % taker_fee(100, 0.50, "crypto"))
    print("  0.49/0.49 crypto arb: gross=$%.2f  taker_net=$%.2f  maker_net=$%.2f"
          % (r["gross_edge"], r["taker_net"], r["maker_net"]))


if __name__ == "__main__":
    _selftest()
