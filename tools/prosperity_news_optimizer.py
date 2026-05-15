#!/usr/bin/env python3
"""
Prosperity Manual News Trading Optimizer
========================================

Command-line optimizer for the Ashflow Alpha / Ignith manual news-trading task.

The model uses signed allocation weights:
    w_i > 0  buy / long product i
    w_i < 0  sell / short product i

Budget and fee interpretation:
    sum(abs(w_i)) <= 1.0
    fee_i = budget * abs(w_i) ** 2

For a scenario return vector r, net PnL is:
    budget * (r @ w - sum(abs(w_i) ** 2))

The continuous optimizer solves a convex mean-variance-fee objective with
projected gradient descent, so it only needs numpy. Optional integer-percent
rounding then does a small local search on the percentage grid.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

BUDGET = 1_000_000.0
EPS = 1e-12


@dataclass(frozen=True)
class ProductPrior:
    name: str
    lo: float
    mode: float
    hi: float
    thesis: str
    beta: float = 0.20


# Editable priors. Returns are decimal one-day returns, not percentages.
# The base set reflects the updated Sulfur index-inclusion text.
BASE_PRIORS: List[ProductPrior] = [
    ProductPrior(
        "Sulfur Reactor / Sulfur Ltd.",
        0.18,
        0.30,
        0.45,
        "Confirmed Elemental Index 118 inclusion; index trackers should add holdings.",
        beta=0.15,
    ),
    ProductPrior(
        "Thermalite Smart Devices",
        0.18,
        0.26,
        0.34,
        "Projected users and usage time both surge; closest to a clean demand-growth story.",
        beta=0.35,
    ),
    ProductPrior(
        "Magma Ink / Lava Fountain Pen",
        0.00,
        0.07,
        0.15,
        "Launch lines and limited-edition hype; positive but easy to overrate.",
        beta=0.30,
    ),
    ProductPrior(
        "Scoria Paste",
        0.06,
        0.15,
        0.25,
        "Stockpiling narrative for an essential product; positive but not true scarcity.",
        beta=0.45,
    ),
    ProductPrior(
        "Lava Cakes",
        -0.80,
        -0.68,
        -0.55,
        "Severe health/safety story with sales halt, review, lawsuits, and vendor returns.",
        beta=0.10,
    ),
    ProductPrior(
        "Pyroflex Cell",
        -0.18,
        -0.10,
        -0.05,
        "Tax cut ends and levy doubles; negative, but tax headlines can underperform.",
        beta=0.25,
    ),
    ProductPrior(
        "Ashes of the Phoenix",
        -0.42,
        -0.30,
        -0.15,
        "Viral sourcing scandal; reputation shock without a formal sales ban.",
        beta=0.15,
    ),
    ProductPrior(
        "Obsidian Cutlery",
        -0.42,
        -0.32,
        -0.20,
        "Manufacturing/safety failure with operational consequences.",
        beta=0.10,
    ),
    ProductPrior(
        "Volcanic Incense",
        -0.08,
        -0.03,
        0.08,
        "Already-rallied influencer pump; low edge and crowding/reversal risk.",
        beta=0.40,
    ),
]

CONSERVATIVE_PRIORS: List[ProductPrior] = [
    ProductPrior("Sulfur Reactor / Sulfur Ltd.", 0.10, 0.24, 0.38, "Softer index-inclusion forced-flow prior.", beta=0.15),
    ProductPrior("Thermalite Smart Devices", 0.12, 0.22, 0.30, "Softer usage-growth prior.", beta=0.35),
    ProductPrior("Magma Ink / Lava Fountain Pen", -0.03, 0.04, 0.12, "Consumer hype can be nearly flat.", beta=0.30),
    ProductPrior("Scoria Paste", 0.00, 0.10, 0.20, "Useful product, weaker source quality.", beta=0.45),
    ProductPrior("Lava Cakes", -0.75, -0.60, -0.40, "Severe negative softened for market underreaction.", beta=0.10),
    ProductPrior("Pyroflex Cell", -0.14, -0.07, -0.02, "Tax shock with underreaction risk.", beta=0.25),
    ProductPrior("Ashes of the Phoenix", -0.35, -0.22, -0.05, "Reputation scandal, but not a ban.", beta=0.15),
    ProductPrior("Obsidian Cutlery", -0.35, -0.25, -0.10, "Safety failure with possible supply-offset ambiguity.", beta=0.10),
    ProductPrior("Volcanic Incense", -0.12, -0.02, 0.10, "Crowded pump could reverse or keep drifting.", beta=0.40),
]

AGGRESSIVE_PRIORS: List[ProductPrior] = [
    ProductPrior("Sulfur Reactor / Sulfur Ltd.", 0.25, 0.38, 0.55, "Large index-tracker buying response.", beta=0.15),
    ProductPrior("Thermalite Smart Devices", 0.22, 0.30, 0.42, "Strong usage-growth response.", beta=0.35),
    ProductPrior("Magma Ink / Lava Fountain Pen", 0.03, 0.10, 0.20, "Launch demand is treated as real demand.", beta=0.30),
    ProductPrior("Scoria Paste", 0.12, 0.25, 0.45, "Aggressive stockpiling/scarcity interpretation.", beta=0.45),
    ProductPrior("Lava Cakes", -0.90, -0.75, -0.55, "Extreme health/sales-halt collapse.", beta=0.10),
    ProductPrior("Pyroflex Cell", -0.25, -0.14, -0.05, "Large response to tax-cut removal.", beta=0.25),
    ProductPrior("Ashes of the Phoenix", -0.50, -0.35, -0.15, "Large viral public-backlash response.", beta=0.15),
    ProductPrior("Obsidian Cutlery", -0.50, -0.38, -0.20, "Large product-safety/production failure response.", beta=0.10),
    ProductPrior("Volcanic Incense", -0.10, 0.00, 0.20, "Pump may continue despite weak fundamentals.", beta=0.40),
]

PRIORS_BY_NAME: Dict[str, List[ProductPrior]] = {
    "base": BASE_PRIORS,
    "conservative": CONSERVATIVE_PRIORS,
    "aggressive": AGGRESSIVE_PRIORS,
}


def normalize_fraction(value: float) -> float:
    """Accept either decimal returns or percentage-style inputs."""
    if abs(value) > 1.0:
        return value / 100.0
    return value


def make_blended_priors(weight_aggressive: float) -> List[ProductPrior]:
    """Blend conservative and aggressive priors. 0=conservative, 1=aggressive."""
    a = float(np.clip(weight_aggressive, 0.0, 1.0))
    priors: List[ProductPrior] = []
    for conservative, aggressive, base in zip(CONSERVATIVE_PRIORS, AGGRESSIVE_PRIORS, BASE_PRIORS):
        if conservative.name != aggressive.name:
            raise ValueError("Conservative/aggressive prior lists are misaligned.")
        priors.append(
            ProductPrior(
                conservative.name,
                (1.0 - a) * conservative.lo + a * aggressive.lo,
                (1.0 - a) * conservative.mode + a * aggressive.mode,
                (1.0 - a) * conservative.hi + a * aggressive.hi,
                f"Blend {100.0 * a:.0f}% aggressive / {100.0 * (1.0 - a):.0f}% conservative. Base thesis: {base.thesis}",
                beta=(1.0 - a) * conservative.beta + a * aggressive.beta,
            )
        )
    return priors


def load_priors_csv(path: Path) -> List[ProductPrior]:
    """Load priors from CSV columns: name,lo,mode,hi,thesis,beta.

    lo/mode/hi may be decimals or percentages. beta is optional.
    """
    priors: List[ProductPrior] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"name", "lo", "mode", "hi"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing required columns: {sorted(missing)}")
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            priors.append(
                ProductPrior(
                    name=name,
                    lo=normalize_fraction(float(row["lo"])),
                    mode=normalize_fraction(float(row["mode"])),
                    hi=normalize_fraction(float(row["hi"])),
                    thesis=(row.get("thesis") or "").strip(),
                    beta=float(row.get("beta") or 0.20),
                )
            )
    if not priors:
        raise ValueError(f"No priors loaded from {path}")
    return priors


def write_priors_template(path: Path, priors: Sequence[ProductPrior]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "lo", "mode", "hi", "beta", "thesis"])
        for p in priors:
            writer.writerow([p.name, p.lo, p.mode, p.hi, p.beta, p.thesis])


def triangular_samples(
    priors: Sequence[ProductPrior],
    n_sims: int,
    rng: np.random.Generator,
    common_factor_sigma: float = 0.04,
    clip_to_ranges: bool = True,
) -> np.ndarray:
    """Generate Monte Carlo return scenarios from triangular priors."""
    samples = np.empty((n_sims, len(priors)), dtype=float)
    for j, p in enumerate(priors):
        lo, mode, hi = sorted((p.lo, p.mode, p.hi))
        samples[:, j] = rng.triangular(lo, mode, hi, size=n_sims)

    if common_factor_sigma > 0.0:
        market = rng.normal(0.0, common_factor_sigma, size=n_sims)
        betas = np.array([p.beta for p in priors], dtype=float)
        samples += market[:, None] * betas[None, :]

    if clip_to_ranges:
        lo = np.array([min(p.lo, p.hi) for p in priors], dtype=float)
        hi = np.array([max(p.lo, p.hi) for p in priors], dtype=float)
        samples = np.clip(samples, lo, hi)
    return samples


def nearest_psd(cov: np.ndarray, eps: float = EPS) -> np.ndarray:
    cov = 0.5 * (cov + cov.T)
    vals, vecs = np.linalg.eigh(cov)
    vals = np.maximum(vals, eps)
    return (vecs * vals) @ vecs.T


def estimate_moments(samples: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    mu = samples.mean(axis=0)
    cov = np.atleast_2d(np.cov(samples, rowvar=False))
    return mu, nearest_psd(cov)


def objective_value(mu: np.ndarray, cov: np.ndarray, w: np.ndarray, risk_aversion: float) -> float:
    return -float(mu @ w) + float(w @ w) + risk_aversion * float(w @ cov @ w)


def project_box_l1(v: np.ndarray, l1_bound: float, box_bound: float) -> np.ndarray:
    """Project onto {w: sum(abs(w)) <= l1_bound, abs(w_i) <= box_bound}."""
    if l1_bound <= 0.0 or box_bound <= 0.0:
        return np.zeros_like(v)

    sign = np.sign(v)
    a = np.abs(v)
    clipped = np.minimum(a, box_bound)
    if clipped.sum() <= l1_bound + EPS:
        return sign * clipped

    lo, hi = 0.0, float(a.max())
    for _ in range(100):
        tau = 0.5 * (lo + hi)
        x = np.minimum(np.maximum(a - tau, 0.0), box_bound)
        if x.sum() > l1_bound:
            lo = tau
        else:
            hi = tau
    x = np.minimum(np.maximum(a - hi, 0.0), box_bound)
    return sign * x


def convex_optimize_projected_gradient(
    mu: np.ndarray,
    cov: np.ndarray,
    max_used: float = 1.0,
    max_per_product: float = 0.25,
    risk_aversion: float = 3.0,
    max_iter: int = 50_000,
    tol: float = 1e-13,
) -> np.ndarray:
    """Solve mean-variance-fee allocation by projected gradient descent."""
    n = len(mu)
    max_used = min(max_used, n * max_per_product)
    largest_eigen = float(np.linalg.eigvalsh(cov).max())
    step = 1.0 / max(2.0 + 2.0 * risk_aversion * largest_eigen, EPS)

    # Without covariance, the unconstrained optimum is w = mu / 2.
    w = project_box_l1(mu / 2.0, max_used, max_per_product)
    last_obj = np.inf

    for _ in range(max_iter):
        grad = -mu + 2.0 * w + 2.0 * risk_aversion * (cov @ w)
        w_new = project_box_l1(w - step * grad, max_used, max_per_product)
        obj = objective_value(mu, cov, w_new, risk_aversion)
        if abs(last_obj - obj) < tol and np.linalg.norm(w_new - w) < tol:
            w = w_new
            break
        w, last_obj = w_new, obj

    w[np.abs(w) < 1e-10] = 0.0
    return w


def feasible(w: np.ndarray, max_used: float, max_per_product: float) -> bool:
    return bool(np.sum(np.abs(w)) <= max_used + 1e-9 and np.all(np.abs(w) <= max_per_product + 1e-9))


def round_to_step_and_repair(w: np.ndarray, step: float, max_used: float, max_per_product: float) -> np.ndarray:
    """Round weights to a percentage grid while preserving feasibility."""
    if step <= 0.0:
        return w.copy()

    sign = np.sign(w)
    abs_w = np.abs(w)
    box_steps = int(np.floor(max_per_product / step + 1e-9))
    used_steps = int(np.floor(max_used / step + 1e-9))

    raw_steps = abs_w / step
    k = np.minimum(np.floor(raw_steps + 1e-12).astype(int), box_steps)

    while k.sum() > used_steps:
        idx = int(np.argmax(k))
        k[idx] -= 1

    leftover = used_steps - int(k.sum())
    residual_order = np.argsort(-(raw_steps - k))
    for idx in residual_order:
        if leftover <= 0:
            break
        if sign[idx] != 0.0 and k[idx] < box_steps:
            k[idx] += 1
            leftover -= 1

    rounded = sign * k * step
    rounded[np.abs(rounded) < step / 2.0] = 0.0
    return rounded


def polish_discrete_weights(
    mu: np.ndarray,
    cov: np.ndarray,
    initial_w: np.ndarray,
    step: float,
    max_used: float,
    max_per_product: float,
    risk_aversion: float,
    max_passes: int = 2_000,
) -> np.ndarray:
    """Local search over one-step moves and two-product swaps."""
    if step <= 0.0:
        return initial_w.copy()

    n = len(initial_w)
    w = np.round(initial_w / step) * step
    if not feasible(w, max_used, max_per_product):
        raise ValueError("Initial discrete weights are infeasible.")

    current_obj = objective_value(mu, cov, w, risk_aversion)
    one_step = np.array([-step, step], dtype=float)

    for _ in range(max_passes):
        best_w = w
        best_obj = current_obj

        for i in range(n):
            for di in one_step:
                cand = w.copy()
                cand[i] = np.round((cand[i] + di) / step) * step
                if feasible(cand, max_used, max_per_product):
                    obj = objective_value(mu, cov, cand, risk_aversion)
                    if obj < best_obj - 1e-14:
                        best_obj, best_w = obj, cand

        for i in range(n):
            for j in range(i + 1, n):
                for di in one_step:
                    for dj in one_step:
                        cand = w.copy()
                        cand[i] = np.round((cand[i] + di) / step) * step
                        cand[j] = np.round((cand[j] + dj) / step) * step
                        if feasible(cand, max_used, max_per_product):
                            obj = objective_value(mu, cov, cand, risk_aversion)
                            if obj < best_obj - 1e-14:
                                best_obj, best_w = obj, cand

        if best_obj < current_obj - 1e-14:
            w, current_obj = best_w, best_obj
        else:
            break

    w[np.abs(w) < step / 2.0] = 0.0
    return w


def prune_and_reoptimize(
    mu: np.ndarray,
    cov: np.ndarray,
    weights: np.ndarray,
    min_abs_weight: float,
    max_used: float,
    max_per_product: float,
    risk_aversion: float,
) -> np.ndarray:
    if min_abs_weight <= 0.0:
        return weights.copy()

    active = np.abs(weights) >= min_abs_weight
    if active.all():
        return weights.copy()
    if not active.any():
        return np.zeros_like(weights)

    sub_weights = convex_optimize_projected_gradient(
        mu[active],
        cov[np.ix_(active, active)],
        max_used=max_used,
        max_per_product=max_per_product,
        risk_aversion=risk_aversion,
    )
    out = np.zeros_like(weights)
    out[active] = sub_weights
    return out


def run_optimization(
    priors: Sequence[ProductPrior],
    n_sims: int,
    seed: int,
    max_used: float,
    max_per_product: float,
    risk_aversion: float,
    common_factor_sigma: float,
    prune_under: float = 0.0,
    integer_percent: bool = False,
    round_step: float = 0.01,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    samples = triangular_samples(priors, n_sims, rng, common_factor_sigma=common_factor_sigma)
    mu, cov = estimate_moments(samples)

    weights = convex_optimize_projected_gradient(
        mu,
        cov,
        max_used=max_used,
        max_per_product=max_per_product,
        risk_aversion=risk_aversion,
    )

    if prune_under > 0.0:
        weights = prune_and_reoptimize(mu, cov, weights, prune_under, max_used, max_per_product, risk_aversion)

    if integer_percent:
        rounded = round_to_step_and_repair(weights, round_step, max_used, max_per_product)
        weights = polish_discrete_weights(mu, cov, rounded, round_step, max_used, max_per_product, risk_aversion)

    return samples, mu, cov, weights


def net_return_scenarios(samples: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return samples @ weights - np.sum(np.abs(weights) ** 2)


def summarize_distribution(net_returns: np.ndarray, budget: float = BUDGET) -> Dict[str, float]:
    p01, p05, p10, p50, p90, p95, p99 = np.quantile(net_returns, [0.01, 0.05, 0.10, 0.50, 0.90, 0.95, 0.99])
    tail = net_returns[net_returns <= p05]
    cvar05 = float(tail.mean()) if len(tail) else float(p05)
    return {
        "mean_$": float(net_returns.mean() * budget),
        "std_$": float(net_returns.std(ddof=1) * budget),
        "median_$": float(p50 * budget),
        "p01_$": float(p01 * budget),
        "p05_$": float(p05 * budget),
        "p10_$": float(p10 * budget),
        "p90_$": float(p90 * budget),
        "p95_$": float(p95 * budget),
        "p99_$": float(p99 * budget),
        "cvar05_$": float(cvar05 * budget),
        "prob_loss": float(np.mean(net_returns < 0.0)),
        "expected_net_return_%": float(100.0 * net_returns.mean()),
    }


def bootstrap_allocations(
    priors: Sequence[ProductPrior],
    n_bootstrap: int,
    n_sims_per_bootstrap: int,
    seed: int,
    max_used: float,
    max_per_product: float,
    risk_aversion: float,
    common_factor_sigma: float,
    integer_percent: bool,
    round_step: float,
    prune_under: float,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    allocations = []
    for _ in range(n_bootstrap):
        boot_seed = int(rng.integers(0, 2**32 - 1))
        _, _, _, weights = run_optimization(
            priors,
            n_sims=n_sims_per_bootstrap,
            seed=boot_seed,
            max_used=max_used,
            max_per_product=max_per_product,
            risk_aversion=risk_aversion,
            common_factor_sigma=common_factor_sigma,
            prune_under=prune_under,
            integer_percent=integer_percent,
            round_step=round_step,
        )
        allocations.append(weights)
    return np.vstack(allocations)


def money(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float) -> str:
    return f"{100.0 * x:,.2f}%"


def side_for_weight(w: float) -> str:
    if w > 1e-9:
        return "BUY"
    if w < -1e-9:
        return "SELL"
    return "SKIP"


def print_prior_table(priors: Sequence[ProductPrior], mu: np.ndarray) -> None:
    print("\n=== Return priors and simulated means ===")
    header = f"{'Product':36s} {'Low':>9s} {'Mode':>9s} {'High':>9s} {'MC mean':>9s}"
    print(header)
    print("-" * len(header))
    for p, m in zip(priors, mu):
        print(f"{p.name[:36]:36s} {pct(p.lo):>9s} {pct(p.mode):>9s} {pct(p.hi):>9s} {pct(m):>9s}")


def print_profile_summary(samples: np.ndarray, mu: np.ndarray, weights: np.ndarray) -> Dict[str, float]:
    net = net_return_scenarios(samples, weights)
    summary = summarize_distribution(net)
    print("\n=== Optimized profile summary ===")
    print(f"Budget used:          {pct(np.sum(np.abs(weights)))}")
    print(f"Quadratic fees:       {money(BUDGET * np.sum(np.abs(weights) ** 2))}")
    print(f"Expected gross PnL:   {money(BUDGET * float(mu @ weights))}")
    print(f"Expected net PnL:     {money(summary['mean_$'])}  ({summary['expected_net_return_%']:.2f}% of budget)")
    print(f"Std dev net PnL:      {money(summary['std_$'])}")
    print(f"Probability of loss:  {100.0 * summary['prob_loss']:.2f}%")
    print(f"1% / 5% / 10% PnL:    {money(summary['p01_$'])} / {money(summary['p05_$'])} / {money(summary['p10_$'])}")
    print(f"5% CVaR:              {money(summary['cvar05_$'])}")
    print(f"Median / 95% PnL:     {money(summary['median_$'])} / {money(summary['p95_$'])}")
    return summary


def allocation_rows(
    priors: Sequence[ProductPrior],
    weights: np.ndarray,
    mu: np.ndarray,
    samples: np.ndarray,
    budget: float = BUDGET,
) -> List[Dict[str, object]]:
    fee_by_name = np.abs(weights) ** 2 * budget
    gross_by_name = weights * mu * budget
    net_by_name = gross_by_name - fee_by_name
    contrib_scenarios = samples * weights[None, :] - (np.abs(weights) ** 2)[None, :]
    p05 = np.quantile(contrib_scenarios, 0.05, axis=0) * budget
    p95 = np.quantile(contrib_scenarios, 0.95, axis=0) * budget
    direction = np.sign(weights)
    directional_mean = direction * mu
    marginal_edge = directional_mean - 2.0 * np.abs(weights)

    rows: List[Dict[str, object]] = []
    for i, p in enumerate(priors):
        rows.append(
            {
                "product": p.name,
                "side": side_for_weight(weights[i]),
                "budget_pct": 100.0 * abs(weights[i]),
                "signed_pct": 100.0 * weights[i],
                "lo_pct": 100.0 * p.lo,
                "mode_pct": 100.0 * p.mode,
                "hi_pct": 100.0 * p.hi,
                "mc_mean_return_pct": 100.0 * mu[i],
                "directional_mean_pct": 100.0 * directional_mean[i],
                "marginal_edge_pct": 100.0 * marginal_edge[i],
                "fee_usd": fee_by_name[i],
                "expected_gross_usd": gross_by_name[i],
                "expected_net_contribution_usd": net_by_name[i],
                "contribution_p05_usd": p05[i],
                "contribution_p95_usd": p95[i],
                "thesis": p.thesis,
            }
        )
    rows.sort(key=lambda r: abs(float(r["signed_pct"])), reverse=True)
    return rows


def print_allocation_table(
    priors: Sequence[ProductPrior],
    weights: np.ndarray,
    mu: np.ndarray,
    samples: np.ndarray,
    boot: Optional[np.ndarray] = None,
) -> None:
    rows = allocation_rows(priors, weights, mu, samples)

    print("\n=== Allocation detail ===")
    header = (
        f"{'Product':36s} {'Side':>5s} {'Budget':>9s} {'Signed':>9s} "
        f"{'DirMean':>9s} {'MargEdge':>9s} {'Fee':>11s} {'ExpGross':>11s} "
        f"{'ExpNet':>11s} {'P05':>11s} {'P95':>11s}"
    )
    if boot is not None:
        header += f" {'BootAvg':>9s} {'BootSD':>9s}"
    print(header)
    print("-" * len(header))

    boot_by_name: Dict[str, Tuple[float, float]] = {}
    if boot is not None:
        for i, p in enumerate(priors):
            boot_by_name[p.name] = (float(boot[:, i].mean()), float(boot[:, i].std(ddof=1)))

    for row in rows:
        line = (
            f"{str(row['product'])[:36]:36s} {str(row['side']):>5s} "
            f"{row['budget_pct']:8.2f}% {row['signed_pct']:8.2f}% "
            f"{row['directional_mean_pct']:8.2f}% {row['marginal_edge_pct']:8.2f}% "
            f"{money(float(row['fee_usd'])):>11s} {money(float(row['expected_gross_usd'])):>11s} "
            f"{money(float(row['expected_net_contribution_usd'])):>11s} "
            f"{money(float(row['contribution_p05_usd'])):>11s} {money(float(row['contribution_p95_usd'])):>11s}"
        )
        if boot is not None:
            mean, sd = boot_by_name[str(row["product"])]
            line += f" {pct(mean):>9s} {pct(sd):>9s}"
        print(line)

    print("\nMargEdge is the approximate incremental expected net return from adding a tiny same-direction trade.")
    print("Positive MargEdge usually means the name is capped or budget-constrained.")


def save_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    fieldnames = [
        "product",
        "side",
        "budget_pct",
        "signed_pct",
        "lo_pct",
        "mode_pct",
        "hi_pct",
        "mc_mean_return_pct",
        "directional_mean_pct",
        "marginal_edge_pct",
        "fee_usd",
        "expected_gross_usd",
        "expected_net_contribution_usd",
        "contribution_p05_usd",
        "contribution_p95_usd",
        "thesis",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_cap_sweep(text: str) -> List[float]:
    values = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        val = float(chunk)
        if val > 1.0:
            val /= 100.0
        values.append(val)
    return values


def run_cap_sweep(priors: Sequence[ProductPrior], args: argparse.Namespace, caps: Iterable[float]) -> None:
    print("\n=== Max-per-product cap sweep ===")
    header = f"{'Cap':>8s} {'Used':>8s} {'ExpNet':>12s} {'Fees':>12s} {'Top position':>44s}"
    print(header)
    print("-" * len(header))
    for cap in caps:
        samples, mu, _, weights = run_optimization(
            priors,
            args.n_sims,
            args.seed,
            args.max_used,
            cap,
            args.risk_aversion,
            args.common_factor_sigma,
            prune_under=args.prune_under,
            integer_percent=args.integer_percent,
            round_step=args.round_step,
        )
        net = net_return_scenarios(samples, weights)
        top_i = int(np.argmax(np.abs(weights)))
        top_desc = f"{side_for_weight(weights[top_i])} {pct(abs(weights[top_i]))} {priors[top_i].name[:28]}"
        print(
            f"{pct(cap):>8s} {pct(np.sum(np.abs(weights))):>8s} "
            f"{money(net.mean() * BUDGET):>12s} {money(BUDGET * np.sum(np.abs(weights) ** 2)):>12s} "
            f"{top_desc:>44s}"
        )


def run_prior_sensitivity(args: argparse.Namespace) -> None:
    print("\n=== Prior-set sensitivity ===")
    header = f"{'Prior set':>14s} {'Used':>8s} {'ExpNet':>12s} {'P05':>12s} {'Top three positions':>72s}"
    print(header)
    print("-" * len(header))
    for name, priors in PRIORS_BY_NAME.items():
        samples, _, _, weights = run_optimization(
            priors,
            args.n_sims,
            args.seed,
            args.max_used,
            args.max_per_product,
            args.risk_aversion,
            args.common_factor_sigma,
            prune_under=args.prune_under,
            integer_percent=args.integer_percent,
            round_step=args.round_step,
        )
        net = net_return_scenarios(samples, weights)
        p05 = float(np.quantile(net, 0.05) * BUDGET)
        top = np.argsort(-np.abs(weights))[:3]
        desc = "; ".join(f"{side_for_weight(weights[i])} {pct(abs(weights[i]))} {priors[i].name[:18]}" for i in top)
        print(f"{name:>14s} {pct(np.sum(np.abs(weights))):>8s} {money(net.mean() * BUDGET):>12s} {money(p05):>12s} {desc:>72s}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize the Prosperity manual news-trading profile.")
    parser.add_argument("--prior-set", choices=["base", "conservative", "aggressive", "blend"], default="base")
    parser.add_argument("--blend-weight", type=float, default=0.50, help="For --prior-set blend: 0=conservative, 1=aggressive.")
    parser.add_argument("--priors-csv", type=Path, default=None, help="Optional custom priors CSV with name,lo,mode,hi,thesis,beta.")
    parser.add_argument("--write-template", type=Path, default=None, help="Write a priors CSV template, then exit.")
    parser.add_argument("--n-sims", type=int, default=200_000, help="Monte Carlo scenarios for final optimization.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--risk-aversion", type=float, default=3.0, help="Variance penalty. 0=risk-neutral; 2-6 is a useful range.")
    parser.add_argument("--max-used", type=float, default=1.0, help="Max total absolute allocation. 1.0 = 100%% of budget.")
    parser.add_argument("--max-per-product", type=float, default=0.25, help="Max absolute allocation per product. 0.25 = 25%%.")
    parser.add_argument("--common-factor-sigma", type=float, default=0.04, help="Std dev of common market shock. 0 removes correlation.")
    parser.add_argument("--prune-under", type=float, default=0.0, help="Drop positions below this absolute fraction and re-optimize.")
    parser.add_argument("--integer-percent", action="store_true", help="Round to a discrete grid and polish locally.")
    parser.add_argument("--round-step", type=float, default=0.01, help="Discrete grid step. 0.01 = 1 percentage point.")
    parser.add_argument("--bootstrap-runs", type=int, default=50, help="Repeated MC optimizations for stability diagnostics. 0 skips.")
    parser.add_argument("--bootstrap-sims", type=int, default=25_000, help="Scenarios per bootstrap optimization.")
    parser.add_argument("--save-csv", type=Path, default=None, help="Optional allocation diagnostics CSV path.")
    parser.add_argument("--cap-sweep", type=str, default="", help="Comma-separated max-per-product caps, e.g. 0.15,0.20,0.25,0.35,1.0")
    parser.add_argument("--sensitivity", action="store_true", help="Compare conservative/base/aggressive prior sets.")
    return parser


def select_priors(args: argparse.Namespace) -> List[ProductPrior]:
    if args.priors_csv is not None:
        return load_priors_csv(args.priors_csv)
    if args.prior_set == "blend":
        return make_blended_priors(args.blend_weight)
    return PRIORS_BY_NAME[args.prior_set]


def validate_args(args: argparse.Namespace) -> None:
    if not 0.0 <= args.max_used <= 1.0:
        raise ValueError("--max-used must be between 0 and 1")
    if not 0.0 < args.max_per_product <= 1.0:
        raise ValueError("--max-per-product must be in (0, 1]")
    if args.round_step <= 0.0:
        raise ValueError("--round-step must be positive")
    if args.n_sims <= 0:
        raise ValueError("--n-sims must be positive")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    validate_args(args)

    if args.write_template is not None:
        write_priors_template(args.write_template, select_priors(args))
        print(f"Wrote priors template to: {args.write_template}")
        return

    priors = select_priors(args)
    samples, mu, _, weights = run_optimization(
        priors,
        n_sims=args.n_sims,
        seed=args.seed,
        max_used=args.max_used,
        max_per_product=args.max_per_product,
        risk_aversion=args.risk_aversion,
        common_factor_sigma=args.common_factor_sigma,
        prune_under=args.prune_under,
        integer_percent=args.integer_percent,
        round_step=args.round_step,
    )

    boot = None
    if args.bootstrap_runs > 0:
        boot = bootstrap_allocations(
            priors,
            n_bootstrap=args.bootstrap_runs,
            n_sims_per_bootstrap=args.bootstrap_sims,
            seed=args.seed + 10_000,
            max_used=args.max_used,
            max_per_product=args.max_per_product,
            risk_aversion=args.risk_aversion,
            common_factor_sigma=args.common_factor_sigma,
            integer_percent=args.integer_percent,
            round_step=args.round_step,
            prune_under=args.prune_under,
        )

    print_prior_table(priors, mu)
    print_profile_summary(samples, mu, weights)
    print_allocation_table(priors, weights, mu, samples, boot=boot)

    rows = allocation_rows(priors, weights, mu, samples)
    if args.save_csv is not None:
        save_csv(args.save_csv, rows)
        print(f"\nSaved CSV to: {args.save_csv}")

    print("\n=== Submission-ready signed profile ===")
    used_pct = 0.0
    threshold = max(args.round_step / 2.0 if args.integer_percent else 0.001, 0.0005)
    for p, w in zip(priors, weights):
        if abs(w) >= threshold:
            side = "BUY " if w > 0.0 else "SELL"
            print(f"{side:4s} {abs(w) * 100.0:6.2f}%  {p.name}")
            used_pct += abs(w) * 100.0
    print(f"Total used: {used_pct:.2f}%")

    if args.cap_sweep:
        run_cap_sweep(priors, args, parse_cap_sweep(args.cap_sweep))

    if args.sensitivity:
        run_prior_sensitivity(args)


if __name__ == "__main__":
    main()
