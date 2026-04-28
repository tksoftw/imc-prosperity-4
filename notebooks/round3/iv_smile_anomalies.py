"""Scan ROUND_3 option IV/smile anomalies.

This is deliberately research-only. It asks:

1. Does a leave-one-out smile fit find repeated option mispricings?
2. Do those residuals mean-revert after removing VELVET delta?
3. Does the edge survive crossing top-of-book spread?
4. Are far-wing 0/1 vouchers tradable or just tape decoration?
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "ROUND_3"
OUT = ROOT / "notebooks" / "round3" / "iv_smile_anomaly_report.txt"

VELVET = "VELVETFRUIT_EXTRACT"
STRIKES = (5000, 5100, 5200, 5300, 5400, 5500)
ALL_WINGS = (5500, 6000, 6500)
TTE = 1.0
IV_MIN = 1e-4
IV_MAX = 0.08
N = NormalDist()


@dataclass(frozen=True)
class ResidualRow:
    day: int
    idx: int
    timestamp: int
    strike: int
    spot: float
    bid: float
    ask: float
    mid: float
    spread: float
    iv: float
    smile_iv: float
    fair: float
    delta: float
    resid: float
    resid_iv: float


def bs_call(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return max(s - k, 0.0)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return s * N.cdf(d1) - k * N.cdf(d2)


def bs_delta(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return 1.0 if s > k else 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    return N.cdf(d1)


def bs_vega(s: float, k: float, t: float, sigma: float) -> float:
    if t <= 0 or sigma <= 0:
        return 0.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    return s * N.pdf(d1) * sqrt_t


def implied_vol(s: float, k: float, price: float, seed: float = 0.03) -> float:
    intrinsic = max(s - k, 0.0)
    if price <= intrinsic + 1e-7:
        return IV_MIN
    sigma = seed
    for _ in range(30):
        diff = bs_call(s, k, TTE, sigma) - price
        if abs(diff) < 1e-7:
            break
        vega = bs_vega(s, k, TTE, sigma)
        if vega < 1e-8:
            break
        sigma = min(IV_MAX, max(IV_MIN, sigma - diff / vega))
    return sigma


def load_day(day: int) -> pd.DataFrame:
    path = DATA_DIR / f"prices_round_3_day_{day}.csv"
    df = pd.read_csv(path, sep=";")
    keep = ["day", "timestamp", "product", "bid_price_1", "ask_price_1", "mid_price"]
    df = df[keep].copy()
    df["day"] = day
    return df


def pivot_day(day: int) -> pd.DataFrame:
    raw = load_day(day)
    piv = raw.pivot(index="timestamp", columns="product")
    piv = piv.sort_index()
    flat = pd.DataFrame(index=piv.index)
    for col in ("bid_price_1", "ask_price_1", "mid_price"):
        for product in piv[col].columns:
            flat[f"{product}:{col}"] = piv[col][product]
    flat["day"] = day
    flat["timestamp"] = flat.index.astype(int)
    flat = flat.reset_index(drop=True)
    return flat


def fit_smile_rows(day: int) -> list[ResidualRow]:
    df = pivot_day(day)
    out: list[ResidualRow] = []
    prev_iv_seed = {k: 0.03 for k in STRIKES}

    for idx, row in df.iterrows():
        spot = float(row[f"{VELVET}:mid_price"])
        if not np.isfinite(spot) or spot <= 0:
            continue

        obs: list[tuple[int, float, float, float, float, float, float]] = []
        for strike in STRIKES:
            product = f"VEV_{strike}"
            bid = float(row[f"{product}:bid_price_1"])
            ask = float(row[f"{product}:ask_price_1"])
            mid = float(row[f"{product}:mid_price"])
            if not all(np.isfinite(v) for v in (bid, ask, mid)) or ask <= bid:
                continue
            iv = implied_vol(spot, strike, mid, prev_iv_seed[strike])
            prev_iv_seed[strike] = iv
            x = (strike - spot) / 100.0
            spread = ask - bid
            weight = 1.0 / max(spread, 1.0)
            obs.append((strike, x, iv, bid, ask, mid, weight))

        fit_obs = [o for o in obs if o[2] > 0.005]
        if len(fit_obs) < 4:
            continue

        for strike, x, iv, bid, ask, mid, _weight in obs:
            others = [o for o in fit_obs if o[0] != strike]
            if len(others) < 4:
                others = fit_obs
            xs = np.array([o[1] for o in others], dtype=float)
            ys = np.array([o[2] for o in others], dtype=float)
            ws = np.array([o[6] for o in others], dtype=float)
            # Quadratic is enough to capture skew/curvature without chasing noise.
            coeff = np.polyfit(xs, ys, 2, w=np.sqrt(ws))
            smile_iv = float(np.polyval(coeff, x))
            smile_iv = min(IV_MAX, max(IV_MIN, smile_iv))
            fair = bs_call(spot, strike, TTE, smile_iv)
            delta = bs_delta(spot, strike, TTE, smile_iv)
            out.append(
                ResidualRow(
                    day=day,
                    idx=int(idx),
                    timestamp=int(row["timestamp"]),
                    strike=strike,
                    spot=spot,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    spread=ask - bid,
                    iv=iv,
                    smile_iv=smile_iv,
                    fair=fair,
                    delta=delta,
                    resid=mid - fair,
                    resid_iv=iv - smile_iv,
                )
            )
    return out


def rows_to_frame(rows: Iterable[ResidualRow]) -> pd.DataFrame:
    return pd.DataFrame([r.__dict__ for r in rows])


def future_columns(resid: pd.DataFrame, days: dict[int, pd.DataFrame], horizons: tuple[int, ...]) -> pd.DataFrame:
    res = resid.copy()
    for h in horizons:
        res[f"spot_f{h}"] = np.nan
        res[f"mid_f{h}"] = np.nan
        res[f"bid_f{h}"] = np.nan
        res[f"ask_f{h}"] = np.nan

    for day, day_res in res.groupby("day"):
        px = days[int(day)]
        for strike, group in day_res.groupby("strike"):
            product = f"VEV_{int(strike)}"
            idxs = group["idx"].to_numpy(dtype=int)
            for h in horizons:
                valid = idxs + h < len(px)
                if not valid.any():
                    continue
                target = idxs[valid] + h
                locs = group.index.to_numpy()[valid]
                res.loc[locs, f"spot_f{h}"] = px.iloc[target][f"{VELVET}:mid_price"].to_numpy()
                res.loc[locs, f"mid_f{h}"] = px.iloc[target][f"{product}:mid_price"].to_numpy()
                res.loc[locs, f"bid_f{h}"] = px.iloc[target][f"{product}:bid_price_1"].to_numpy()
                res.loc[locs, f"ask_f{h}"] = px.iloc[target][f"{product}:ask_price_1"].to_numpy()
    return res


def summarize_residuals(res: pd.DataFrame, horizons: tuple[int, ...]) -> list[str]:
    lines: list[str] = []
    lines.append("IV/SMILE ANOMALY SCAN")
    lines.append("=" * 72)
    lines.append(f"Rows: {len(res):,} option/tick residuals, strikes {list(STRIKES)}, days {sorted(res.day.unique())}")
    lines.append("Model: per-tick weighted quadratic IV smile, leave-one-strike-out.")
    lines.append("")

    q = res.groupby("strike").agg(
        n=("resid", "size"),
        mean_resid=("resid", "mean"),
        std_resid=("resid", "std"),
        p95_abs_resid=("resid", lambda s: float(np.percentile(np.abs(s), 95))),
        mean_spread=("spread", "mean"),
        mean_iv=("iv", "mean"),
        mean_smile_iv=("smile_iv", "mean"),
    )
    lines.append("Residual size by strike (mid - leave-one-out smile fair):")
    lines.append(q.round(4).to_string())
    lines.append("")

    for h in horizons:
        mid_pnl = -np.sign(res["resid"]) * (res[f"mid_f{h}"] - res["mid"])
        hedge_pnl = -np.sign(res["resid"]) * (
            (res[f"mid_f{h}"] - res["mid"]) - res["delta"] * (res[f"spot_f{h}"] - res["spot"])
        )
        cross_pnl = np.where(
            res["resid"] > 0,
            res["bid"] - res[f"ask_f{h}"],
            res[f"bid_f{h}"] - res["ask"],
        )
        res[f"mid_fade_pnl_{h}"] = mid_pnl
        res[f"hedged_fade_pnl_{h}"] = hedge_pnl
        res[f"cross_fade_pnl_{h}"] = cross_pnl

    lines.append("Fade residual edge by threshold, all strikes pooled:")
    summary_rows = []
    for thr in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
        m = res[np.abs(res["resid"]) >= thr]
        if m.empty:
            continue
        row = {"thr": thr, "n": len(m), "avg_abs": m["resid"].abs().mean()}
        for h in (1, 5, 20):
            row[f"mid_h{h}"] = m[f"mid_fade_pnl_{h}"].mean()
            row[f"hedged_h{h}"] = m[f"hedged_fade_pnl_{h}"].mean()
            row[f"cross_h{h}"] = m[f"cross_fade_pnl_{h}"].mean()
        summary_rows.append(row)
    lines.append(pd.DataFrame(summary_rows).round(3).to_string(index=False))
    lines.append("")
    lines.append("Interpretation: mid_h is paper fade; hedged_h removes VELVET delta; cross_h pays spread both ways.")
    lines.append("")

    lines.append("Best per-strike thresholds by delta-neutral mid fade (h=5):")
    best_rows = []
    for strike, g in res.groupby("strike"):
        for thr in (0.5, 1.0, 1.5, 2.0, 3.0):
            m = g[np.abs(g["resid"]) >= thr]
            if len(m) < 50:
                continue
            best_rows.append(
                {
                    "strike": int(strike),
                    "thr": thr,
                    "n": len(m),
                    "hedged_h1": m["hedged_fade_pnl_1"].mean(),
                    "hedged_h5": m["hedged_fade_pnl_5"].mean(),
                    "cross_h5": m["cross_fade_pnl_5"].mean(),
                    "hit_h5": (m["hedged_fade_pnl_5"] > 0).mean(),
                }
            )
    best = pd.DataFrame(best_rows)
    if not best.empty:
        idx = best.groupby("strike")["hedged_h5"].idxmax()
        lines.append(best.loc[idx].sort_values("strike").round(3).to_string(index=False))
    lines.append("")

    # Directional diagnostics: if signs are highly aligned with VELVET move,
    # it is not pure IV; if hedged fade survives, smile anomaly has teeth.
    for h in (1, 5, 20):
        m = res[np.abs(res["resid"]) >= 1.0].copy()
        signed_spot = -np.sign(m["resid"]) * (m[f"spot_f{h}"] - m["spot"])
        lines.append(
            f"Residual>=1, h={h}: signed spot move mean {signed_spot.mean():.4f}, "
            f"paper option fade {m[f'mid_fade_pnl_{h}'].mean():.4f}, "
            f"delta-neutral fade {m[f'hedged_fade_pnl_{h}'].mean():.4f}, "
            f"cross fade {m[f'cross_fade_pnl_{h}'].mean():.4f}"
        )
    lines.append("")

    return lines


def summarize_smile_drift(res: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    lines.append("Smile level/drift by day:")
    level = res.groupby(["day", "strike"]).agg(
        iv_mean=("iv", "mean"),
        iv_first=("iv", lambda s: float(s.iloc[:100].mean())),
        iv_last=("iv", lambda s: float(s.iloc[-100:].mean())),
        resid_abs=("resid", lambda s: float(np.mean(np.abs(s)))),
    )
    level["iv_last_minus_first"] = level["iv_last"] - level["iv_first"]
    lines.append(level.round(5).to_string())
    lines.append("")

    # Local vol level vs future delta-neutral returns.
    live_iv = res[res["iv"] > 0.005]
    base = live_iv.groupby(["day", "idx"])["iv"].mean().rename("surface_iv").reset_index()
    base["iv_ema"] = base.groupby("day")["surface_iv"].transform(lambda s: s.ewm(span=200, adjust=False).mean())
    base["iv_z"] = base.groupby("day")["surface_iv"].transform(
        lambda s: (s - s.rolling(500, min_periods=100).mean()) / s.rolling(500, min_periods=100).std()
    )
    joined = res.merge(base, on=["day", "idx"], how="left")
    lines.append("Surface IV regime buckets vs h=20 delta-neutral long-option PnL:")
    # Long option delta-hedged one lot at mid. Positive means gamma/vol long paid.
    joined["long_hedged_h20"] = (joined["mid_f20"] - joined["mid"]) - joined["delta"] * (joined["spot_f20"] - joined["spot"])
    joined["iv_bucket"] = pd.cut(joined["iv_z"], [-np.inf, -1.0, -0.25, 0.25, 1.0, np.inf])
    bucket = joined.groupby("iv_bucket", observed=True).agg(
        n=("long_hedged_h20", "size"),
        mean_long_hedged=("long_hedged_h20", "mean"),
        hit=("long_hedged_h20", lambda s: float((s > 0).mean())),
        mean_surface_iv=("surface_iv", "mean"),
    )
    lines.append(bucket.round(4).to_string())
    lines.append("")

    # Whole-surface mean reversion: shift the current fitted smile toward a
    # slow EMA of surface IV, then ask if that model edge clears top-of-book.
    valid = joined.dropna(subset=["surface_iv", "iv_ema", "spot_f20", "mid_f20"]).copy()
    valid["surface_shift"] = valid["iv_ema"] - valid["surface_iv"]
    fair_regime = []
    for row in valid.itertuples(index=False):
        target_iv = min(IV_MAX, max(IV_MIN, row.smile_iv + row.surface_shift))
        fair_regime.append(bs_call(row.spot, row.strike, TTE, target_iv))
    valid["regime_fair"] = fair_regime
    valid["buy_edge"] = valid["regime_fair"] - valid["ask"]
    valid["sell_edge"] = valid["bid"] - valid["regime_fair"]
    lines.append("Surface-level IV mean-reversion economics:")
    rows = []
    for edge in (0.0, 0.5, 1.0, 2.0, 3.0):
        buys = valid[valid["buy_edge"] >= edge]
        sells = valid[valid["sell_edge"] >= edge]
        for side, m in (("buy_low_iv", buys), ("sell_high_iv", sells)):
            if len(m) < 50:
                continue
            if side == "buy_low_iv":
                cross = m["bid_f20"] - m["ask"]
                hedged = cross - m["delta"] * (m["spot_f20"] - m["spot"])
            else:
                cross = m["bid"] - m["ask_f20"]
                hedged = cross + m["delta"] * (m["spot_f20"] - m["spot"])
            rows.append(
                {
                    "side": side,
                    "model_edge": edge,
                    "n": len(m),
                    "cross_h20": cross.mean(),
                    "hedged_cross_h20": hedged.mean(),
                    "hit": (hedged > 0).mean(),
                    "avg_surface_shift": m["surface_shift"].mean(),
                }
            )
    lines.append(pd.DataFrame(rows).round(4).to_string(index=False))
    lines.append("")
    return lines


def summarize_wings(days: dict[int, pd.DataFrame]) -> list[str]:
    lines: list[str] = []
    lines.append("Far-wing 0/1 diagnostics:")
    rows = []
    for day, df in days.items():
        for strike in ALL_WINGS:
            product = f"VEV_{strike}"
            bid = df[f"{product}:bid_price_1"]
            ask = df[f"{product}:ask_price_1"]
            mid = df[f"{product}:mid_price"]
            rows.append(
                {
                    "day": day,
                    "strike": strike,
                    "n": len(df),
                    "bid0_pct": float((bid == 0).mean()),
                    "ask1_pct": float((ask == 1).mean()),
                    "mid_le_0.5_pct": float((mid <= 0.5).mean()),
                    "mid_mean": float(mid.mean()),
                    "spread_mean": float((ask - bid).mean()),
                }
            )
    lines.append(pd.DataFrame(rows).round(4).to_string(index=False))

    trade_rows = []
    for day in days:
        tpath = DATA_DIR / f"trades_round_3_day_{day}.csv"
        if not tpath.exists():
            continue
        tr = pd.read_csv(tpath, sep=";")
        for strike in (5400, 5500, 6000, 6500):
            product = f"VEV_{strike}"
            g = tr[tr["symbol"] == product]
            trade_rows.append(
                {
                    "day": day,
                    "product": product,
                    "n_trades": len(g),
                    "qty": int(g["quantity"].sum()) if not g.empty else 0,
                    "price0_qty": int(g.loc[g["price"] == 0, "quantity"].sum()) if not g.empty else 0,
                    "price1_qty": int(g.loc[g["price"] == 1, "quantity"].sum()) if not g.empty else 0,
                    "basket_ts": int(g["timestamp"].nunique()) if not g.empty else 0,
                }
            )
    lines.append("")
    lines.append("Tape prints on wing vouchers (passive 0 fills are not captured by normal rank):")
    lines.append(pd.DataFrame(trade_rows).to_string(index=False))
    lines.append("")
    return lines


def main() -> None:
    days = {day: pivot_day(day) for day in (0, 1, 2, 3)}
    all_rows: list[ResidualRow] = []
    for day in days:
        all_rows.extend(fit_smile_rows(day))
    res = rows_to_frame(all_rows)
    horizons = (1, 2, 5, 10, 20, 50)
    res = future_columns(res, days, horizons)

    lines: list[str] = []
    lines.extend(summarize_residuals(res, horizons))
    lines.extend(summarize_smile_drift(res))
    lines.extend(summarize_wings(days))

    lines.append("Bottom line:")
    lines.append("- Smile residuals exist, but crossing spread is the gatekeeper.")
    lines.append("- If delta-neutral fade is positive while cross fade is negative, trade it by quoting, not by panic-taking.")
    lines.append("- If long-option hedged PnL is only positive in low-IV buckets, that is gamma scalp entry filter.")
    lines.append("- Far-wing @0 prints are real tape events, but normal rank only rewards them if our resting bid can be hit.")

    text = "\n".join(lines) + "\n"
    OUT.write_text(text)
    print(text)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
