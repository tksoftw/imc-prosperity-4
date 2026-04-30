#!/usr/bin/env python3
"""Optimize Aether Casino positions from the websim payoff model.

The script still imports tools/exotics/websim.py for market data and payoff
logic, but it avoids treating one noisy 100k Monte Carlo precompute as truth.
By default it uses closed-form fair values where possible, estimates the two
path-dependent exotics separately, centers the payoff sample to those fair
values, and then searches integer portfolios on an EV/variance frontier.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import math
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

try:
    from scipy.optimize import minimize
except Exception:  # pragma: no cover - max-EV mode still works without scipy.
    minimize = None


WEB_SIM_PATH = Path(__file__).with_name("websim.py")


class _UiFactory:
    """Tiny callable object used to import websim.py without Dash installed."""

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"args": args, "kwargs": kwargs}

    def __getattr__(self, name: str) -> "_UiFactory":
        return self


class _DashApp:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.title = ""

    def callback(self, *args: Any, **kwargs: Any):
        def decorator(func):
            return func

        return decorator

    def run(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Figure:
    def add_hline(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add_trace(self, *args: Any, **kwargs: Any) -> None:
        return None

    def add_vline(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update_layout(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Trace:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        return None


def install_ui_stubs() -> None:
    """Install minimal Dash/Plotly stubs when the UI dependencies are absent."""

    if "dash" not in sys.modules and importlib.util.find_spec("dash") is None:
        dash = types.ModuleType("dash")
        dash.Dash = _DashApp
        dash.dcc = _UiFactory()
        dash.html = _UiFactory()
        dash.Input = _UiFactory()
        dash.Output = _UiFactory()
        dash.State = _UiFactory()
        dash.ctx = types.SimpleNamespace(triggered=[])
        dash.no_update = object()
        sys.modules["dash"] = dash

    if "plotly" not in sys.modules and importlib.util.find_spec("plotly") is None:
        plotly = types.ModuleType("plotly")
        graph_objects = types.ModuleType("plotly.graph_objects")
        graph_objects.Figure = _Figure
        graph_objects.Scatter = _Trace
        graph_objects.Bar = _Trace
        plotly.graph_objects = graph_objects
        sys.modules["plotly"] = plotly
        sys.modules["plotly.graph_objects"] = graph_objects


def load_websim(path: Path, *, seed: int | None, quiet: bool) -> types.ModuleType:
    """Execute websim.py and return its module object.

    websim.py precomputes PAYOFFS_PRE at import time.  Seeding NumPy here makes
    this optimizer reproducible while still using websim's own payoff logic.
    """

    install_ui_stubs()
    if seed is not None:
        np.random.seed(seed)

    spec = importlib.util.spec_from_file_location("_aether_websim_source", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")

    module = importlib.util.module_from_spec(spec)
    if quiet:
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
    else:
        spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class MarketData:
    ids: tuple[str, ...]
    names: tuple[str, ...]
    bids: np.ndarray
    asks: np.ndarray
    sizes: np.ndarray
    fair_values: np.ndarray
    payoffs: np.ndarray
    multiplier: float

    @property
    def n(self) -> int:
        return len(self.ids)


def market_from_websim(websim: types.ModuleType) -> MarketData:
    market = websim.MARKET
    ids = tuple(c["id"] for c in market)
    names = tuple(c["name"] for c in market)
    bids = np.array([c["bid"] for c in market], dtype=float)
    asks = np.array([c["ask"] for c in market], dtype=float)
    sizes = np.array([c["size"] for c in market], dtype=int)
    payoffs = np.column_stack([websim.PAYOFFS_PRE[cid] for cid in ids]).astype(float, copy=False)
    fair_values = payoffs.mean(axis=0)
    return MarketData(
        ids=ids,
        names=names,
        bids=bids,
        asks=asks,
        sizes=sizes,
        fair_values=fair_values,
        payoffs=payoffs,
        multiplier=float(websim.MULTIPLIER),
    )


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_call(spot: float, strike: float, sigma: float, time: float) -> float:
    vol = sigma * math.sqrt(time)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time) / vol
    d2 = d1 - vol
    return spot * normal_cdf(d1) - strike * normal_cdf(d2)


def black_scholes_put(spot: float, strike: float, sigma: float, time: float) -> float:
    vol = sigma * math.sqrt(time)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * time) / vol
    d2 = d1 - vol
    return strike * normal_cdf(-d2) - spot * normal_cdf(-d1)


def binary_put_value(spot: float, strike: float, sigma: float, time: float, payout: float) -> float:
    vol = sigma * math.sqrt(time)
    d = (math.log(strike / spot) + 0.5 * sigma * sigma * time) / vol
    return payout * normal_cdf(d)


def estimate_path_dependent_fair_values(
    websim: types.ModuleType,
    *,
    paths: int,
    seed: int,
    chunk_size: int = 100_000,
) -> dict[str, float]:
    """Estimate chooser and KO fair values with antithetic paths.

    websim.py's 100k-path PAYOFFS_PRE is fine for showing the game, but it is
    noisy enough to flip small EV signs.  This keeps the same payoff formulas
    while using a separate, reproducible estimate for path-dependent exotics.
    """

    if paths <= 0:
        return {}

    rng = np.random.default_rng(seed)
    count = 0
    chooser_sum = 0.0
    ko_sum = 0.0
    spot = float(websim.SPOT)
    sigma = float(websim.SIGMA)
    dt = float(websim.DT)
    sqrt_dt = math.sqrt(dt)

    while count < paths:
        base_n = max(1, min(chunk_size // 2, math.ceil((paths - count) / 2)))
        z_base = rng.standard_normal((base_n, 60))
        z = np.vstack([z_base, -z_base])
        if count + len(z) > paths:
            z = z[: paths - count]

        log_returns = (-0.5 * sigma * sigma * dt) + sigma * sqrt_dt * z
        paths_chunk = np.hstack([np.ones((len(z), 1)), np.exp(np.cumsum(log_returns, axis=1))]) * spot
        s_2w = paths_chunk[:, 40]
        s_3w = paths_chunk[:, 60]
        min_s = np.min(paths_chunk[:, 1:], axis=1)

        chooser = np.where(
            s_2w > 50.0,
            np.maximum(s_3w - 50.0, 0.0),
            np.maximum(50.0 - s_3w, 0.0),
        )
        ko = np.where(
            min_s >= float(websim.KO_BARRIER),
            np.maximum(45.0 - s_3w, 0.0),
            0.0,
        )

        chooser_sum += float(chooser.sum())
        ko_sum += float(ko.sum())
        count += len(z)

    return {
        "AC_50_CO": chooser_sum / count,
        "AC_45_KO": ko_sum / count,
    }


def hybrid_fair_values(
    websim: types.ModuleType,
    market: MarketData,
    *,
    fair_paths: int,
    fair_seed: int,
) -> np.ndarray:
    """Use closed-form values where available and MC only for path exotics."""

    spot = float(websim.SPOT)
    sigma = float(websim.SIGMA)
    t_2w = 40 * float(websim.DT)
    t_3w = 60 * float(websim.DT)
    path_values = estimate_path_dependent_fair_values(websim, paths=fair_paths, seed=fair_seed)
    sample_means = dict(zip(market.ids, market.payoffs.mean(axis=0)))

    values: dict[str, float] = {
        "AC": spot,
        "AC_50_P_2": black_scholes_put(spot, 50.0, sigma, t_2w),
        "AC_50_C_2": black_scholes_call(spot, 50.0, sigma, t_2w),
        "AC_50_P": black_scholes_put(spot, 50.0, sigma, t_3w),
        "AC_50_C": black_scholes_call(spot, 50.0, sigma, t_3w),
        "AC_35_P": black_scholes_put(spot, 35.0, sigma, t_3w),
        "AC_40_P": black_scholes_put(spot, 40.0, sigma, t_3w),
        "AC_45_P": black_scholes_put(spot, 45.0, sigma, t_3w),
        "AC_60_C": black_scholes_call(spot, 60.0, sigma, t_3w),
        "AC_40_BP": binary_put_value(spot, 40.0, sigma, t_3w, float(websim.BINARY_PAYOUT)),
        "AC_50_CO": path_values.get("AC_50_CO", sample_means["AC_50_CO"]),
        "AC_45_KO": path_values.get("AC_45_KO", sample_means["AC_45_KO"]),
    }
    return np.array([values[cid] for cid in market.ids], dtype=float)


def with_fair_values(market: MarketData, fair_values: np.ndarray, *, center_payoffs: bool) -> MarketData:
    fair_values = np.asarray(fair_values, dtype=float)
    payoffs = np.array(market.payoffs, dtype=float, copy=True)
    if center_payoffs:
        payoffs += fair_values - payoffs.mean(axis=0)
    return MarketData(
        ids=market.ids,
        names=market.names,
        bids=market.bids,
        asks=market.asks,
        sizes=market.sizes,
        fair_values=fair_values,
        payoffs=payoffs,
        multiplier=market.multiplier,
    )


def signed_cost(qty: np.ndarray, market: MarketData) -> float:
    qty = np.asarray(qty, dtype=float)
    return float(np.where(qty >= 0, qty * market.asks, qty * market.bids).sum())


def portfolio_path_pnls(qty: np.ndarray, market: MarketData) -> np.ndarray:
    qty = np.asarray(qty, dtype=float)
    return (market.payoffs @ qty - signed_cost(qty, market)) * market.multiplier


def portfolio_ev(qty: np.ndarray, market: MarketData) -> float:
    qty = np.asarray(qty, dtype=float)
    long_ev = qty * (market.fair_values - market.asks)
    short_ev = (-qty) * (market.bids - market.fair_values)
    return float(np.where(qty >= 0, long_ev, short_ev).sum() * market.multiplier)


def seed_covariance(market: MarketData, seed_paths: int) -> np.ndarray:
    return np.cov(market.payoffs, rowvar=False, ddof=0) * (market.multiplier**2 / seed_paths)


def portfolio_seed_variance(qty: np.ndarray, cov_seed: np.ndarray) -> float:
    qty = np.asarray(qty, dtype=float)
    return float(qty @ cov_seed @ qty)


def exact_max_ev_quantities(market: MarketData) -> np.ndarray:
    long_edges = market.fair_values - market.asks
    short_edges = market.bids - market.fair_values
    qty = np.zeros(market.n, dtype=int)
    for i, size in enumerate(market.sizes):
        best_edge = max(long_edges[i], short_edges[i])
        if best_edge <= 0:
            continue
        qty[i] = int(size if long_edges[i] >= short_edges[i] else -size)
    return qty


def split_from_qty(qty: np.ndarray) -> np.ndarray:
    qty = np.asarray(qty, dtype=float)
    return np.concatenate([np.maximum(qty, 0.0), np.maximum(-qty, 0.0)])


def qty_from_split(x: np.ndarray, n: int) -> np.ndarray:
    return np.asarray(x[:n] - x[n:], dtype=float)


def round_qty(qty: np.ndarray, market: MarketData) -> np.ndarray:
    return np.clip(np.rint(qty), -market.sizes, market.sizes).astype(int)


def optimize_min_variance_for_ev(
    market: MarketData,
    cov_seed: np.ndarray,
    target_ev: float,
    max_ev_qty: np.ndarray,
    *,
    maxiter: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    if target_ev <= 0:
        return np.zeros(market.n, dtype=int), {"success": True, "message": "zero target"}
    if minimize is None:
        raise RuntimeError("scipy is required for frontier optimization")

    n = market.n
    long_edges = (market.fair_values - market.asks) * market.multiplier
    short_edges = (market.bids - market.fair_values) * market.multiplier
    ev_gradient = np.concatenate([long_edges, short_edges])

    max_ev = portfolio_ev(max_ev_qty, market)
    scale = min(1.0, max(0.0, target_ev / max(max_ev, 1.0)))
    x0 = split_from_qty(max_ev_qty.astype(float) * scale)
    bounds = [(0.0, float(size)) for size in market.sizes] * 2

    def objective(x: np.ndarray) -> float:
        qty = qty_from_split(x, n)
        return portfolio_seed_variance(qty, cov_seed)

    def jacobian(x: np.ndarray) -> np.ndarray:
        qty = qty_from_split(x, n)
        grad_qty = 2.0 * cov_seed @ qty
        return np.concatenate([grad_qty, -grad_qty])

    def ev_constraint(x: np.ndarray) -> float:
        return float(ev_gradient @ x - target_ev)

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        jac=jacobian,
        bounds=bounds,
        constraints=[{"type": "ineq", "fun": ev_constraint, "jac": lambda x: ev_gradient}],
        options={"maxiter": maxiter, "ftol": 1e-7, "disp": False},
    )

    qty = round_qty(qty_from_split(result.x, n), market)
    qty = repair_ev_target(qty, target_ev, market, cov_seed)
    return qty, {"success": bool(result.success), "message": result.message, "fun": float(result.fun)}


def repair_ev_target(
    qty: np.ndarray,
    target_ev: float,
    market: MarketData,
    cov_seed: np.ndarray,
    *,
    max_steps: int = 20_000,
) -> np.ndarray:
    """Local integer repair: meet EV target, then greedily lower variance."""

    qty = np.array(qty, dtype=int, copy=True)

    def candidates(q: np.ndarray) -> Iterable[np.ndarray]:
        for i, size in enumerate(market.sizes):
            if q[i] < size:
                c = q.copy()
                c[i] += 1
                yield c
            if q[i] > -size:
                c = q.copy()
                c[i] -= 1
                yield c

    cur_ev = portfolio_ev(qty, market)
    cur_var = portfolio_seed_variance(qty, cov_seed)

    for _ in range(max_steps):
        if cur_ev >= target_ev - 1e-6:
            break
        best: tuple[float, float, np.ndarray, float, float] | None = None
        for cand in candidates(qty):
            ev = portfolio_ev(cand, market)
            delta_ev = ev - cur_ev
            if delta_ev <= 1e-9:
                continue
            var = portfolio_seed_variance(cand, cov_seed)
            delta_var = var - cur_var
            score = delta_var / delta_ev
            item = (score, -delta_ev, cand, ev, var)
            if best is None or item[:2] < best[:2]:
                best = item
        if best is None:
            break
        _, _, qty, cur_ev, cur_var = best

    for _ in range(max_steps):
        best_improvement: tuple[float, np.ndarray, float, float] | None = None
        for cand in candidates(qty):
            ev = portfolio_ev(cand, market)
            if ev < target_ev - 1e-6:
                continue
            var = portfolio_seed_variance(cand, cov_seed)
            improvement = cur_var - var
            if improvement <= 1e-6:
                continue
            item = (improvement, cand, ev, var)
            if best_improvement is None or improvement > best_improvement[0]:
                best_improvement = item
        if best_improvement is None:
            break
        _, qty, cur_ev, cur_var = best_improvement

    return qty


def quantity_ev_tables(market: MarketData) -> tuple[list[np.ndarray], list[np.ndarray]]:
    values = [np.arange(-int(size), int(size) + 1, dtype=int) for size in market.sizes]
    ev_tables: list[np.ndarray] = []
    for i, vals in enumerate(values):
        long_edge = (market.fair_values[i] - market.asks[i]) * market.multiplier
        short_edge = (market.bids[i] - market.fair_values[i]) * market.multiplier
        ev_tables.append(np.where(vals >= 0, vals * long_edge, (-vals) * short_edge))
    return values, ev_tables


def quantity_search_tables(
    market: MarketData,
    *,
    max_ev_qty: np.ndarray,
    grid_points: int,
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    full_values, full_ev_tables = quantity_ev_tables(market)
    if grid_points <= 0:
        return full_values, full_ev_tables, full_values, full_ev_tables

    search_values: list[np.ndarray] = []
    search_ev_tables: list[np.ndarray] = []
    for i, full_vals in enumerate(full_values):
        size = int(market.sizes[i])
        if len(full_vals) <= grid_points:
            vals = full_vals
        else:
            grid = np.rint(np.linspace(-size, size, grid_points)).astype(int)
            extras = np.array([-size, 0, size, int(max_ev_qty[i])], dtype=int)
            vals = np.unique(np.clip(np.concatenate([grid, extras]), -size, size))
        search_values.append(vals.astype(int))
        search_ev_tables.append(full_ev_tables[i][search_values[-1] + size])

    return full_values, full_ev_tables, search_values, search_ev_tables


def optimize_min_variance_for_ev_coordinate(
    market: MarketData,
    cov_seed: np.ndarray,
    target_ev: float,
    max_ev_qty: np.ndarray,
    *,
    restarts: int,
    coord_iters: int,
    penalty: float,
    random_seed: int,
    extra_starts: Iterable[np.ndarray] = (),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Integer coordinate descent for min variance subject to an EV target.

    Each coordinate step scans every legal quantity for one instrument and
    chooses the best penalized objective exactly. Multiple starts matter because
    allowing negative-EV hedges makes the integer problem non-convex.
    """

    if target_ev <= 0:
        return np.zeros(market.n, dtype=int), {"success": True, "message": "zero target"}

    rng = np.random.default_rng(random_seed)
    values, ev_tables = quantity_ev_tables(market)

    def ev_fast(qty: np.ndarray) -> float:
        return float(sum(ev_tables[i][int(qty[i]) + int(market.sizes[i])] for i in range(market.n)))

    def var_fast(qty: np.ndarray) -> float:
        q = qty.astype(float)
        return float(q @ cov_seed @ q)

    starts: list[np.ndarray] = [
        np.zeros(market.n, dtype=int),
        np.array(max_ev_qty, dtype=int, copy=True),
    ]
    starts.extend(np.rint(max_ev_qty * frac).astype(int) for frac in np.linspace(0.1, 1.0, 10))
    starts.extend(np.array(start, dtype=int, copy=True) for start in extra_starts)

    for _ in range(max(0, restarts)):
        if rng.random() < 0.65:
            frac = rng.uniform(0.0, 1.05)
            noise = np.array([rng.integers(-max(1, size // 15), max(2, size // 15 + 1)) for size in market.sizes])
            start = np.rint(max_ev_qty * frac).astype(int) + noise
            starts.append(np.clip(start, -market.sizes, market.sizes).astype(int))
        else:
            starts.append(np.array([rng.integers(-size, size + 1) for size in market.sizes], dtype=int))

    best: tuple[tuple[bool, float, float], np.ndarray, float, float] | None = None
    starts_examined = 0

    for start in starts:
        qty = np.clip(np.array(start, dtype=int, copy=True), -market.sizes, market.sizes)
        cur_ev = ev_fast(qty)
        cur_var = var_fast(qty)
        starts_examined += 1

        for _ in range(coord_iters):
            changed = False
            for i in rng.permutation(market.n):
                vals = values[i]
                old = int(qty[i])
                old_offset = old + int(market.sizes[i])
                cross = float(cov_seed[i] @ qty - cov_seed[i, i] * old)
                var_candidates = cur_var + cov_seed[i, i] * (vals * vals - old * old) + 2.0 * (vals - old) * cross
                ev_candidates = cur_ev - ev_tables[i][old_offset] + ev_tables[i]
                gaps = np.maximum(0.0, target_ev - ev_candidates)
                feasible = gaps <= 1e-6
                scores = var_candidates + penalty * gaps * gaps
                idx = int(np.lexsort((-ev_candidates, var_candidates, ~feasible, scores))[0])
                new = int(vals[idx])

                if new != old:
                    qty[i] = new
                    cur_ev = float(ev_candidates[idx])
                    cur_var = float(var_candidates[idx])
                    changed = True

            if not changed:
                break

        feasible = cur_ev >= target_ev - 1e-6
        key = (not feasible, cur_var if feasible else target_ev - cur_ev, -cur_ev)
        if best is None or key < best[0]:
            best = (key, qty.copy(), cur_ev, cur_var)

    if best is None:
        return np.zeros(market.n, dtype=int), {"success": False, "message": "no starts examined"}

    qty = repair_ev_target(best[1], target_ev, market, cov_seed)
    ev = portfolio_ev(qty, market)
    variance = portfolio_seed_variance(qty, cov_seed)
    return qty, {
        "success": ev >= target_ev - 1e-6,
        "message": "coordinate descent",
        "starts": starts_examined,
        "ev": ev,
        "seed_variance": variance,
    }


def bootstrap_seed_summary(
    path_pnls: np.ndarray,
    *,
    seed_paths: int,
    samples: int,
    rng: np.random.Generator,
    chunk_size: int = 20_000,
) -> dict[str, float]:
    if samples <= 0:
        return {}

    seed_pnls = np.empty(samples, dtype=float)
    n_paths = len(path_pnls)
    offset = 0
    while offset < samples:
        n = min(chunk_size, samples - offset)
        idx = rng.integers(0, n_paths, size=(n, seed_paths), dtype=np.int32)
        seed_pnls[offset : offset + n] = path_pnls[idx].mean(axis=1)
        offset += n

    q1, var5, median, p95, p99 = np.percentile(seed_pnls, [1, 5, 50, 95, 99])
    var5 = float(var5)
    tail = seed_pnls[seed_pnls <= var5]
    return {
        "bootstrap_mean": float(seed_pnls.mean()),
        "bootstrap_std": float(seed_pnls.std(ddof=0)),
        "p_loss": float((seed_pnls < 0).mean()),
        "var1": float(q1),
        "var5": var5,
        "cvar5": float(tail.mean()) if len(tail) else var5,
        "median": float(median),
        "p95": float(p95),
        "p99": float(p99),
    }


def stats_for(
    qty: np.ndarray,
    market: MarketData,
    *,
    seed_paths: int,
    bootstrap_samples: int,
    rng: np.random.Generator,
) -> dict[str, float]:
    path_pnls = portfolio_path_pnls(qty, market)
    path_std = float(path_pnls.std(ddof=0))
    seed_std = path_std / math.sqrt(seed_paths)
    fair_ev = portfolio_ev(qty, market)
    path_mean = float(path_pnls.mean())
    stats = {
        "ev": fair_ev,
        "fair_ev": fair_ev,
        "path_mean": path_mean,
        "path_std": path_std,
        "seed_std": seed_std,
        "seed_variance": seed_std**2,
        "sharpe": fair_ev / seed_std if seed_std > 0 else math.inf,
    }
    stats.update(
        bootstrap_seed_summary(
            path_pnls,
            seed_paths=seed_paths,
            samples=bootstrap_samples,
            rng=rng,
        )
    )
    return stats


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def pct(value: float) -> str:
    return f"{100.0 * value:,.2f}%"


def format_qty(qty: np.ndarray, market: MarketData, *, include_zero: bool = True, sep: str = "\n") -> str:
    parts = []
    for cid, q in zip(market.ids, qty):
        if include_zero or q:
            parts.append(f"{cid}={int(q):+d}")
    return sep.join(parts) if parts else "all zero"


def table(rows: list[list[str]]) -> str:
    split_rows = [[[line for line in cell.splitlines()] or [""] for cell in row] for row in rows]
    widths = [
        max(max(len(line) for line in row[i]) for row in split_rows)
        for i in range(len(rows[0]))
    ]
    rendered: list[str] = []
    for row in split_rows:
        height = max(len(cell_lines) for cell_lines in row)
        for line_idx in range(height):
            rendered.append(
                "  ".join(
                    (cell_lines[line_idx] if line_idx < len(cell_lines) else "").ljust(widths[i])
                    for i, cell_lines in enumerate(row)
                )
            )
    return "\n".join(rendered)


def parse_fracs(raw: str) -> list[float]:
    if ":" in raw and "," not in raw:
        pieces = [piece.strip() for piece in raw.split(":")]
        if len(pieces) not in (2, 3):
            raise ValueError("range syntax is start:stop or start:stop:count")
        start = float(pieces[0])
        stop = float(pieces[1])
        count = int(pieces[2]) if len(pieces) == 3 else 25
        if start > 1.0:
            start /= 100.0
        if stop > 1.0:
            stop /= 100.0
        return [float(x) for x in np.linspace(start, stop, count)]

    fracs = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        value = float(piece)
        if value > 1.0:
            value /= 100.0
        fracs.append(value)
    return sorted(set(fracs))


def parse_chances(raw: str) -> list[float]:
    chances: list[float] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        value = float(piece)
        if value >= 1.0:
            value /= 100.0
        if not (0.0 < value < 1.0):
            raise ValueError("chance values must be between 0 and 1, e.g. 5 or 0.05")
        chances.append(value)
    return chances


def parse_optional_money_values(raw: str) -> list[float | None]:
    values: list[float | None] = []
    seen: set[str] = set()
    for piece in raw.split(","):
        piece = piece.strip().lower()
        if not piece:
            continue
        if piece in {"none", "uncapped", "inf", "infinite"}:
            key = "none"
            value = None
        else:
            value = float(piece.replace("_", "").replace("$", ""))
            if value < 0:
                raise ValueError("money caps must be nonnegative or none")
            key = f"{value:.12g}"
        if key not in seen:
            seen.add(key)
            values.append(value)
    return values


def build_frontier(
    market: MarketData,
    max_ev_qty: np.ndarray,
    *,
    seed_paths: int,
    target_fracs: list[float],
    method: str,
    maxiter: int,
    restarts: int,
    coord_iters: int,
    penalty: float,
    random_seed: int,
) -> list[dict[str, Any]]:
    cov_seed = seed_covariance(market, seed_paths)
    max_ev = portfolio_ev(max_ev_qty, market)
    rows = []
    previous_qtys: list[np.ndarray] = []
    for frac in target_fracs:
        target = max_ev * frac
        if method == "slsqp":
            qty, solver = optimize_min_variance_for_ev(
                market,
                cov_seed,
                target,
                max_ev_qty,
                maxiter=maxiter,
            )
        else:
            qty, solver = optimize_min_variance_for_ev_coordinate(
                market,
                cov_seed,
                target,
                max_ev_qty,
                restarts=restarts,
                coord_iters=coord_iters,
                penalty=penalty,
                random_seed=random_seed + len(rows),
                extra_starts=previous_qtys,
            )
        ev = portfolio_ev(qty, market)
        seed_var = portfolio_seed_variance(qty, cov_seed)
        seed_std = math.sqrt(max(seed_var, 0.0))
        previous_qtys.append(qty)
        rows.append(
            {
                "target_frac": frac,
                "target_ev": target,
                "qty": qty,
                "ev": ev,
                "seed_std": seed_std,
                "sharpe": ev / seed_std if seed_std > 0 else math.inf,
                "solver": solver,
            }
        )
    return rows


def sample_seed_payoffs(
    market: MarketData,
    *,
    seed_paths: int,
    samples: int,
    rng: np.random.Generator,
    chunk_size: int = 10_000,
) -> np.ndarray:
    seed_payoffs = np.empty((samples, market.n), dtype=float)
    offset = 0
    n_paths = len(market.payoffs)
    while offset < samples:
        n = min(chunk_size, samples - offset)
        idx = rng.integers(0, n_paths, size=(n, seed_paths), dtype=np.int32)
        seed_payoffs[offset : offset + n] = market.payoffs[idx].mean(axis=1)
        offset += n
    return seed_payoffs


def seed_pnls_from_seed_payoffs(qty: np.ndarray, market: MarketData, seed_payoffs: np.ndarray) -> np.ndarray:
    qty = np.asarray(qty, dtype=float)
    return (seed_payoffs @ qty - signed_cost(qty, market)) * market.multiplier


def instrument_contribution(vals: np.ndarray, instrument_seed_payoffs: np.ndarray, bid: float, ask: float, multiplier: float) -> np.ndarray:
    vals = vals.astype(float)
    costs = np.where(vals >= 0, vals * ask, vals * bid)
    return (vals[:, None] * instrument_seed_payoffs[None, :] - costs[:, None]) * multiplier


def seed_distribution_summary(seed_pnls: np.ndarray, *, lottery_percentile: float) -> dict[str, float]:
    q1, var5, median, p95, p99, lottery_quantile = np.percentile(
        seed_pnls,
        [1, 5, 50, 95, 99, lottery_percentile],
    )
    var5 = float(var5)
    tail = seed_pnls[seed_pnls <= var5]
    return {
        "bootstrap_mean": float(seed_pnls.mean()),
        "bootstrap_std": float(seed_pnls.std(ddof=0)),
        "p_loss": float((seed_pnls < 0).mean()),
        "var1": float(q1),
        "var5": var5,
        "cvar5": float(tail.mean()) if len(tail) else var5,
        "median": float(median),
        "p95": float(p95),
        "p99": float(p99),
        "lottery_quantile": float(lottery_quantile),
    }


def optimize_lottery_quantile(
    market: MarketData,
    *,
    seed_payoffs: np.ndarray,
    percentile: float,
    min_ev: float,
    max_ev_qty: np.ndarray,
    restarts: int,
    coord_iters: int,
    random_seed: int,
    extra_starts: Iterable[np.ndarray] = (),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Maximize an upper seed-PnL quantile, optionally with an EV floor."""

    rng = np.random.default_rng(random_seed)
    values, ev_tables = quantity_ev_tables(market)

    def ev_fast(qty: np.ndarray) -> float:
        return float(sum(ev_tables[i][int(qty[i]) + int(market.sizes[i])] for i in range(market.n)))

    starts: list[np.ndarray] = [
        np.zeros(market.n, dtype=int),
        np.array(max_ev_qty, dtype=int, copy=True),
        np.array(market.sizes, dtype=int, copy=True),
        -np.array(market.sizes, dtype=int, copy=True),
    ]
    starts.extend(np.rint(max_ev_qty * frac).astype(int) for frac in np.linspace(0.25, 1.0, 4))
    starts.extend(np.array(start, dtype=int, copy=True) for start in extra_starts)

    for _ in range(max(0, restarts)):
        mode = rng.random()
        if mode < 0.45:
            frac = rng.uniform(0.0, 1.2)
            noise = np.array([rng.integers(-max(1, size // 8), max(2, size // 8 + 1)) for size in market.sizes])
            start = np.rint(max_ev_qty * frac).astype(int) + noise
        elif mode < 0.75:
            signs = rng.choice(np.array([-1, 0, 1], dtype=int), size=market.n, p=[0.35, 0.15, 0.50])
            start = signs * market.sizes
        else:
            start = np.array([rng.integers(-size, size + 1) for size in market.sizes], dtype=int)
        starts.append(np.clip(start, -market.sizes, market.sizes).astype(int))

    best: tuple[tuple[bool, float, float], np.ndarray, float, float] | None = None
    starts_examined = 0

    for start in starts:
        qty = np.clip(np.array(start, dtype=int, copy=True), -market.sizes, market.sizes)
        cur_ev = ev_fast(qty)
        cur_seed_pnls = seed_pnls_from_seed_payoffs(qty, market, seed_payoffs)
        starts_examined += 1

        for _ in range(coord_iters):
            changed = False
            for i in rng.permutation(market.n):
                vals = values[i]
                old = int(qty[i])
                old_offset = old + int(market.sizes[i])
                old_contrib = instrument_contribution(
                    np.array([old], dtype=int),
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )[0]
                base_seed_pnls = cur_seed_pnls - old_contrib
                contrib = instrument_contribution(
                    vals,
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )
                candidate_seed_pnls = base_seed_pnls[None, :] + contrib
                quantiles = np.percentile(candidate_seed_pnls, percentile, axis=1)
                ev_candidates = cur_ev - ev_tables[i][old_offset] + ev_tables[i]
                feasible = ev_candidates >= min_ev - 1e-6

                if np.any(feasible):
                    masked = np.where(feasible, quantiles, -np.inf)
                    idx = int(np.lexsort((-ev_candidates, -quantiles, -masked))[0])
                else:
                    gaps = min_ev - ev_candidates
                    idx = int(np.lexsort((-quantiles, gaps))[0])

                new = int(vals[idx])
                if new != old:
                    qty[i] = new
                    cur_ev = float(ev_candidates[idx])
                    cur_seed_pnls = candidate_seed_pnls[idx].copy()
                    changed = True

            if not changed:
                break

        quantile = float(np.percentile(cur_seed_pnls, percentile))
        feasible = cur_ev >= min_ev - 1e-6
        key = (not feasible, -quantile if feasible else min_ev - cur_ev, -cur_ev)
        if best is None or key < best[0]:
            best = (key, qty.copy(), cur_ev, quantile)

    if best is None:
        return np.zeros(market.n, dtype=int), {"success": False, "message": "no starts examined"}

    qty = best[1]
    seed_pnls = seed_pnls_from_seed_payoffs(qty, market, seed_payoffs)
    summary = seed_distribution_summary(seed_pnls, lottery_percentile=percentile)
    return qty, {
        "success": portfolio_ev(qty, market) >= min_ev - 1e-6,
        "message": "lottery coordinate ascent",
        "starts": starts_examined,
        "percentile": percentile,
        "min_ev": min_ev,
        "fair_ev": portfolio_ev(qty, market),
        **summary,
    }


def _row_percentiles_nearest(candidate_seed_pnls: np.ndarray, percentiles: Iterable[float]) -> np.ndarray:
    """Fast row-wise nearest percentiles for inner-loop search scoring."""

    rows = np.asarray(candidate_seed_pnls)
    qs = np.asarray(list(percentiles), dtype=float)
    if rows.ndim != 2:
        raise ValueError("candidate_seed_pnls must be 2D")
    if rows.shape[1] == 0:
        raise ValueError("candidate_seed_pnls must have at least one sample")
    kth = np.rint(np.clip(qs, 0.0, 100.0) * (rows.shape[1] - 1) / 100.0).astype(int)
    unique_kth = np.unique(kth)
    partitioned = np.partition(rows, unique_kth, axis=1)
    return partitioned[:, kth].T


def _row_var5_upper_cvar5_nearest(candidate_seed_pnls: np.ndarray, upper_percentile: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fast VaR5, upper quantile, and CVaR5 loss for coordinate-search rows."""

    rows = np.asarray(candidate_seed_pnls)
    if rows.ndim != 2:
        raise ValueError("candidate_seed_pnls must be 2D")
    if rows.shape[1] == 0:
        raise ValueError("candidate_seed_pnls must have at least one sample")
    n = rows.shape[1]
    kth5 = int(round(0.05 * (n - 1)))
    kth_upper = int(round(np.clip(upper_percentile, 0.0, 100.0) * (n - 1) / 100.0))
    partitioned = np.partition(rows, np.unique([kth5, kth_upper]), axis=1)
    var5 = partitioned[:, kth5]
    upper = partitioned[:, kth_upper]
    cvar5_loss = np.maximum(0.0, -partitioned[:, : kth5 + 1].mean(axis=1))
    return var5, upper, cvar5_loss


def _row_cvar_loss_from_var(candidate_seed_pnls: np.ndarray, var: np.ndarray) -> np.ndarray:
    mask = candidate_seed_pnls <= np.asarray(var)[:, None]
    tail_sum = np.where(mask, candidate_seed_pnls, 0.0).sum(axis=1)
    tail_count = np.maximum(mask.sum(axis=1), 1)
    return np.maximum(0.0, -(tail_sum / tail_count))


def _row_cvar_loss(candidate_seed_pnls: np.ndarray, alpha_percentile: float = 5.0) -> np.ndarray:
    """Row-wise positive CVaR loss: max(0, -E[PnL | PnL <= VaR_alpha])."""

    var = np.percentile(candidate_seed_pnls, alpha_percentile, axis=1)
    return _row_cvar_loss_from_var(candidate_seed_pnls, var)


def tail_profile(seed_pnls: np.ndarray, *, chance: float) -> dict[str, float]:
    """Summarize a portfolio as 'make at least X with chance p' plus downside."""

    percentile = 100.0 * (1.0 - chance)
    floor = float(np.percentile(seed_pnls, percentile))
    var5 = float(np.percentile(seed_pnls, 5))
    cvar5_loss = float(_row_cvar_loss(seed_pnls[None, :], 5.0)[0])

    return {
        "chance": float(chance),
        "percentile": float(percentile),
        "make_at_least": floor,
        "actual_hit_prob": float((seed_pnls >= floor).mean()),
        "mean": float(seed_pnls.mean()),
        "std": float(seed_pnls.std(ddof=0)),
        "p_loss": float((seed_pnls < 0).mean()),
        "var5": var5,
        "var5_loss": max(0.0, -var5),
        "cvar5_loss": cvar5_loss,
        "median": float(np.median(seed_pnls)),
        "p90": float(np.percentile(seed_pnls, 90)),
        "p95": float(np.percentile(seed_pnls, 95)),
        "p99": float(np.percentile(seed_pnls, 99)),
    }


def optimize_upper_quantile_with_risk_cap(
    market: MarketData,
    *,
    seed_payoffs: np.ndarray,
    chance: float,
    max_ev_qty: np.ndarray,
    min_ev: float = 0.0,
    max_cvar5_loss: float | None = None,
    max_var5_loss: float | None = None,
    max_loss_prob: float | None = None,
    restarts: int = 150,
    coord_iters: int = 50,
    grid_points: int = 0,
    random_seed: int = 1,
    extra_starts: Iterable[np.ndarray] = (),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Maximize the PnL floor hit with probability `chance`, subject to risk caps."""

    if not (0.0 < chance < 1.0):
        raise ValueError("chance must be between 0 and 1, e.g. 0.05 for 5%")

    rng = np.random.default_rng(random_seed)
    _, full_ev_tables, values, ev_tables = quantity_search_tables(
        market,
        max_ev_qty=max_ev_qty,
        grid_points=grid_points,
    )
    percentile = 100.0 * (1.0 - chance)

    def ev_fast(qty: np.ndarray) -> float:
        return float(sum(full_ev_tables[i][int(qty[i]) + int(market.sizes[i])] for i in range(market.n)))

    def violation_from_metrics(
        *,
        fair_ev: float,
        p_loss: float,
        var5_loss: float,
        cvar5_loss: float,
    ) -> float:
        violation = 0.0

        if fair_ev < min_ev:
            violation += (min_ev - fair_ev) / max(abs(min_ev), 1.0)
        if max_loss_prob is not None and p_loss > max_loss_prob:
            violation += (p_loss - max_loss_prob) / max(max_loss_prob, 1e-9)
        if max_var5_loss is not None and var5_loss > max_var5_loss:
            violation += (var5_loss - max_var5_loss) / max(max_var5_loss, 1.0)
        if max_cvar5_loss is not None and cvar5_loss > max_cvar5_loss:
            violation += (cvar5_loss - max_cvar5_loss) / max(max_cvar5_loss, 1.0)

        return float(violation)

    starts: list[np.ndarray] = [
        np.zeros(market.n, dtype=int),
        np.array(max_ev_qty, dtype=int, copy=True),
    ]
    if restarts >= 4:
        starts.extend(
            [
                np.array(market.sizes, dtype=int, copy=True),
                -np.array(market.sizes, dtype=int, copy=True),
            ]
        )

    scaled_count = 13 if restarts >= 20 else max(3, min(7, restarts + 3))
    starts.extend(np.rint(max_ev_qty * frac).astype(int) for frac in np.linspace(0.05, 1.25, scaled_count))
    starts.extend(np.array(start, dtype=int, copy=True) for start in extra_starts)

    for _ in range(max(0, restarts)):
        mode = rng.random()
        if mode < 0.40:
            frac = rng.uniform(0.0, 1.35)
            noise = np.array([rng.integers(-max(1, size // 6), max(2, size // 6 + 1)) for size in market.sizes])
            start = np.rint(max_ev_qty * frac).astype(int) + noise
        elif mode < 0.70:
            start = np.zeros(market.n, dtype=int)
            for i, size in enumerate(market.sizes):
                if rng.random() < 0.55:
                    start[i] = int(rng.integers(-size, size + 1))
        elif mode < 0.90:
            signs = rng.choice(np.array([-1, 0, 1], dtype=int), size=market.n, p=[0.35, 0.20, 0.45])
            start = signs * market.sizes
        else:
            start = np.array([rng.integers(-size, size + 1) for size in market.sizes], dtype=int)

        starts.append(np.clip(start, -market.sizes, market.sizes).astype(int))

    best: tuple[tuple[float, float, float, float, float], np.ndarray, np.ndarray] | None = None
    starts_examined = 0

    for start in starts:
        qty = np.clip(np.array(start, dtype=int, copy=True), -market.sizes, market.sizes)
        cur_ev = ev_fast(qty)
        cur_seed_pnls = seed_pnls_from_seed_payoffs(qty, market, seed_payoffs)
        starts_examined += 1

        for _ in range(coord_iters):
            changed = False

            for i in rng.permutation(market.n):
                vals = values[i]
                old = int(qty[i])
                old_offset = old + int(market.sizes[i])
                if not np.any(vals == old):
                    vals = np.sort(np.append(vals, old)).astype(int)
                    ev_table = full_ev_tables[i][vals + int(market.sizes[i])]
                else:
                    ev_table = ev_tables[i]

                old_contrib = instrument_contribution(
                    np.array([old], dtype=int),
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )[0]
                base_seed_pnls = cur_seed_pnls - old_contrib
                contrib = instrument_contribution(
                    vals,
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )

                candidate_seed_pnls = base_seed_pnls[None, :] + contrib
                ev_candidates = cur_ev - full_ev_tables[i][old_offset] + ev_table

                var5, upper_quantiles, cvar5_loss = _row_var5_upper_cvar5_nearest(candidate_seed_pnls, percentile)
                var5_loss = np.maximum(0.0, -var5)
                p_loss = (candidate_seed_pnls < 0).mean(axis=1)

                violations = np.maximum(0.0, min_ev - ev_candidates) / max(abs(min_ev), 1.0)
                if max_loss_prob is not None:
                    violations += np.maximum(0.0, p_loss - max_loss_prob) / max(max_loss_prob, 1e-9)
                if max_var5_loss is not None:
                    violations += np.maximum(0.0, var5_loss - max_var5_loss) / max(max_var5_loss, 1.0)
                if max_cvar5_loss is not None:
                    violations += np.maximum(0.0, cvar5_loss - max_cvar5_loss) / max(max_cvar5_loss, 1.0)

                feasible = violations <= 1e-12
                if np.any(feasible):
                    masked = np.where(feasible, upper_quantiles, -np.inf)
                    idx = int(np.lexsort((-ev_candidates, cvar5_loss, -masked))[0])
                else:
                    idx = int(np.lexsort((-upper_quantiles, violations))[0])

                new = int(vals[idx])
                if new != old:
                    qty[i] = new
                    cur_ev = float(ev_candidates[idx])
                    cur_seed_pnls = candidate_seed_pnls[idx].copy()
                    changed = True

            if not changed:
                break

        profile = tail_profile(cur_seed_pnls, chance=chance)
        violation = violation_from_metrics(
            fair_ev=cur_ev,
            p_loss=profile["p_loss"],
            var5_loss=profile["var5_loss"],
            cvar5_loss=profile["cvar5_loss"],
        )
        key = (
            1.0 if violation <= 1e-12 else 0.0,
            -violation,
            profile["make_at_least"],
            -profile["cvar5_loss"],
            cur_ev,
        )

        if best is None or key > best[0]:
            best = (key, qty.copy(), cur_seed_pnls.copy())

    if best is None:
        return np.zeros(market.n, dtype=int), {"success": False, "message": "no starts examined"}

    qty = best[1]
    seed_pnls = best[2]
    fair_ev = portfolio_ev(qty, market)
    profile = tail_profile(seed_pnls, chance=chance)
    violation = violation_from_metrics(
        fair_ev=fair_ev,
        p_loss=profile["p_loss"],
        var5_loss=profile["var5_loss"],
        cvar5_loss=profile["cvar5_loss"],
    )

    return qty, {
        "success": violation <= 1e-12,
        "message": "upper-quantile risk-capped coordinate search",
        "starts": starts_examined,
        "chance": chance,
        "percentile": percentile,
        "fair_ev": fair_ev,
        "constraint_violation": violation,
        "max_cvar5_loss": max_cvar5_loss,
        "max_var5_loss": max_var5_loss,
        "max_loss_prob": max_loss_prob,
        "min_ev": min_ev,
        **profile,
    }


def build_achievable_value_menu(
    market: MarketData,
    *,
    seed_payoffs: np.ndarray,
    max_ev_qty: np.ndarray,
    chances: Iterable[float] = (0.50, 0.25, 0.10, 0.05, 0.01),
    cvar5_caps: Iterable[float | None] = (250_000.0,),
    min_ev: float = 0.0,
    max_loss_prob: float | None = None,
    restarts: int = 150,
    coord_iters: int = 50,
    grid_points: int = 0,
    random_seed: int = 1,
    extra_starts: Iterable[np.ndarray] = (),
) -> list[dict[str, Any]]:
    """Build a menu of achievable PnL floors by hit probability and downside cap."""

    rows: list[dict[str, Any]] = []
    warm_starts = [np.array(start, dtype=int, copy=True) for start in extra_starts]

    run_id = 0
    for cap in cvar5_caps:
        for chance in chances:
            qty, info = optimize_upper_quantile_with_risk_cap(
                market,
                seed_payoffs=seed_payoffs,
                chance=chance,
                max_ev_qty=max_ev_qty,
                min_ev=min_ev,
                max_cvar5_loss=cap,
                max_loss_prob=max_loss_prob,
                restarts=restarts,
                coord_iters=coord_iters,
                grid_points=grid_points,
                random_seed=random_seed + run_id,
                extra_starts=warm_starts,
            )
            warm_starts.append(qty)
            rows.append({"qty": qty, "cap": cap, **info})
            run_id += 1

    return rows


def print_achievable_value_menu(rows: list[dict[str, Any]], market: MarketData) -> None:
    """Pretty-print the achievable value menu."""

    out = [
        [
            "CVaR5 cap",
            "chance",
            "make at least",
            "EV",
            "P(loss)",
            "VaR5",
            "CVaR5 loss",
            "hit",
            "ok",
            "qty",
        ]
    ]

    for row in rows:
        cap = "none" if row["cap"] is None else money(float(row["cap"]))
        out.append(
            [
                cap,
                pct(float(row["chance"])),
                money(float(row["make_at_least"])),
                money(float(row["fair_ev"])),
                pct(float(row["p_loss"])),
                money(float(row["var5"])),
                money(float(row["cvar5_loss"])),
                pct(float(row["actual_hit_prob"])),
                "yes" if row["success"] else "no",
                format_qty(row["qty"], market),
            ]
        )

    print(table(out))


def log_growth_summary(seed_pnls: np.ndarray, *, capital: float, scale: float = 1.0) -> dict[str, float]:
    returns = scale * seed_pnls / capital
    ruin = returns <= -1.0
    if np.any(~ruin):
        clipped_logs = np.log1p(returns[~ruin])
        mean_log_survivors = float(clipped_logs.mean())
    else:
        mean_log_survivors = -math.inf

    if np.any(ruin):
        mean_log = -math.inf
        growth_equivalent = -1.0
    else:
        mean_log = mean_log_survivors
        growth_equivalent = float(math.expm1(mean_log))

    mu = float(returns.mean())
    var = float(returns.var(ddof=0))
    full_kelly_scale = mu / var if var > 0 else math.inf
    worst_return = float(returns.min())
    max_safe_scale = math.inf if worst_return >= 0 else 0.999 / abs(worst_return)

    return {
        "mean_log_growth": mean_log,
        "mean_log_survivors": mean_log_survivors,
        "growth_equivalent": growth_equivalent,
        "ruin_probability": float(ruin.mean()),
        "mean_return": mu,
        "return_variance": var,
        "full_kelly_scale": full_kelly_scale,
        "fractional_kelly_scale": min(scale * full_kelly_scale, max_safe_scale) if full_kelly_scale > 0 else 0.0,
        "max_safe_scale": max_safe_scale,
        "worst_return": worst_return,
    }


def log_utility_scores(candidate_seed_pnls: np.ndarray, *, capital: float, scale: float) -> tuple[np.ndarray, np.ndarray]:
    returns = scale * candidate_seed_pnls / capital
    min_returns = returns.min(axis=1)
    valid = min_returns > -1.0
    scores = np.full(candidate_seed_pnls.shape[0], -np.inf, dtype=float)
    if np.any(valid):
        scores[valid] = np.log1p(returns[valid]).mean(axis=1)
    return scores, min_returns


def optimize_log_utility_coordinate(
    market: MarketData,
    *,
    seed_payoffs: np.ndarray,
    capital: float,
    kelly_fraction: float,
    min_ev: float,
    gross_limit: float,
    long_only: bool,
    max_ev_qty: np.ndarray,
    restarts: int,
    coord_iters: int,
    random_seed: int,
    extra_starts: Iterable[np.ndarray] = (),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Maximize sampled expected log growth over integer positions."""

    if capital <= 0:
        raise ValueError("capital must be positive")
    if kelly_fraction <= 0:
        raise ValueError("kelly_fraction must be positive")

    rng = np.random.default_rng(random_seed)
    values, ev_tables = quantity_ev_tables(market)
    notional_coeff = 0.5 * (market.bids + market.asks) * market.multiplier
    gross_cap = gross_limit * capital if gross_limit > 0 else math.inf

    def ev_fast(qty: np.ndarray) -> float:
        return float(sum(ev_tables[i][int(qty[i]) + int(market.sizes[i])] for i in range(market.n)))

    def normalize_start(qty: np.ndarray) -> np.ndarray:
        qty = np.clip(np.array(qty, dtype=int, copy=True), -market.sizes, market.sizes)
        if long_only:
            qty = np.maximum(qty, 0)
        if gross_limit > 0:
            gross = gross_notional(qty, market)
            if gross > gross_cap and gross > 0:
                qty = round_qty(qty * (gross_cap / gross), market)
                if long_only:
                    qty = np.maximum(qty, 0)
        return qty

    starts: list[np.ndarray] = [
        np.zeros(market.n, dtype=int),
        np.array(max_ev_qty, dtype=int, copy=True),
    ]
    starts.extend(np.rint(max_ev_qty * frac).astype(int) for frac in (0.05, 0.10, 0.20, 0.35, 0.50, 0.75))
    starts.extend(np.array(start, dtype=int, copy=True) for start in extra_starts)

    for _ in range(max(0, restarts)):
        mode = rng.random()
        if mode < 0.40:
            frac = rng.uniform(0.0, 0.85)
            noise = np.array([rng.integers(-max(1, size // 5), max(2, size // 5 + 1)) for size in market.sizes])
            start = np.rint(max_ev_qty * frac).astype(int) + noise
        elif mode < 0.70:
            start = np.zeros(market.n, dtype=int)
            for i, size in enumerate(market.sizes):
                if rng.random() < 0.50:
                    start[i] = int(rng.integers(-size, size + 1))
        elif mode < 0.90:
            signs = rng.choice(np.array([-1, 0, 1], dtype=int), size=market.n, p=[0.35, 0.35, 0.30])
            start = signs * market.sizes
        else:
            start = np.array([rng.integers(-size, size + 1) for size in market.sizes], dtype=int)
        starts.append(normalize_start(start))

    best: tuple[tuple[float, float, float], np.ndarray, np.ndarray] | None = None
    starts_examined = 0

    for start in starts:
        qty = normalize_start(start)
        cur_ev = ev_fast(qty)
        cur_gross = gross_notional(qty, market)
        cur_seed_pnls = seed_pnls_from_seed_payoffs(qty, market, seed_payoffs)
        starts_examined += 1

        for _ in range(coord_iters):
            changed = False
            for i in rng.permutation(market.n):
                vals = values[i]
                old = int(qty[i])
                old_offset = old + int(market.sizes[i])
                old_contrib = instrument_contribution(
                    np.array([old], dtype=int),
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )[0]
                base_seed_pnls = cur_seed_pnls - old_contrib
                contrib = instrument_contribution(
                    vals,
                    seed_payoffs[:, i],
                    float(market.bids[i]),
                    float(market.asks[i]),
                    market.multiplier,
                )
                candidate_seed_pnls = base_seed_pnls[None, :] + contrib
                ev_candidates = cur_ev - ev_tables[i][old_offset] + ev_tables[i]
                gross_candidates = cur_gross - abs(old) * notional_coeff[i] + np.abs(vals) * notional_coeff[i]
                log_scores, min_returns = log_utility_scores(
                    candidate_seed_pnls,
                    capital=capital,
                    scale=kelly_fraction,
                )
                feasible = (ev_candidates >= min_ev - 1e-6) & (gross_candidates <= gross_cap + 1e-6)
                if long_only:
                    feasible &= vals >= 0

                if np.any(feasible & np.isfinite(log_scores)):
                    masked_scores = np.where(feasible, log_scores, -np.inf)
                    idx = int(np.lexsort((-ev_candidates, -masked_scores))[0])
                elif np.any(np.isfinite(log_scores)):
                    ev_gaps = np.maximum(0.0, min_ev - ev_candidates)
                    gross_gaps = np.maximum(0.0, gross_candidates - gross_cap)
                    long_gaps = np.maximum(0, -vals) if long_only else np.zeros_like(vals)
                    idx = int(np.lexsort((-log_scores, ev_gaps, gross_gaps, long_gaps))[0])
                else:
                    gross_gaps = np.maximum(0.0, gross_candidates - gross_cap)
                    idx = int(np.lexsort((-ev_candidates, gross_gaps, -min_returns))[0])

                new = int(vals[idx])
                if new != old:
                    qty[i] = new
                    cur_ev = float(ev_candidates[idx])
                    cur_gross = float(gross_candidates[idx])
                    cur_seed_pnls = candidate_seed_pnls[idx].copy()
                    changed = True

            if not changed:
                break

        summary = log_growth_summary(cur_seed_pnls, capital=capital, scale=kelly_fraction)
        score = summary["mean_log_growth"]
        feasible = cur_ev >= min_ev - 1e-6 and cur_gross <= gross_cap + 1e-6 and np.isfinite(score)
        key = (score if feasible else -math.inf, cur_ev, -float(cur_seed_pnls.var(ddof=0)))
        if best is None or key > best[0]:
            best = (key, qty.copy(), cur_seed_pnls.copy())

    if best is None:
        return np.zeros(market.n, dtype=int), {"success": False, "message": "no starts examined"}

    qty = best[1]
    seed_pnls = best[2]
    fractional_summary = log_growth_summary(seed_pnls, capital=capital, scale=kelly_fraction)
    actual_summary = log_growth_summary(seed_pnls, capital=capital, scale=1.0)
    distribution = seed_distribution_summary(seed_pnls, lottery_percentile=95.0)
    return qty, {
        "success": np.isfinite(fractional_summary["mean_log_growth"]),
        "message": "integer coordinate log-utility search",
        "starts": starts_examined,
        "capital": capital,
        "kelly_fraction": kelly_fraction,
        "min_ev": min_ev,
        "fair_ev": portfolio_ev(qty, market),
        "fractional_log": fractional_summary,
        "actual_log": actual_summary,
        **distribution,
    }


def gross_notional(qty: np.ndarray, market: MarketData) -> float:
    mids = 0.5 * (market.bids + market.asks)
    return float(np.abs(qty).astype(float) @ mids * market.multiplier)


def format_float_qty(qty: np.ndarray, market: MarketData, *, threshold: float = 0.05) -> str:
    parts = []
    for cid, q in zip(market.ids, qty):
        if abs(float(q)) >= threshold:
            parts.append(f"{cid}={float(q):+.2f}")
    return ", ".join(parts) if parts else "all zero"


def optimize_portfolio_kelly_qp(
    market: MarketData,
    *,
    cov_seed: np.ndarray,
    capital: float,
    kelly_fraction: float,
    mu_shrink: float,
    gross_limit: float,
    long_only: bool,
    max_ev_qty: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Continuous multi-asset Kelly approximation with position constraints.

    The objective is the second-order log-growth approximation:
        shrink*E[PnL] - Var(PnL) / (2 * fractional_kelly * capital)
    over split long/short quantities.
    """

    if capital <= 0:
        raise ValueError("capital must be positive")
    if kelly_fraction <= 0:
        raise ValueError("kelly_fraction must be positive")
    if minimize is None:
        rounded = exact_max_ev_quantities(market)
        return rounded.astype(float), rounded, {"success": False, "message": "scipy unavailable"}

    n = market.n
    eye = np.eye(n)
    split_to_qty = np.concatenate([eye, -eye], axis=1)
    cov_split = split_to_qty.T @ cov_seed @ split_to_qty
    long_edges = (market.fair_values - market.asks) * market.multiplier
    short_edges = (market.bids - market.fair_values) * market.multiplier
    edge = np.concatenate([long_edges, short_edges]) * mu_shrink
    risk_aversion = 1.0 / (kelly_fraction * capital)

    long_bounds = [(0.0, float(size)) for size in market.sizes]
    short_bounds = [(0.0, 0.0 if long_only else float(size)) for size in market.sizes]
    bounds = long_bounds + short_bounds

    def objective(x: np.ndarray) -> float:
        return float(-(edge @ x) + 0.5 * risk_aversion * (x @ cov_split @ x))

    def jacobian(x: np.ndarray) -> np.ndarray:
        return -edge + risk_aversion * (cov_split @ x)

    constraints = []
    if gross_limit > 0:
        mids = 0.5 * (market.bids + market.asks) * market.multiplier
        gross_coeff = np.concatenate([mids, mids])
        gross_cap = gross_limit * capital
        constraints.append({"type": "ineq", "fun": lambda x: gross_cap - float(gross_coeff @ x), "jac": lambda x: -gross_coeff})

    starts = [
        np.zeros(2 * n, dtype=float),
        split_from_qty(max_ev_qty),
        split_from_qty(np.rint(max_ev_qty * min(1.0, kelly_fraction)).astype(int)),
    ]

    best_result = None
    for x0 in starts:
        result = minimize(
            objective,
            x0,
            method="SLSQP",
            jac=jacobian,
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1_000, "ftol": 1e-9, "disp": False},
        )
        if best_result is None or result.fun < best_result.fun:
            best_result = result

    assert best_result is not None
    qty_cont = qty_from_split(best_result.x, n)
    qty_round = round_qty(qty_cont, market)
    if gross_limit > 0 and gross_notional(qty_round, market) > gross_limit * capital:
        scale = (gross_limit * capital) / gross_notional(qty_round, market)
        qty_round = round_qty(qty_round * scale, market)

    ev = portfolio_ev(qty_cont, market)
    seed_var = portfolio_seed_variance(qty_cont, cov_seed)
    return qty_cont, qty_round, {
        "success": bool(best_result.success),
        "message": str(best_result.message),
        "objective": -float(best_result.fun),
        "fair_ev": ev,
        "seed_std": math.sqrt(max(seed_var, 0.0)),
        "approx_log_growth": (ev / capital) - 0.5 * seed_var / (capital * capital),
        "gross_notional": gross_notional(qty_cont, market),
        "mu_shrink": mu_shrink,
        "kelly_fraction": kelly_fraction,
        "gross_limit": gross_limit,
        "long_only": long_only,
    }


def side_return_matrix(
    market: MarketData,
    seed_payoffs: np.ndarray,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Return per $1 allocated to each long/short side."""

    cols = []
    labels = []
    unit_prices = []
    for i, cid in enumerate(market.ids):
        ask = float(market.asks[i])
        bid = float(market.bids[i])
        cols.append((seed_payoffs[:, i] - ask) / ask)
        labels.append(f"{cid}:long")
        unit_prices.append(ask)
        cols.append((bid - seed_payoffs[:, i]) / bid)
        labels.append(f"{cid}:short")
        unit_prices.append(bid)
    return np.column_stack(cols), labels, np.array(unit_prices, dtype=float)


def return_summary(returns: np.ndarray) -> dict[str, float]:
    mean = float(returns.mean())
    std = float(returns.std(ddof=0))
    valid = returns > -1.0
    mean_log = float(np.log1p(returns[valid]).mean()) if np.all(valid) else -math.inf
    return {
        "mean": mean,
        "std": std,
        "sharpe": mean / std if std > 0 else math.inf,
        "mean_log": mean_log,
        "growth_equivalent": float(math.expm1(mean_log)) if np.isfinite(mean_log) else -1.0,
        "p_loss": float((returns < 0).mean()),
        "var5": float(np.percentile(returns, 5)),
        "var1": float(np.percentile(returns, 1)),
        "median": float(np.median(returns)),
        "p95": float(np.percentile(returns, 95)),
    }


def optimize_return_space_kelly(
    market: MarketData,
    *,
    seed_payoffs: np.ndarray,
    kelly_fraction: float,
    mu_shrink: float,
    max_side_weight: float,
    ridge: float,
) -> tuple[np.ndarray, list[str], dict[str, float]]:
    """Capital-independent Kelly weights over long/short instrument sides."""

    returns, labels, _ = side_return_matrix(market, seed_payoffs)
    mu = returns.mean(axis=0)
    cov = np.cov(returns, rowvar=False, ddof=0)
    avg_var = float(np.trace(cov) / max(1, cov.shape[0]))
    cov = cov + np.eye(cov.shape[0]) * max(ridge, ridge * avg_var)
    n = len(mu)

    if minimize is None:
        raw = np.linalg.pinv(cov) @ (mu * mu_shrink)
        weights = np.clip(raw * kelly_fraction, 0.0, max_side_weight)
        gross = weights.sum()
        if gross > 1.0:
            weights /= gross
    else:
        risk_aversion = 1.0 / kelly_fraction

        def objective(w: np.ndarray) -> float:
            return float(-(mu_shrink * mu @ w) + 0.5 * risk_aversion * (w @ cov @ w))

        def jacobian(w: np.ndarray) -> np.ndarray:
            return -(mu_shrink * mu) + risk_aversion * (cov @ w)

        score = np.maximum(mu / np.maximum(np.diag(cov), 1e-12), 0.0)
        x0 = score / score.sum() if score.sum() > 0 else np.zeros(n, dtype=float)
        x0 = np.minimum(x0, max_side_weight)
        if x0.sum() > 1.0:
            x0 /= x0.sum()
        result = minimize(
            objective,
            x0,
            method="SLSQP",
            jac=jacobian,
            bounds=[(0.0, max_side_weight)] * n,
            constraints=[{"type": "ineq", "fun": lambda w: 1.0 - float(w.sum()), "jac": lambda w: -np.ones_like(w)}],
            options={"maxiter": 1_000, "ftol": 1e-11, "disp": False},
        )
        weights = np.clip(result.x, 0.0, max_side_weight)
        if weights.sum() > 1.0:
            weights /= weights.sum()

    portfolio_returns = returns @ weights
    summary = return_summary(portfolio_returns)
    summary.update(
        {
            "gross_weight": float(weights.sum()),
            "kelly_fraction": kelly_fraction,
            "mu_shrink": mu_shrink,
        }
    )
    return weights, labels, summary


def signed_weights_from_side_weights(weights: np.ndarray, market: MarketData) -> np.ndarray:
    signed = np.zeros(market.n, dtype=float)
    for i in range(market.n):
        signed[i] = float(weights[2 * i] - weights[2 * i + 1])
    return signed


def limit_scaled_qty_from_weights(weights: np.ndarray, market: MarketData) -> tuple[np.ndarray, float]:
    signed = signed_weights_from_side_weights(weights, market)
    caps = []
    for i, w in enumerate(signed):
        if abs(w) < 1e-12:
            continue
        unit_price = float(market.asks[i] if w > 0 else market.bids[i]) * market.multiplier
        caps.append(float(market.sizes[i]) * unit_price / abs(w))
    scale = min(caps) if caps else 0.0
    qty = np.zeros(market.n, dtype=int)
    for i, w in enumerate(signed):
        if abs(w) < 1e-12:
            continue
        unit_price = float(market.asks[i] if w > 0 else market.bids[i]) * market.multiplier
        qty[i] = int(np.clip(round(scale * w / unit_price), -market.sizes[i], market.sizes[i]))
    return qty, scale


def format_weights(weights: np.ndarray, labels: list[str], *, threshold: float = 0.005) -> str:
    parts = []
    for label, weight in sorted(zip(labels, weights), key=lambda item: abs(item[1]), reverse=True):
        if abs(float(weight)) >= threshold:
            parts.append(f"{label}={100.0 * float(weight):.1f}%")
    return ", ".join(parts) if parts else "all zero"


def polish_max_sharpe_coordinate(
    qty: np.ndarray,
    market: MarketData,
    cov_seed: np.ndarray,
    *,
    min_ev: float,
    coord_iters: int,
    random_seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(random_seed)
    values, ev_tables = quantity_ev_tables(market)
    qty = np.clip(np.array(qty, dtype=int, copy=True), -market.sizes, market.sizes)

    def ev_fast(q: np.ndarray) -> float:
        return float(sum(ev_tables[i][int(q[i]) + int(market.sizes[i])] for i in range(market.n)))

    def var_fast(q: np.ndarray) -> float:
        q_float = q.astype(float)
        return float(q_float @ cov_seed @ q_float)

    cur_ev = ev_fast(qty)
    cur_var = var_fast(qty)
    for _ in range(coord_iters):
        changed = False
        for i in rng.permutation(market.n):
            vals = values[i]
            old = int(qty[i])
            old_offset = old + int(market.sizes[i])
            cross = float(cov_seed[i] @ qty - cov_seed[i, i] * old)
            var_candidates = cur_var + cov_seed[i, i] * (vals * vals - old * old) + 2.0 * (vals - old) * cross
            ev_candidates = cur_ev - ev_tables[i][old_offset] + ev_tables[i]
            feasible = (ev_candidates >= min_ev - 1e-6) & (var_candidates > 1e-9)
            sharpes = np.where(feasible, ev_candidates / np.sqrt(np.maximum(var_candidates, 1e-9)), -np.inf)

            if np.any(np.isfinite(sharpes)):
                idx = int(np.lexsort((-ev_candidates, var_candidates, -sharpes))[0])
            else:
                gaps = np.maximum(0.0, min_ev - ev_candidates)
                idx = int(np.lexsort((var_candidates, gaps))[0])

            new = int(vals[idx])
            if new != old:
                qty[i] = new
                cur_ev = float(ev_candidates[idx])
                cur_var = float(var_candidates[idx])
                changed = True

        if not changed:
            break

    return qty


def optimize_max_sharpe(
    market: MarketData,
    *,
    cov_seed: np.ndarray,
    min_ev: float,
    max_ev_qty: np.ndarray,
    restarts: int,
    coord_iters: int,
    random_seed: int,
    continuous_starts: int,
    extra_starts: Iterable[np.ndarray] = (),
) -> tuple[np.ndarray, dict[str, Any]]:
    """Find a high integer Sharpe portfolio via continuous tangency starts."""

    rng = np.random.default_rng(random_seed)
    starts: list[np.ndarray] = [
        np.zeros(market.n, dtype=int),
        np.array(max_ev_qty, dtype=int, copy=True),
    ]
    starts.extend(np.rint(max_ev_qty * frac).astype(int) for frac in (0.03, 0.05, 0.1, 0.2, 0.5))
    starts.extend(np.array(start, dtype=int, copy=True) for start in extra_starts)

    for _ in range(max(0, restarts)):
        mode = rng.random()
        if mode < 0.35:
            start = np.zeros(market.n, dtype=int)
            for i, size in enumerate(market.sizes):
                if rng.random() < 0.45:
                    start[i] = int(rng.integers(-size, size + 1))
        elif mode < 0.70:
            frac = rng.uniform(0.0, 0.45)
            noise = np.array([rng.integers(-max(1, size // 3), max(2, size // 3 + 1)) for size in market.sizes])
            start = np.rint(max_ev_qty * frac).astype(int) + noise
        elif mode < 0.90:
            start = np.array([rng.choice([-size, 0, size], p=[0.35, 0.30, 0.35]) for size in market.sizes], dtype=int)
        else:
            start = np.array([rng.integers(-size, size + 1) for size in market.sizes], dtype=int)
        starts.append(np.clip(start, -market.sizes, market.sizes).astype(int))

    all_starts = starts[:]
    if minimize is not None and continuous_starts > 0:
        n = market.n
        eye = np.eye(n)
        split_to_qty = np.concatenate([eye, -eye], axis=1)
        cov_split = split_to_qty.T @ cov_seed @ split_to_qty
        long_edges = (market.fair_values - market.asks) * market.multiplier
        short_edges = (market.bids - market.fair_values) * market.multiplier
        ev_edges = np.concatenate([long_edges, short_edges])
        bounds = [(0.0, float(size)) for size in market.sizes] * 2

        def objective(x: np.ndarray) -> float:
            ev = float(ev_edges @ x)
            var = float(x @ cov_split @ x)
            if var <= 1e-9:
                return 1e6
            if ev <= 0:
                return 1e6 - ev
            return -ev / math.sqrt(var)

        def jacobian(x: np.ndarray) -> np.ndarray:
            ev = float(ev_edges @ x)
            var = float(x @ cov_split @ x)
            if var <= 1e-9 or ev <= 0:
                return -ev_edges
            return -(ev_edges / math.sqrt(var) - ev * (cov_split @ x) / (var ** 1.5))

        for start in starts[: min(len(starts), continuous_starts)]:
            x0 = split_from_qty(start)
            result = minimize(
                objective,
                x0,
                method="SLSQP",
                jac=jacobian,
                bounds=bounds,
                options={"maxiter": 500, "ftol": 1e-10, "disp": False},
            )
            q_cont = qty_from_split(result.x, n)
            all_starts.append(round_qty(q_cont, market))

    best: tuple[tuple[float, float, float], np.ndarray] | None = None
    for i, start in enumerate(all_starts):
        polished = polish_max_sharpe_coordinate(
            start,
            market,
            cov_seed,
            min_ev=min_ev,
            coord_iters=coord_iters,
            random_seed=random_seed + i,
        )
        ev = portfolio_ev(polished, market)
        var = portfolio_seed_variance(polished, cov_seed)
        if ev < min_ev - 1e-6 or var <= 1e-9:
            continue
        sharpe = ev / math.sqrt(var)
        key = (sharpe, ev, -var)
        if best is None or key > best[0]:
            best = (key, polished)

    if best is None:
        return np.zeros(market.n, dtype=int), {"success": False, "message": "no feasible Sharpe candidate"}

    qty = best[1]
    ev = portfolio_ev(qty, market)
    seed_var = portfolio_seed_variance(qty, cov_seed)
    return qty, {
        "success": True,
        "message": "continuous tangency + integer coordinate polish",
        "starts": len(all_starts),
        "fair_ev": ev,
        "seed_std": math.sqrt(max(seed_var, 0.0)),
        "sharpe": ev / math.sqrt(seed_var) if seed_var > 0 else math.inf,
    }


def best_sharpe_candidate(
    frontier: list[dict[str, Any]],
    *,
    min_ev: float,
) -> dict[str, Any] | None:
    candidates = [row for row in frontier if row["ev"] >= min_ev and np.isfinite(row["sharpe"])]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row["sharpe"], row["ev"]))


def print_edge_table(market: MarketData) -> None:
    rows = [["instrument", "bid", "ask", "fair", "long EV/u", "short EV/u", "max-EV action"]]
    long_edges = (market.fair_values - market.asks) * market.multiplier
    short_edges = (market.bids - market.fair_values) * market.multiplier
    max_qty = exact_max_ev_quantities(market)
    for i, cid in enumerate(market.ids):
        rows.append(
            [
                cid,
                f"{market.bids[i]:.3f}",
                f"{market.asks[i]:.3f}",
                f"{market.fair_values[i]:.3f}",
                money(long_edges[i]),
                money(short_edges[i]),
                f"{max_qty[i]:+d}",
            ]
        )
    print(table(rows))


def render_simple(args: argparse.Namespace, market: MarketData) -> None:
    rng = np.random.default_rng(args.bootstrap_seed)
    max_ev_qty = exact_max_ev_quantities(market)
    max_ev = portfolio_ev(max_ev_qty, market)
    cov_seed = seed_covariance(market, args.seed_paths)
    seed_payoffs = sample_seed_payoffs(
        market,
        seed_paths=args.seed_paths,
        samples=args.simple_samples,
        rng=np.random.default_rng(args.kelly_seed),
    )

    weights, labels, weight_stats = optimize_return_space_kelly(
        market,
        seed_payoffs=seed_payoffs,
        kelly_fraction=args.kelly_fraction,
        mu_shrink=args.kelly_mu_shrink,
        max_side_weight=args.simple_max_side_weight,
        ridge=args.simple_ridge,
    )
    kelly_qty, implied_capital = limit_scaled_qty_from_weights(weights, market)
    kelly_stats = stats_for(
        kelly_qty,
        market,
        seed_paths=args.seed_paths,
        bootstrap_samples=args.simple_bootstrap_samples,
        rng=rng,
    )

    sharpe_qty, sharpe_info = optimize_max_sharpe(
        market,
        cov_seed=cov_seed,
        min_ev=max_ev * args.sharpe_min_ev_frac,
        max_ev_qty=max_ev_qty,
        restarts=args.sharpe_restarts,
        coord_iters=args.sharpe_iters,
        random_seed=args.search_seed + 10_000,
        continuous_starts=args.sharpe_continuous_starts,
        extra_starts=[kelly_qty],
    )
    sharpe_stats = stats_for(
        sharpe_qty,
        market,
        seed_paths=args.seed_paths,
        bootstrap_samples=args.simple_bootstrap_samples,
        rng=rng,
    )

    frontier = build_frontier(
        market,
        max_ev_qty,
        seed_paths=args.seed_paths,
        target_fracs=[0.9735],
        method=args.method,
        maxiter=args.maxiter,
        restarts=max(40, args.restarts // 2),
        coord_iters=args.coord_iters,
        penalty=args.penalty,
        random_seed=args.search_seed,
    )
    frontier_qty = frontier[0]["qty"]
    frontier_stats = stats_for(
        frontier_qty,
        market,
        seed_paths=args.seed_paths,
        bootstrap_samples=args.simple_bootstrap_samples,
        rng=rng,
    )

    menu_rows: list[dict[str, Any]] = []
    if not args.no_menu:
        menu_seed_payoffs = sample_seed_payoffs(
            market,
            seed_paths=args.seed_paths,
            samples=args.menu_samples,
            rng=np.random.default_rng(args.menu_seed),
        )
        menu_rows = build_achievable_value_menu(
            market,
            seed_payoffs=menu_seed_payoffs,
            max_ev_qty=max_ev_qty,
            chances=parse_chances(args.menu_chances),
            cvar5_caps=parse_optional_money_values(args.menu_cvar5_caps),
            min_ev=max_ev * args.menu_min_ev_frac,
            max_loss_prob=args.menu_max_loss_prob,
            restarts=args.menu_restarts,
            coord_iters=args.menu_iters,
            grid_points=args.menu_grid_points,
            random_seed=args.menu_seed + 1,
            extra_starts=[kelly_qty, sharpe_qty, frontier_qty],
        )

    print("Aether Optimizer")
    print("Run: python3 tools/exotics/optimize_websim.py")
    print()
    print("Capital-independent Kelly weights")
    print(f"  weights: {format_weights(weights, labels)}")
    print(
        f"  return-space: mean={pct(weight_stats['mean'])}, "
        f"std={pct(weight_stats['std'])}, Sharpe={weight_stats['sharpe']:.3f}, "
        f"log-growth={pct(weight_stats['growth_equivalent'])}"
    )
    print(f"  note: weights are fractions; the position list below is scaled to exchange limits, not a chosen bankroll.")
    print()

    rows = [
        ["candidate", "fair EV", "seed std", "Sharpe", "P(loss)", "VaR5", "CVaR5L", "P95", "P99", "qty"],
        [
            "Kelly-scaled",
            money(kelly_stats["fair_ev"]),
            money(kelly_stats["seed_std"]),
            f"{kelly_stats['sharpe']:.3f}",
            pct(kelly_stats["p_loss"]),
            money(kelly_stats["var5"]),
            money(max(0.0, -kelly_stats["cvar5"])),
            money(kelly_stats["p95"]),
            money(kelly_stats["p99"]),
            format_qty(kelly_qty, market),
        ],
        [
            "Max-Sharpe",
            money(sharpe_stats["fair_ev"]),
            money(sharpe_stats["seed_std"]),
            f"{sharpe_stats['sharpe']:.3f}",
            pct(sharpe_stats["p_loss"]),
            money(sharpe_stats["var5"]),
            money(max(0.0, -sharpe_stats["cvar5"])),
            money(sharpe_stats["p95"]),
            money(sharpe_stats["p99"]),
            format_qty(sharpe_qty, market),
        ],
        [
            "High-EV sane",
            money(frontier_stats["fair_ev"]),
            money(frontier_stats["seed_std"]),
            f"{frontier_stats['sharpe']:.3f}",
            pct(frontier_stats["p_loss"]),
            money(frontier_stats["var5"]),
            money(max(0.0, -frontier_stats["cvar5"])),
            money(frontier_stats["p95"]),
            money(frontier_stats["p99"]),
            format_qty(frontier_qty, market),
        ],
    ]
    print(table(rows))
    if menu_rows:
        print()
        print("Achievable Value Menu")
        print("Read: chance=5% means the row maximizes the 95th percentile under the downside cap.")
        if args.menu_max_loss_prob is not None:
            print(f"Shared constraint: P(loss)<={pct(args.menu_max_loss_prob)}")
        print_achievable_value_menu(menu_rows, market)
    print()
    print(f"Max-EV reference: {money(max_ev)} EV, Sharpe {stats_for(max_ev_qty, market, seed_paths=args.seed_paths, bootstrap_samples=0, rng=rng)['sharpe']:.3f}")


def render_text(args: argparse.Namespace, market: MarketData) -> None:
    rng = np.random.default_rng(args.bootstrap_seed)
    max_ev_qty = exact_max_ev_quantities(market)
    max_stats = stats_for(
        max_ev_qty,
        market,
        seed_paths=args.seed_paths,
        bootstrap_samples=args.bootstrap_samples,
        rng=rng,
    )

    print(f"Loaded {args.websim} with {len(market.payoffs):,} simulated paths")
    print(f"Seed paths per judge roll: {args.seed_paths:,}")
    print(f"Fair value source: {args.fair_source}")
    if args.fair_source == "hybrid" and args.fair_paths > 0:
        print(f"Extra fair-value paths for chooser/KO: {args.fair_paths:,}")
    if args.seed is not None:
        print(f"Websim precompute seed: {args.seed}")
    print()

    if args.edges:
        print("Fair Values And Unit Edges")
        print_edge_table(market)
        print()

    print("Exact Max-EV Portfolio")
    print(f"  qty: {format_qty(max_ev_qty, market)}")
    print(f"  fair EV: {money(max_stats['fair_ev'])}")
    print(f"  path mean: {money(max_stats['path_mean'])}")
    print(f"  seed std: {money(max_stats['seed_std'])}")
    print(f"  Sharpe: {max_stats['sharpe']:.3f}")
    if args.bootstrap_samples > 0:
        print(
            "  bootstrap: "
            f"mean={money(max_stats['bootstrap_mean'])}, "
            f"P(loss)={pct(max_stats['p_loss'])}, "
            f"VaR5={money(max_stats['var5'])}, "
            f"VaR1={money(max_stats['var1'])}, "
            f"CVaR5={money(max_stats['cvar5'])}, "
            f"P95={money(max_stats['p95'])}"
        )

    if args.no_frontier:
        frontier = []
    else:
        if args.method == "slsqp" and minimize is None:
            print("\nFrontier skipped: scipy is not available.")
            frontier = []
        else:
            print()
            print("Minimum-Variance Frontier")
            frontier = build_frontier(
                market,
                max_ev_qty,
                seed_paths=args.seed_paths,
                target_fracs=parse_fracs(args.target_fracs),
                method=args.method,
                maxiter=args.maxiter,
                restarts=args.restarts,
                coord_iters=args.coord_iters,
                penalty=args.penalty,
                random_seed=args.search_seed,
            )
            if args.bootstrap_samples > 0:
                rows = [["target", "fair EV", "sim mean", "seed std", "P(loss)", "VaR5", "VaR1", "median", "P95", "qty"]]
            else:
                rows = [["target", "fair EV", "seed std", "Sharpe", "qty"]]
            for row in frontier:
                if args.bootstrap_samples > 0:
                    row_stats = stats_for(
                        row["qty"],
                        market,
                        seed_paths=args.seed_paths,
                        bootstrap_samples=args.bootstrap_samples,
                        rng=rng,
                    )
                    rows.append(
                        [
                            f"{row['target_frac']:.2%}",
                            money(row_stats["fair_ev"]),
                            money(row_stats["bootstrap_mean"]),
                            money(row_stats["seed_std"]),
                            pct(row_stats["p_loss"]),
                            money(row_stats["var5"]),
                            money(row_stats["var1"]),
                            money(row_stats["median"]),
                            money(row_stats["p95"]),
                            format_qty(row["qty"], market),
                        ]
                    )
                else:
                    rows.append(
                        [
                            f"{row['target_frac']:.2%}",
                            money(row["ev"]),
                            money(row["seed_std"]),
                            f"{row['sharpe']:.3f}",
                            format_qty(row["qty"], market),
                        ]
                    )
            print(table(rows))

    max_ev = portfolio_ev(max_ev_qty, market)
    search_frontier = frontier
    cov_seed = seed_covariance(market, args.seed_paths)
    extra_starts_for_menu: list[np.ndarray] = [row["qty"] for row in frontier]
    sharpe_qty_for_starts: np.ndarray | None = None

    if not args.no_sharpe:
        sharpe_qty, sharpe_info = optimize_max_sharpe(
            market,
            cov_seed=cov_seed,
            min_ev=max_ev * args.sharpe_min_ev_frac,
            max_ev_qty=max_ev_qty,
            restarts=args.sharpe_restarts,
            coord_iters=args.sharpe_iters,
            random_seed=args.search_seed + 10_000,
            continuous_starts=args.sharpe_continuous_starts,
            extra_starts=[row["qty"] for row in frontier],
        )
        if sharpe_info["success"]:
            sharpe_qty_for_starts = sharpe_qty
            extra_starts_for_menu.append(sharpe_qty)
            best_stats = stats_for(
                sharpe_qty,
                market,
                seed_paths=args.seed_paths,
                bootstrap_samples=args.bootstrap_samples,
                rng=rng,
            )
            print()
            print("Best Sharpe Candidate")
            print(f"  objective: maximize fair EV / seed std with EV >= {args.sharpe_min_ev_frac:.0%} of max EV")
            print(f"  qty: {format_qty(sharpe_qty, market)}")
            print(
                f"  fair EV: {money(best_stats['fair_ev'])} | "
                f"seed std: {money(best_stats['seed_std'])} | "
                f"Sharpe: {best_stats['sharpe']:.3f}"
            )
            if args.bootstrap_samples > 0:
                print(
                    "  bootstrap: "
                    f"mean={money(best_stats['bootstrap_mean'])}, "
                    f"P(loss)={pct(best_stats['p_loss'])}, "
                    f"VaR5={money(best_stats['var5'])}, "
                    f"VaR1={money(best_stats['var1'])}, "
                    f"P95={money(best_stats['p95'])}"
                )

    if not args.no_kelly:
        kelly_seed_payoffs = sample_seed_payoffs(
            market,
            seed_paths=args.seed_paths,
            samples=args.kelly_samples,
            rng=np.random.default_rng(args.kelly_seed),
        )
        qty_cont, qty_qp, qp_info = optimize_portfolio_kelly_qp(
            market,
            cov_seed=cov_seed,
            capital=args.capital,
            kelly_fraction=args.kelly_fraction,
            mu_shrink=args.kelly_mu_shrink,
            gross_limit=args.kelly_gross_limit,
            long_only=args.kelly_long_only,
            max_ev_qty=max_ev_qty,
        )
        qp_stats = stats_for(
            qty_qp,
            market,
            seed_paths=args.seed_paths,
            bootstrap_samples=args.bootstrap_samples,
            rng=rng,
        )
        print()
        print("Portfolio Kelly QP Candidate")
        print(
            f"  objective: shrink*EV - Var/(2*c*capital), "
            f"capital={money(args.capital)}, c={args.kelly_fraction:.2f}, shrink={args.kelly_mu_shrink:.2f}"
        )
        if args.kelly_gross_limit > 0:
            print(f"  gross cap: {args.kelly_gross_limit:.2f}x capital")
        print(f"  continuous qty: {format_float_qty(qty_cont, market)}")
        print(f"  rounded qty: {format_qty(qty_qp, market)}")
        print(
            f"  fair EV: {money(qp_stats['fair_ev'])} | "
            f"seed std: {money(qp_stats['seed_std'])} | "
            f"Sharpe: {qp_stats['sharpe']:.3f} | "
            f"gross: {money(gross_notional(qty_qp, market))}"
        )
        extra_starts_for_menu.append(qty_qp)

        kelly_qty, kelly_info = optimize_log_utility_coordinate(
            market,
            seed_payoffs=kelly_seed_payoffs,
            capital=args.capital,
            kelly_fraction=args.kelly_fraction,
            min_ev=max_ev * args.kelly_min_ev_frac,
            gross_limit=args.kelly_gross_limit,
            long_only=args.kelly_long_only,
            max_ev_qty=max_ev_qty,
            restarts=args.kelly_restarts,
            coord_iters=args.kelly_iters,
            random_seed=args.kelly_seed + 1,
            extra_starts=extra_starts_for_menu,
        )
        kelly_stats = stats_for(
            kelly_qty,
            market,
            seed_paths=args.seed_paths,
            bootstrap_samples=args.bootstrap_samples,
            rng=rng,
        )
        frac_log = kelly_info["fractional_log"]
        actual_log = kelly_info["actual_log"]
        print()
        print("Kelly Log-Utility Candidate")
        print(
            f"  objective: maximize E[log(1 + {args.kelly_fraction:.2f} * PnL / {money(args.capital)})] "
            f"with EV >= {args.kelly_min_ev_frac:.0%} of max EV"
        )
        if args.kelly_gross_limit > 0 or args.kelly_long_only:
            constraint_bits = []
            if args.kelly_gross_limit > 0:
                constraint_bits.append(f"gross <= {args.kelly_gross_limit:.2f}x capital")
            if args.kelly_long_only:
                constraint_bits.append("long-only")
            print(f"  constraints: {', '.join(constraint_bits)}")
        print(f"  qty: {format_qty(kelly_qty, market)}")
        print(
            f"  fair EV: {money(kelly_stats['fair_ev'])} | "
            f"seed std: {money(kelly_stats['seed_std'])} | "
            f"Sharpe: {kelly_stats['sharpe']:.3f} | "
            f"gross: {money(gross_notional(kelly_qty, market))}"
        )
        print(
            f"  fractional log growth: {frac_log['mean_log_growth']:.6f} "
            f"(equiv {pct(frac_log['growth_equivalent'])}) | "
            f"actual log growth: {actual_log['mean_log_growth']:.6f} "
            f"(equiv {pct(actual_log['growth_equivalent'])})"
        )
        print(
            f"  approx full-Kelly scale of this book: {frac_log['full_kelly_scale']:.2f}x | "
            f"{args.kelly_fraction:.2f}-Kelly capped scale: {frac_log['fractional_kelly_scale']:.2f}x"
        )
        if args.bootstrap_samples > 0:
            print(
                "  bootstrap: "
                f"mean={money(kelly_stats['bootstrap_mean'])}, "
                f"P(loss)={pct(kelly_stats['p_loss'])}, "
                f"VaR5={money(kelly_stats['var5'])}, "
                f"VaR1={money(kelly_stats['var1'])}, "
                f"P95={money(kelly_stats['p95'])}"
        )
        extra_starts_for_menu.append(kelly_qty)

    if not args.no_menu:
        menu_seed_payoffs = sample_seed_payoffs(
            market,
            seed_paths=args.seed_paths,
            samples=args.menu_samples,
            rng=np.random.default_rng(args.menu_seed),
        )
        menu_rows = build_achievable_value_menu(
            market,
            seed_payoffs=menu_seed_payoffs,
            max_ev_qty=max_ev_qty,
            chances=parse_chances(args.menu_chances),
            cvar5_caps=parse_optional_money_values(args.menu_cvar5_caps),
            min_ev=max_ev * args.menu_min_ev_frac,
            max_loss_prob=args.menu_max_loss_prob,
            restarts=args.menu_restarts,
            coord_iters=args.menu_iters,
            grid_points=args.menu_grid_points,
            random_seed=args.menu_seed + 1,
            extra_starts=extra_starts_for_menu,
        )
        print()
        print("Achievable Value Menu")
        print("  chance=5% means the row maximizes the 95th percentile under the downside cap.")
        if args.menu_max_loss_prob is not None:
            print(f"  shared constraint: P(loss)<={pct(args.menu_max_loss_prob)}")
        print_achievable_value_menu(menu_rows, market)
        extra_starts_for_menu.extend(row["qty"] for row in menu_rows)

    if not args.no_lottery:
        if args.lottery_n <= 1:
            raise ValueError("--lottery-n must be greater than 1")
        lottery_percentile = 100.0 * (1.0 - 1.0 / args.lottery_n)
        seed_payoffs = sample_seed_payoffs(
            market,
            seed_paths=args.seed_paths,
            samples=args.lottery_samples,
            rng=np.random.default_rng(args.lottery_seed),
        )
        extra_starts = extra_starts_for_menu + [row["qty"] for row in search_frontier]
        lottery_qty, lottery_info = optimize_lottery_quantile(
            market,
            seed_payoffs=seed_payoffs,
            percentile=lottery_percentile,
            min_ev=max_ev * args.lottery_min_ev_frac,
            max_ev_qty=max_ev_qty,
            restarts=args.lottery_restarts,
            coord_iters=args.lottery_iters,
            random_seed=args.lottery_seed + 1,
            extra_starts=extra_starts,
        )
        lottery_stats = stats_for(
            lottery_qty,
            market,
            seed_paths=args.seed_paths,
            bootstrap_samples=args.bootstrap_samples,
            rng=rng,
        )
        print()
        print(f"Lottery Candidate ({1}/{args.lottery_n} Upside)")
        print(f"  objective: maximize P{lottery_percentile:.2f} seed PnL with EV >= {args.lottery_min_ev_frac:.0%} of max EV")
        print(f"  qty: {format_qty(lottery_qty, market)}")
        print(
            f"  fair EV: {money(lottery_stats['fair_ev'])} | "
            f"seed std: {money(lottery_stats['seed_std'])} | "
            f"search P{lottery_percentile:.2f}: {money(lottery_info['lottery_quantile'])}"
        )
        if args.bootstrap_samples > 0:
            print(
                "  bootstrap: "
                f"mean={money(lottery_stats['bootstrap_mean'])}, "
                f"P(loss)={pct(lottery_stats['p_loss'])}, "
                f"VaR5={money(lottery_stats['var5'])}, "
                f"VaR1={money(lottery_stats['var1'])}, "
                f"P95={money(lottery_stats['p95'])}"
            )


def render_json(args: argparse.Namespace, market: MarketData) -> None:
    rng = np.random.default_rng(args.bootstrap_seed)
    max_ev_qty = exact_max_ev_quantities(market)
    max_stats = stats_for(
        max_ev_qty,
        market,
        seed_paths=args.seed_paths,
        bootstrap_samples=args.bootstrap_samples,
        rng=rng,
    )
    payload: dict[str, Any] = {
        "websim": str(args.websim),
        "seed": args.seed,
        "seed_paths": args.seed_paths,
        "fair_source": args.fair_source,
        "fair_paths": args.fair_paths,
        "center_payoffs": not args.no_center_payoffs,
        "instruments": [
            {
                "id": cid,
                "name": name,
                "bid": float(bid),
                "ask": float(ask),
                "size": int(size),
                "fair_value": float(fair),
            }
            for cid, name, bid, ask, size, fair in zip(
                market.ids, market.names, market.bids, market.asks, market.sizes, market.fair_values
            )
        ],
        "max_ev": {
            "qty": {cid: int(q) for cid, q in zip(market.ids, max_ev_qty)},
            "stats": max_stats,
        },
    }
    if not args.no_frontier and (args.method != "slsqp" or minimize is not None):
        frontier_payload = []
        for row in build_frontier(
            market,
            max_ev_qty,
            seed_paths=args.seed_paths,
            target_fracs=parse_fracs(args.target_fracs),
            method=args.method,
            maxiter=args.maxiter,
            restarts=args.restarts,
            coord_iters=args.coord_iters,
            penalty=args.penalty,
            random_seed=args.search_seed,
        ):
            frontier_payload.append(
                {
                    "target_frac": row["target_frac"],
                    "target_ev": row["target_ev"],
                    "ev": row["ev"],
                    "seed_std": row["seed_std"],
                    "sharpe": row["sharpe"],
                    "qty": {cid: int(q) for cid, q in zip(market.ids, row["qty"])},
                    "solver": row["solver"],
                    "stats": stats_for(
                        row["qty"],
                        market,
                        seed_paths=args.seed_paths,
                        bootstrap_samples=args.bootstrap_samples,
                        rng=rng,
                    ),
                }
            )
        payload["frontier"] = frontier_payload
    print(json.dumps(payload, indent=2, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimize tools/exotics/websim.py positions. Default mode prints a simple capital-independent report.",
    )
    parser.add_argument("--full", action="store_true", help="Show the detailed EV/frontier/Sharpe/Kelly/menu/lottery report")
    parser.add_argument("--websim", type=Path, default=WEB_SIM_PATH, help="Path to websim.py")
    parser.add_argument("--seed", type=int, default=1337, help="Seed used before websim precomputes paths")
    parser.add_argument(
        "--fair-source",
        choices=["hybrid", "sample"],
        default="hybrid",
        help="hybrid uses closed-form fair values plus MC for path exotics; sample uses websim PAYOFFS_PRE means",
    )
    parser.add_argument(
        "--fair-paths",
        type=int,
        default=1_000_000,
        help="Extra antithetic paths for chooser/KO fair values in hybrid mode; 0 reuses websim sample means",
    )
    parser.add_argument("--fair-seed", type=int, default=20260429, help="Seed for hybrid path-dependent fair values")
    parser.add_argument(
        "--no-center-payoffs",
        action="store_true",
        help="Do not shift simulated payoff samples to the selected fair values",
    )
    parser.add_argument("--seed-paths", type=int, default=100, help="Paths averaged in one judge roll")
    parser.add_argument("--bootstrap-samples", type=int, default=100_000, help="Bootstrap samples for max-EV stats")
    parser.add_argument("--bootstrap-seed", type=int, default=20260428, help="Seed for bootstrap judge sampling")
    parser.add_argument("--simple-samples", type=int, default=12_000, help=argparse.SUPPRESS)
    parser.add_argument("--simple-bootstrap-samples", type=int, default=50_000, help=argparse.SUPPRESS)
    parser.add_argument("--simple-max-side-weight", type=float, default=0.35, help=argparse.SUPPRESS)
    parser.add_argument("--simple-ridge", type=float, default=1e-6, help=argparse.SUPPRESS)
    parser.add_argument("--target-fracs", default="90,95,96,97,98,100", help="Comma-separated EV target percentages")
    parser.add_argument("--method", choices=["coordinate", "slsqp"], default="coordinate", help="Frontier optimizer")
    parser.add_argument("--restarts", type=int, default=160, help="Random restarts for coordinate frontier search")
    parser.add_argument("--coord-iters", type=int, default=80, help="Coordinate sweeps per restart")
    parser.add_argument("--penalty", type=float, default=100_000.0, help="EV shortfall penalty for coordinate search")
    parser.add_argument("--search-seed", type=int, default=91210, help="Seed for coordinate search restarts")
    parser.add_argument("--no-sharpe", action="store_true", help="Skip best-Sharpe search")
    parser.add_argument("--sharpe-fracs", default="20:100:17", help=argparse.SUPPRESS)
    parser.add_argument("--sharpe-min-ev-frac", type=float, default=0.10, help="Minimum EV fraction allowed for Sharpe pick")
    parser.add_argument("--sharpe-restarts", type=int, default=120, help="Random/continuous starts for direct Sharpe search")
    parser.add_argument("--sharpe-iters", type=int, default=80, help="Coordinate-polish sweeps for direct Sharpe search")
    parser.add_argument("--sharpe-continuous-starts", type=int, default=6, help="SLSQP continuous tangency starts before integer polish")
    parser.add_argument("--no-kelly", action="store_true", help="Skip portfolio-Kelly and log-utility search")
    parser.add_argument("--capital", type=float, default=1_000_000.0, help="Bankroll/capital used to convert PnL to returns")
    parser.add_argument("--kelly-fraction", type=float, default=0.30, help="Fractional Kelly scale used for log objective")
    parser.add_argument("--kelly-mu-shrink", type=float, default=0.50, help="Shrink expected edges in continuous Kelly QP")
    parser.add_argument("--kelly-min-ev-frac", type=float, default=0.0, help="Minimum EV fraction for log-utility search")
    parser.add_argument("--kelly-gross-limit", type=float, default=0.0, help="Optional gross notional cap as multiple of capital; 0 disables")
    parser.add_argument("--kelly-long-only", action="store_true", help="Disallow short legs in continuous Kelly QP")
    parser.add_argument("--kelly-samples", type=int, default=4_000, help="Bootstrap seed samples used during log-utility search")
    parser.add_argument("--kelly-restarts", type=int, default=10, help="Random restarts for log-utility search")
    parser.add_argument("--kelly-iters", type=int, default=5, help="Coordinate sweeps per log-utility restart")
    parser.add_argument("--kelly-seed", type=int, default=44721, help="Seed for Kelly bootstrap/search")
    parser.add_argument("--no-menu", dest="no_menu", action="store_true", help="Skip achievable value menu")
    parser.add_argument("--no-tail", dest="no_menu", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--menu-chances", default="50,25,10,5,1", help="Comma-separated hit probabilities, e.g. 50,25,10,5,1")
    parser.add_argument("--menu-cvar5-caps", default="250000", help="Comma-separated CVaR5 loss caps, or none")
    parser.add_argument("--menu-min-ev-frac", type=float, default=0.0, help="Minimum EV fraction for menu rows")
    parser.add_argument("--menu-max-loss-prob", type=float, default=0.50, help="Hard cap for P(PnL < 0); 0 disables")
    parser.add_argument("--menu-samples", type=int, default=2_000, help="Bootstrap seed samples used by achievable menu search")
    parser.add_argument("--menu-restarts", type=int, default=1, help="Random restarts per achievable menu row")
    parser.add_argument("--menu-iters", type=int, default=1, help="Coordinate sweeps per achievable menu row")
    parser.add_argument("--menu-grid-points", type=int, default=51, help="Candidate quantities per instrument for menu search; 0 scans every integer")
    parser.add_argument("--menu-seed", type=int, default=60331, help="Seed for achievable menu bootstrap/search")
    parser.add_argument("--no-lottery", action="store_true", help="Skip upside-tail lottery search")
    parser.add_argument("--lottery-n", type=int, default=20, help="Optimize about a 1/N good-seed chance")
    parser.add_argument("--lottery-min-ev-frac", type=float, default=0.90, help="EV floor as fraction of max EV for lottery search")
    parser.add_argument("--lottery-samples", type=int, default=6_000, help="Bootstrap seed samples used during lottery search")
    parser.add_argument("--lottery-restarts", type=int, default=18, help="Random restarts for lottery search")
    parser.add_argument("--lottery-iters", type=int, default=8, help="Coordinate sweeps per lottery restart")
    parser.add_argument("--lottery-seed", type=int, default=73117, help="Seed for lottery bootstrap/search")
    parser.add_argument("--maxiter", type=int, default=1_000, help="SLSQP max iterations for frontier points")
    parser.add_argument("--edges", action="store_true", help="Print fair values and unit EV edges")
    parser.add_argument("--no-frontier", action="store_true", help="Only print exact max-EV portfolio")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--verbose-import", action="store_true", help="Show websim.py import/precompute output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sharpe_min_ev_frac > 1.0:
        args.sharpe_min_ev_frac /= 100.0
    if args.kelly_fraction > 1.0:
        args.kelly_fraction /= 100.0
    if args.kelly_mu_shrink > 1.0:
        args.kelly_mu_shrink /= 100.0
    if args.kelly_min_ev_frac > 1.0:
        args.kelly_min_ev_frac /= 100.0
    if args.menu_min_ev_frac > 1.0:
        args.menu_min_ev_frac /= 100.0
    if args.menu_max_loss_prob > 1.0:
        args.menu_max_loss_prob /= 100.0
    if args.menu_max_loss_prob <= 0:
        args.menu_max_loss_prob = None
    if args.lottery_min_ev_frac > 1.0:
        args.lottery_min_ev_frac /= 100.0
    args.websim = args.websim.resolve()
    websim = load_websim(args.websim, seed=args.seed, quiet=not args.verbose_import)
    market = market_from_websim(websim)
    if args.fair_source == "hybrid":
        fair_values = hybrid_fair_values(
            websim,
            market,
            fair_paths=args.fair_paths,
            fair_seed=args.fair_seed,
        )
        market = with_fair_values(market, fair_values, center_payoffs=not args.no_center_payoffs)
    if args.json:
        render_json(args, market)
    elif not args.full:
        render_simple(args, market)
    else:
        render_text(args, market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
