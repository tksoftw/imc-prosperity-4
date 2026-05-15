from __future__ import annotations

from pathlib import Path
import itertools

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data" / "ROUND_5"
OUT_DIR = ROOT / "notebooks" / "ROUND_5"
PEBBLES = ["PEBBLES_L", "PEBBLES_M", "PEBBLES_S", "PEBBLES_XL", "PEBBLES_XS"]
DAYS = [2, 3, 4]


def load_prices() -> pd.DataFrame:
    frames = []
    for day in DAYS:
        prices = pd.read_csv(DATA_DIR / f"prices_round_5_day_{day}.csv", sep=";")
        piv = prices[prices["product"].isin(PEBBLES)].pivot(
            index="timestamp", columns="product", values="mid_price"
        )[PEBBLES]
        piv["day"] = day
        frames.append(piv.reset_index().set_index(["day", "timestamp"]))
    return pd.concat(frames).sort_index()


def summarize_trends(prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for day, day_prices in prices.groupby(level=0):
        for product in PEBBLES:
            series = day_prices[product]
            rows.append(
                {
                    "day": day,
                    "product": product,
                    "start": series.iloc[0],
                    "end": series.iloc[-1],
                    "change": series.iloc[-1] - series.iloc[0],
                    "min": series.min(),
                    "max": series.max(),
                    "mean": series.mean(),
                    "std": series.std(),
                }
            )
    return pd.DataFrame(rows)


def pca_table(matrix: np.ndarray, names: list[str], label: str) -> pd.DataFrame:
    covariance = np.cov(matrix, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(covariance)
    order = np.argsort(eigvals)[::-1]
    total = float(eigvals.sum())
    rows = []
    for rank, idx in enumerate(order, start=1):
        row = {
            "space": label,
            "component": rank,
            "eigenvalue": eigvals[idx],
            "explained": eigvals[idx] / total if total else np.nan,
        }
        for product, value in zip(names, eigvecs[:, idx]):
            row[product] = value
        rows.append(row)
    return pd.DataFrame(rows)


def markowitz_weights(prices: pd.DataFrame) -> pd.DataFrame:
    day_changes = []
    for _, day_prices in prices.groupby(level=0):
        day_changes.append((day_prices.iloc[-1] - day_prices.iloc[0]).to_numpy())
    day_changes = np.vstack(day_changes)
    avg_change = day_changes.mean(axis=0)
    returns = prices.groupby(level=0).diff().dropna().to_numpy()
    covariance = np.cov(returns, rowvar=False)
    weights = np.linalg.pinv(covariance) @ avg_change
    weights = weights - weights.mean()
    weights = weights / np.max(np.abs(weights))
    return pd.DataFrame(
        {
            "product": PEBBLES,
            "avg_day_change": avg_change,
            "risk_adjusted_weight": weights,
            "target_at_240": np.round(weights * 240).astype(int),
        }
    )


def scan_integer_portfolios(prices: pd.DataFrame) -> pd.DataFrame:
    returns = prices.groupby(level=0).diff().dropna()
    rows = []
    seen = set()
    for raw_weights in itertools.product(range(-3, 4), repeat=len(PEBBLES)):
        weights = np.asarray(raw_weights, dtype=float)
        if not np.any(weights) or np.all(weights >= 0) or np.all(weights <= 0):
            continue
        if np.sum(np.abs(weights)) > 8:
            continue

        # Adding the same weight to every Pebble is almost pure constant because
        # their sum is pinned near 50000. Canonicalize to a demeaned vector.
        normalized = weights - weights.mean()
        scale = np.max(np.abs(normalized))
        if not scale:
            continue
        normalized = np.round(normalized / scale, 4)
        key = tuple(normalized)
        neg_key = tuple(-normalized)
        if key in seen or neg_key in seen:
            continue
        seen.add(key)

        portfolio = (prices[PEBBLES] * normalized).sum(axis=1)
        portfolio_returns = (returns[PEBBLES] * normalized).sum(axis=1)
        day_changes = []
        for _, day_portfolio in portfolio.groupby(level=0):
            day_changes.append(day_portfolio.iloc[-1] - day_portfolio.iloc[0])
        total_change = float(sum(day_changes))
        ret_std = float(portfolio_returns.std())
        if ret_std == 0:
            continue
        if total_change < 0:
            normalized = -normalized
            day_changes = [-value for value in day_changes]
            total_change = -total_change

        rows.append(
            {
                "weights": ", ".join(f"{product}:{weight:.3f}" for product, weight in zip(PEBBLES, normalized)),
                "total_change": total_change,
                "ret_std": ret_std,
                "change_per_ret_std": total_change / ret_std,
                "d2": day_changes[0],
                "d3": day_changes[1],
                "d4": day_changes[2],
                "same_sign_days": all(value > 0 for value in day_changes),
            }
        )
    return pd.DataFrame(rows).sort_values("change_per_ret_std", ascending=False)


def markdown_table(df: pd.DataFrame, limit: int) -> str:
    shown = df.head(limit).copy()
    for col in shown.columns:
        if pd.api.types.is_float_dtype(shown[col]):
            shown[col] = shown[col].map(lambda value: f"{value:.4f}")
    headers = list(shown.columns)
    rows = [[str(value) for value in row] for row in shown.to_numpy()]
    widths = [max(len(header), *(len(row[idx]) for row in rows)) for idx, header in enumerate(headers)]
    header_line = "| " + " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * widths[idx] for idx in range(len(headers))) + " |"
    row_lines = ["| " + " | ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))) + " |" for row in rows]
    return "\n".join([header_line, sep_line, *row_lines])


def write_report(
    trend: pd.DataFrame,
    pca: pd.DataFrame,
    weights: pd.DataFrame,
    portfolios: pd.DataFrame,
    path: Path,
) -> None:
    sum_notes = [
        "- `PEBBLES_XS` is down on all three days.",
        "- `PEBBLES_S` is also down on all three days.",
        "- `PEBBLES_XL` is not straight down: it is up hard on days 2 and 4, down on day 3, and is the dominant opposite factor.",
        "- The near-identity remains `L + M + S + XL + XS ~= 50000`; the standard deviation of the sum is about 2.8 ticks.",
        "- Return PCA's largest component is basically `XL` versus an equal basket of the other four Pebbles.",
    ]
    content = [
        "# Pebbles Factor Scan",
        "",
        "## Takeaways",
        *sum_notes,
        "",
        "## Daily Trends",
        markdown_table(trend, 20),
        "",
        "## PCA",
        markdown_table(pca, 10),
        "",
        "## Risk-Adjusted Drift Weights",
        markdown_table(weights, 10),
        "",
        "## Integer Factor Portfolios",
        markdown_table(portfolios[portfolios["same_sign_days"]], 20),
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prices = load_prices()
    trend = summarize_trends(prices)
    centered_pca = pca_table((prices[PEBBLES] - 10000).to_numpy(), PEBBLES, "centered_price")
    return_pca = pca_table(prices.groupby(level=0).diff().dropna().to_numpy(), PEBBLES, "tick_return")
    pca = pd.concat([centered_pca, return_pca], ignore_index=True)
    weights = markowitz_weights(prices)
    portfolios = scan_integer_portfolios(prices)

    trend.to_csv(OUT_DIR / "pebbles_daily_trends.csv", index=False)
    pca.to_csv(OUT_DIR / "pebbles_pca.csv", index=False)
    weights.to_csv(OUT_DIR / "pebbles_drift_weights.csv", index=False)
    portfolios.to_csv(OUT_DIR / "pebbles_integer_factor_scan.csv", index=False)
    write_report(trend, pca, weights, portfolios, OUT_DIR / "pebbles_factor_report.md")
    print(f"wrote {OUT_DIR / 'pebbles_factor_report.md'}")
    print("Risk-adjusted drift weights:")
    print(weights.to_string(index=False))
    print()
    print("Top persistent integer factors:")
    print(portfolios[portfolios["same_sign_days"]].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
