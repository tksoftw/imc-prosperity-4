"""ROUND_4 bot-name alpha discovery.

In ROUND_4 the public tape exposes counterparty names (``Mark 01`` … ``Mark 67``)
both in the live trades CSVs (``data/ROUND_4/trades_round_4_day_*.csv``) and in
real submission logs (``imc_logs/ROUND_4/*.log``).  This file mines the data
for which bot is informed on which product.

Run with ``uv run python notebooks/round4/bot_alpha_analysis.py`` — it prints a
self-contained report.  Output sections:

  1. NET FLOW per bot per product (who is the basket dumper / accumulator?)
  2. PRICE BEHAVIOR per bot (do they take the offer or hit the bid?)
  3. LEAD-LAG / MARKOUT: for each bot+side, mean signed future mid
     markout from that bot's trade price at horizon 100/500/1000.
  4. BASKET DUMP TIMING: when Mark 22 fires multi-product dumps, what are the
     cross-product opportunities?
  5. own_trades vs market_trades semantics from the open-source backtester.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data" / "ROUND_4"
DAYS = (1, 2, 3)


def load_round4() -> tuple[pd.DataFrame, pd.DataFrame]:
    trades = pd.concat(
        [
            pd.read_csv(DATA / f"trades_round_4_day_{d}.csv", sep=";").assign(day=d)
            for d in DAYS
        ],
        ignore_index=True,
    )
    prices = pd.concat(
        [
            pd.read_csv(DATA / f"prices_round_4_day_{d}.csv", sep=";").assign(day=d)
            for d in DAYS
        ],
        ignore_index=True,
    )
    return trades, prices


# ── 1. Net flow: who's directional? ────────────────────────────────────────

def net_flow_table(trades: pd.DataFrame) -> pd.DataFrame:
    bots = sorted(set(trades["buyer"]) | set(trades["seller"]))
    prods = sorted(trades["symbol"].unique())
    rows = []
    for b in bots:
        row = {"bot": b}
        tot_buy = tot_sell = 0
        for p in prods:
            bq = trades.loc[(trades["symbol"] == p) & (trades["buyer"] == b), "quantity"].sum()
            sq = trades.loc[(trades["symbol"] == p) & (trades["seller"] == b), "quantity"].sum()
            row[p] = int(bq - sq)
            tot_buy += bq
            tot_sell += sq
        row["TOT_BUY"] = int(tot_buy)
        row["TOT_SELL"] = int(tot_sell)
        rows.append(row)
    return pd.DataFrame(rows).set_index("bot")


# ── 2. Aggression: is the bot a taker or a maker? ──────────────────────────

def aggression_table(trades: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    pmid = (
        prices[["day", "timestamp", "product", "mid_price"]]
        .rename(columns={"product": "symbol"})
    )
    merged = trades.merge(pmid, on=["day", "timestamp", "symbol"], how="left")
    merged["delta"] = merged["price"] - merged["mid_price"]

    rows = []
    for (b, side), sub in (
        merged.assign(role="buyer").rename(columns={"buyer": "bot"})[["bot", "delta", "symbol", "quantity", "role"]]
        .pipe(
            lambda df: pd.concat(
                [
                    df.assign(side="buy"),
                    merged.assign(role="seller")
                    .rename(columns={"seller": "bot"})[["bot", "delta", "symbol", "quantity", "role"]]
                    .assign(side="sell", delta=-merged["delta"]),
                ],
                ignore_index=True,
            )
        )
        .groupby(["bot", "side"])
    ):
        rows.append(
            {
                "bot": b,
                "side": side,
                "n_trades": len(sub),
                "qty_total": int(sub["quantity"].sum()),
                "delta_mean": sub["delta"].mean(),
                "delta_median": sub["delta"].median(),
                "share_takes_offer": (sub["delta"] > 0).mean(),
                "share_hits_bid": (sub["delta"] < 0).mean(),
            }
        )
    return pd.DataFrame(rows).sort_values(["bot", "side"]).reset_index(drop=True)


# ── 3. Lead-lag: does bot's print price have positive markout? ─────────────

def lead_lag_table(
    trades: pd.DataFrame,
    prices: pd.DataFrame,
    product: str,
    horizons: Iterable[int] = (100, 500, 1000, 5000),
) -> pd.DataFrame:
    p_prod = (
        prices.loc[prices["product"] == product, ["day", "timestamp", "mid_price"]]
        .sort_values(["day", "timestamp"])
        .reset_index(drop=True)
    )
    t_prod = trades[trades["symbol"] == product]
    bots = sorted(set(t_prod["buyer"]) | set(t_prod["seller"]))

    rows = []
    for b in bots:
        for side in ("buy", "sell"):
            evs = t_prod[t_prod["buyer" if side == "buy" else "seller"] == b]
            if len(evs) == 0:
                continue
            sample = {"bot": b, "side": side, "n_events": len(evs)}
            for h in horizons:
                moves = []
                for _, r in evs.iterrows():
                    fut = p_prod[
                        (p_prod["day"] == r["day"]) & (p_prod["timestamp"] >= r["timestamp"] + h)
                    ]
                    if len(fut):
                        delta = fut.iloc[0]["mid_price"] - r["price"]
                        moves.append(delta if side == "buy" else -delta)
                if moves:
                    sample[f"avg_move_h{h}"] = float(np.mean(moves))
                    sample[f"hit_h{h}"] = float(np.mean([m > 0 for m in moves]))
            rows.append(sample)
    return pd.DataFrame(rows)


# ── 4. Basket dumps: simultaneous-timestamp multi-product Mark 22 sells ────

def basket_dump_summary(trades: pd.DataFrame, dumper: str = "Mark 22", min_legs: int = 4) -> pd.DataFrame:
    sells = trades[trades["seller"] == dumper]
    g = sells.groupby(["day", "timestamp"])["symbol"].agg(list)
    bursts = [(d, t, sorted(s)) for (d, t), s in g.items() if len(s) >= min_legs]
    rows = [
        {
            "day": d,
            "timestamp": t,
            "n_legs": len(legs),
            "products": ",".join(sorted(set(legs))),
        }
        for (d, t, legs) in bursts
    ]
    return pd.DataFrame(rows)


# ── 5. own_trades vs market_trades reference (from prosperity4bt source) ──

OWN_VS_MARKET_NOTE = """
own_trades vs market_trades — DISJOINT, never overlap.

