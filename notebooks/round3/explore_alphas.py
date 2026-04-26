"""Caveman explore — find alphas trader_final_OPTIMUM doesn't yet trade.

Run from repo root: `uv run python notebooks/round3/explore_alphas.py`
"""

from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(__file__).resolve().parents[2] / "data" / "ROUND_3"

prices = pd.concat([
    pd.read_csv(DATA / f"prices_round_3_day_{d}.csv", sep=";").assign(day=d)
    for d in (0, 1, 2)
], ignore_index=True)
trades = pd.concat([
    pd.read_csv(DATA / f"trades_round_3_day_{d}.csv", sep=";").assign(day=d)
    for d in (0, 1, 2)
], ignore_index=True)

prices["spread"] = prices["ask_price_1"] - prices["bid_price_1"]

# ── 1. Accumulator → ATM call price drift -----------------------------------
print("=" * 70)
print("1. Accumulator (VELVET ≥9 lot BUY) → does VEV_5200 / 5100 mid rise?")
print("=" * 70)

# Identify Accumulator events
acc = trades[(trades["symbol"] == "VELVETFRUIT_EXTRACT") & (trades["quantity"] >= 9)].copy()
# Direction: 100% BUY per the notebook
print(f"Accumulator events total: {len(acc)} across 3 days.")

mids = (prices.pivot_table(index=["day", "timestamp"], columns="product",
                           values="mid_price").sort_index())

def horizon_returns(strike, horizon_ticks):
    """Mean change in option mid `horizon_ticks` after each Accumulator event."""
    col = f"VEV_{strike}"
    out = []
    for _, row in acc.iterrows():
        d, t = row["day"], row["timestamp"]
        try:
            now = mids.loc[(d, t), col]
            then = mids.loc[(d, t + horizon_ticks), col]
            out.append(then - now)
        except KeyError:
            continue
    if not out:
        return None
    arr = np.array(out)
    return arr.mean(), arr.std() / np.sqrt(len(arr)), len(arr)

for strike in (5000, 5100, 5200, 5300):
    print(f"\n  VEV_{strike}:")
    for h in (200, 500, 1000, 2000):
        res = horizon_returns(strike, h)
        if res is None:
            print(f"    h={h}: no data")
        else:
            mean, se, n = res
            print(f"    h={h:>4}: mean drift = {mean:+.3f}  SE = {se:.3f}  n = {n}  "
                  f"{'✓ alpha' if abs(mean) > 2 * se else '· noise'}")

# Also VELVET itself for sanity
print("\n  VELVETFRUIT_EXTRACT (sanity check, expect +2 tick @ 500):")
for h in (200, 500, 1000, 2000):
    out = []
    for _, row in acc.iterrows():
        d, t = row["day"], row["timestamp"]
        try:
            now = mids.loc[(d, t), "VELVETFRUIT_EXTRACT"]
            then = mids.loc[(d, t + h), "VELVETFRUIT_EXTRACT"]
            out.append(then - now)
        except KeyError:
            continue
    arr = np.array(out)
    print(f"    h={h:>4}: mean drift = {arr.mean():+.3f}  SE = {arr.std()/np.sqrt(len(arr)):.3f}  n = {len(arr)}")


# ── 2. Wing-seller basket → VELVET / 5300 short-term drift ------------------
print("\n" + "=" * 70)
print("2. Wing-seller basket fires (multi-strike OTM SELL) → next-tick drift")
print("=" * 70)

wing_syms = ["VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"]
wing = trades[trades["symbol"].isin(wing_syms)].copy()
basket_ts = wing.groupby(["day", "timestamp"])["symbol"].nunique()
basket_events = basket_ts[basket_ts >= 3].reset_index()  # ≥3 strikes same tick
print(f"Wing-seller basket events (≥3 strikes same tick): {len(basket_events)}")

for sym in ("VELVETFRUIT_EXTRACT", "VEV_5200", "VEV_5300", "VEV_5400"):
    print(f"\n  {sym}:")
    for h in (100, 300, 1000, 3000):
        out = []
        for _, row in basket_events.iterrows():
            d, t = row["day"], row["timestamp"]
            try:
                now = mids.loc[(d, t), sym]
                then = mids.loc[(d, t + h), sym]
                out.append(then - now)
            except KeyError:
                continue
        if not out:
            continue
        arr = np.array(out)
        sig = "✓ alpha" if abs(arr.mean()) > 2 * arr.std() / np.sqrt(len(arr)) else "· noise"
        print(f"    h={h:>4}: mean drift = {arr.mean():+.4f}  SE = {arr.std()/np.sqrt(len(arr)):.4f}  n = {len(arr)}  {sig}")


