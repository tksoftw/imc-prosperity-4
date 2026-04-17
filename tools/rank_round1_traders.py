#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable



ROUND = 1



if len(sys.argv) == 2:
    try:
        ROUND = int(sys.argv[1])
    except ValueError:
        print(f"Invalid round number: {sys.argv[1]}")
        raise SystemExit(1)
elif len(sys.argv) > 2:
    print("Usage: rank_round1_traders.py [ROUND]")
    raise SystemExit(1)



ROOT = Path(__file__).resolve().parent.parent
ROUND_DIR = ROOT / f"ROUND{ROUND}"
DATA_DIR = ROOT / "data" / f"ROUND{ROUND}"
RUNS_DIR = ROOT / "runs"
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"


@dataclass
class TraderResult:
    trader_path: Path
    totals_by_day: dict[int, float]
    per_product_by_day: dict[int, dict[str, float]]
    trade_count_by_day: dict[int, int]

    @property
    def total_pnl(self) -> float:
        return sum(self.totals_by_day.values())

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / len(self.totals_by_day) if self.totals_by_day else 0.0

    @property
    def min_day_pnl(self) -> float:
        return min(self.totals_by_day.values()) if self.totals_by_day else 0.0


def discover_traders(round_dir: Path) -> list[Path]:
    traders = sorted(path for path in round_dir.glob("*.py") if path.is_file())
    traders = [path for path in traders if "__pycache__" not in path.parts]
    return traders


def discover_days(data_dir: Path) -> list[int]:
    days = []
    for path in sorted(data_dir.glob(f"prices_round_{ROUND}_day_*.csv")):
        match = re.search(rf"prices_round_{ROUND}_day_(-?\d+)\.csv$", path.name)
        if match:
            days.append(int(match.group(1)))
    return sorted(days)


def stable_slug(path: Path, round_dir: Path) -> str:
    rel = path.relative_to(round_dir).as_posix()
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", rel).strip("_").lower()
    return f"{stem[:50]}_{digest}"


def run_backtest(trader_path: Path, day: int) -> dict:
    dataset = DATA_DIR / f"prices_round_{ROUND}_day_{day}.csv"
    run_id = f"rank_round{ROUND}__{stable_slug(trader_path, ROUND_DIR)}__d{day}"
    run_dir = RUNS_DIR / run_id
    metrics_path = run_dir / "metrics.json"

    if metrics_path.exists():
        return json.loads(metrics_path.read_text())

    cmd = [
        str(BACKTESTER),
        "--trader",
        str(trader_path.relative_to(ROOT)),
        "--dataset",
        str(dataset.relative_to(ROOT)),
        f"--day={day}",
        "--run-id",
        run_id,
        "--output-root",
        str(RUNS_DIR.relative_to(ROOT)),
        "--artifact-mode",
        "none",
        "--products",
        "summary",
    ]
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Backtest failed for {trader_path} day {day}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    if not metrics_path.exists():
        raise FileNotFoundError(f"Expected metrics file not found: {metrics_path}")
    return json.loads(metrics_path.read_text())


def evaluate_trader(trader_path: Path, days: Iterable[int]) -> TraderResult:
    totals_by_day: dict[int, float] = {}
    per_product_by_day: dict[int, dict[str, float]] = {}
    trade_count_by_day: dict[int, int] = {}
    for day in days:
        metrics = run_backtest(trader_path, day)
        totals_by_day[day] = float(metrics["final_pnl_total"])
        per_product_by_day[day] = {
            product: float(value)
            for product, value in metrics["final_pnl_by_product"].items()
        }
        trade_count_by_day[day] = int(metrics["own_trade_count"])
    return TraderResult(
        trader_path=trader_path,
        totals_by_day=totals_by_day,
        per_product_by_day=per_product_by_day,
        trade_count_by_day=trade_count_by_day,
    )


def print_table(results: list[TraderResult], days: list[int], round_dir: Path) -> None:
    headers = ["rank", "trader", "total", "avg", "min_day", "trades"] + [f"d{day}" for day in days]
    rows = []
    for idx, result in enumerate(results, start=1):
        rel = result.trader_path.relative_to(round_dir).as_posix()
        rows.append(
            [
                str(idx),
                rel,
                f"{result.total_pnl:.1f}",
                f"{result.avg_pnl:.1f}",
                f"{result.min_day_pnl:.1f}",
                str(sum(result.trade_count_by_day.values())),
                *[f"{result.totals_by_day[day]:.1f}" for day in days],
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def fmt(row: list[str]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(row))

    print(fmt(headers))
    print(fmt(["-" * width for width in widths]))
    for row in rows:
        print(fmt(row))


def main() -> int:
    if not BACKTESTER.exists():
        print(f"Backtester not found at {BACKTESTER}")
        return 1

    traders = discover_traders(ROUND_DIR)
    days = discover_days(DATA_DIR)

    if not traders:
        print("No trader scripts found.")
        return 1
    if not days:
        print("No datasets found.")
        return 1

    print(
        f"Discovered {len(traders)} trader scripts and {len(days)} days: "
        f"{', '.join(str(day) for day in days)}"
    )

    results = []
    for index, trader_path in enumerate(traders, start=1):
        rel = trader_path.relative_to(ROUND_DIR).as_posix()
        print(f"[{index}/{len(traders)}] Evaluating {rel}")
        results.append(evaluate_trader(trader_path, days))

    results.sort(
        key=lambda item: (item.total_pnl, item.avg_pnl, item.min_day_pnl),
        reverse=True,
    )

    print()
    print_table(results, days, ROUND_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