Source: site-packages/prosperity4bt/runner.py @ match_orders():

    market_trades = { ... }                    # tape prints this tick
    for product in data.products:
        new_trades = []
        for order in orders.get(product, []):
            new_trades.extend(match_order(order, market_trades.get(product, []), ...))
        if new_trades:
            state.own_trades[product] = new_trades   # what we filled
    for product, trades in market_trades.items():
        for trade in trades:
            trade.trade.quantity = min(trade.buy_quantity, trade.sell_quantity)
        remaining = [t.trade for t in trades if t.trade.quantity > 0]
        state.market_trades[product] = remaining     # what was left over

Implication:
  * The tape volume that fills against your orders is REMOVED from
    market_trades and appears only in own_trades.
  * If you want to count "everything that printed this tick", you must
    union own_trades + market_trades.
  * Any bot-name signal you compute from market_trades alone undercounts
    the prints you yourself participated in (which still carry counterparty
    info on the live IMC website).
"""


# ── Main / CLI ─────────────────────────────────────────────────────────────

def _print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ROUND_4 bot-alpha discovery report.")
    parser.add_argument("--out", type=str, default=None,
                        help="if given, also write the report to this file")
    args = parser.parse_args(argv)

    trades, prices = load_round4()

    # Quick sanity / context
    bots = sorted(set(trades["buyer"]) | set(trades["seller"]))
    _print_section("Round 4 context")
    print(f"  trades: {len(trades):,}  across days={list(DAYS)}  bots={bots}")
    print(f"  price snapshots: {len(prices):,}  products={sorted(prices['product'].unique())}")

    _print_section("1. NET FLOW per bot per product (qty bought - qty sold)")
    nf = net_flow_table(trades)
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(nf.to_string())

    _print_section("2. AGGRESSION per bot per side (price - mid; > 0 means takes offer)")
    ag = aggression_table(trades, prices)
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(ag.to_string(index=False, float_format=lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)))

    _print_section("3. LEAD-LAG/MARKOUT: signed future mid less trade price on VELVETFRUIT_EXTRACT")
    ll_v = lead_lag_table(trades, prices, "VELVETFRUIT_EXTRACT")
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.float_format", lambda x: f"{x:+.3f}"):
        print(ll_v.to_string(index=False))

    _print_section("3b. LEAD-LAG/MARKOUT: same on HYDROGEL_PACK")
    ll_h = lead_lag_table(trades, prices, "HYDROGEL_PACK")
    with pd.option_context("display.width", 220, "display.max_columns", 30, "display.float_format", lambda x: f"{x:+.3f}"):
        print(ll_h.to_string(index=False))

    _print_section("4. BASKET DUMP bursts (>=4 simultaneous Mark 22 sells)")
    bd = basket_dump_summary(trades)
    print(f"  total bursts: {len(bd)}  (covers Mark 22's basket-liquidator pattern)")
    print(bd.head(15).to_string(index=False))

    _print_section("5. own_trades vs market_trades semantics")
    print(OWN_VS_MARKET_NOTE)

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w") as f:
            import contextlib, io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main([])
            f.write(buf.getvalue())
        print(f"\nWrote {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
