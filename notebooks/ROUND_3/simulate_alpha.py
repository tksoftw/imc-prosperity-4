"""Caveman quick-sim: how much PnL would Wing-seller-basket → BUY 5200 give us?"""

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

# Build per-tick price snapshots
pivot = prices.pivot_table(
    index=["day", "timestamp"],
    columns="product",
    values=["bid_price_1", "ask_price_1", "mid_price"],
).sort_index()


# Wing seller events
wing_syms = ["VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"]
wing = trades[trades["symbol"].isin(wing_syms)].copy()
basket_ts = wing.groupby(["day", "timestamp"])["symbol"].nunique()


def sim_strategy(min_strikes, hold_ticks, sym, exit_mode="bid"):
    """For each basket event with ≥min_strikes, BUY at ask, exit `hold_ticks`
    later at `exit_mode` (bid=cross spread, mid=mark, ask=join offer/lift).
    Reports avg PnL per trade and total over 3 days at size=1.
    """
    events = basket_ts[basket_ts >= min_strikes].reset_index()
    pnls = []
    fills = 0
    for _, row in events.iterrows():
        d, t = row["day"], row["timestamp"]
        try:
            buy_px = pivot.loc[(d, t), ("ask_price_1", sym)]
            if exit_mode == "bid":
                exit_px = pivot.loc[(d, t + hold_ticks), ("bid_price_1", sym)]
            elif exit_mode == "mid":
                exit_px = pivot.loc[(d, t + hold_ticks), ("mid_price", sym)]
            elif exit_mode == "ask":
                exit_px = pivot.loc[(d, t + hold_ticks), ("ask_price_1", sym)]
            if pd.isna(buy_px) or pd.isna(exit_px):
                continue
            pnls.append(exit_px - buy_px)
            fills += 1
        except KeyError:
            continue
    if not pnls:
        return None
    arr = np.array(pnls)
    return arr.mean(), arr.sum(), fills


print("\n[1] Strategy: Lift ASK at basket event, exit at given mode after `hold` ticks")
for sym in ("VEV_5200", "VEV_5300", "VEV_5400"):
    print(f"\n  {sym}:")
    for min_strikes in (2, 3, 4):
        for hold in (100, 300, 1000, 3000):
            for mode in ("bid", "mid", "ask"):
                res = sim_strategy(min_strikes, hold, sym, mode)
                if res is None:
                    continue
                avg, tot, n = res
                marker = "✓" if avg > 0 else " "
                print(f"   ≥{min_strikes}-strike, hold={hold:>4}, exit@{mode}: "
                      f"avg = {avg:+.3f}  total = {tot:+.1f}  fills={n} {marker}")


# ── 2. Vertical spread mean-reversion strategy --------------------------------
print("\n\n[2] Vertical spread (5200-5300) mean reversion sim")
mid_5200 = pivot[("mid_price", "VEV_5200")].dropna()
mid_5300 = pivot[("mid_price", "VEV_5300")].dropna()
spread = (mid_5200 - mid_5300).dropna()

mu, sigma = spread.mean(), spread.std()
print(f"  spread mean = {mu:.2f}, std = {sigma:.2f}")

for k in (1.5, 2.0, 2.5, 3.0):
    enter = mu + k * sigma
    exit_lvl = mu
    # When spread > enter: short the spread (sell 5200 buy 5300). EV = enter - exit_lvl
    # When spread < mu - k*sigma: long the spread.
    above = spread > enter
    below = spread < (mu - k * sigma)
    n_short = above.sum()
    n_long = below.sum()
    avg_above = (spread[above] - enter).mean() if n_short else 0
    print(f"  k={k}: enter±{k}σ = ({mu - k * sigma:.1f}, {enter:.1f}). "
          f"n_above={n_short}, n_below={n_long}, "
          f"avg dev above = {avg_above:+.2f}")


# ── 3. Same strategy but using only ASK / BID instead of mid -------------------
print("\n\n[3] Realistic vertical-spread arb with bid/ask costs (5200-5300)")
ask_5200 = pivot[("ask_price_1", "VEV_5200")]
bid_5200 = pivot[("bid_price_1", "VEV_5200")]
ask_5300 = pivot[("ask_price_1", "VEV_5300")]
bid_5300 = pivot[("bid_price_1", "VEV_5300")]

# When bid_5200 - ask_5300 > some threshold: sell 5200 (hit bid_5200), buy 5300 (lift ask_5300)
# Reverse when ask_5200 - bid_5300 < -threshold.

# Compute realised payoff over time
buy_long = ask_5200 - bid_5300   # cost to enter long-spread (buy 5200 sell 5300)
sell_short = bid_5200 - ask_5300 # cash from short-spread (sell 5200 buy 5300)

print(f"  cost to enter long-spread (5200-5300):  median = {buy_long.median():.2f}, "
      f"min = {buy_long.min():.2f}")
print(f"  cash from short-spread (5200-5300):     median = {sell_short.median():.2f}, "
      f"max = {sell_short.max():.2f}")
print(f"  if mid_spread mean = {mu:.2f}, ANY entry far from mean is mean-rev opp")

# Test: when buy_long < mu - 4 (enter cheap), close at mu later. Win = mu - buy_long
mask_long = buy_long < mu - 4
if mask_long.sum():
    print(f"\n  LONG entries (buy 5200, sell 5300) at price < mu-4 = {mu - 4:.1f}: "
          f"n = {mask_long.sum()}")
mask_short = sell_short > mu + 4
if mask_short.sum():
    print(f"  SHORT entries (sell 5200, buy 5300) at price > mu+4 = {mu + 4:.1f}: "
          f"n = {mask_short.sum()}")
