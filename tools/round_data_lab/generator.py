from __future__ import annotations

import csv
import io
import math
import random
import secrets
from typing import Any

from .models import Snapshot


TYPE_OPTIONS = {
    "flat": "Tight anchor with minimal drift.",
    "flat_random_walk": "Sideways process with light local wandering.",
    "mean_reverting_band": "Stable anchor with repeated snapback.",
    "trend_channel": "Sloped path inside a bounded channel.",
    "volatile_random_walk": "Higher-noise process with weaker structural boundaries.",
}

EVENT_MODE_OPTIONS = {
    "none": "No single macro event.",
    "crash": "One random crash timestamp that sharply drops the market.",
    "jump": "One random jump timestamp that sharply lifts the market.",
    "either": "Pick crash or jump at random from the seed.",
}

PARAMETER_SCHEMA = [
    {"key": "ticks", "label": "Ticks", "min": 100, "max": 20000, "step": 100},
    {"key": "seed", "label": "Seed", "min": 0, "max": 9999, "step": 1},
    {"key": "start_price", "label": "Start Price", "min": 1, "max": 50000, "step": 0.5},
    {"key": "anchor_price", "label": "Anchor Price", "min": 1, "max": 50000, "step": 0.5},
    {"key": "drift_per_tick", "label": "Drift / Tick", "min": -5, "max": 5, "step": 0.01},
    {"key": "noise", "label": "Noise", "min": 0.1, "max": 25, "step": 0.05},
    {"key": "reversion_strength", "label": "Reversion", "min": 0, "max": 1, "step": 0.01},
    {"key": "lower_bound", "label": "Lower Bound", "min": 1, "max": 50000, "step": 0.5},
    {"key": "upper_bound", "label": "Upper Bound", "min": 1, "max": 50000, "step": 0.5},
    {"key": "lower_bound_offset", "label": "Lower Offset", "min": -1000, "max": 1000, "step": 0.1},
    {"key": "upper_bound_offset", "label": "Upper Offset", "min": -1000, "max": 1000, "step": 0.1},
    {"key": "spread_mean", "label": "Spread Mean", "min": 1, "max": 100, "step": 0.5},
    {"key": "spread_jitter", "label": "Spread Jitter", "min": 0, "max": 40, "step": 0.1},
    {"key": "shock_probability", "label": "Shock Prob.", "min": 0, "max": 0.2, "step": 0.001},
    {"key": "shock_size", "label": "Shock Size", "min": 0, "max": 1000, "step": 0.5},
    {"key": "shock_bias", "label": "Shock Bias", "min": -1, "max": 1, "step": 0.05},
    {"key": "major_event_size", "label": "Event Size", "min": 0, "max": 5000, "step": 0.5},
    {"key": "major_event_persistence", "label": "Event Persistence", "min": 0, "max": 0.999, "step": 0.001},
    {"key": "major_event_volatility", "label": "Event Volatility", "min": 1, "max": 20, "step": 0.1},
    {"key": "volume_base", "label": "Base Volume", "min": 1, "max": 100, "step": 1},
]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_trend_channel_bounds(config: dict[str, Any]) -> None:
    ticks = int(config["ticks"])
    start_price = float(config["start_price"])
    drift = float(config["drift_per_tick"])
    lower_offset = float(config["lower_bound_offset"])
    upper_offset = float(config["upper_bound_offset"])
    projected_end = start_price + drift * max(0, ticks - 1)
    required_lower = min(start_price, projected_end) + lower_offset
    required_upper = max(start_price, projected_end) + upper_offset
    config["lower_bound"] = min(float(config["lower_bound"]), required_lower)
    config["upper_bound"] = max(float(config["upper_bound"]), required_upper)


