"""Round 3 manual challenge playground (Celestial Gardeners' Guild).

Interactive simulator for the Prosperity 4 Round 3 manual challenge. You
submit two bids `b1` and `b2` against counterparties whose reserve prices
are uniform on the discrete grid {670, 675, ..., 920}. A counterparty with
reserve `r` trades:

  - at `b1` if `b1 > r`, with payoff `920 - b1`; else
  - at `b2` if `b2 > r`. If `b2 > avg_b2` the payoff is `920 - b2`; else the
    payoff is `(920 - b2) * ((920 - avg_b2) / (920 - b2))^3`.

The CLI opens a matplotlib window with text-input boxes for each parameter
(integer values; press Enter in a box to apply). Use `--no-gui` to just print
a text summary, useful for scripting.

Examples
--------
    python tools/round_3_playground.py
    python tools/round_3_playground.py --b1 755 --b2 845 --avg-b2 845
    python tools/round_3_playground.py --no-gui --b1 755 --b2 840 --avg-b2 840
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Literal

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import TextBox

# ---------------------------------------------------------------------------
# Constants from the Prosperity 4 wiki ("Round 3 - Gloves Off").
# ---------------------------------------------------------------------------
RESALE_PRICE = 920
RESERVE_MIN = 670
RESERVE_MAX = 920
RESERVE_STEP = 5

TradeType = Literal["b1", "b2_full", "b2_penalty", "none"]
COLORS: dict[str, str] = {
    "b1": "#4ea1ff",
    "b2_full": "#5cd683",
    "b2_penalty": "#ffc857",
    "none": "#4b5160",
}


# ---------------------------------------------------------------------------
# Math: scalar (single counterparty) and vectorized (full grid + sweep).
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    trade_type: TradeType
    price: float | None
    profit: float
    penalty: float | None


def reserve_grid() -> np.ndarray:
    return np.arange(RESERVE_MIN, RESERVE_MAX + 1, RESERVE_STEP)


def snap_to_grid(value: float) -> int:
    snapped = round(value / RESERVE_STEP) * RESERVE_STEP
    return int(min(RESERVE_MAX, max(RESERVE_MIN, snapped)))


def evaluate_one(
    reserve: float, b1: float, b2: float, avg_b2: float, resale: float = RESALE_PRICE
) -> Outcome:
    if b1 > reserve:
        return Outcome("b1", float(b1), float(resale - b1), None)
    if b2 > reserve:
        if b2 > avg_b2:
            return Outcome("b2_full", float(b2), float(resale - b2), 1.0)
        denom = max(resale - b2, 1e-9)
        penalty = float(np.clip(((resale - avg_b2) / denom) ** 3, 0.0, 1.0))
        return Outcome("b2_penalty", float(b2), float((resale - b2) * penalty), penalty)
    return Outcome("none", None, 0.0, None)


def evaluate_population(
    b1: float, b2: float, avg_b2: float, cpp: int = 1, resale: float = RESALE_PRICE
) -> dict:
    """Apply the rules to every reserve on the grid; aggregate PnL."""
    grid = reserve_grid()
    outcomes = [evaluate_one(int(r), b1, b2, avg_b2, resale) for r in grid]

    counts = {t: 0 for t in ("b1", "b2_full", "b2_penalty", "none")}
    pnl_split = {"b1": 0.0, "b2_full": 0.0, "b2_penalty": 0.0}
    for o in outcomes:
        counts[o.trade_type] += cpp
        if o.trade_type in pnl_split:
            pnl_split[o.trade_type] += o.profit * cpp

    return {
        "grid": grid,
        "outcomes": outcomes,
        "per_profit": np.array([o.profit for o in outcomes], dtype=float),
        "types": [o.trade_type for o in outcomes],
        "pnl": float(sum(o.profit for o in outcomes) * cpp),
        "counts": counts,
        "pnl_split": pnl_split,
    }


def pnl_grid_b1_b2(
    avg_b2: float, cpp: int = 1, resale: float = RESALE_PRICE
) -> tuple[np.ndarray, np.ndarray, int, int, float]:
    """Total PnL for every (b1, b2) pair on the 51x51 reserve grid.

    Returns `(grid, axis, best_b1, best_b2, best_pnl)` where `grid[i, j]` is
    the PnL when `b1 = axis[i]`, `b2 = axis[j]`.
    """
    grid = reserve_grid().astype(float)

    # b1 captures r iff b1 > r.
    b1_capture = grid[:, None] > grid[None, :]                 # (b1, r)
    b1_per = (resale - grid)[:, None]                          # (b1, 1)
    b1_total = (b1_per * b1_capture).sum(axis=1)               # (b1,)

    # Per-counterparty payoff on the b2 branch (depends only on b2 vs avg_b2).
    full = grid > avg_b2
    denom = np.maximum(resale - grid, 1e-9)
    penalty = np.clip(((resale - avg_b2) / denom) ** 3, 0.0, 1.0)
    b2_per = np.where(full, resale - grid, (resale - grid) * penalty)  # (b2,)

    not_b1 = ~b1_capture                                       # (b1, r)
    b2_capture = grid[:, None] > grid[None, :]                 # (b2, r)
    valid = not_b1[:, None, :] & b2_capture[None, :, :]        # (b1, b2, r)
    b2_total = (b2_per[None, :, None] * valid).sum(axis=2)     # (b1, b2)

    total = (b1_total[:, None] + b2_total) * cpp
    flat = int(np.argmax(total))
    i, j = divmod(flat, total.shape[1])
    return total, grid.astype(int), int(grid[i]), int(grid[j]), float(total[i, j])


def pnl_vs_b2(
    b1: float, avg_b2: float, cpp: int = 1, resale: float = RESALE_PRICE
) -> tuple[np.ndarray, np.ndarray]:
    """Total PnL as a function of b2 on each integer in [RESERVE_MIN, RESERVE_MAX]."""
    grid = reserve_grid().astype(float)
    b1_capture = b1 > grid
    not_b1 = ~b1_capture
    b1_pnl = float(((resale - b1) * b1_capture).sum() * cpp)

    b2 = np.arange(RESERVE_MIN, RESERVE_MAX + 1, dtype=float)
    full = b2 > avg_b2
    denom = np.maximum(resale - b2, 1e-9)
    pen = np.clip(((resale - avg_b2) / denom) ** 3, 0.0, 1.0)
    b2_per = np.where(full, resale - b2, (resale - b2) * pen)        # (b2,)

    capture = b2[:, None] > grid[None, :]                            # (b2, r)
    residual_n = (capture & not_b1[None, :]).astype(float).sum(axis=1)
    return b2, b1_pnl + b2_per * residual_n * cpp


def pnl_vs_b1(
    b2: float, avg_b2: float, cpp: int = 1, resale: float = RESALE_PRICE
) -> tuple[np.ndarray, np.ndarray]:
    """Total PnL as a function of b1 on each integer in [RESERVE_MIN, RESERVE_MAX]."""
    grid = reserve_grid().astype(float)

    if b2 > avg_b2:
        b2_per = float(resale - b2)
    else:
        denom = max(resale - b2, 1e-9)
        penalty = min(1.0, max(0.0, ((resale - avg_b2) / denom) ** 3))
        b2_per = float((resale - b2) * penalty)
    b2_capture_r = b2 > grid                                         # (51_r,)

    b1_axis = np.arange(RESERVE_MIN, RESERVE_MAX + 1, dtype=float)
    b1_capture = b1_axis[:, None] > grid[None, :]                    # (b1, r)
    b1_contrib = ((resale - b1_axis)[:, None] * b1_capture).sum(axis=1)
    not_b1 = ~b1_capture
    b2_count = (not_b1 & b2_capture_r[None, :]).astype(float).sum(axis=1)
    return b1_axis, (b1_contrib + b2_per * b2_count) * cpp


# ---------------------------------------------------------------------------
# Text summary (used by --no-gui and the GUI's text panel).
# ---------------------------------------------------------------------------

def text_summary(
    b1: float, b2: float, avg_b2: float, cpp: int
) -> str:
    pop = evaluate_population(b1, b2, avg_b2, cpp)
    _, _, best_b1, best_b2, best_pnl = pnl_grid_b1_b2(avg_b2, cpp)

    s = pop["pnl_split"]
    c = pop["counts"]
    lines = [
        f"  b1 = {b1:<5g}  b2 = {b2:<5g}  avg_b2 = {avg_b2:<5g}  cpp = {cpp}",
        f"  Total PnL: {pop['pnl']:.2f}",
        f"    via b1         {s['b1']:>10.2f}   ({c['b1']} trades)",
        f"    via b2 full    {s['b2_full']:>10.2f}   ({c['b2_full']} trades)",
        f"    via b2 pen.    {s['b2_penalty']:>10.2f}   ({c['b2_penalty']} trades)",
        f"    no trade                       ({c['none']} counterparties)",
        "",
        f"  Optimum at this avg_b2: (b1, b2) = ({best_b1}, {best_b2})  ->  PnL {best_pnl:.2f}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive GUI: matplotlib + Slider widgets.
# ---------------------------------------------------------------------------

def run_gui(b1: int, b2: int, avg_b2: int, cpp: int) -> None:
    plt.rcParams.update({
        "figure.facecolor": "#0f1115",
        "axes.facecolor": "#171a21",
        "axes.edgecolor": "#2a2f3a",
        "axes.labelcolor": "#e6e8ec",
        "axes.titlecolor": "#e6e8ec",
        "text.color": "#e6e8ec",
        "xtick.color": "#9aa0a6",
        "ytick.color": "#9aa0a6",
        "grid.color": "#2a2f3a",
        "savefig.facecolor": "#0f1115",
    })

    fig = plt.figure(figsize=(14.0, 8.6))
    try:
        fig.canvas.manager.set_window_title(
            "Round 3 Playground - Celestial Gardeners' Guild"
        )
    except Exception:
        pass

    # Layout:
    #   - Top header strip (PnL number, legend) drawn with fig.text().
    #   - 2-row gridspec for charts (full-width bar chart on top, two sweeps below).
    #   - Bottom strip with five integer text boxes.
    gs = fig.add_gridspec(
        2, 2,
        height_ratios=[1.5, 1.0],
        width_ratios=[1.0, 1.0],
        left=0.06, right=0.985, top=0.86, bottom=0.20,
        hspace=0.50, wspace=0.20,
    )
    ax_bars = fig.add_subplot(gs[0, :])
    ax_b1 = fig.add_subplot(gs[1, 0])
    ax_b2 = fig.add_subplot(gs[1, 1])

    # --- Header (title + big PnL + legend) -------------------------------
    fig.text(0.06, 0.955, "Round 3 Playground · Celestial Gardeners' Guild",
             color="#e6e8ec", fontsize=13, fontweight="bold")
    pnl_title = fig.text(0.06, 0.905, "Total PnL: —",
                         color="#e6e8ec", fontsize=22, fontweight="bold",
                         family="monospace")

    # Legend swatches at the top right.
    legend_items = [
        ("trade via b1",         COLORS["b1"]),
        ("trade via b2 (full)",  COLORS["b2_full"]),
        ("trade via b2 (pen.)",  COLORS["b2_penalty"]),
        ("no trade",             COLORS["none"]),
    ]
    lx = 0.55
    for label, color in legend_items:
        fig.patches.append(
            plt.Rectangle((lx, 0.92), 0.013, 0.022, transform=fig.transFigure,
                          facecolor=color, edgecolor="none")
        )
        fig.text(lx + 0.018, 0.927, label, color="#c8ccd4", fontsize=10,
                 va="center")
        lx += 0.115

    # --- Bottom strip: integer text boxes ---------------------------------
    state = {"b1": int(b1), "b2": int(b2), "avg_b2": int(avg_b2),
             "cpp": int(cpp)}

    box_specs = [
        ("b1",     "b1",        state["b1"]),
        ("b2",     "b2",        state["b2"]),
        ("avg_b2", "avg b2",    state["avg_b2"]),
        ("cpp",    "count/lvl", state["cpp"]),
    ]
    boxes: dict[str, TextBox] = {}

    n = len(box_specs)
    bottom_y = 0.05
    box_h = 0.050
    span = 0.985 - 0.06
    cell = span / n
    box_w = 0.075
    for i, (key, label, val) in enumerate(box_specs):
        cell_left = 0.06 + i * cell
        ax_box = fig.add_axes([cell_left + cell - box_w - 0.01,
                               bottom_y, box_w, box_h])
        ax_box.set_facecolor("#0c0e13")
        for spine in ax_box.spines.values():
            spine.set_color("#2a2f3a")
        tb = TextBox(ax_box, label + "  ", initial=str(val),
                     color="#0c0e13", hovercolor="#1f232c",
                     label_pad=0.04)
        tb.label.set_color("#9aa0a6")
        tb.label.set_fontsize(11)
        tb.text_disp.set_color("#e6e8ec")
        tb.text_disp.set_fontsize(13)
        # Brighten the cursor (default is black, invisible on dark theme) and
        # make it a touch thicker.
        try:
            tb.cursor.set_color("#e6e8ec")
            tb.cursor.set_linewidth(1.6)
        except Exception:
            pass
        boxes[key] = tb

    # Blinking-cursor effect: every 500 ms, toggle the cursor visibility for
    # any TextBox that currently has keyboard focus. matplotlib hides the
    # cursor automatically when a box loses focus, so we just need to flip
    # the state for the focused one.
    blink_state = {"on": True}

    def _blink(_event=None):
        blink_state["on"] = not blink_state["on"]
        for tb in boxes.values():
            if getattr(tb, "capturekeystrokes", False):
                tb.cursor.set_visible(blink_state["on"])
        fig.canvas.draw_idle()

    try:
        blink_timer = fig.canvas.new_timer(interval=500)
        blink_timer.add_callback(_blink)
        blink_timer.start()
    except Exception:
        # Some backends (e.g. headless Agg) don't run timers; the boxes still
        # show a static cursor while focused, which is enough.
        pass

    # Hint text under the input row.
    fig.text(0.06, 0.012,
             "Click a box, type an integer, press Enter to apply.",
             color="#6b7280", fontsize=10)

    # --- Redraw ----------------------------------------------------------
    def redraw() -> None:
        b1v = state["b1"]; b2v = state["b2"]; avgv = state["avg_b2"]
        cppv = max(1, int(state["cpp"]))

        pop = evaluate_population(b1v, b2v, avgv, cppv)
        b1_axis, pnl_b1 = pnl_vs_b1(b2v, avgv, cppv)
        b2_axis, pnl_b2 = pnl_vs_b2(b1v, avgv, cppv)

        pnl_title.set_text(f"Total PnL: {pop['pnl']:,.0f}")

        # Per-counterparty bar chart -------------------------------------
        ax_bars.cla()
        bar_colors = [COLORS[t] for t in pop["types"]]
        bar_heights = pop["per_profit"] * cppv
        ax_bars.bar(pop["grid"], bar_heights, width=4.2,
                    color=bar_colors, edgecolor="none")

        ymax = float(max(bar_heights.max(), 1.0)) * 1.18
        ax_bars.set_ylim(0, ymax)

        line_specs = [
            (b1v,  COLORS["b1"],          "b1",     "--", 0.96),
            (b2v,  COLORS["b2_full"],     "b2",     "--", 0.88),
            (avgv, COLORS["b2_penalty"],  "avg b2", ":",  0.80),
        ]
        for x, color, label, ls, yf in line_specs:
            ax_bars.axvline(x, color=color, linestyle=ls, linewidth=1.4, alpha=0.9)
            ax_bars.text(x, ymax * yf, f" {label}={x}",
                         color=color, fontsize=10, va="top", ha="left",
                         fontweight="bold",
                         bbox=dict(facecolor="#0f1115", edgecolor="none",
                                   pad=1.5, alpha=0.7))
        ax_bars.set_xlim(RESERVE_MIN - 5, RESERVE_MAX + 5)
        ax_bars.set_xlabel("counterparty reserve price (lower = cheaper to buy from)")
        ax_bars.set_ylabel("your PnL from that counterparty")
        c = pop["counts"]
        ax_bars.set_title(
            f"Per-counterparty PnL  ·  trades: b1={c['b1']}, "
            f"b2 full={c['b2_full']}, b2 pen.={c['b2_penalty']}, "
            f"none={c['none']}",
            fontsize=11, loc="left", pad=8,
        )
        ax_bars.grid(True, axis="y", alpha=0.3)

        # 1D sweep helpers -----------------------------------------------
        def draw_sweep(ax, axis, pnl_curve, current_x, current_pnl, color, name):
            ax.cla()
            ax.plot(axis, pnl_curve, color=color, linewidth=2)
            best_idx = int(np.argmax(pnl_curve))
            best_x = float(axis[best_idx]); best_y = float(pnl_curve[best_idx])
            ax.axvline(current_x, color="white", linestyle="--", linewidth=1, alpha=0.6)
            ax.scatter([current_x], [current_pnl], c="white", marker="D",
                       s=80, edgecolors="#0f1115", linewidths=1, zorder=5)
            ax.scatter([best_x], [best_y], c="#ff7b72", marker="*",
                       s=260, edgecolors="white", linewidths=1.0, zorder=6)
            # Inline labels next to the markers.
            ax.annotate(f"you ({int(current_x)}) → {current_pnl:,.0f}",
                        xy=(current_x, current_pnl),
                        xytext=(8, -14), textcoords="offset points",
                        color="#c8ccd4", fontsize=9)
            ax.annotate(f"best ({int(best_x)}) → {best_y:,.0f}",
                        xy=(best_x, best_y),
                        xytext=(8, 8), textcoords="offset points",
                        color="#ff7b72", fontsize=9, fontweight="bold")
            ax.set_xlabel(f"your {name}")
            ax.set_ylabel("total PnL")
            ax.set_title(f"PnL as a function of your {name}",
                         fontsize=10, loc="left")
            ax.set_xlim(RESERVE_MIN - 5, RESERVE_MAX + 5)
            ymin, ymax_ = float(pnl_curve.min()), float(pnl_curve.max())
            pad = max(1.0, (ymax_ - ymin) * 0.18)
            ax.set_ylim(ymin - pad * 0.4, ymax_ + pad)
            ax.grid(True, alpha=0.3)

        # Look up current PnL on each curve so the marker is on the line.
        cur_b1_pnl = float(pnl_b1[int(np.clip(b1v - RESERVE_MIN, 0, len(pnl_b1) - 1))])
        cur_b2_pnl = float(pnl_b2[int(np.clip(b2v - RESERVE_MIN, 0, len(pnl_b2) - 1))])
        draw_sweep(ax_b1, b1_axis, pnl_b1, b1v, cur_b1_pnl, COLORS["b1"], "b1")
        draw_sweep(ax_b2, b2_axis, pnl_b2, b2v, cur_b2_pnl, COLORS["b2_full"], "b2")

        fig.canvas.draw_idle()

    # --- Input handling --------------------------------------------------
    def make_handler(key: str):
        def _on_submit(text: str) -> None:
            try:
                v = int(text.strip())
            except (ValueError, AttributeError):
                boxes[key].set_val(str(state[key]))
                return
            if key == "cpp":
                v = max(1, v)
            state[key] = v
            boxes[key].set_val(str(v))
            redraw()
        return _on_submit

    for key in boxes:
        boxes[key].on_submit(make_handler(key))

    redraw()
    plt.show()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Round 3 manual challenge playground (Celestial Gardeners' Guild).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in (__doc__ or "") else "",
    )
    parser.add_argument("--b1", type=int, default=755, help="your first bid (default 755)")
    parser.add_argument("--b2", type=int, default=840, help="your second bid (default 840)")
    parser.add_argument("--avg-b2", type=int, default=840, help="assumed mean of other players' second bids (default 840)")
    parser.add_argument("--cpp", type=int, default=1, help="counterparties per reserve level (default 1, so 51 total)")
    parser.add_argument("--no-gui", action="store_true", help="print summary and exit; no interactive window")
    args = parser.parse_args()

    if args.no_gui:
        print(text_summary(args.b1, args.b2, args.avg_b2, args.cpp))
        return

    run_gui(args.b1, args.b2, args.avg_b2, args.cpp)


if __name__ == "__main__":
    main()
