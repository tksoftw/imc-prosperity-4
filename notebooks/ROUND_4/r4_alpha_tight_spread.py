"""Round 4 alpha research: tight-spread signal + combined confirmation.

Key findings vs prior bot_alpha_analysis.py:
  1. own_trades and market_trades are DISJOINT in the backtester (confirmed).
     The volume we match against is REMOVED from market_trades and moves to
     own_trades. On the live website both carry counterparty names — so for
     live robustness use ``market_trades + own_trades`` (HYPER already does
     this for HYDRO).

  2. VELVET tight spread (spread <= 2) is a STRONG bullish signal that
     doesn't require bot names at all:
       spread=1: SR=1.78 next tick, P(up)=89%, mean mid-change=+1.87
       spread=2: SR=0.75 next tick, P(up)=80%, mean mid-change=+1.13
     This fires ~1218 times / 3 days (~406/day) vs only 165 M67 buys / 3
     days.  Much higher frequency, comparable per-event strength.

  3. Markout vs mid-change distinction: the bot_alpha_report uses
     ``future_mid - trade_price`` which includes bid-ask in the numerics.
     Passive buyers (hit bid) show positive markout mechanically; passive
     sellers (at ask) show negative markout.  Only the MID-TO-MID change
     measures true directional alpha.  Mid-to-mid corrections:
       Mark 67 BUY:  mean +1.97 @ 100ms  (SR=2.05) — real directional alpha
       Mark 49 SELL: mean +1.82 @ 100ms  (SR=0.94) — real directional alpha
       Mark 22 SELL: mean +1.51 @ 100ms  (SR=1.01) — real directional alpha
       Mark 14 HYDRO (any): markout +8 is ENTIRELY bid-ask (they trade at
         bid/ask, not directional). Still useful as regime indicator.

  4. Tight spread occurs WHEN Mark 67 is about to buy — the spread compression
     IS the pre-condition for M67's sweep.  So the two signals are correlated:
       * Tight spread fires BEFORE or SIMULTANEOUSLY with M67 buy
       * Bot-name signal fires ONE TICK AFTER (market_trades lag)
     Using tight spread for the same-tick entry + bot-name to hold longer is
     the right layering.

  5. Chasing a tight-spread tick at the ask and exiting at the future bid is
     unprofitable (mean -1.1 at 1s exit) due to the 5-point spread at exit.
     The signal should be used to LOWER THE ENTRY THRESHOLD on existing
     swing positions, not as a standalone market-order strategy.

Run this file:
    uv run python notebooks/round4/r4_alpha_tight_spread.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "ROUND_4"

# ── Load data ─────────────────────────────────────────────────────────────────
prices = pd.concat(
    [pd.read_csv(DATA / f"prices_round_4_day_{d}.csv", sep=";").assign(day=d) for d in [1, 2, 3]]
)
trades = pd.concat(
    [pd.read_csv(DATA / f"trades_round_4_day_{d}.csv", sep=";").assign(day=d) for d in [1, 2, 3]]
)


def mid_change(df_prices: pd.DataFrame, product: str, lookahead: int) -> pd.Series:
    """Return future_mid(t+lookahead) - current_mid(t) indexed by (day, timestamp)."""
    p = df_prices[df_prices["product"] == product][["day", "timestamp", "mid_price"]].set_index(
        ["day", "timestamp"]
    )
    shifted = p["mid_price"].copy()
    # shift by lookahead ticks (100ms each)
    steps = lookahead // 100
    result = {}
    for (day, ts), mid in p["mid_price"].items():
        key = (day, ts + lookahead)
        if key in p.index:
            result[(day, ts)] = p.loc[key, "mid_price"] - mid
    return pd.Series(result)


# ── 1. Tight-spread signal ────────────────────────────────────────────────────
print("=" * 70)
print("1. VELVET TIGHT-SPREAD SIGNAL")
print("=" * 70)

velvet = prices[prices["product"] == "VELVETFRUIT_EXTRACT"].copy()
velvet["spread"] = velvet["ask_price_1"] - velvet["bid_price_1"]
velvet_idx = velvet.set_index(["day", "timestamp"])

for spread_val in [1, 2, 3, 4, 5]:
    subset = velvet[velvet["spread"] == spread_val]
    if len(subset) < 10:
        continue
    changes = []
    for _, row in subset.iterrows():
        day, ts = row["day"], int(row["timestamp"])
        try:
            future = velvet_idx.loc[(day, ts + 100), "mid_price"]
            changes.append(float(future) - float(row["mid_price"]))
        except KeyError:
            pass
    c = np.array(changes)
    sr = c.mean() / c.std() if c.std() > 0 else 0.0
    pup = 100 * (c > 0).mean()
    print(
        f"  spread={spread_val}: n={len(c):5d} ({100*len(c)/len(velvet):5.1f}%)"
        f"  mean={c.mean():+.4f}  SR={sr:+.3f}  P(up)={pup:.1f}%"
    )

# ── 2. Bot-name signal (mid-to-mid, correct measurement) ─────────────────────
print()
print("=" * 70)
print("2. BOT-NAME SIGNAL — mid-to-mid (not markout)")
print("=" * 70)

v_idx = velvet.set_index(["day", "timestamp"])["mid_price"]
vt = trades[trades["symbol"] == "VELVETFRUIT_EXTRACT"]

signals = {
    "Mark 67 BUY": vt[vt["buyer"] == "Mark 67"],
    "Mark 49 SELL": vt[vt["seller"] == "Mark 49"],
    "Mark 22 SELL VELVET": vt[vt["seller"] == "Mark 22"],
    "Mark 55 BUY": vt[vt["buyer"] == "Mark 55"],
}

for label, subset in signals.items():
    for la in [100, 500]:
        ch = []
        for _, t in subset.iterrows():
            day, ts = t["day"], int(t["timestamp"])
            try:
                c = v_idx.loc[(day, ts)]
                f = v_idx.loc[(day, ts + la)]
                ch.append(float(f) - float(c))
            except KeyError:
                pass
        if ch:
            c = np.array(ch)
            sr = c.mean() / c.std() if c.std() > 0 else 0.0
            print(f"  {label}  +{la}ms: n={len(c):4d}  mean={c.mean():+.3f}  SR={sr:+.3f}")


# ── 3. Combined signal: tight spread OR bot-bullish ───────────────────────────
print()
print("=" * 70)
print("3. COMBINED SIGNAL — tight-spread OR bot-bullish")
print("=" * 70)

tight_ts = set()
for _, row in velvet[velvet["spread"] <= 2].iterrows():
    tight_ts.add((row["day"], int(row["timestamp"])))

bot_bullish_ts = set()
for _, t in vt[(vt["buyer"] == "Mark 67") | (vt["seller"] == "Mark 49") | (vt["seller"] == "Mark 22")].iterrows():
    bot_bullish_ts.add((t["day"], int(t["timestamp"])))

overlap = tight_ts & bot_bullish_ts
print(f"  Tight-spread events:   {len(tight_ts):5d}")
print(f"  Bot-bullish events:    {len(bot_bullish_ts):5d}")
print(f"  Overlap:               {len(overlap):5d} ({100*len(overlap)/len(tight_ts):.1f}% of tight-spread events)")
print()
print("  → Tight-spread fires ~{} events that are NOT already M67/M49/M22".format(
    len(tight_ts - bot_bullish_ts)
))
print("    These are ADDITIONAL bullish opportunities not in current HYPER signal")


# ── 4. Tight-spread events NOT covered by bot signal ─────────────────────────
print()
print("=" * 70)
print("4. ADDITIONAL VALUE: tight-spread events not in bot-signal")
print("=" * 70)

new_ts = tight_ts - bot_bullish_ts
ch_new = []
for day, ts in new_ts:
    try:
        c = v_idx.loc[(day, ts)]
        f = v_idx.loc[(day, ts + 100)]
        ch_new.append(float(f) - float(c))
    except KeyError:
        pass

c = np.array(ch_new)
sr = c.mean() / c.std() if c.std() > 0 else 0.0
pup = 100 * (c > 0).mean()
print(f"  n={len(c)}, mean={c.mean():+.4f}, SR={sr:+.3f}, P(up)={pup:.1f}%")
print()
print("  These are tight-spread ticks not covered by M67/M49/M22 signal.")
print("  SR={:.3f} means the tight-spread signal is ADDITIVE.".format(sr))


# ── 5. Practical strategy: lower threshold at tight spread ────────────────────
print()
print("=" * 70)
print("5. STRATEGY IMPLICATION")
print("=" * 70)
print("""
  HYPER already tilts the VELVET edge by +2 to +3 when bot signal fires.
  Adding tight-spread detection (order book spread ≤ 2) to LOWER the
  entry threshold provides earlier entries:

    Current HYPER entry:
      swing edge > 6  →  enter  (baseline)
      bot signal +3 tilt: effective threshold = 3  (fires ~200/3days)

    TIGHTEDGE enhancement:
      tight spread ≤ 2:  set bullish_tilt = max(existing, +3)  (~1200/3days)
      TTL = 500ms after last tight tick

  The tight-spread condition fires 6x more often than the bot signal and
  has comparable per-event strength. It catches the same M67 event earlier
  (same tick, not one tick late) and also catches OTHER tight-spread events
  not triggered by named bots.

  NOTE: On the live website the tight-spread signal is tick-synchronous
  (order book is available immediately), while bot-name signal requires
  market_trades which may be one tick delayed. The tight-spread is STRICTLY
  better for same-tick entry.
""")

print("Analysis complete.")
