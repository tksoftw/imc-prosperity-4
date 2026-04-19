import argparse
from collections import defaultdict
from math import log as ln
import matplotlib.pyplot as plt
import numpy as np
import random

# Default total budget the auction problem ships with (50k coins).
# Marginal cost per percentage point is BUDGET / 100.
BUDGET_DEFAULT = 50_000

# Speed multiplier you get when you skip the auction entirely (no z bid):
# the floor of the rank curve, i.e. the same value as being last in the auction.
NON_PARTICIPATION_SPEED = 0.1


# ---------------------------------------------------------------------------
# Slow / scalar versions — readable, used as ground truth.
# ---------------------------------------------------------------------------

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

def spend(x_percent: int, y_percent: int, z_percent: int, budget: int = BUDGET_DEFAULT):
    return budget * (x_percent/100 + y_percent/100 + z_percent/100)

def profit(x_percent: int, y_percent: int, z_percent: int, BIDS: defaultdict[int, int],
           budget: int = BUDGET_DEFAULT):
    return (
        research(x_percent) * scale(y_percent) * speed(BIDS, z_percent)
        - spend(x_percent, y_percent, z_percent, budget)
    )


# old function
def build_profit_grid(BIDS, participate_in_auction: bool = True, budget: int = BUDGET_DEFAULT):
    profit_grid = np.full((101, 101), np.nan, dtype=float)

    p_max = float("-inf")
    xm = ym = zm = 0

    for x in range(101):
        for y in range(101 - x):
            if participate_in_auction:
                best_profit = float("-inf")
                best_z = 0
                for z in range(101 - x - y):
                    p_cur = profit(x, y, z, BIDS, budget)
                    if p_cur > best_profit:
                        best_profit = p_cur
                        best_z = z
            else:
                # Skip the auction: speed pinned to NON_PARTICIPATION_SPEED, z = 0.
                best_z = 0
                best_profit = (
                    research(x) * scale(y) * NON_PARTICIPATION_SPEED
                    - spend(x, y, 0, budget)
                )

            profit_grid[x, y] = best_profit
            if best_profit > p_max:
                p_max = best_profit
                xm, ym, zm = x, y, best_z

    return profit_grid, (p_max, xm, ym, zm)


# ---------------------------------------------------------------------------
# Fast / vectorized versions — used by the dashboard.
# Each `_fast` mirrors a scalar function above but returns a precomputed
# numpy array so the inner grid loop stays a one-liner.
# ---------------------------------------------------------------------------

def research_fast() -> np.ndarray:
    """research(x) for x in 0..100, as a length-101 array."""
    return np.array([research(x) for x in range(101)], dtype=float)


def scale_fast() -> np.ndarray:
    """scale(y) for y in 0..100, as a length-101 array."""
    return np.array([scale(y) for y in range(101)], dtype=float)


def speed_fast(BIDS: dict[int, int]) -> np.ndarray:
    """speed(BIDS, z) for z in 0..100, as a length-101 array.

    Equivalent to calling `speed()` 101 times but O(N) in the bid count
    thanks to a single suffix-sum pass over the bid histogram.
    """
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


def spend_fast(x: int, y: int, z_array: np.ndarray, budget: int = BUDGET_DEFAULT) -> np.ndarray:
    """Vectorized `spend()` over a vector of z values for fixed (x, y)."""
    return (budget / 100) * (x + y + z_array)


def profit_fast(
    x: int,
    y: int,
    max_z: int,
    research_vec: np.ndarray,
    scale_vec: np.ndarray,
    speed_vec: np.ndarray,
    budget: int = BUDGET_DEFAULT,
) -> np.ndarray:
    """Vectorized `profit()` over z in 0..max_z for fixed (x, y).

    `research_vec`, `scale_vec`, `speed_vec` are the precomputed length-101
    arrays from `research_fast`, `scale_fast`, `speed_fast`.
    """
    z_arr = np.arange(max_z + 1, dtype=float)
    revenue = research_vec[x] * scale_vec[y] * speed_vec[: max_z + 1]
    cost = spend_fast(x, y, z_arr, budget)
    return revenue - cost


def build_profit_grid_fast(
    BIDS,
    budget: int = BUDGET_DEFAULT,
    participate_in_auction: bool = True,
):
    research_vec = research_fast()
    scale_vec = scale_fast()

    if not participate_in_auction:
        # No auction: speed pinned to NON_PARTICIPATION_SPEED, z = 0.
        # Profit collapses to a 2D problem in (x, y), so vectorize directly.
        xs = np.arange(101)[:, None]
        ys = np.arange(101)[None, :]
        revenue = np.outer(research_vec, scale_vec) * NON_PARTICIPATION_SPEED
        cost = (budget / 100) * (xs + ys)
        profit_grid = np.where(xs + ys <= 100, revenue - cost, np.nan)

        flat = int(np.nanargmax(profit_grid))
        xm, ym = int(flat // 101), int(flat % 101)
        return profit_grid, (float(profit_grid[xm, ym]), xm, ym, 0)

    speed_vec = speed_fast(BIDS)

    profit_grid = np.full((101, 101), np.nan, dtype=float)
    p_max = float("-inf")
    xm = ym = zm = 0

    for x in range(101):
        for y in range(101 - x):
            max_z = 100 - x - y

            profits = profit_fast(x, y, max_z, research_vec, scale_vec, speed_vec, budget)
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
