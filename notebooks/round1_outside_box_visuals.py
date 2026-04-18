from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns


ROOT = Path("/home/tk/imc-prosperity-4")
OUTPUT_DIR = ROOT / "notebooks" / "round1_visuals"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_prices() -> pd.DataFrame:
    frames = [pd.read_csv(path, sep=";") for path in sorted((ROOT / "data/ROUND1").glob("prices_round_1_day_*.csv"))]
    prices = pd.concat(frames, ignore_index=True)
    for column in prices.columns:
        if column != "product":
            prices[column] = pd.to_numeric(prices[column], errors="coerce")
    prices = prices.dropna(subset=["bid_price_1", "ask_price_1"]).copy()
    prices["mid"] = (prices["bid_price_1"] + prices["ask_price_1"]) / 2
    prices["spread"] = prices["ask_price_1"] - prices["bid_price_1"]
    prices["step"] = prices["timestamp"] / 100
    return prices.sort_values(["product", "day", "timestamp"])


def plot_pepper_drift(prices: pd.DataFrame) -> None:
    pepper = prices[prices["product"] == "INTARIAN_PEPPER_ROOT"].copy()
    sns.set_theme(style="whitegrid")
    fig, axes = plt.subplots(3, 1, figsize=(13, 12), sharex=True)

    for ax, day in zip(axes, sorted(pepper["day"].unique())):
        day_data = pepper[pepper["day"] == day].copy()
        step = day_data["step"]
        mid = day_data["mid"]
        slope = ((step - step.mean()) * (mid - mid.mean())).sum() / ((step - step.mean()) ** 2).sum()
        intercept = mid.mean() - slope * step.mean()
        fair = intercept + slope * step

        ax.plot(day_data["timestamp"], mid, color="#004488", linewidth=1.4, label="Mid Price")
        ax.plot(day_data["timestamp"], fair, color="#D55E00", linewidth=1.6, label="Drift Fit")
        ax.fill_between(
            day_data["timestamp"],
            fair + 8,
            fair + 13,
            color="#EECC66",
            alpha=0.25,
            label="Passive Sell Zone",
        )
        ax.set_title(f"Pepper Root Day {int(day)}: deterministic drift plus optional sell band")
        ax.set_ylabel("Price")
        ax.legend(loc="upper left")

    axes[-1].set_xlabel("Timestamp")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "pepper_drift_and_sell_band.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def plot_mean_reversion(prices: pd.DataFrame) -> None:
    enriched = prices.copy()
    enriched["ret"] = enriched.groupby(["product", "day"])["mid"].diff()
    enriched["next_ret"] = enriched.groupby(["product", "day"])["ret"].shift(-1)
    curve = (
        enriched.dropna(subset=["ret", "next_ret"])
        .groupby(["product", "ret"], as_index=False)
        .agg(next_ret_mean=("next_ret", "mean"), count=("next_ret", "size"))
    )
    curve = curve[curve["count"] >= 20]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    palette = {
        "ASH_COATED_OSMIUM": "#117733",
        "INTARIAN_PEPPER_ROOT": "#882255",
    }

    for ax, product in zip(axes, ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]):
        data = curve[curve["product"] == product]
        ax.plot(data["ret"], data["next_ret_mean"], color=palette[product], linewidth=2)
        ax.axhline(0, color="black", linewidth=0.8)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_title(product.replace("_", " ").title())
        ax.set_xlabel("Current Mid Change")
        ax.set_ylabel("Average Next Mid Change")

    fig.suptitle("Both products exhibit strong one-step mean reversion", y=1.02, fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "one_step_mean_reversion.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def load_activity(run_id: str) -> pd.DataFrame:
    activity = pd.read_csv(ROOT / "runs" / run_id / "activity.csv", sep=";")
    activity["strategy"] = run_id
    return activity


def plot_strategy_comparison() -> pd.DataFrame:
    score_table = pd.DataFrame(
        [
            {"strategy": "greedy3_reupload", "day": -2, "pnl": 92159},
            {"strategy": "greedy3_reupload", "day": -1, "pnl": 92281},
            {"strategy": "greedy3_reupload", "day": 0, "pnl": 92458},
            {"strategy": "pepper_osmium_combo", "day": -2, "pnl": 93996},
            {"strategy": "pepper_osmium_combo", "day": -1, "pnl": 94838},
            {"strategy": "pepper_osmium_combo", "day": 0, "pnl": 93437},
            {"strategy": "pepper_osmium_outside_box", "day": -2, "pnl": 94112},
            {"strategy": "pepper_osmium_outside_box", "day": -1, "pnl": 94936},
            {"strategy": "pepper_osmium_outside_box", "day": 0, "pnl": 93548},
        ]
    )

    baseline = load_activity("baseline_full_d-1")
    improved = load_activity("outside_box_full_d-1")

    baseline_total = (
        baseline.groupby("timestamp", as_index=False)["profit_and_loss"].sum().rename(columns={"profit_and_loss": "baseline"})
    )
    improved_total = (
        improved.groupby("timestamp", as_index=False)["profit_and_loss"].sum().rename(columns={"profit_and_loss": "outside_box"})
    )
    pnl_curve = baseline_total.merge(improved_total, on="timestamp", how="inner")

    fig, axes = plt.subplots(1, 2, figsize=(15, 5))
    sns.barplot(data=score_table, x="day", y="pnl", hue="strategy", ax=axes[0], palette="Set2")
    axes[0].set_title("Round 1 score comparison across training days")
    axes[0].set_ylabel("Final PnL")

    axes[1].plot(pnl_curve["timestamp"], pnl_curve["baseline"], color="#999999", linewidth=1.5, label="greedy3_reupload")
    axes[1].plot(pnl_curve["timestamp"], pnl_curve["outside_box"], color="#CC3311", linewidth=1.8, label="pepper_osmium_outside_box")
    axes[1].set_title("Day -1 cumulative PnL path")
    axes[1].set_xlabel("Timestamp")
    axes[1].set_ylabel("Profit and Loss")
    axes[1].legend(loc="upper left")

    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "strategy_score_and_pnl_path.png", dpi=160, bbox_inches="tight")
    plt.close(fig)
    return score_table


def main() -> pd.DataFrame:
    prices = load_prices()
    plot_pepper_drift(prices)
    plot_mean_reversion(prices)
    score_table = plot_strategy_comparison()
    summary = score_table.pivot(index="day", columns="strategy", values="pnl")
    summary["improvement_vs_baseline"] = summary["pepper_osmium_outside_box"] - summary["greedy3_reupload"]
    print(summary)
    return summary


if __name__ == "__main__":
    main()