def default_config(type_key: str) -> dict[str, Any]:
    base = {
        "product_name": f"SYNTH_{type_key.upper()}",
        "type_key": type_key,
        "ticks": 10000,
        "seed": None,
        "start_price": 10000.0,
        "anchor_price": 10000.0,
        "drift_per_tick": 0.0,
        "noise": 1.0,
        "reversion_strength": 0.25,
        "lower_bound": 9990.0,
        "upper_bound": 10010.0,
        "lower_bound_offset": -3.0,
        "upper_bound_offset": 3.0,
        "spread_mean": 10.0,
        "spread_jitter": 2.0,
        "shock_probability": 0.0,
        "shock_size": 0.0,
        "shock_bias": 0.0,
        "major_event_mode": "none",
        "major_event_size": 60.0,
        "major_event_persistence": 0.992,
        "major_event_volatility": 2.6,
        "volume_base": 18,
    }
    overrides = {
        "flat": {
            "noise": 0.6,
            "spread_mean": 6.0,
            "spread_jitter": 1.0,
        },
        "flat_random_walk": {
            "noise": 1.2,
            "lower_bound": 9980.0,
            "upper_bound": 10020.0,
            "spread_mean": 8.0,
        },
        "mean_reverting_band": {
            "noise": 1.5,
            "reversion_strength": 0.35,
            "lower_bound": 9985.0,
            "upper_bound": 10015.0,
            "spread_mean": 12.0,
        },
        "trend_channel": {
            "start_price": 11000.0,
            "anchor_price": 11000.0,
            "drift_per_tick": 0.1,
            "noise": 1.4,
            "lower_bound": 11000.0,
            "upper_bound": 12020.0,
            "lower_bound_offset": -4.0,
            "upper_bound_offset": 4.0,
            "spread_mean": 14.0,
            "spread_jitter": 2.5,
            "major_event_size": 110.0,
        },
        "volatile_random_walk": {
            "noise": 3.5,
            "lower_bound": 9950.0,
            "upper_bound": 10050.0,
            "spread_mean": 16.0,
            "spread_jitter": 4.0,
            "major_event_size": 95.0,
            "major_event_volatility": 3.4,
        },
    }
    merged = dict(base)
    merged.update(overrides.get(type_key, {}))
    return merged


def merge_config(raw: dict[str, Any]) -> dict[str, Any]:
    type_key = str(raw.get("type_key") or "flat_random_walk")
    config = default_config(type_key)
    for key, value in raw.items():
        if value is None:
            continue
        config[key] = value
    config["type_key"] = type_key
    config["major_event_mode"] = str(config.get("major_event_mode") or "none")
    if float(config["lower_bound"]) > float(config["upper_bound"]):
        config["lower_bound"], config["upper_bound"] = config["upper_bound"], config["lower_bound"]
    if float(config["lower_bound_offset"]) > float(config["upper_bound_offset"]):
        config["lower_bound_offset"], config["upper_bound_offset"] = (
            config["upper_bound_offset"],
            config["lower_bound_offset"],
        )
    if type_key == "trend_channel":
        _normalize_trend_channel_bounds(config)
    if config.get("seed") is None:
        config["seed"] = secrets.randbelow(10000)
    return config


def _reflect(value: float, lower: float, upper: float) -> float:
    if lower >= upper:
        return value
    current = value
    while current < lower or current > upper:
        if current < lower:
            current = lower + (lower - current)
        if current > upper:
            current = upper - (current - upper)
    return current


def _level_delta(spread: int, step: int) -> int:
    return max(1, int(round(spread * (0.25 + 0.2 * step))))


