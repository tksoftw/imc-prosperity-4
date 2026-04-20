from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ROUND = 1

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"


@dataclass
class TraderResult:
    trader_path: Path
    totals_by_day: dict[int, float]
    per_product_by_day: dict[int, dict[str, float]]
    trade_count_by_day: dict[int, int]
    trade_count_by_day_per_product: dict[int, dict[str, int]]
    bot_trade_count_by_day: dict[int, int]
    bot_trade_count_by_day_per_product: dict[int, dict[str, int]]

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


def discover_days(data_dir: Path, round_num: int) -> list[int]:
    days = []
    for path in sorted(data_dir.glob(f"prices_round_{round_num}_day_*.csv")):
        match = re.search(rf"prices_round_{round_num}_day_(-?\d+)\.csv$", path.name)
        if match:
            days.append(int(match.group(1)))
    return sorted(days)


def run_backtest(
    trader_path: Path,
    day: int,
    round_num: int,
    data_dir: Path,
) -> tuple[dict, Path]:
    dataset = data_dir / f"prices_round_{round_num}_day_{day}.csv"
    run_id = f"rank_round{round_num}_{trader_path.stem}_d{day}"
    run_dir = RUNS_DIR / run_id
    metrics_path = run_dir / "metrics.json"
    submission_log_path = run_dir / f"submission_{trader_path.stem}.log"

    if metrics_path.exists() and submission_log_path.exists():
        return json.loads(metrics_path.read_text()), submission_log_path

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
        "full",
        "--products",
        "full",
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
    try:
        p = run_dir / "submission.log"
        submission_log_path = p.rename(p.with_name(p.stem + '_' + trader_path.stem + '.log'))
    except Exception as e:
        raise FileNotFoundError(
            f"Error while renaming: original expected submission.log not found: {e}"
        )
    return json.loads(metrics_path.read_text()), submission_log_path


@dataclass
class TradeCounts:
    own_total: int
    own_by_product: dict[str, int]
    bot_total: int
    bot_by_product: dict[str, int]


def count_trades(submission_log_path: Path) -> TradeCounts:
    """Count own (SUBMISSION) and bot (external) trades from a backtester submission.log.

    A trade is "ours" iff its buyer or seller is "SUBMISSION"; otherwise it is
    treated as a bot/external trade.
    """
    payload = json.loads(submission_log_path.read_text())
    trade_history = payload.get("tradeHistory", []) or []
    counts = TradeCounts(own_total=0, own_by_product={}, bot_total=0, bot_by_product={})
    for trade in trade_history:
        is_ours = (
            trade.get("buyer") == "SUBMISSION" or trade.get("seller") == "SUBMISSION"
        )
        symbol = trade.get("symbol")
        if is_ours:
            counts.own_total += 1
            if symbol is not None:
                counts.own_by_product[symbol] = counts.own_by_product.get(symbol, 0) + 1
        else:
            counts.bot_total += 1
            if symbol is not None:
                counts.bot_by_product[symbol] = counts.bot_by_product.get(symbol, 0) + 1
    return counts


def evaluate_trader(
    trader_path: Path,
    days: Iterable[int],
    round_num: int,
    data_dir: Path,
) -> TraderResult:
    totals_by_day: dict[int, float] = {}
    per_product_by_day: dict[int, dict[str, float]] = {}
    trade_count_by_day: dict[int, int] = {}
    trade_count_by_day_per_product: dict[int, dict[str, int]] = {}
    bot_trade_count_by_day: dict[int, int] = {}
    bot_trade_count_by_day_per_product: dict[int, dict[str, int]] = {}
    for day in days:
        metrics, submission_log_path = run_backtest(
            trader_path, day, round_num, data_dir
        )
        totals_by_day[day] = float(metrics["final_pnl_total"])
        per_product_by_day[day] = {
            product: float(value)
            for product, value in metrics["final_pnl_by_product"].items()
        }
        counts = count_trades(submission_log_path)
        trade_count_by_day[day] = counts.own_total
        trade_count_by_day_per_product[day] = counts.own_by_product
        bot_trade_count_by_day[day] = counts.bot_total
        bot_trade_count_by_day_per_product[day] = counts.bot_by_product
    return TraderResult(
        trader_path=trader_path,
        totals_by_day=totals_by_day,
        per_product_by_day=per_product_by_day,
        trade_count_by_day=trade_count_by_day,
        trade_count_by_day_per_product=trade_count_by_day_per_product,
        bot_trade_count_by_day=bot_trade_count_by_day,
        bot_trade_count_by_day_per_product=bot_trade_count_by_day_per_product,
    )


