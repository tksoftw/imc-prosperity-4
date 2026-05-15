from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import math

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "ROUND_5"
OUT_DIR = ROOT / "notebooks" / "ROUND_5"
DAYS = [2, 3, 4]

FAMILY_PREFIXES = [
    "GALAXY_SOUNDS",
    "MICROCHIP",
    "OXYGEN_SHAKE",
    "PANEL",
    "PEBBLES",
    "ROBOT",
    "SLEEP_POD",
    "SNACKPACK",
    "TRANSLATOR",
    "UV_VISOR",
]


@dataclass
class Regression:
    intercept: float
    betas: np.ndarray
    residual: np.ndarray
    r2: float


def family(product: str) -> str:
    for prefix in FAMILY_PREFIXES:
        if product.startswith(prefix + "_"):
            return prefix
    return product.split("_", 1)[0]


def fit_ols(y: Iterable[float], x: np.ndarray) -> Regression:
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    if x_arr.ndim == 1:
        x_arr = x_arr[:, None]
    design = np.c_[np.ones(len(x_arr)), x_arr]
    coef, *_ = np.linalg.lstsq(design, y_arr, rcond=None)
    pred = design @ coef
    residual = y_arr - pred
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((y_arr - y_arr.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot else float("nan")
    return Regression(float(coef[0]), coef[1:], residual, r2)


def corr(a: pd.Series | np.ndarray, b: pd.Series | np.ndarray) -> float:
    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    mask = np.isfinite(a_arr) & np.isfinite(b_arr)
    if mask.sum() < 3:
        return float("nan")
    return float(np.corrcoef(a_arr[mask], b_arr[mask])[0, 1])


def load_mid_prices() -> pd.DataFrame:
    frames = []
    for day in DAYS:
        prices = pd.read_csv(DATA_DIR / f"prices_round_5_day_{day}.csv", sep=";")
        frames.append(prices[["day", "timestamp", "product", "mid_price"]])
    raw = pd.concat(frames, ignore_index=True)
    mids = raw.pivot_table(index=["day", "timestamp"], columns="product", values="mid_price")
    return mids.sort_index().dropna(axis=1, how="any")


def consecutive_pairs(series: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    left = []
    right = []
    for _, day_series in series.groupby(level=0):
        values = day_series.to_numpy(dtype=float)
        if len(values) > 1:
            left.extend(values[:-1])
            right.extend(values[1:])
    return np.asarray(left), np.asarray(right)


def next_values(series: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    now = []
    nxt = []
    for _, day_series in series.groupby(level=0):
        values = day_series.to_numpy(dtype=float)
        if len(values) > 1:
            now.extend(values[:-1])
            nxt.extend(values[1:])
    return np.asarray(now), np.asarray(nxt)


def lag_corr(returns: pd.DataFrame, leader: str, follower: str, lag: int) -> float:
    xs = []
    ys = []
    for _, day_returns in returns.groupby(level=0):
        x = day_returns[leader].to_numpy(dtype=float)
        y = day_returns[follower].to_numpy(dtype=float)
        if len(x) > lag:
            xs.extend(x[:-lag])
            ys.extend(y[lag:])
    return corr(np.asarray(xs), np.asarray(ys))


def scan_pairs(mids: pd.DataFrame) -> pd.DataFrame:
    products = list(mids.columns)
    log_mid = np.log(mids)
    log_ret = log_mid.groupby(level=0).diff().dropna()
    rows = []
    candidate_pairs = [
        (target, reference)
        for target in products
        for reference in products
        if target != reference and family(target) == family(reference)
    ]

    for target, reference in candidate_pairs:
            reg = fit_ols(log_mid[target], log_mid[reference])
            spread = pd.Series(reg.residual, index=log_mid.index)
            spread_std = float(spread.std())
            z = spread / spread_std if spread_std else spread * float("nan")

            s_now, s_next = next_values(spread)
            z_now, _ = next_values(z)
            ds_next = s_next - s_now
            rev = fit_ols(ds_next, z_now) if len(ds_next) else Regression(float("nan"), np.array([float("nan")]), np.array([]), float("nan"))
            rev_bps_per_z = float(rev.betas[0] * 10_000.0)
            rev_t = float("nan")
            if len(rev.residual) > 3:
                x = z_now - z_now.mean()
                se = math.sqrt(np.sum(rev.residual**2) / (len(x) - 2) / np.sum(x**2)) if np.sum(x**2) else float("nan")
                rev_t = float(rev.betas[0] / se) if se and np.isfinite(se) else float("nan")

            s_lag, s_cur = consecutive_pairs(spread)
            ar = fit_ols(s_cur, s_lag).betas[0] if len(s_lag) else float("nan")
            half_life = float("nan")
            if 0.0 < ar < 1.0:
                half_life = float(math.log(0.5) / math.log(ar))

            extreme = np.abs(z_now) >= 2.0
            extreme_count = int(extreme.sum())
            extreme_reversion_bps = float("nan")
            if extreme_count:
                extreme_reversion_bps = float(np.mean(-np.sign(z_now[extreme]) * ds_next[extreme]) * 10_000.0)

            same_ret_corr = corr(log_ret[target], log_ret[reference])
            lead_candidates = []
            for lag in [1, 2, 3]:
                ref_leads = lag_corr(log_ret, reference, target, lag)
                target_leads = lag_corr(log_ret, target, reference, lag)
                lead_candidates.append((abs(ref_leads), ref_leads, f"{reference}-> {target} lag{lag}"))
                lead_candidates.append((abs(target_leads), target_leads, f"{target}-> {reference} lag{lag}"))
            best_abs_lead, best_lead_corr, best_lead = max(lead_candidates, key=lambda item: item[0])

            beta = float(reg.betas[0])
            high_spread_action = (
                f"SELL {target}, {'BUY' if beta > 0 else 'SELL'} {abs(beta):.3f}x {reference}"
            )
            low_spread_action = (
                f"BUY {target}, {'SELL' if beta > 0 else 'BUY'} {abs(beta):.3f}x {reference}"
            )

            rows.append(
                {
                    "target": target,
                    "reference": reference,
                    "target_family": family(target),
                    "reference_family": family(reference),
                    "same_family": True,
                    "log_beta": beta,
                    "direction": "same" if beta > 0 else "reversed",
                    "high_spread_action": high_spread_action,
                    "low_spread_action": low_spread_action,
                    "log_intercept": reg.intercept,
                    "log_r2": reg.r2,
                    "spread_std_bps": spread_std * 10_000.0,
                    "ar1": float(ar),
                    "half_life_ticks": half_life,
                    "reversion_bps_per_z_next_tick": rev_bps_per_z,
                    "reversion_t": rev_t,
                    "z2_count": extreme_count,
                    "z2_next_tick_reversion_bps": extreme_reversion_bps,
                    "log_return_corr": same_ret_corr,
                    "abs_log_return_corr": abs(same_ret_corr),
                    "best_lead": best_lead,
                    "best_lead_corr": best_lead_corr,
                    "abs_best_lead_corr": best_abs_lead,
                }
            )

    out = pd.DataFrame(rows)
    out["pair_score"] = (
        out["same_family"].astype(float) * 0.75
        + out["log_r2"].clip(lower=0).fillna(0)
        + out["abs_log_return_corr"].fillna(0)
        + (-out["reversion_bps_per_z_next_tick"]).clip(lower=0).fillna(0) / 10.0
        + out["z2_count"].clip(upper=500).fillna(0) / 5000.0
    )
    return out.sort_values("pair_score", ascending=False)


def scan_group_combos(mids: pd.DataFrame) -> pd.DataFrame:
    rows = []
    centered = mids - 10_000.0
    products = list(mids.columns)
    for fam in sorted({family(product) for product in products}):
        group_products = [product for product in products if family(product) == fam]
        if len(group_products) < 3:
            continue
        for target in group_products:
            references = [product for product in group_products if product != target]
            centered_reg = fit_ols(centered[target], centered[references])
            log_reg = fit_ols(np.log(mids[target]), np.log(mids[references]))
            rows.append(
                {
                    "family": fam,
                    "target": target,
                    "references": ", ".join(references),
                    "centered_intercept": centered_reg.intercept,
                    "centered_betas": ", ".join(f"{ref}:{beta:.4f}" for ref, beta in zip(references, centered_reg.betas)),
                    "centered_r2": centered_reg.r2,
                    "centered_resid_std": float(pd.Series(centered_reg.residual).std()),
                    "log_intercept": log_reg.intercept,
                    "log_betas": ", ".join(f"{ref}:{beta:.4f}" for ref, beta in zip(references, log_reg.betas)),
                    "log_r2": log_reg.r2,
                    "log_resid_std_bps": float(pd.Series(log_reg.residual).std() * 10_000.0),
                }
            )
    return pd.DataFrame(rows).sort_values(["centered_r2", "log_r2"], ascending=False)


def markdown_table(df: pd.DataFrame, columns: list[str], limit: int) -> str:
    shown = df.loc[:, columns].head(limit).copy()
    for col in shown.columns:
        if pd.api.types.is_float_dtype(shown[col]):
            shown[col] = shown[col].map(lambda value: "" if pd.isna(value) else f"{value:.4f}")
    headers = list(shown.columns)
    rows = [[str(value) for value in row] for row in shown.to_numpy()]
    widths = [
        max(len(header), *(len(row[idx]) for row in rows)) if rows else len(header)
        for idx, header in enumerate(headers)
    ]
    header_line = "| " + " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[idx] for idx in range(len(headers))) + " |"
    row_lines = [
        "| " + " | ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line, *row_lines])


def write_report(pair_scan: pd.DataFrame, combo_scan: pd.DataFrame, path: Path) -> None:
    pebbles_pairs = pair_scan[
        pair_scan["same_family"]
        & pair_scan["target"].str.startswith("PEBBLES_")
        & pair_scan["reference"].str.startswith("PEBBLES_")
    ]
    same_family = pair_scan[pair_scan["same_family"]]
    lead_lag = pair_scan.sort_values("abs_best_lead_corr", ascending=False)
    ret_corr = pair_scan.sort_values("abs_log_return_corr", ascending=False)

    content = [
        "# Round 5 Pair Correlation Scan",
        "",
        "Method: directed log-price regressions of `log(target) = a + beta * log(reference)`, plus log-return correlation, residual AR(1), next-tick z-score reversion, and lead-lag correlations. A high-spread signal means the target is rich versus the fitted reference.",
        "",
        "## Pebbles Pair Fits",
        markdown_table(
            pebbles_pairs,
            [
                "target",
                "reference",
                "direction",
                "log_beta",
                "log_r2",
                "spread_std_bps",
                "log_return_corr",
                "half_life_ticks",
                "z2_next_tick_reversion_bps",
                "high_spread_action",
            ],
            20,
        ),
        "",
        "## Best Same-Family Pairs",
        markdown_table(
            same_family,
            [
                "target",
                "reference",
                "direction",
                "log_beta",
                "log_r2",
                "spread_std_bps",
                "log_return_corr",
                "z2_count",
                "z2_next_tick_reversion_bps",
                "high_spread_action",
            ],
            30,
        ),
        "",
        "## Strongest Same-Tick Return Correlations",
        markdown_table(
            ret_corr,
            ["target", "reference", "target_family", "reference_family", "log_return_corr", "log_beta", "log_r2"],
            30,
        ),
        "",
        "## Strongest Lead-Lag Candidates",
        markdown_table(
            lead_lag,
            ["target", "reference", "best_lead", "best_lead_corr", "log_return_corr", "log_r2"],
            30,
        ),
        "",
        "## Best Same-Family Multi-Name Combos",
        markdown_table(
            combo_scan,
            [
                "family",
                "target",
                "references",
                "centered_r2",
                "centered_resid_std",
                "centered_betas",
                "log_r2",
                "log_betas",
            ],
            30,
        ),
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mids = load_mid_prices()
    pair_scan = scan_pairs(mids)
    combo_scan = scan_group_combos(mids)

    pair_path = OUT_DIR / "pair_correlation_scan.csv"
    combo_path = OUT_DIR / "group_combo_scan.csv"
    report_path = OUT_DIR / "pair_correlation_report.md"
    pair_scan.to_csv(pair_path, index=False)
    combo_scan.to_csv(combo_path, index=False)
    write_report(pair_scan, combo_scan, report_path)

    print(f"wrote {pair_path}")
    print(f"wrote {combo_path}")
    print(f"wrote {report_path}")
    print()
    print("Top Pebbles directed pair fits:")
    pebbles = pair_scan[
        pair_scan["same_family"]
        & pair_scan["target"].str.startswith("PEBBLES_")
        & pair_scan["reference"].str.startswith("PEBBLES_")
    ]
    print(
        pebbles[
            [
                "target",
                "reference",
                "direction",
                "log_beta",
                "log_r2",
                "log_return_corr",
                "spread_std_bps",
                "high_spread_action",
            ]
        ]
        .head(12)
        .to_string(index=False)
    )
    print()
    print("Top same-family group combos:")
    print(
        combo_scan[
            ["family", "target", "centered_r2", "centered_resid_std", "centered_betas"]
        ]
        .head(12)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
