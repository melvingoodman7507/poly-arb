"""DO MAKERS ACTUALLY MAKE MONEY ON POLYMARKET? — the poly-arb thesis, tested on real resolved trades.

Every fill has a cash side (asset_id '0' = USDC) and a token side. Whoever pays cash is BUYING that
token at price = cash/tokens. The token's market resolves to 1 or 0, so the buyer's P&L per share is
(outcome - price) and the seller's is the mirror. We know which side was the maker, so we can total
the two books separately — which is exactly the poly-arb claim: the same trade that loses as a taker
wins as a maker.

Caveat carried into every number: this is the ~3.9% of the trades table that survived the interrupted
download (1,587 of ~40,449 offset partitions), so it is a partial and NOT randomly drawn sample.
"""
import duckdb

D = '/tmp/claude-0/-root-workspace/c5c8a4be-13e4-479a-9277-f536c20858d7/scratchpad/pmdata/data/polymarket'
T, M = f"'{D}/trades/*.parquet'", f"'{D}/markets/*.parquet'"
con = duckdb.connect()
con.execute("SET memory_limit='6GB'; SET threads=4;")

con.execute(f"""
CREATE OR REPLACE TEMP TABLE token_outcome AS
WITH m AS (
  SELECT TRY_CAST(clob_token_ids AS VARCHAR[]) AS toks,
         TRY_CAST(outcome_prices AS VARCHAR[]) AS prices
  FROM {M} WHERE closed
)
SELECT t.tok AS token_id, TRY_CAST(p.px AS DOUBLE) AS outcome
FROM m, UNNEST(m.toks) WITH ORDINALITY AS t(tok, i),
        UNNEST(m.prices) WITH ORDINALITY AS p(px, j)
WHERE i = j AND t.tok IS NOT NULL
""")
n_tok = con.execute("SELECT count(*), count(*) FILTER (WHERE outcome IN (0,1)) FROM token_outcome").fetchone()
print(f"resolved tokens mapped: {n_tok[0]:,} rows, {n_tok[1]:,} with a clean 0/1 outcome")

con.execute(f"""
CREATE OR REPLACE TEMP TABLE fills AS
SELECT
  CASE WHEN maker_asset_id = '0' THEN taker_asset_id ELSE maker_asset_id END AS token_id,
  -- the cash leg and the token leg, in whole units (both 6dp on Polymarket)
  CASE WHEN maker_asset_id = '0' THEN maker_amount ELSE taker_amount END / 1e6 AS cash,
  CASE WHEN maker_asset_id = '0' THEN taker_amount ELSE maker_amount END / 1e6 AS shares,
  -- who paid the cash = who BOUGHT the token
  CASE WHEN maker_asset_id = '0' THEN 'maker' ELSE 'taker' END AS buyer,
  fee / 1e6 AS fee_usdc,
  block_number
FROM {T}
WHERE (maker_asset_id = '0') <> (taker_asset_id = '0')     -- exactly one cash leg
""")
print("fills with a clean cash/token structure:",
      f"{con.execute('SELECT count(*) FROM fills').fetchone()[0]:,}")

q = con.execute("""
WITH j AS (
  SELECT f.*, o.outcome,
         f.cash / NULLIF(f.shares,0) AS price
  FROM fills f JOIN token_outcome o USING (token_id)
  WHERE o.outcome IN (0,1) AND f.shares > 0 AND f.cash > 0
), pnl AS (
  SELECT *,
    -- buyer of the token gains (outcome - price) per share; the seller gains the mirror
    shares * (outcome - price) AS buyer_pnl,
    CASE WHEN buyer = 'maker' THEN  shares * (outcome - price)
         ELSE -shares * (outcome - price) END AS maker_pnl,
    CASE WHEN buyer = 'taker' THEN  shares * (outcome - price)
         ELSE -shares * (outcome - price) END AS taker_pnl
  FROM j WHERE price BETWEEN 0.01 AND 0.99
)
SELECT count(*) AS n,
       round(sum(shares),0) AS shares,
       round(sum(maker_pnl),0) AS maker_gross,
       round(sum(taker_pnl),0) AS taker_gross,
       round(sum(fee_usdc),0) AS fees,
       round(avg(maker_pnl),4) AS maker_per_fill,
       round(avg(taker_pnl),4) AS taker_per_fill,
       round(100.0*count(*) FILTER (WHERE maker_pnl > 0)/count(*),2) AS maker_win_pct
FROM pnl
""").fetchone()
print(f"""
=== MAKER vs TAKER, on {q[0]:,} resolved fills ({q[1]:,.0f} shares) ===
  maker gross P&L : ${q[2]:>14,.0f}     per fill ${q[5]:+.4f}
  taker gross P&L : ${q[3]:>14,.0f}     per fill ${q[6]:+.4f}
  fees recorded   : ${q[4]:>14,.0f}
  maker wins {q[7]}% of fills
""")

print("=== the same split, by price bucket (is it just the longshot bias?) ===")
for r in con.execute("""
WITH j AS (SELECT f.*, o.outcome, f.cash/NULLIF(f.shares,0) AS price
           FROM fills f JOIN token_outcome o USING (token_id)
           WHERE o.outcome IN (0,1) AND f.shares>0 AND f.cash>0),
p AS (SELECT *, CASE WHEN buyer='maker' THEN shares*(outcome-price) ELSE -shares*(outcome-price) END AS maker_pnl
      FROM j WHERE price BETWEEN 0.01 AND 0.99)
SELECT width_bucket(price,0,1,10) AS b, count(*) AS n,
       round(sum(maker_pnl),0) AS maker_pnl, round(avg(maker_pnl),4) AS per_fill
FROM p GROUP BY 1 ORDER BY 1""").fetchall():
    lo = (r[0]-1)*10
    print(f"  price {lo:>3}-{lo+10:>3}c  n={r[1]:>9,}  maker P&L ${r[2]:>12,.0f}  per fill ${r[3]:+.4f}")
