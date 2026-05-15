"""Check IV drift over time within data/ROUND_3."""
import math
from pathlib import Path
from statistics import NormalDist
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NORMAL = NormalDist()

def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0: return max(S - K, 0.0)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    return S * NORMAL.cdf(d1) - K * NORMAL.cdf(d2)

def bs_vega(S, K, T, sigma):
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    return S * NORMAL.pdf(d1) * sq

def iv(S, K, T, mp, init=0.03, iters=30):
    if mp <= max(S - K, 0.0) + 1e-6: return None
    s = init
    for _ in range(iters):
        d = bs_call(S, K, T, s) - mp
        if abs(d) < 1e-6: return s
        v = bs_vega(S, K, T, s)
        if v < 1e-8: return None
        s = max(1e-4, min(0.1, s - d / v))
    return s

prices = pd.concat([
    pd.read_csv(ROOT / "data" / "ROUND_3" / f"prices_round_3_day_{d}.csv", sep=";").assign(day=d)
    for d in (0, 1, 2)
], ignore_index=True)
mids = prices.pivot_table(index=["day", "timestamp"], columns="product", values="mid_price")
S_col = mids["VELVETFRUIT_EXTRACT"]

print(f"{'day':>4} {'tick_bucket':>12}  K=5100 IV  K=5200 IV  K=5300 IV")
print("-" * 60)
for day in (0, 1, 2):
    d_mids = mids.loc[day]
    d_S = S_col.loc[day]
    n = len(d_mids)
    for bucket in range(5):  # 5 sub-buckets per day
        lo = bucket * n // 5
        hi = (bucket + 1) * n // 5
        slice_mids = d_mids.iloc[lo:hi]
        slice_S = d_S.iloc[lo:hi]
        ivs_per_K = {}
        for K in (5100, 5200, 5300):
            col = f"VEV_{K}"
            ivs = []
            for ts, mid in slice_mids[col].items():
                S = slice_S.get(ts)
                if S is None or pd.isna(mid): continue
                iv_val = iv(S, K, 1.0, mid)
                if iv_val: ivs.append(iv_val)
            ivs_per_K[K] = sum(ivs)/len(ivs) if ivs else None
        print(f"{day:>4} {bucket}/{5}        " + "  ".join(
            f"{ivs_per_K[K]:.4f}" if ivs_per_K[K] else "  N/A" for K in (5100, 5200, 5300)
        ))
