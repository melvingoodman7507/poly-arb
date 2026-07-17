"""poly-arb: a read-only Polymarket maker-arbitrage scanner + paper-trading simulator.

No wallet, no private key, no funds are used anywhere in this package. Every
network call is an unauthenticated GET/POST to Polymarket's public Gamma and
CLOB read endpoints. Execution is *simulated* only (see polyarb.paper).
"""

__version__ = "0.1.0"
