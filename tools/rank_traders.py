from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable


# DEFAULT_ROUND = 1

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"

# find the default round by looking for the highest existing ROUND_{N} directory.
# if none exist, error out and ask the user to specify a round.
DEFAULT_ROUND = None
for path in ROOT.glob("ROUND_*"):
    if path.is_dir():
        match = re.match(r"ROUND_(\d+)", path.name)
        if match:
            round_num = int(match.group(1))
            if DEFAULT_ROUND is None or round_num > DEFAULT_ROUND:
                DEFAULT_ROUND = round_num



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


def _multi_day_run_dirs(
    run_id: str, round_num: int, days: Iterable[int], output_dir: Path
) -> dict[int, Path]:
    """The rust backtester writes per-day subdirs as
    `{run_id}-round-{N}-day{day}` where day uses an explicit sign
    (e.g. `day-0`, `day+1`). Map each requested day to its subdir.
    """
    out = {}
    for day in days:
        sign = "-" if day == 0 else "+" if day > 0 else "-"
        out[day] = output_dir / f"{run_id}-round-{round_num}-day{sign}{abs(day)}"
    return out


def run_backtest(
    trader_path: Path,
    round_num: int,
    data_dir: Path,
    day: int | None = None,
    output_dir: Path | None = None,
) -> dict[int, tuple[dict, Path]]:
    """Run the backtester once and return {day: (metrics, submission_log_path)}.

    - If `day` is None: pass the round directory as the dataset; the
      backtester auto-discovers and runs every day in one call. We then
      read metrics from each per-day subdir.
    - If `day` is an int: pass the specific day's CSV and `--day={day}`;
      a single per-day metrics file is produced.
    - If `output_dir` is provided, all per-day subdirs are placed under
      it (used by `--agent N` to isolate caches). Defaults to RUNS_DIR.
    """
    if output_dir is None:
        output_dir = RUNS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    if day is None:
        run_id = f"rank_round{round_num}_{trader_path.stem}"
        dataset = data_dir
        days = discover_days(data_dir, round_num)
        per_day_dirs = _multi_day_run_dirs(run_id, round_num, days, output_dir)
    else:
        run_id = f"rank_round{round_num}_{trader_path.stem}_d{day}"
        dataset = data_dir / f"prices_round_{round_num}_day_{day}.csv"
        days = [day]
        per_day_dirs = {day: output_dir / run_id}

    def renamed_log(run_dir: Path, d: int) -> Path:
        return run_dir / f"{trader_path.stem}_submission_d{d}.log"

    # Cache hit: every expected per-day dir already has metrics + the renamed
    # submission log.
    if all(
        (run_dir / "metrics.json").exists() and renamed_log(run_dir, d).exists()
        for d, run_dir in per_day_dirs.items()
    ):
        return {
            d: (
                json.loads((per_day_dirs[d] / "metrics.json").read_text()),
                renamed_log(per_day_dirs[d], d),
            )
            for d in days
        }

    cmd = [
        str(BACKTESTER),
        "--trader",
        str(trader_path.relative_to(ROOT)),
        "--dataset",
        str(dataset.relative_to(ROOT)),
        "--run-id",
        run_id,
        "--output-root",
        str(output_dir.relative_to(ROOT)),
        "--artifact-mode",
        "submission",
        "--products",
        "full",
    ]
    if day is not None:
        cmd.append(f"--day={day}")

    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Backtest failed for {trader_path} (day={day})\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )

    results: dict[int, tuple[dict, Path]] = {}
    for d, run_dir in per_day_dirs.items():
        metrics_file = run_dir / "metrics.json"
        raw_log = run_dir / "submission.log"
        target_log = renamed_log(run_dir, d)
        if not metrics_file.exists():
            raise FileNotFoundError(f"Expected metrics file not found: {metrics_file}")
        if raw_log.exists():
            raw_log.rename(target_log)
        elif not target_log.exists():
            raise FileNotFoundError(f"Expected submission log not found: {raw_log}")
        results[d] = (json.loads(metrics_file.read_text()), target_log)
    return results


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
    round_num: int,
    data_dir: Path,
    day: int | None = None,
    output_dir: Path | None = None,
) -> TraderResult:
    day_results = run_backtest(trader_path, round_num, data_dir, day, output_dir)

    totals_by_day: dict[int, float] = {}
    per_product_by_day: dict[int, dict[str, float]] = {}
    trade_count_by_day: dict[int, int] = {}
    trade_count_by_day_per_product: dict[int, dict[str, int]] = {}
    bot_trade_count_by_day: dict[int, int] = {}
    bot_trade_count_by_day_per_product: dict[int, dict[str, int]] = {}

    for d, (metrics, submission_log_path) in day_results.items():
        totals_by_day[d] = float(metrics["final_pnl_total"])
        per_product_by_day[d] = {
            product: float(value)
            for product, value in metrics["final_pnl_by_product"].items()
        }
        counts = count_trades(submission_log_path)
        trade_count_by_day[d] = counts.own_total
        trade_count_by_day_per_product[d] = counts.own_by_product
        bot_trade_count_by_day[d] = counts.bot_total
        bot_trade_count_by_day_per_product[d] = counts.bot_by_product

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
        required=DEFAULT_ROUND is None,
        metavar="N",
        help="round number (default: %(default)s); selects ROUND_<N>/ and data/ROUND_<N>/",
        default=DEFAULT_ROUND,
    )
    parser.add_argument(
        "--day",
        type=int,
        default=None,
        metavar="DAY",
        help="restrict to a single day (default: backtester auto-runs every day in the round dir)",
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

    parser.add_argument(
        "--agent",
        type=int,
        default=None,
        metavar="N",
        help=(
            "isolate this run inside runs/agentN/. On first use the script "
            "seeds the agent dir by copying any top-level run dirs from "
            "runs/ (excluding sibling agent*/ folders); subsequent runs "
            "treat the agent dir as the cache root. Use to keep parallel "
            "experiments from clobbering each other's cached metrics."
        ),
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "[TEMPORARILY DISABLED] aggregate ranks across every "
            "runs/agent*/ folder. Race condition: while one agent is "
            "writing per-day metrics, another `--all` reader could see "
            "partial JSON and crash. Will be re-enabled with file-locking."
        ),
    )
    parser.add_argument(
        "--clean",
        nargs="?",          # optional value
        type=int,
        const=-1,           # passed with no value → -1 (means "all")
        default=None,       # not provided → None
        metavar="N",
        help=(
            "with arg N: delete the runs/agentN/ folder (must use --agent "
            "is unnecessary — N selects the agent dir directly). The "
            "no-arg form (delete all of runs/) is TEMPORARILY DISABLED — "
            "wiping runs/* while sibling --agent processes are mid-flight "
            "would nuke their in-progress metrics. Will be re-enabled "
            "with a runs/.lock guard."
        ),
    )

    return parser.parse_args(argv)

