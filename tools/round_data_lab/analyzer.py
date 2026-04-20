from __future__ import annotations

import math
import statistics
from collections import defaultdict
from functools import lru_cache

from .data_loader import load_round
from .models import BoundaryBand, ProductProfile, Snapshot


TYPE_CATALOG = {
    "flat": {
        "label": "Flat",
        "description": "Very tight stationary price action around a stable anchor.",
    },
    "flat_random_walk": {
        "label": "Flat Random Walk",
        "description": "Sideways action with light wandering and low structural drift.",
    },
    "mean_reverting_band": {
        "label": "Mean-Reverting Band",
        "description": "Range-bound process that keeps snapping back toward a central value.",
    },
    "trend_channel": {
        "label": "Trend Channel",
        "description": "Persistent drift with noise that stays inside a sloped channel.",
    },
    "volatile_random_walk": {
        "label": "Volatile Random Walk",
        "description": "Looser price action with higher variance and weaker structural anchors.",
    },
}


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = q * (len(ordered) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def safe_std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if len(xs) != len(ys) or not xs:
        return 0.0, ys[0] if ys else 0.0
    if len(xs) == 1:
        return 0.0, ys[0]
    x_bar = safe_mean(xs)
    y_bar = safe_mean(ys)
    denom = sum((x - x_bar) ** 2 for x in xs)
    if denom == 0:
        return 0.0, y_bar
    slope = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys)) / denom
    intercept = y_bar - slope * x_bar
    return slope, intercept


def autocorrelation(values: list[float], lag_value: int = 1) -> float:
    if len(values) <= lag_value:
        return 0.0
    left = values[:-lag_value]
    right = values[lag_value:]
    left_bar = safe_mean(left)
    right_bar = safe_mean(right)
    denom_left = sum((value - left_bar) ** 2 for value in left)
    denom_right = sum((value - right_bar) ** 2 for value in right)
    if denom_left == 0 or denom_right == 0:
        return 0.0
    numer = sum((a - left_bar) * (b - right_bar) for a, b in zip(left, right))
    return numer / math.sqrt(denom_left * denom_right)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# Default length for synthetic series seeded from an analyzed real product.
SYNTHETIC_DEFAULT_TICKS = 10_000


