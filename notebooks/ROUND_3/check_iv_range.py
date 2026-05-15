"""Caveman IV range check — what's the actual IV in data/ROUND_3 vs
imc_logs (real)? Trader_ff's old clamp was [0.026, 0.037].
"""
import math, json
from pathlib import Path
from statistics import NormalDist
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NORMAL = NormalDist()

def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    return S * NORMAL.cdf(d1) - K * NORMAL.cdf(d2)

def bs_vega(S, K, T, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sq)
    return S * NORMAL.pdf(d1) * sq

def implied_vol(S, K, T, market_price, sigma_init=0.03, iters=30):
    if market_price <= max(S - K, 0.0) + 1e-6:
        return None
    sigma = sigma_init
    for _ in range(iters):
        diff = bs_call(S, K, T, sigma) - market_price
        if abs(diff) < 1e-6:
            return sigma
        v = bs_vega(S, K, T, sigma)
        if v < 1e-8:
            return None
        sigma = max(1e-4, min(0.1, sigma - diff / v))
    return sigma

print("=== data/ROUND_3 historical (3-day backtest source) ===")
prices = pd.concat([
    pd.read_csv(ROOT / "data" / "ROUND_3" / f"prices_round_3_day_{d}.csv", sep=";").assign(day=d)
    for d in (0, 1, 2)
], ignore_index=True)

mids = prices.pivot_table(index=["day", "timestamp"], columns="product", values="mid_price")
S_col = mids["VELVETFRUIT_EXTRACT"]

for K in (5000, 5100, 5200, 5300, 5400, 5500):
    col = f"VEV_{K}"
    if col not in mids:
        continue
    sub = mids[[col]].join(S_col.rename("S")).dropna()
    sub = sub.iloc[::200]  # subsample
    ivs = []
    for _, row in sub.iterrows():
        iv = implied_vol(row["S"], K, 1.0, row[col])
        if iv:
            ivs.append(iv)
    if ivs:
        ivs_s = pd.Series(ivs)
        print(f"  K={K}: n={len(ivs)} IV range = [{ivs_s.min():.4f}, {ivs_s.max():.4f}], "
              f"mean={ivs_s.mean():.4f}")

print()
print("=== imc_logs/ROUND_3 (real day-2 submissions) ===")
LOG_DIR = ROOT / "imc_logs" / "ROUND_3"
for log_path in sorted(LOG_DIR.glob("*.log")):
    payload = json.loads(log_path.read_text())
    rows = payload.get("activitiesLog", "").strip().split("\n")
    header = rows[0].split(";")
    idx = {h: i for i, h in enumerate(header)}
    by_ts = {}
    for row in rows[1:]:
        cols = row.split(";")
        if len(cols) < len(header): continue
        ts = int(cols[idx["timestamp"]])
        prod = cols[idx["product"]]
        try:
            mid = float(cols[idx["mid_price"]])
        except ValueError:
            continue
        by_ts.setdefault(ts, {})[prod] = mid
    ivs_per_K = {K: [] for K in (5000, 5100, 5200, 5300, 5400, 5500)}
    for ts in sorted(by_ts)[::200]:
        snap = by_ts[ts]
        S = snap.get("VELVETFRUIT_EXTRACT")
        if S is None: continue
        for K in ivs_per_K:
            mid = snap.get(f"VEV_{K}")
            if mid is None: continue
            iv = implied_vol(S, K, 1.0, mid)
            if iv: ivs_per_K[K].append(iv)
    print(f"\n  {log_path.name}:")
    for K, ivs in ivs_per_K.items():
        if ivs:
            print(f"    K={K}: n={len(ivs)} IV range = [{min(ivs):.4f}, {max(ivs):.4f}]")
    break  # just first log
