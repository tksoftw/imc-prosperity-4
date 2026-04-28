"""Look for executable bug-alpha in ROUND_3.

This is stricter than IV charts:

* Deep ITM vouchers are valued from VELVET, not their own mid.
* ATM/wing vouchers are valued from a clean smile fit.
* A bug only counts if the *bid or ask* crosses fair enough to pay spread.
* We also inspect passive zero-tail fills, because tape @0 does not mean
  the backtester gives our order queue priority.
"""

from __future__ import annotations

import math
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "ROUND_3"
OUT = ROOT / "notebooks" / "round3" / "bug_alpha_report.txt"

VELVET = "VELVETFRUIT_EXTRACT"
STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)
SMILE_STRIKES = (5000, 5100, 5200, 5300, 5400, 5500)
TTE = 1.0
IV_LO, IV_HI = 1e-4, 0.08
N = NormalDist()


def bs_call(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return max(s - k, 0.0)
    rt = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * rt)
    d2 = d1 - sigma * rt
    return s * N.cdf(d1) - k * N.cdf(d2)


def bs_vega(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return 0.0
    rt = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * rt)
    return s * N.pdf(d1) * rt


def implied_vol(s: float, k: float, price: float, seed: float = 0.03) -> float:
    if price <= max(s - k, 0.0) + 1e-7:
        return IV_LO
    sigma = seed
    for _ in range(30):
        diff = bs_call(s, k, TTE, sigma) - price
        if abs(diff) < 1e-7:
            break
        vega = bs_vega(s, k, TTE, sigma)
        if vega < 1e-8:
            break
        sigma = min(IV_HI, max(IV_LO, sigma - diff / vega))
    return sigma


def load_day(day: int) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"prices_round_3_day_{day}.csv", sep=";")
    piv = df.pivot(index="timestamp", columns="product").sort_index()
    flat = pd.DataFrame(index=piv.index)
    for field in (
        "bid_price_1", "bid_volume_1", "bid_price_2", "bid_volume_2", "bid_price_3", "bid_volume_3",
        "ask_price_1", "ask_volume_1", "ask_price_2", "ask_volume_2", "ask_price_3", "ask_volume_3",
        "mid_price",
    ):
        for product in piv[field].columns:
            flat[f"{product}:{field}"] = piv[field][product]
    flat["timestamp"] = flat.index.astype(int)
    flat["day"] = day
    return flat.reset_index(drop=True)


def fit_smile_fairs(df: pd.DataFrame) -> dict[int, np.ndarray]:
    fairs = {k: np.full(len(df), np.nan) for k in SMILE_STRIKES}
    seeds = {k: 0.03 for k in SMILE_STRIKES}
    for i, row in df.iterrows():
        s = float(row[f"{VELVET}:mid_price"])
        obs = []
        for k in SMILE_STRIKES:
            mid = float(row[f"VEV_{k}:mid_price"])
            bid = float(row[f"VEV_{k}:bid_price_1"])
            ask = float(row[f"VEV_{k}:ask_price_1"])
            if not all(np.isfinite(v) for v in (mid, bid, ask)) or ask <= bid:
                continue
            iv = implied_vol(s, k, mid, seeds[k])
            seeds[k] = iv
            if iv <= 0.005:
                continue
            x = (k - s) / 100.0
            w = 1.0 / max(1.0, ask - bid)
            obs.append((k, x, iv, w))
        if len(obs) < 4:
            continue
        for k in SMILE_STRIKES:
            others = [o for o in obs if o[0] != k]
            if len(others) < 4:
                others = obs
            xs = np.array([o[1] for o in others])
            ys = np.array([o[2] for o in others])
            ws = np.array([o[3] for o in others])
            x = (k - s) / 100.0
            coeff = np.polyfit(xs, ys, 2, w=np.sqrt(ws))
            iv = min(IV_HI, max(IV_LO, float(np.polyval(coeff, x))))
            fairs[k][i] = bs_call(s, k, TTE, iv)
    return fairs


def build_fairs(df: pd.DataFrame) -> dict[int, np.ndarray]:
    s = df[f"{VELVET}:mid_price"].to_numpy(float)
    fairs = {
        4000: np.maximum(s - 4000, 0.0),
        4500: np.maximum(s - 4500, 0.0),
        6000: np.zeros(len(df)),
        6500: np.zeros(len(df)),
    }
    fairs.update(fit_smile_fairs(df))
    return fairs


def level_values(df: pd.DataFrame, product: str, side: str) -> tuple[np.ndarray, np.ndarray]:
    prices = []
    vols = []
    for lvl in (1, 2, 3):
        p = df[f"{product}:{side}_price_{lvl}"].to_numpy(float)
        v = df[f"{product}:{side}_volume_{lvl}"].fillna(0).to_numpy(float)
        prices.append(p)
        vols.append(np.abs(v))
    return np.vstack(prices), np.vstack(vols)


