"""Interactive allocation optimizer backend.

Drop-in replacement for `allocation_cluster_plotly.py` that avoids Streamlit
re-renders. The frontend (see `frontend/index.html`) uses `Plotly.react()`
to patch existing traces in place, and only posts the small cluster config
to this server for each recompute.
"""

import os
import sys
import math
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Allow importing allocation from the sibling `tools/` directory.
_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from allocation import (  # noqa: E402
    BUDGET_DEFAULT,
    build_profit_grid_fast,
    research_fast,
    scale_fast,
    speed_fast,
    spend_fast,
)

app = FastAPI(default_response_class=ORJSONResponse)


class Cluster(BaseModel):
    id: int
    center: int = Field(ge=0, le=100)
    size: int = Field(ge=0)
    width: int = Field(default=0, ge=0, le=100)
    weight_mode: Literal["linear", "exponential"] = "linear"
    # L = mass only on z <= center (spread left); R = only z >= center; F = both sides (symmetric).
    spread: Literal["L", "R", "F"] = "F"


class ComputeRequest(BaseModel):
    clusters: list[Cluster] = Field(default_factory=list)
    base_floor: int = Field(default=0, ge=0)
    probe_z: int = Field(default=50, ge=0, le=100)
    budget: int = Field(default=BUDGET_DEFAULT, ge=1)
    # Optional legacy/global override; when set, replaces every cluster's weight_mode for this request.
    cluster_weight_mode: Literal["linear", "exponential"] | None = None


def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def build_bids_from_clusters(clusters: list[Cluster], base_floor: int) -> dict[int, int]:
    bids = {i: int(base_floor) for i in range(101)}

    for c in clusters:
        center = _clamp(int(c.center), 0, 100)
        size = max(0, int(c.size))
        width = max(0, int(c.width))
        mode = c.weight_mode

        if size == 0:
            continue

        if width == 0:
            bids[center] += size
            continue

        spread = c.spread
        if spread == "L":
            lo, hi = max(0, center - width), center
        elif spread == "R":
            lo, hi = center, min(100, center + width)
        else:
            lo, hi = max(0, center - width), min(100, center + width)

        raw: list[tuple[int, float]] = []
        total_weight = 0.0
        for b in range(lo, hi + 1):
            if spread == "L":
                dist = center - b
            elif spread == "R":
                dist = b - center
            else:
                dist = abs(b - center)
            if mode == "exponential":
                # Stronger at center; ~e^-2 at the edge (dist == width).
                w = math.exp(-2.0 * dist / float(width))
            else:
                w = float(width - dist + 1)
            raw.append((b, w))
            total_weight += w

        assigned = 0
        for b, w in raw:
            add = int(size * w / total_weight) if total_weight else 0
            bids[b] += add
            assigned += add

        leftover = size - assigned
        order = sorted(raw, key=lambda x: abs(x[0] - center))
        idx = 0
        while leftover > 0 and order:
            bids[order[idx % len(order)][0]] += 1
            leftover -= 1
            idx += 1

    return bids


def _speed_at(bids_vec: np.ndarray, z: int) -> float:
    """Speed if you bid z; `bids_vec` is the count of OTHER bidders per integer."""
    counts = bids_vec.copy()
    counts[z] += 1
    total = int(counts.sum())
    if total <= 1:
        return 0.9
    active = np.flatnonzero(counts)
    min_bid = int(active[0])
    max_bid = int(active[-1])
    if z == max_bid:
        return 0.9
    if z == min_bid:
        return 0.1
    your_rank = int(counts[z + 1 :].sum()) + 1
    m = (0.1 - 0.9) / (total - 1)
    return 0.9 + m * (your_rank - 1)


def compute(req: ComputeRequest) -> dict[str, Any]:
    clusters = list(req.clusters)
    if req.cluster_weight_mode is not None:
        clusters = [c.model_copy(update={"weight_mode": req.cluster_weight_mode}) for c in clusters]
    bids = build_bids_from_clusters(clusters, req.base_floor)
    bids_vec = np.array([bids[i] for i in range(101)], dtype=int)
    budget = int(req.budget)

    profit_grid, (p_max, xm, ym, zm) = build_profit_grid_fast(bids, budget=budget)

    speed_curve = [_speed_at(bids_vec, z) for z in range(101)]
    above_curve = [int(bids_vec[z + 1 :].sum()) for z in range(101)]

    # Plotly / JSON cannot carry NaN; send None for masked cells.
    grid_T = profit_grid.T
    profit_z: list[list[float | None]] = [
        [None if not np.isfinite(v) else float(v) for v in row] for row in grid_T
    ]

    z_probe = int(req.probe_z)
    probe_speed = _speed_at(bids_vec, z_probe)
    counts_with_probe = bids_vec.copy()
    counts_with_probe[z_probe] += 1
    probe_total = int(counts_with_probe.sum())
    probe_rank = int(counts_with_probe[z_probe + 1 :].sum()) + 1

    # Best (x, y) when forced to bid z = probe_z.
    speed_vec = speed_fast(bids)
    research_vec = research_fast()
    scale_vec = scale_fast()
    xs = np.arange(101)[:, None]
    ys = np.arange(101)[None, :]
    revenue = np.outer(research_vec, scale_vec) * speed_vec[z_probe]
    cost = spend_fast(xs, ys, z_probe, budget=budget)
    probe_profits = np.where(xs + ys + z_probe <= 100, revenue - cost, -np.inf)
    probe_flat = int(np.argmax(probe_profits))
    probe_bx, probe_by = int(probe_flat // 101), int(probe_flat % 101)
    probe_profit = float(probe_profits[probe_bx, probe_by])

    return {
        "bids": bids_vec.tolist(),
        "speed_curve": speed_curve,
        "above_curve": above_curve,
        "profit_grid": profit_z,
        "best": {
            "x": int(xm),
            "y": int(ym),
            "z": int(zm),
            "profit": float(p_max),
        },
        "total_bids": int(bids_vec.sum()),
        "probe": {
            "z": z_probe,
            "speed": float(probe_speed),
            "rank": probe_rank,
            "total": probe_total,
            "profit": probe_profit,
            "best_x": probe_bx,
            "best_y": probe_by,
        },
    }


@app.post("/api/compute")
def api_compute(req: ComputeRequest) -> dict[str, Any]:
    return compute(req)


@app.get("/api/healthz")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
