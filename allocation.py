import argparse
from collections import defaultdict
from math import log as ln
import matplotlib.pyplot as plt
import numpy as np


def research(x_percent):
    return 200_000*ln(1+x_percent)/ln(101)

def scale(y_percent: int):
    return 7*y_percent/100

def speed(BIDS: dict[int, int], z_percent: int):
    BIDS = defaultdict(int, BIDS) # ensure pass by value
    # z_percent is YOUR bid
    # BIDS are all OTHER bids
    BIDS[z_percent] += 1  # include our bid in the count
    
    bid_counts = sorted(BIDS.items())
    total_bids = sum(count for _, count in bid_counts)
    your_rank = sum(count for bid, count in bid_counts if z_percent < bid) + 1
    if your_rank == 1:
        return 0.9
    elif your_rank == total_bids:
        return 0.1
    
    m = (0.1 - 0.9) / (total_bids - 1)
    return 0.9 + m * (your_rank - 1)

def spend(x_percent: int, y_percent: int, z_percent: int):
    return 50_000*(x_percent/100 + y_percent/100 + z_percent/100)

def profit(x_percent: int, y_percent: int, z_percent: int, BIDS: defaultdict[int, int]):
    return research(x_percent)*scale(y_percent)*speed(BIDS, z_percent) - spend(x_percent, y_percent, z_percent)

def build_profit_grid(BIDS):
    profit_grid = np.full((101, 101), np.nan, dtype=float)

    p_max = float("-inf")
    xm = ym = zm = 0

    for x in range(101):
        for y in range(101 - x):
            best_profit = float("-inf")
            for z in range(101 - x - y):
                p_cur = profit(x, y, z, BIDS)
                if p_cur > p_max:
                    p_max = p_cur
                    xm, ym, zm = x, y, z
                if p_cur > best_profit:
                    best_profit = p_cur
            profit_grid[x, y] = best_profit

    return profit_grid, (p_max, xm, ym, zm)


def plot_heatmap(
    grid,
    title,
    max_point,
    xlabel="x percent",
    ylabel="y percent",
    cmap="viridis",
    show_colorbar=True,

):
    fig, ax = plt.subplots(figsize=(9, 7))
    masked = np.ma.masked_invalid(grid.T)
    im = ax.imshow(masked, origin="lower", aspect="auto", cmap=cmap)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_xticks(np.arange(0, 101, 10))
    ax.set_yticks(np.arange(0, 101, 10))
    ax.set_xlim(-0.5, 100.5)
    ax.set_ylim(-0.5, 100.5)

    mx, my, mz = max_point
    ax.scatter([mx], [my], c="red", s=70, edgecolors="white", linewidths=1.2, zorder=10)
    ax.text(
        mx + 1,
        my + 1,
        f"({mx},{my},{mz})",
        color="red",
        fontsize=9,
        weight="bold",
    )

    if show_colorbar:
        fig.colorbar(im, ax=ax, label="Value")
    plt.tight_layout()
    plt.show()


def main(show_heatmap: bool):

    # Example BIDS distribution(s)
    # there were ~300 manual traders last time
    BIDS = {
        100: 5,
        99: 35,
        98: 60,
        97: 80,
        96: 50,
        95: 30,
        94: 20,
        93: 10,
        92: 5,
        91: 3,
        0: 5
    }
    print(sum(BIDS.values()), "total bids")

    profit_grid, best_point = build_profit_grid(BIDS)
    p_max, xm, ym, zm = best_point
    print(f"Best overall profit: {p_max:.2f} at x={xm}, y={ym}, z={zm}")

    if show_heatmap:
        plot_heatmap(
            profit_grid,
            title="Max Profit over z for each x/y combination",
            xlabel="x percent",
            ylabel="y percent",
            cmap="viridis",
            max_point=(xm, ym, zm),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute profit grid and display heatmaps.")
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Show a heatmap of the profit surface",
    )
    args = parser.parse_args()
    main(show_heatmap=args.heatmap)