def _chart_points(
    snapshots: list[Snapshot],
    day_fits: dict[int, tuple[float, float]],
) -> list[dict[str, float | int]]:
    chart_snaps = [
        snap for snap in snapshots
        if (
            snap.bid_price_1 is not None
            and snap.ask_price_1 is not None
            and snap.bid_price_1 > 0
            and snap.ask_price_1 > 0
        )
    ]
    if not chart_snaps:
        return []
    stride = max(1, len(chart_snaps) // 700)
    points: list[dict[str, float | int]] = []
    for idx, snap in enumerate(chart_snaps[::stride]):
        trend = None
        fit = day_fits.get(snap.day)
        if fit is not None and snap.mid_price is not None:
            slope, intercept = fit
            trend = intercept + slope * snap.tick
        points.append(
            {
                "index": idx * stride,
                "day": snap.day,
                "timestamp": snap.timestamp,
                "tick": snap.tick,
                "mid": round(float(snap.mid_price), 4) if snap.mid_price is not None else None,
                "bid": round(float(snap.bid_price_1), 4) if snap.bid_price_1 is not None else None,
                "ask": round(float(snap.ask_price_1), 4) if snap.ask_price_1 is not None else None,
                "spread": round(float(snap.spread), 4) if snap.spread is not None else None,
                "trend": round(float(trend), 4) if trend is not None else None,
            }
        )
    return points


def _score_types(
    mean_mid: float,
    range_width: float,
    avg_day_range: float,
    slope_per_tick: float,
    avg_trend_move: float,
    residual_std: float,
    return_std: float,
    return_autocorr: float,
    direction_consistency: float,
    anchor_strength: float,
) -> dict[str, float]:
    flat_range_cutoff = max(20.0, mean_mid * 0.0015)
    band_tightness = clamp(1.0 - residual_std / max(range_width, 1.0), 0.0, 1.0)
    trend_strength = clamp(abs(avg_trend_move) / max(avg_day_range, 1.0), 0.0, 1.0)
    drift_quietness = clamp(1.0 - abs(slope_per_tick) / 0.12, 0.0, 1.0)
    reversion = clamp(-return_autocorr, 0.0, 1.0)
    stability = clamp(1.0 - return_std / max(range_width / 6.0, 1.0), 0.0, 1.0)
    small_range = clamp(1.0 - avg_day_range / flat_range_cutoff, 0.0, 1.0)
    wandering = clamp(avg_day_range / max(flat_range_cutoff * 2.5, 1.0), 0.0, 1.0)

    scores = {
        "flat": 0.33 * drift_quietness + 0.24 * stability + 0.18 * band_tightness + 0.25 * small_range,
        "flat_random_walk": (
            0.26 * drift_quietness
            + 0.18 * stability
            + 0.16 * (1.0 - reversion)
            + 0.20 * wandering
            + 0.20 * (1.0 - direction_consistency)
        ),
        "mean_reverting_band": 0.35 * reversion + 0.20 * drift_quietness + 0.20 * band_tightness + 0.25 * anchor_strength,
        "trend_channel": 0.45 * trend_strength + 0.20 * band_tightness + 0.20 * direction_consistency + 0.15 * (1.0 - drift_quietness),
        "volatile_random_walk": 0.55 * (1.0 - stability) + 0.25 * (1.0 - band_tightness) + 0.20 * (1.0 - reversion),
    }
    return {key: clamp(value, 0.0, 1.0) for key, value in scores.items()}


def _generator_template(
    product: str,
    type_key: str,
    first_mid: float,
    mean_mid: float,
    slope_per_tick: float,
    return_std: float,
    return_autocorr: float,
    spreads: list[float],
    mids: list[float],
    residuals: list[float],
) -> dict[str, float | int | str]:
    q01_mid = quantile(mids, 0.01)
    q99_mid = quantile(mids, 0.99)
    lower_offset = round(quantile(residuals, 0.01), 4) if residuals else -3.0
    upper_offset = round(quantile(residuals, 0.99), 4) if residuals else 3.0
    lower_bound = round(q01_mid, 4)
    upper_bound = round(q99_mid, 4)
    if type_key == "trend_channel":
        projected_end = first_mid + slope_per_tick * float(SYNTHETIC_DEFAULT_TICKS)
        lower_bound = round(min(first_mid, projected_end) + lower_offset, 4)
        upper_bound = round(max(first_mid, projected_end) + upper_offset, 4)
    template = {
        "product_name": product,
        "type_key": type_key,
        "ticks": SYNTHETIC_DEFAULT_TICKS,
        "start_price": round(first_mid, 4),
        "anchor_price": round(mean_mid, 4),
        "drift_per_tick": round(slope_per_tick, 4),
        "noise": round(max(0.35, return_std), 4),
        "reversion_strength": round(clamp(-return_autocorr * 0.55, 0.05, 0.9), 4),
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "lower_bound_offset": lower_offset,
        "upper_bound_offset": upper_offset,
        "spread_mean": round(safe_mean(spreads), 4),
        "spread_jitter": round(max(0.5, safe_std(spreads)), 4),
        "shock_probability": 0.0,
        "shock_size": 0.0,
        "shock_bias": 0.0,
        "volume_base": 18,
    }
    return template


def analyze_snapshots(
    product: str,
    snapshots: list[Snapshot],
    round_num: int | None = None,
) -> ProductProfile:
    mid_snaps = [snap for snap in snapshots if snap.mid_price is not None]
    if not mid_snaps:
        raise ValueError(f"No midpoint data available for {product}")

    by_day: dict[int, list[Snapshot]] = defaultdict(list)
    for snap in mid_snaps:
        by_day[snap.day].append(snap)

    mids = [float(snap.mid_price) for snap in mid_snaps]
    spreads = [float(snap.spread) for snap in mid_snaps if snap.spread is not None]
    residuals: list[float] = []
    returns: list[float] = []
    day_fits: dict[int, tuple[float, float]] = {}
    day_slopes: list[float] = []
    day_trend_moves: list[float] = []
    day_ranges: list[float] = []
    day_slope_signs: list[int] = []

    for day, day_snaps in by_day.items():
        day_snaps.sort(key=lambda snap: snap.timestamp)
        xs = [float(snap.tick) for snap in day_snaps]
        ys = [float(snap.mid_price) for snap in day_snaps if snap.mid_price is not None]
        slope, intercept = linear_fit(xs, ys)
        day_fits[day] = (slope, intercept)
        day_slopes.append(slope)
        if abs(slope) >= 0.0005:
            day_slope_signs.append(1 if slope > 0 else -1)
        if xs:
            day_trend_moves.append(slope * (xs[-1] - xs[0]))
        if ys:
            day_ranges.append(max(ys) - min(ys))
        residuals.extend(
            mid - (intercept + slope * x)
            for x, mid in zip(xs, ys)
        )
        returns.extend(ys[idx] - ys[idx - 1] for idx in range(1, len(ys)))

    mean_mid = safe_mean(mids)
    range_width = max(mids) - min(mids)
    spread_mean = safe_mean(spreads)
    spread_std = safe_std(spreads)
    slope_per_tick = safe_mean(day_slopes)
    avg_trend_move = safe_mean(day_trend_moves)
    avg_day_range = safe_mean(day_ranges)
    residual_std = safe_std(residuals)
    return_std = safe_std(returns)
    return_autocorr = autocorrelation(returns, lag_value=1)
    mean_reversion_score = clamp(-return_autocorr, 0.0, 1.0)
    direction_consistency = (
        abs(sum(day_slope_signs)) / len(day_slope_signs) if day_slope_signs else 0.0
    )
    anchor_strength = clamp(return_std / max(residual_std, 1e-6), 0.0, 1.0)
    flat_range_cutoff = max(20.0, mean_mid * 0.0015)

    type_scores = _score_types(
        mean_mid=mean_mid,
        range_width=range_width,
        avg_day_range=avg_day_range,
        slope_per_tick=slope_per_tick,
        avg_trend_move=avg_trend_move,
        residual_std=residual_std,
        return_std=return_std,
        return_autocorr=return_autocorr,
        direction_consistency=direction_consistency,
        anchor_strength=anchor_strength,
    )

    type_key = max(type_scores, key=type_scores.get)
    if (
        abs(avg_trend_move) > max(75.0, avg_day_range * 0.72)
        and residual_std < max(12.0, avg_day_range * 0.08)
        and direction_consistency >= 0.6
    ):
        type_key = "trend_channel"
    elif (
        avg_day_range <= flat_range_cutoff
        and residual_std <= max(1.5, flat_range_cutoff * 0.2)
        and abs(avg_trend_move) <= max(6.0, avg_day_range * 0.2)
    ):
        type_key = "flat"
    elif (
        abs(slope_per_tick) < 0.05
        and return_autocorr < -0.08
        and range_width < max(80.0, flat_range_cutoff * 4.0)
        and anchor_strength >= 0.45
    ):
        type_key = "mean_reverting_band"
    elif abs(slope_per_tick) < 0.05 and direction_consistency < 0.6:
        type_key = "flat_random_walk"

    metadata = TYPE_CATALOG[type_key]
    confidence = 0.52 + 0.43 * type_scores[type_key]
    metrics = {
        "mid_mean": mean_mid,
        "mid_std": safe_std(mids),
        "mid_min": min(mids),
        "mid_max": max(mids),
        "mid_range": range_width,
        "spread_mean": spread_mean,
        "spread_std": spread_std,
        "return_std": return_std,
        "return_autocorr": return_autocorr,
        "slope_per_tick": slope_per_tick,
        "avg_daily_trend_move": avg_trend_move,
        "avg_daily_range": avg_day_range,
        "residual_std": residual_std,
        "direction_consistency": direction_consistency,
        "anchor_strength": anchor_strength,
        "mean_reversion_score": mean_reversion_score,
    }
    boundaries = {
        "mid_soft": BoundaryBand(quantile(mids, 0.05), quantile(mids, 0.95)),
        "mid_hard": BoundaryBand(quantile(mids, 0.01), quantile(mids, 0.99)),
        "spread_soft": BoundaryBand(quantile(spreads, 0.05), quantile(spreads, 0.95)),
        "spread_hard": BoundaryBand(quantile(spreads, 0.01), quantile(spreads, 0.99)),
    }
    if residuals:
        boundaries["trend_residual_soft"] = BoundaryBand(
            quantile(residuals, 0.05),
            quantile(residuals, 0.95),
        )
        boundaries["trend_residual_hard"] = BoundaryBand(
            quantile(residuals, 0.01),
            quantile(residuals, 0.99),
        )

    generator_template = _generator_template(
        product=product,
        type_key=type_key,
        first_mid=float(mid_snaps[0].mid_price),
        mean_mid=mean_mid,
        slope_per_tick=slope_per_tick,
        return_std=return_std,
        return_autocorr=return_autocorr,
        spreads=spreads,
        mids=mids,
        residuals=residuals,
    )

    return ProductProfile(
        round_num=round_num,
        product=product,
        sample_count=len(mid_snaps),
        day_count=len(by_day),
        type_key=type_key,
        type_label=metadata["label"],
        description=metadata["description"],
        confidence=confidence,
        metrics=metrics,
        boundaries=boundaries,
        type_scores=type_scores,
        generator_template=generator_template,
        chart_points=_chart_points(mid_snaps, day_fits),
    )


@lru_cache(maxsize=16)
def analyze_round(round_num: int) -> dict[str, ProductProfile]:
    round_data = load_round(round_num)
    return {
        product: analyze_snapshots(product, snapshots, round_num=round_num)
        for product, snapshots in sorted(round_data.items())
    }


def round_summary(round_num: int) -> dict[str, object]:
    profiles = analyze_round(round_num)
    return {
        "round": round_num,
        "products": [
            profile.to_dict(include_series=False) for profile in profiles.values()
        ],
    }


def product_detail(round_num: int, product: str) -> dict[str, object]:
    profiles = analyze_round(round_num)
    if product not in profiles:
        raise KeyError(f"Unknown product {product} for round {round_num}")
    return profiles[product].to_dict(include_series=True)