_AGENT_DIR_RE = re.compile(r"^agent\d+$")


def _agent_dir(agent: int) -> Path:
    return RUNS_DIR / f"agent{agent}"


def _seed_agent_dir(agent_dir: Path) -> None:
    """Copy every top-level run dir from RUNS_DIR/ into agent_dir/, but
    NOT the sibling agent*/ folders. Existing entries in agent_dir are
    left intact (don't overwrite freshly produced metrics).

    Race-condition note: if a parallel `--agent N` invocation is mid-
    seed, the second seeder may copy a partially-written run dir. The
    rust backtester writes metrics.json atomically at the end of a
    day, but the renamed_log step is racy. We mitigate by skipping any
    target that already exists; full safety needs a `runs/.lock` file
    + flock(), which is the same lock that `--all` and `--clean` (no-
    arg form) need before they can be re-enabled.
    """
    if not RUNS_DIR.is_dir():
        return
    agent_dir.mkdir(parents=True, exist_ok=True)
    for src in RUNS_DIR.iterdir():
        if not src.is_dir():
            continue
        if _AGENT_DIR_RE.match(src.name):
            continue  # don't pull from sibling agent dirs
        if src.resolve() == agent_dir.resolve():
            continue
        dst = agent_dir / src.name
        if dst.exists():
            continue
        shutil.copytree(src, dst)


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

    # ── --all (temporarily disabled) ─────────────────────────────────
    # Race: while a sibling `--agent N` is mid-write of metrics.json /
    # submission logs, an `--all` aggregator that walks every agent dir
    # could see partial files and crash on JSONDecodeError. Re-enable
    # once we have a `runs/.lock` flock guard around per-day writes.
    if args.all:
        print(
            "--all is temporarily disabled (race vs concurrent --agent N writes). "
            "Re-enable once runs/.lock flock is in place."
        )
        return 1

    # ── --clean (no-arg form temporarily disabled) ───────────────────
    # The legacy "delete latest K runs" form scanned RUNS_DIR/* and
    # rm -rf'd from the top of the mtime list. With agent isolation,
    # the agent dirs themselves have a fresh mtime every run, which
    # would now be the FIRST things deleted — silently nuking sibling
    # caches. Until clean grows agent-awareness, only allow the
    # explicit `--clean N` form which deletes runs/agentN/ exactly.
    if args.clean is not None:
        if args.clean == -1:
            print(
                "Bare `--clean` is temporarily disabled. Use `--clean N` "
                "to delete runs/agentN/, or `rm -rf runs/<dirname>` directly."
            )
            return 1
        target = _agent_dir(args.clean)
        if target.exists():
            shutil.rmtree(target)
            print(f"Deleted {target.relative_to(ROOT)}")
        else:
            print(f"No such agent dir: {target.relative_to(ROOT)}")
        return 0

    # ── --agent N: isolate cache + bootstrap from runs/ ──────────────
    if args.agent is not None:
        output_dir = _agent_dir(args.agent)
        _seed_agent_dir(output_dir)
        print(f"Using agent cache dir: {output_dir.relative_to(ROOT)}")
    else:
        output_dir = RUNS_DIR

    traders = discover_traders(round_dir)
    if args.traders:
        needles = [needle.lower() for needle in args.traders]
        traders = [t for t in traders if any(n in t.name.lower() for n in needles)]

    available_days = discover_days(data_dir, args.round)
    if args.day is not None:
        if args.day not in available_days:
            print(f"Requested day not found in {data_dir}: {args.day}")
            return 1
        days = [args.day]
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
        results.append(
            evaluate_trader(trader_path, args.round, data_dir, args.day, output_dir)
        )

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