# ── 3. Vertical spread (C5200 - C5300) mean reversion -----------------------
print("\n" + "=" * 70)
print("3. Vertical spread mean reversion — does (C_K - C_{K+100}) mean-revert?")
print("=" * 70)

for k_lo in (5100, 5200, 5300, 5400):
    k_hi = k_lo + 100
    a = mids[f"VEV_{k_lo}"]
    b = mids[f"VEV_{k_hi}"]
    spread = (a - b).dropna()
    if spread.empty:
        continue
    # AR(1) on returns
    s = spread.diff().dropna()
    if len(s) < 100:
        continue
    rho = s.autocorr(lag=1)
    print(f"  spread {k_lo}-{k_hi}: mean={spread.mean():.2f}  std={spread.std():.2f}  "
          f"diff_AR1 = {rho:+.3f}  {'✓ mean-rev' if rho < -0.2 else '· no edge'}")


# ── 4. VEV_4000 trader directional copy -------------------------------------
print("\n" + "=" * 70)
print("4. VEV_4000 trader: does VELVET drift after their trades?")
print("=" * 70)

v4 = trades[trades["symbol"] == "VEV_4000"].copy()
print(f"VEV_4000 trades: {len(v4)} across 3 days, sizes 1-3")
# Signed quantity proxy: if trade price > mid, it's a BUY (lifted ask); else SELL
# Reconstruct: compare price to mid at that timestamp
def sign_for(row):
    try:
        m = mids.loc[(row["day"], row["timestamp"]), "VEV_4000"]
        if row["price"] > m:
            return 1
        if row["price"] < m:
            return -1
        return 0
    except KeyError:
        return 0

v4["sign"] = v4.apply(sign_for, axis=1)

for side, mark in (("BUY", 1), ("SELL", -1)):
    sub = v4[v4["sign"] == mark]
    if sub.empty:
        continue
    out = []
    for _, row in sub.iterrows():
        d, t = row["day"], row["timestamp"]
        try:
            now = mids.loc[(d, t), "VELVETFRUIT_EXTRACT"]
            then = mids.loc[(d, t + 1000), "VELVETFRUIT_EXTRACT"]
            out.append(then - now)
        except KeyError:
            continue
    arr = np.array(out)
    if len(arr) == 0:
        continue
    print(f"  VEV_4000 {side}: n={len(arr)}, drift @1k = {arr.mean():+.3f}  SE={arr.std()/np.sqrt(len(arr)):.3f}")


# ── 5. Realised vol vs mid extrinsic at ATM (gamma scalping screen) ---------
print("\n" + "=" * 70)
print("5. Realised vol vs implied at VEV_5200 / 5100 (gamma scalp screen)")
print("=" * 70)

velvet = mids["VELVETFRUIT_EXTRACT"].dropna()
ret = velvet.diff().dropna()
realised_var = (ret ** 2).mean()  # tick variance
realised_vol = np.sqrt(realised_var)
print(f"  Velvet 1-tick realized stdev = {realised_vol:.3f} ticks")
# Per-day std
for day in (0, 1, 2):
    s = mids.loc[day, "VELVETFRUIT_EXTRACT"].dropna()
    print(f"    day {day}: 1-tick std = {s.diff().std():.3f}, total std = {s.std():.2f}")


# ── 6. Underlying mean reversion (Volcanic Rock style) ----------------------
print("\n" + "=" * 70)
print("6. VELVET return autocorr (mean-rev test)")
print("=" * 70)

for day in (0, 1, 2):
    s = mids.loc[day, "VELVETFRUIT_EXTRACT"].dropna()
    rs = s.diff().dropna()
    print(f"  day {day}: 1-lag autocorr = {rs.autocorr(1):+.3f}, 5-lag = {rs.autocorr(5):+.3f}, "
          f"30-lag = {rs.autocorr(30):+.3f}")
