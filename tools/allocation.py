import argparse
from collections import defaultdict
from math import log as ln
import matplotlib.pyplot as plt
import numpy as np
import random

def research(x_percent):
    return 200_000*ln(1+x_percent)/ln(101)

def scale(y_percent: int):
    return 7*y_percent/100

def speed(BIDS: dict[int, int], z_percent: int): 
    # z_percent is YOUR bid, BIDS are all OTHER bids
    BIDS = defaultdict(int, BIDS) # ensure pass by value
    BIDS[z_percent] += 1  # include our bid in the count
    
    if z_percent == max(BIDS): # rank 1
        return 0.9
    if z_percent == min(BIDS): # rank last
        return 0.1
    
    your_rank = sum(count for bid, count in BIDS.items() if z_percent < bid) + 1

    total_bids = sum(BIDS.values())
    m = (0.1 - 0.9) / (total_bids - 1)
    return 0.9 + m * (your_rank - 1)

def spend(x_percent: int, y_percent: int, z_percent: int):
    return 50_000*(x_percent/100 + y_percent/100 + z_percent/100)

def profit(x_percent: int, y_percent: int, z_percent: int, BIDS: defaultdict[int, int]):
    return research(x_percent)*scale(y_percent)*speed(BIDS, z_percent) - spend(x_percent, y_percent, z_percent)


# old function
def build_profit_grid(BIDS, participate_in_auction=True):
    profit_grid = np.full((101, 101), np.nan, dtype=float)

    p_max = float("-inf")
    xm = ym = zm = 0

    for x in range(101):
        for y in range(101 - x):
            best_profit = float("-inf")
            for z in range(101 - x - y):
                p_cur = profit(x, y, z if participate_in_auction else 0, BIDS)
                if p_cur > p_max:
                    p_max = p_cur
                    xm, ym, zm = x, y, z
                if p_cur > best_profit:
                    best_profit = p_cur
            profit_grid[x, y] = best_profit

    return profit_grid, (p_max, xm, ym, zm)


# Faster calculator:
# Precompute speed(z) for every z once, then reuse it inside the grid search.
def build_speed_lookup(BIDS: dict[int, int]) -> np.ndarray:
    counts = np.zeros(101, dtype=int)
    for bid, count in BIDS.items():
        if 0 <= bid <= 100:
            counts[bid] += count
        else:
            raise ValueError(f"Bid {bid} out of range [0, 100]")

    other_total = int(counts.sum())
    speed_lookup = np.empty(101, dtype=float)

    if other_total == 0:
        speed_lookup.fill(0.9)
        return speed_lookup

    min_bid = int(np.flatnonzero(counts)[0])
    max_bid = int(np.flatnonzero(counts)[-1])

    # above_counts[z] = number of OTHER bids strictly greater than z
    above_counts = np.zeros(101, dtype=int)
    running = 0
    for z in range(100, -1, -1):
        above_counts[z] = running
        running += counts[z]

    total_bids = other_total + 1
    m = (0.1 - 0.9) / (total_bids - 1)

    for z in range(101):
        if z == max_bid:
            speed_lookup[z] = 0.9
        elif z == min_bid:
            speed_lookup[z] = 0.1
        else:
            your_rank = above_counts[z] + 1
            speed_lookup[z] = 0.9 + m * (your_rank - 1)

    return speed_lookup

def build_profit_grid_fast(BIDS):
    profit_grid = np.full((101, 101), np.nan, dtype=float)

    research_vals = np.array([research(x) for x in range(101)], dtype=float)
    scale_vals = np.array([scale(y) for y in range(101)], dtype=float)
    speed_vals = build_speed_lookup(BIDS)
    z_penalty = 500 * np.arange(101, dtype=float)

    p_max = float("-inf")
    xm = ym = zm = 0

    for x in range(101):
        rx = research_vals[x]
        for y in range(101 - x):
            ry = scale_vals[y]
            max_z = 100 - x - y

            profits = rx * ry * speed_vals[:max_z + 1] - 500 * (x + y) - z_penalty[:max_z + 1]
            best_z = int(np.argmax(profits))
            best_profit = float(profits[best_z])

            profit_grid[x, y] = best_profit

            if best_profit > p_max:
                p_max = best_profit
                xm, ym, zm = x, y, best_z

    return profit_grid, (p_max, xm, ym, zm)

def plot_heatmap(grid, max_point):
    fig, ax = plt.subplots(figsize=(9, 7))
    masked = np.ma.masked_invalid(grid.T)
    im = ax.imshow(masked, origin="lower", aspect="auto", cmap="viridis")
    ax.set_title("Max Profit over z for each x/y combination")
    ax.set_xlabel("x percent")
    ax.set_ylabel("y percent")
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

    BIDS_UNIFORM = {bid: random.randint(1,10) for bid in range(101)}  # uniform distribution of bids
    print(sum(BIDS.values()), "total bids")

    profit_grid, best_point = build_profit_grid(BIDS, participate_in_auction=False)
    p_max, xm, ym, zm = best_point
    print(f"Best overall profit: {p_max:.2f} at x={xm}, y={ym}, z={zm}")

    if show_heatmap:
        plot_heatmap(profit_grid, (xm, ym, zm))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compute profit grid and display heatmaps.")
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Show a heatmap of the profit surface",
    )
    args = parser.parse_args()
    main(show_heatmap=args.heatmap)