def _render_table(
    results: list[TraderResult],
    days: list[int],
    round_dir: Path,
    pnl_by_day: dict[Path, dict[int, float]],
    trades_by_day: dict[Path, dict[int, int]],
    bot_trades_by_day: dict[Path, dict[int, int]],
) -> None:
    headers = (
        ["rank", "trader", "total", "avg", "min_day", "trades", "bots"]
        + [f"d{day}" for day in days]
        + [f"t{day}" for day in days]
        + [f"b{day}" for day in days]
    )
    rows = []
    for idx, result in enumerate(results, start=1):
        rel = result.trader_path.relative_to(round_dir).as_posix()
        day_pnls = pnl_by_day[result.trader_path]
        day_trades = trades_by_day[result.trader_path]
        day_bots = bot_trades_by_day[result.trader_path]
        totals = sum(day_pnls.values())
        avg = totals / len(day_pnls) if day_pnls else 0.0
        min_day = min(day_pnls.values()) if day_pnls else 0.0
        rows.append(
            [
                str(idx),
                rel,
                f"{totals:.1f}",
                f"{avg:.1f}",
                f"{min_day:.1f}",
                str(sum(day_trades.values())),
                str(sum(day_bots.values())),
                *[f"{day_pnls[day]:.1f}" for day in days],
                *[str(day_trades[day]) for day in days],
                *[str(day_bots[day]) for day in days],
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


def print_table(results: list[TraderResult], days: list[int], round_dir: Path) -> None:
    pnl_by_day = {r.trader_path: r.totals_by_day for r in results}
    trades_by_day = {r.trader_path: r.trade_count_by_day for r in results}
    bot_trades_by_day = {r.trader_path: r.bot_trade_count_by_day for r in results}
    _render_table(results, days, round_dir, pnl_by_day, trades_by_day, bot_trades_by_day)


def print_per_product_tables(
    results: list[TraderResult],
    days: list[int],
    round_dir: Path,
) -> None:
    products: list[str] = []
    seen: set[str] = set()
    for result in results:
        for day_products in result.per_product_by_day.values():
            for product in day_products:
                if product not in seen:
                    seen.add(product)
                    products.append(product)
    products.sort()

    for product in products:
        pnl_by_day: dict[Path, dict[int, float]] = {}
        trades_by_day: dict[Path, dict[int, int]] = {}
        bot_trades_by_day: dict[Path, dict[int, int]] = {}
        for result in results:
            pnl_by_day[result.trader_path] = {
                day: float(result.per_product_by_day.get(day, {}).get(product, 0.0))
                for day in days
            }
            trades_by_day[result.trader_path] = {
                day: int(
                    result.trade_count_by_day_per_product.get(day, {}).get(product, 0)
                )
                for day in days
            }
            bot_trades_by_day[result.trader_path] = {
                day: int(
                    result.bot_trade_count_by_day_per_product.get(day, {}).get(product, 0)
                )
                for day in days
            }
        ranked = sorted(
            results,
            key=lambda r, pbd=pnl_by_day: (
                sum(pbd[r.trader_path].values()),
                sum(pbd[r.trader_path].values()) / len(days) if days else 0.0,
                min(pbd[r.trader_path].values()) if days else 0.0,
            ),
            reverse=True,
        )
        print()
        print(f"== {product} ==")
        _render_table(ranked, days, round_dir, pnl_by_day, trades_by_day, bot_trades_by_day)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rank_traders",
        description=(
            "Run every trader under ROUND_{N}/ against every day in data/ROUND_{N}/ "
            "using the rust backtester, then print a ranked PnL table."
        ),
    )
    parser.add_argument(
        "-r",
        "--round",
        type=int,
        default=DEFAULT_ROUND,
        metavar="N",
        help="round number (default: %(default)s); selects ROUND_<N>/ and data/ROUND_<N>/",
    )
    parser.add_argument(
        "--days",
        type=int,
        nargs="+",
        metavar="DAY",
        help="restrict to these days (default: every day auto-discovered)",
    )
    parser.add_argument(
        "--trader",
        action="append",
        dest="traders",
        metavar="NAME",
        help="filter to traders whose filename contains NAME (repeatable)",
    )
    parser.add_argument(
        "--show-per-product",
        action="store_true",
        help="after the main table, print one identical table per product",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    round_dir = ROOT / f"ROUND_{args.round}"
    data_dir = ROOT / "data" / f"ROUND_{args.round}"

    if not BACKTESTER.exists():
        print(f"Backtester not found at {BACKTESTER}")
        return 1
    if not round_dir.is_dir():
        print(f"Round directory not found: {round_dir}")
        return 1
    if not data_dir.is_dir():
        print(f"Data directory not found: {data_dir}")
        return 1

    traders = discover_traders(round_dir)
    if args.traders:
        needles = [needle.lower() for needle in args.traders]
        traders = [t for t in traders if any(n in t.name.lower() for n in needles)]

    available_days = discover_days(data_dir, args.round)
    if args.days:
        missing = sorted(set(args.days) - set(available_days))
        if missing:
            print(f"Requested day(s) not found in {data_dir}: {missing}")
            return 1
        days = sorted(args.days)
    else:
        days = available_days

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
        rel = trader_path.relative_to(round_dir).as_posix()
        print(f"[{index}/{len(traders)}] Evaluating {rel}")
        results.append(evaluate_trader(trader_path, days, args.round, data_dir))

    results.sort(
        key=lambda item: (item.total_pnl, item.avg_pnl, item.min_day_pnl),
        reverse=True,
    )

    print()
    print_table(results, days, round_dir)
    if args.show_per_product:
        print_per_product_tables(results, days, round_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