def executable_summary(days: dict[int, pd.DataFrame]) -> list[str]:
    lines = ["EXECUTABLE BUG-ALPHA SCAN", "=" * 72, ""]
    rows = []
    examples = []
    horizons = (1, 5, 20, 100)
    for day, df in days.items():
        fairs = build_fairs(df)
        for k in STRIKES:
            product = f"VEV_{k}"
            fair = fairs[k]
            bidp, bidv = level_values(df, product, "bid")
            askp, askv = level_values(df, product, "ask")
            mid = df[f"{product}:mid_price"].to_numpy(float)
            top_bid = df[f"{product}:bid_price_1"].to_numpy(float)
            top_ask = df[f"{product}:ask_price_1"].to_numpy(float)

            # Best executable bug at any visible level.
            max_sell_edge = np.nanmax(np.where(bidv > 0, bidp - fair, np.nan), axis=0)
            max_buy_edge = np.nanmax(np.where(askv > 0, fair - askp, np.nan), axis=0)
            rows.append(
                {
                    "day": day,
                    "product": product,
                    "sell_edge>=1_ticks": int(np.nansum(max_sell_edge >= 1.0)),
                    "sell_edge>=2_ticks": int(np.nansum(max_sell_edge >= 2.0)),
                    "buy_edge>=1_ticks": int(np.nansum(max_buy_edge >= 1.0)),
                    "buy_edge>=2_ticks": int(np.nansum(max_buy_edge >= 2.0)),
                    "max_sell_edge": float(np.nanmax(max_sell_edge)),
                    "max_buy_edge": float(np.nanmax(max_buy_edge)),
                    "mean_spread": float(np.nanmean(top_ask - top_bid)),
                }
            )

            for side, edge in (("sell", max_sell_edge), ("buy", max_buy_edge)):
                hit = np.where(edge >= 2.0)[0]
                if len(hit):
                    for idx in hit[:3]:
                        examples.append(
                            {
                                "day": day,
                                "ts": int(df.iloc[idx]["timestamp"]),
                                "product": product,
                                "side": side,
                                "edge": float(edge[idx]),
                                "fair": float(fair[idx]),
                                "bid1": float(top_bid[idx]),
                                "ask1": float(top_ask[idx]),
                            }
                        )

            for thresh in (1.0, 2.0, 3.0, 5.0):
                for side, edge in (("sell", max_sell_edge), ("buy", max_buy_edge)):
                    mask = edge >= thresh
                    n = int(np.nansum(mask))
                    if n < 20:
                        continue
                    r = {"day": day, "product": product, "side": side, "thr": thresh, "n": n}
                    idx = np.where(mask)[0]
                    for h in horizons:
                        good = idx[idx + h < len(df)]
                        if len(good) == 0:
                            continue
                        if side == "buy":
                            pnl_mid = mid[good + h] - top_ask[good]
                            pnl_cross = df[f"{product}:bid_price_1"].to_numpy(float)[good + h] - top_ask[good]
                        else:
                            pnl_mid = top_bid[good] - mid[good + h]
                            pnl_cross = top_bid[good] - df[f"{product}:ask_price_1"].to_numpy(float)[good + h]
                        r[f"mid_h{h}"] = float(np.nanmean(pnl_mid))
                        r[f"cross_h{h}"] = float(np.nanmean(pnl_cross))
                    rows.append(r)
    count = pd.DataFrame([r for r in rows if "sell_edge>=1_ticks" in r])
    lines.append("Executable fair-cross counts by product/day:")
    lines.append(count.round(3).to_string(index=False))
    lines.append("")
    econ = pd.DataFrame([r for r in rows if "thr" in r])
    lines.append("Economics if we hit the bug and close later:")
    if econ.empty:
        lines.append("No >=20 sample executable bugs after thresholds.")
    else:
        lines.append(econ.round(3).to_string(index=False))
    lines.append("")
    ex = pd.DataFrame(examples[:80])
    lines.append("First examples with edge>=2:")
    lines.append(ex.round(3).to_string(index=False) if not ex.empty else "none")
    lines.append("")
    return lines


def zero_tail_rank_probe() -> list[str]:
    """Summarize a known rank probe rather than reimplement the matcher."""
    lines = ["Zero-tail queue note:"]
    lines.append("- Public tape has hundreds of VEV_6000/6500 prints at price 0.")
    lines.append("- Existing traders that rest bid=0 still get 0 ranked PnL/trades on those products.")
    lines.append("- Likely reason: bot bid at 0 has queue priority; our same-price bid is behind it.")
    lines.append("- Raising bid to 1 crosses the ask at 1, so in normal rank it burns one tick.")
    lines.append("")
    return lines


def main() -> None:
    days = {d: load_day(d) for d in (0, 1, 2, 3)}
    lines = executable_summary(days)
    lines.extend(zero_tail_rank_probe())
    lines.append("Bottom line:")
    lines.append("- Real bug alpha must hit executable bid/ask, not option mid.")
    lines.append("- Deep ITM flash is the cleanest candidate: sell bid spikes / buy sub-intrinsic asks.")
    lines.append("- Smile IV residual is mostly quote-making alpha because spread kills aggressive takes.")
    lines.append("- Zero-tail @0 is visible in tape but not capturable in current rank without queue priority.")
    text = "\n".join(lines) + "\n"
    OUT.write_text(text)
    print(text)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