def _smoothstep(progress: float) -> float:
    x = clamp(progress, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _sample_major_event_profile(
    ticks: int,
    mode: str,
    size: float,
    persistence: float,
    volatility: float,
    rng: random.Random,
) -> dict[str, float | int] | None:
    if mode == "none" or ticks < 8 or size <= 0.0:
        return None

    left = max(4, ticks // 8)
    right = max(left, ticks - left - 1)
    direction = -1 if mode == "crash" else 1 if mode == "jump" else rng.choice([-1, 1])
    event_start = rng.randint(left, right)
    ramp_steps = rng.randint(max(3, ticks // 100), max(8, ticks // 28))
    permanent_fraction = clamp(
        0.55 + 0.35 * persistence + rng.uniform(-0.05, 0.08),
        0.45,
        0.96,
    )
    peak_fraction = clamp(permanent_fraction + rng.uniform(0.08, 0.28), 0.6, 1.35)
    decay_rate = max(0.004, (1.0 - persistence) * 8.0)
    return {
        "start": event_start,
        "ramp_steps": ramp_steps,
        "direction": direction,
        "permanent_shift": direction * size * permanent_fraction,
        "peak_shift": direction * size * peak_fraction,
        "flow_bias": direction * size * rng.uniform(0.006, 0.02),
        "volatility_multiplier": max(1.0, volatility * rng.uniform(0.9, 1.2)),
        "spread_multiplier": rng.uniform(1.8, 3.2),
        "volume_multiplier": rng.uniform(1.6, 2.7),
        "decay_rate": decay_rate,
    }


def _major_event_state(
    step: int,
    profile: dict[str, float | int] | None,
) -> dict[str, float]:
    if profile is None:
        return {
            "level": 0.0,
            "flow_bias": 0.0,
            "volatility_multiplier": 1.0,
            "spread_multiplier": 1.0,
            "volume_multiplier": 1.0,
        }

    start = int(profile["start"])
    if step < start:
        return {
            "level": 0.0,
            "flow_bias": 0.0,
            "volatility_multiplier": 1.0,
            "spread_multiplier": 1.0,
            "volume_multiplier": 1.0,
        }

    ramp_steps = max(1, int(profile["ramp_steps"]))
    permanent_shift = float(profile["permanent_shift"])
    peak_shift = float(profile["peak_shift"])
    flow_bias = float(profile["flow_bias"])
    volatility_multiplier = float(profile["volatility_multiplier"])
    spread_multiplier = float(profile["spread_multiplier"])
    volume_multiplier = float(profile["volume_multiplier"])
    decay_rate = float(profile["decay_rate"])
    elapsed = step - start

    if elapsed <= ramp_steps:
        progress = _smoothstep((elapsed + 1) / ramp_steps)
        intensity = progress
        return {
            "level": peak_shift * progress,
            "flow_bias": flow_bias * (0.6 + 0.8 * progress),
            "volatility_multiplier": 1.0 + (volatility_multiplier - 1.0) * intensity,
            "spread_multiplier": 1.0 + (spread_multiplier - 1.0) * intensity,
            "volume_multiplier": 1.0 + (volume_multiplier - 1.0) * intensity,
        }

    tail_elapsed = elapsed - ramp_steps
    relaxation = math.exp(-decay_rate * tail_elapsed)
    level = permanent_shift + (peak_shift - permanent_shift) * relaxation
    intensity = relaxation
    return {
        "level": level,
        "flow_bias": flow_bias * intensity,
        "volatility_multiplier": 1.0 + (volatility_multiplier - 1.0) * intensity,
        "spread_multiplier": 1.0 + (spread_multiplier - 1.0) * intensity,
        "volume_multiplier": 1.0 + (volume_multiplier - 1.0) * intensity,
    }


def _book_row(
    day: int,
    timestamp: int,
    product: str,
    mid: float,
    spread: int,
    rng: random.Random,
    volume_base: int,
) -> dict[str, Any] | None:
    half = spread / 2.0
    bid_1 = math.floor(mid - half)
    ask_1 = math.ceil(mid + half)
    if bid_1 <= 0 or ask_1 <= 0:
        return None
    if bid_1 >= ask_1:
        ask_1 = bid_1 + 1

    bid_2 = max(1, bid_1 - _level_delta(spread, 1))
    bid_3 = max(1, bid_2 - _level_delta(spread, 2))
    ask_2 = ask_1 + _level_delta(spread, 1)
    ask_3 = ask_2 + _level_delta(spread, 2)

    def volume(scale: float) -> int:
        return max(1, int(round(volume_base * scale + rng.gauss(0.0, max(1.0, volume_base * 0.15)))))

    return {
        "day": day,
        "timestamp": timestamp,
        "product": product,
        "bid_price_1": bid_1,
        "bid_volume_1": volume(1.0),
        "bid_price_2": bid_2,
        "bid_volume_2": volume(0.9),
        "bid_price_3": bid_3,
        "bid_volume_3": volume(0.8),
        "ask_price_1": ask_1,
        "ask_volume_1": volume(1.0),
        "ask_price_2": ask_2,
        "ask_volume_2": volume(0.9),
        "ask_price_3": ask_3,
        "ask_volume_3": volume(0.8),
        "mid_price": round((bid_1 + ask_1) / 2.0, 4),
        "profit_and_loss": 0.0,
    }


def _valid_price_row(row: dict[str, Any]) -> bool:
    price_keys = [
        "bid_price_1",
        "bid_price_2",
        "bid_price_3",
        "ask_price_1",
        "ask_price_2",
        "ask_price_3",
        "mid_price",
    ]
    return all(float(row[key]) > 0 for key in price_keys)


def generate_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = merge_config(config)
    rng = random.Random(int(cfg["seed"]))
    ticks = int(cfg["ticks"])
    type_key = str(cfg["type_key"])
    current = float(cfg["start_price"])
    trend_base = float(cfg["start_price"])
    anchor = float(cfg["anchor_price"])
    lower = float(cfg["lower_bound"])
    upper = float(cfg["upper_bound"])
    lower_offset = float(cfg["lower_bound_offset"])
    upper_offset = float(cfg["upper_bound_offset"])
    drift = float(cfg["drift_per_tick"])
    noise = float(cfg["noise"])
    reversion = float(cfg["reversion_strength"])
    spread_mean = float(cfg["spread_mean"])
    spread_jitter = float(cfg["spread_jitter"])
    shock_probability = float(cfg["shock_probability"])
    shock_size = float(cfg["shock_size"])
    shock_bias = float(cfg["shock_bias"])
    major_event_mode = str(cfg.get("major_event_mode") or "none")
    major_event_size = float(cfg.get("major_event_size", 0.0))
    major_event_persistence = clamp(float(cfg.get("major_event_persistence", 0.992)), 0.0, 0.999)
    major_event_volatility = max(1.0, float(cfg.get("major_event_volatility", 1.0)))
    volume_base = int(cfg["volume_base"])
    product = str(cfg["product_name"])

    residual = 0.0
    event_profile = _sample_major_event_profile(
        ticks=ticks,
        mode=major_event_mode,
        size=major_event_size,
        persistence=major_event_persistence,
        volatility=major_event_volatility,
        rng=rng,
    )
    event_prev_level = 0.0

    rows: list[dict[str, Any]] = []
    for step in range(ticks):
        event_state = _major_event_state(step, event_profile)
        event_level = event_state["level"]
        level_delta = event_level - event_prev_level
        event_prev_level = event_level

        if level_delta != 0.0:
            current += level_delta
            trend_base += level_delta
            anchor += level_delta
            lower += level_delta
            upper += level_delta

        effective_noise = noise * event_state["volatility_multiplier"]
        if type_key == "flat":
            current = anchor + rng.gauss(0.0, effective_noise * 0.35)
        elif type_key == "flat_random_walk":
            current += drift + rng.gauss(0.0, effective_noise)
            current = _reflect(current, lower, upper)
        elif type_key == "mean_reverting_band":
            current += reversion * (anchor - current) + rng.gauss(0.0, effective_noise)
            current = _reflect(current, lower, upper)
        elif type_key == "trend_channel":
            trend = trend_base + drift * step
            residual = 0.45 * residual + rng.gauss(0.0, effective_noise)
            residual = clamp(residual, lower_offset, upper_offset)
            current = trend + residual
        else:
            current += drift + rng.gauss(0.0, effective_noise * 1.75)
            current = _reflect(current, lower, upper)

        if shock_probability > 0.0 and rng.random() < shock_probability:
            shock = shock_size * clamp(shock_bias + rng.uniform(-0.35, 0.35), -1.0, 1.0)
            current += shock
            if type_key == "trend_channel":
                trend = trend_base + drift * step
                residual = current - trend
                residual = clamp(residual, lower_offset * 2.0, upper_offset * 2.0)

        current += event_state["flow_bias"]
        if type_key == "trend_channel":
            trend = trend_base + drift * step
            residual = current - trend
            residual = clamp(residual, lower_offset * 8.0, upper_offset * 8.0)

        effective_spread_mean = spread_mean * event_state["spread_multiplier"]
        effective_spread_jitter = max(0.1, spread_jitter * max(1.0, event_state["spread_multiplier"] * 0.8))
        spread = max(2, int(round(effective_spread_mean + abs(rng.gauss(0.0, effective_spread_jitter)))))
        effective_volume_base = max(1, int(round(volume_base * event_state["volume_multiplier"])))
        row = _book_row(
            day=0,
            timestamp=step * 100,
            product=product,
            mid=current,
            spread=spread,
            rng=rng,
            volume_base=effective_volume_base,
        )
        if row is not None:
            rows.append(row)
    return rows


def generate_trades(rows: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(int(seed) + 100_003)
    trades: list[dict[str, Any]] = []
    for row in rows:
        if not _valid_price_row(row):
            continue
        spread = float(row["ask_price_1"]) - float(row["bid_price_1"])
        base_probability = min(0.18, 0.018 + 0.12 / max(spread, 2.0))
        if rng.random() > base_probability:
            continue

        trade_count = 1 + int(rng.random() < 0.12)
        bid_1 = int(row["bid_price_1"])
        ask_1 = int(row["ask_price_1"])
        mid = int(round(float(row["mid_price"])))
        price_weights: dict[int, float] = {
            bid_1: 0.34,
            ask_1: 0.34,
            max(bid_1, min(ask_1, mid)): 0.18,
        }
        if ask_1 - bid_1 >= 2:
            price_weights[max(bid_1, min(ask_1, bid_1 + 1))] = price_weights.get(max(bid_1, min(ask_1, bid_1 + 1)), 0.0) + 0.07
            price_weights[max(bid_1, min(ask_1, ask_1 - 1))] = price_weights.get(max(bid_1, min(ask_1, ask_1 - 1)), 0.0) + 0.07

        prices = list(price_weights)
        weights = [price_weights[price] for price in prices]
        max_qty = max(
            1,
            min(
                10,
                int(
                    round(
                        max(
                            float(row["bid_volume_1"]),
                            float(row["ask_volume_1"]),
                        )
                        / 2.5
                    )
                ),
            ),
        )
        for _ in range(trade_count):
            trades.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "buyer": "",
                    "seller": "",
                    "symbol": str(row["product"]),
                    "currency": "XIRECS",
                    "price": float(rng.choices(prices, weights=weights, k=1)[0]),
                    "quantity": rng.randint(1, max_qty),
                }
            )

    trades.sort(key=lambda trade: (int(trade["timestamp"]), str(trade["symbol"]), float(trade["price"])))
    return trades


def rows_to_snapshots(rows: list[dict[str, Any]]) -> list[Snapshot]:
    return [
        Snapshot(
            day=int(row["day"]),
            timestamp=int(row["timestamp"]),
            product=str(row["product"]),
            bid_price_1=float(row["bid_price_1"]),
            ask_price_1=float(row["ask_price_1"]),
            bid_volume_1=int(row["bid_volume_1"]),
            ask_volume_1=int(row["ask_volume_1"]),
            mid_price=float(row["mid_price"]),
        )
        for row in rows
        if _valid_price_row(row)
    ]


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    valid_rows = [row for row in rows if _valid_price_row(row)]
    if not valid_rows:
        return ""
    headers = [
        "day",
        "timestamp",
        "product",
        "bid_price_1",
        "bid_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_1",
        "ask_volume_1",
        "ask_price_2",
        "ask_volume_2",
        "ask_price_3",
        "ask_volume_3",
        "mid_price",
        "profit_and_loss",
    ]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter=";")
    writer.writeheader()
    for row in valid_rows:
        writer.writerow(row)
    return stream.getvalue()


def trades_to_csv(trades: list[dict[str, Any]]) -> str:
    headers = [
        "timestamp",
        "buyer",
        "seller",
        "symbol",
        "currency",
        "price",
        "quantity",
    ]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers, delimiter=";")
    writer.writeheader()
    for trade in trades:
        writer.writerow(trade)
    return stream.getvalue()
