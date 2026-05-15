"""rank_traders — concurrent-safe trader ranking via content-addressed cache.

Cache slots are keyed by `(round, trader_stem, sha256(trader.py)[:8])`, so:

  * editing a trader auto-creates a new cache slot
  * reverting a trader restores the old slot's instant cache hit
  * parallel runs share a single `runs/` dir — different traders never
    collide; same-trader collisions just rerun a deterministic
    backtester and overwrite identical files (no locks, no deadlocks)

Commands:

  uv run rank                              # rank current traders
  uv run rank --trader ff --trader MS      # filter by substring
  uv run rank --show-per-product           # add per-product tables
  uv run rank --no-cache                   # force a fresh backtest
  uv run rank --clean                      # = --clean stale (default)
  uv run rank --clean stale                # drop slots with stale hash
  uv run rank --clean all                  # drop every run dir
  uv run rank --clean 'ff_*'               # glob pattern

A blunt `rm -rf runs/` is still safe; it just wipes all cache.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"

DEFAULT_ROUND = max(
    (   int(p.name.split("_")[1])
        for p in ROOT.glob("traders/ROUND_*")
        if p.is_dir()
    ),
    default=None,
)


# ─── Cache key + dir layout ──────────────────────────────────────────────────

def trader_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def run_dirs(round_num: int, trader: Path, days: list[int], single: bool) -> dict[int, Path]:
    """Map `day` -> directory the rust backtester will write that day's
    output to. The two shapes mirror what rust does for multi vs single day.
    """
    base = f"rank_round{round_num}_{trader.stem}_{trader_hash(trader)}"
    if single:  # one --day was passed; backtester writes runs/{base}_d{D}
        return {days[0]: RUNS_DIR / f"{base}_d{days[0]}"}
    # multi-day: backtester writes runs/{base}-round-{N}-day{±D}
    return {
        d: RUNS_DIR / f"{base}-round-{round_num}-day{'+' if d > 0 else '-'}{abs(d)}"
        for d in days
    }


def carry_dir(round_num: int, trader: Path) -> Path:
    """Carry mode: backtester writes a single dir at runs/{base} (no day suffix)
    because positions are carried across days into one combined run.
    """
    return RUNS_DIR / f"rank_round{round_num}_{trader.stem}_{trader_hash(trader)}"


def renamed_log(run_dir: Path, trader: Path, day: int) -> Path:
    return run_dir / f"{trader.stem}_submission_d{day}.log"


def carry_log(run_dir: Path, trader: Path) -> Path:
    return run_dir / f"{trader.stem}_submission_carry.log"


def carry_per_day_metrics_path(run_dir: Path) -> Path:
    return run_dir / "metrics_per_day.json"


def cache_hit(per_day: dict[int, Path], trader: Path) -> bool:
    return all(
        (rd / "metrics.json").exists() and renamed_log(rd, trader, d).exists()
        for d, rd in per_day.items()
    )


def carry_cache_hit(run_dir: Path, trader: Path) -> bool:
    return (
        (run_dir / "metrics.json").exists()
        and carry_log(run_dir, trader).exists()
        and carry_per_day_metrics_path(run_dir).exists()
    )


# ─── Backtester invocation ───────────────────────────────────────────────────

@dataclass
class TraderResult:
    trader_path: Path
    totals_by_day: dict[int, float] = field(default_factory=dict)
    per_product_by_day: dict[int, dict[str, float]] = field(default_factory=dict)
    own_trades_by_day: dict[int, int] = field(default_factory=dict)
    own_trades_by_day_per_product: dict[int, dict[str, int]] = field(default_factory=dict)
    bot_trades_by_day: dict[int, int] = field(default_factory=dict)
    bot_trades_by_day_per_product: dict[int, dict[str, int]] = field(default_factory=dict)

    @property
    def total_pnl(self) -> float:
        return sum(self.totals_by_day.values())

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / len(self.totals_by_day) if self.totals_by_day else 0.0

    @property
    def min_day_pnl(self) -> float:
        return min(self.totals_by_day.values(), default=0.0)


def discover_traders(scan_dir: Path) -> list[Path]:
    """Discover traders directly in `scan_dir`. NEVER recurses.

    For ranks, `scan_dir` is either ROUND_N/ (default) or ROUND_N/compiled/
    (when `--compiled` is passed). Subdirectories of `scan_dir` are
    invisible to discovery — pass an explicit `--trader subdir/file.py`
    if you want to evaluate something nested.
    """
    return sorted(
        p for p in scan_dir.glob("*.py")
        if p.is_file()
        and p.name != "__init__.py"
        and not p.name.startswith("_")
    )


def resolve_trader(scan_dir: Path, arg: str) -> Path:
    """Resolve a `--trader <path>` argument against `scan_dir`.

    The path may be a bare filename (`trader_X.py`) or a relative path
    (`subdir/trader_X.py`). NO substring matching, NO `.py` autocomplete,
    NO recursive search — if the file doesn't exist exactly where you
    pointed, this raises so you notice the typo immediately.
    """
    candidate = (scan_dir / arg).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"trader not found: {arg!r} (looked at {candidate})"
        )
    # Safety: don't allow a `--trader ../OTHER_ROUND/foo.py` escape.
    try:
        candidate.relative_to(scan_dir.resolve())
    except ValueError as exc:
        raise FileNotFoundError(
            f"trader {arg!r} resolved outside {scan_dir}: {candidate}"
        ) from exc
    return candidate


def discover_days(data_dir: Path, round_num: int) -> list[int]:
    return sorted(
        int(m.group(1))
        for p in data_dir.glob(f"prices_round_{round_num}_day_*.csv")
        if (m := re.search(rf"prices_round_{round_num}_day_(-?\d+)\.csv$", p.name))
    )


def run_backtest(trader: Path, round_num: int, data_dir: Path, day: int | None,
                 no_cache: bool, carry: bool) -> dict[int, tuple[dict, Path]]:
    """Run rust_backtester (or hit cache) and return {day: (metrics, log_path)}.

    Concurrency model: deterministic outputs + atomic per-file renames mean
    same-trader collisions just rerun and overwrite identical content. No locks.

    In carry mode, all days share one output dir + log; per-day metrics are
    derived from the activitiesLog and cached alongside.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    days = [day] if day is not None else discover_days(data_dir, round_num)

    if carry:
        return _run_backtest_carry(trader, round_num, data_dir, days, no_cache)

    per_day = run_dirs(round_num, trader, days, single=day is not None)
    run_id = (
        f"rank_round{round_num}_{trader.stem}_{trader_hash(trader)}"
        + (f"_d{day}" if day is not None else "")
    )
    dataset = data_dir / f"prices_round_{round_num}_day_{day}.csv" if day is not None else data_dir

    if not no_cache and cache_hit(per_day, trader):
        return _read_cache(per_day, trader)

    cmd = [
        str(BACKTESTER),
        "--trader", str(trader.relative_to(ROOT)),
        "--dataset", str(dataset.relative_to(ROOT)),
        "--run-id", run_id,
        "--output-root", str(RUNS_DIR.relative_to(ROOT)),
        "--artifact-mode", "submission",
        "--products", "full",
    ]
    if day is not None:
        cmd.append(f"--day={day}")

    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Backtest failed for {trader} (day={day})\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    # Per-day: rename submission.log -> {stem}_submission_d{D}.log so
    # multiple traders can coexist; tolerate races where a peer renamed
    # first (atomic rename + EEXIST swallow).
    for d, rd in per_day.items():
        if not (rd / "metrics.json").exists():
            raise FileNotFoundError(f"missing metrics: {rd / 'metrics.json'}")
        raw = rd / "submission.log"
        tgt = renamed_log(rd, trader, d)
        if raw.exists() and not tgt.exists():
            try:
                raw.rename(tgt)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOENT):
                    raise

    return _read_cache(per_day, trader)


def _run_backtest_carry(trader: Path, round_num: int, data_dir: Path,
                        days: list[int], no_cache: bool) -> dict[int, tuple[dict, Path]]:
    rd = carry_dir(round_num, trader)
    log_path = carry_log(rd, trader)
    per_day_metrics_path = carry_per_day_metrics_path(rd)
    run_id = rd.name

    if not no_cache and carry_cache_hit(rd, trader):
        return _read_carry_cache(rd, trader, days)

    cmd = [
        str(BACKTESTER),
        "--trader", str(trader.relative_to(ROOT)),
        "--dataset", str(data_dir.relative_to(ROOT)),
        "--run-id", run_id,
        "--output-root", str(RUNS_DIR.relative_to(ROOT)),
        "--artifact-mode", "submission",
        "--products", "full",
        "--carry",
    ]
    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"Backtest failed for {trader} (carry)\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not (rd / "metrics.json").exists():
        raise FileNotFoundError(f"missing metrics: {rd / 'metrics.json'}")

    raw = rd / "submission.log"
    if raw.exists() and not log_path.exists():
        try:
            raw.rename(log_path)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOENT):
                raise

    per_day = _derive_carry_per_day_metrics(log_path)
    per_day_metrics_path.write_text(json.dumps(per_day))

    return _read_carry_cache(rd, trader, days)


def _derive_carry_per_day_metrics(log_path: Path) -> dict[str, dict]:
    """Walk activitiesLog once, keep the highest-timestamp profit_and_loss
    per (day, product). Carry-mode metrics.json only has end-of-run totals;
    this synthesises per-day breakdowns from the activity stream.
    """
    activities = json.loads(log_path.read_text())["activitiesLog"]
    last_ts: dict[tuple[int, str], int] = {}
    last_pnl: dict[int, dict[str, float]] = {}
    lines_iter = iter(activities.split("\n"))
    next(lines_iter, None)  # skip header
    for line in lines_iter:
        if not line:
            continue
        parts = line.split(";")
        if len(parts) < 17:
            continue
        try:
            day = int(parts[0])
            ts = int(parts[1])
            pnl = float(parts[16]) if parts[16] else 0.0
        except ValueError:
            continue
        product = parts[2]
        key = (day, product)
        if key not in last_ts or ts >= last_ts[key]:
            last_ts[key] = ts
            last_pnl.setdefault(day, {})[product] = pnl
    return {
        str(d): {
            "final_pnl_by_product": prods,
            "final_pnl_total": sum(prods.values()),
        }
        for d, prods in last_pnl.items()
    }


def _read_cache(per_day: dict[int, Path], trader: Path) -> dict[int, tuple[dict, Path]]:
    return {
        d: (json.loads((rd / "metrics.json").read_text()), renamed_log(rd, trader, d))
        for d, rd in per_day.items()
    }


def _read_carry_cache(run_dir: Path, trader: Path,
                      days: list[int]) -> dict[int, tuple[dict, Path]]:
    log_path = carry_log(run_dir, trader)
    per_day = json.loads(carry_per_day_metrics_path(run_dir).read_text())
    out: dict[int, tuple[dict, Path]] = {}
    for d in days:
        m = per_day.get(str(d))
        if m is None:
            raise FileNotFoundError(
                f"carry metrics missing day {d} in {carry_per_day_metrics_path(run_dir)}"
            )
        out[d] = (m, log_path)
    return out


def evaluate_trader(trader: Path, round_num: int, data_dir: Path,
                    day: int | None, no_cache: bool, carry: bool) -> TraderResult:
    result = TraderResult(trader_path=trader)
    runs = run_backtest(trader, round_num, data_dir, day, no_cache, carry)

    if carry:
        # Single log shared across days; partition tradeHistory by trade["day"].
        single_log = next(iter(runs.values()))[1]
        all_trades = json.loads(single_log.read_text()).get("tradeHistory") or []
        trades_by_day: dict[int, list] = {d: [] for d in runs.keys()}
        for t in all_trades:
            d = t.get("day")
            if d in trades_by_day:
                trades_by_day[d].append(t)
        for d, (metrics, _) in runs.items():
            _populate_day(result, d, metrics, trades_by_day[d])
    else:
        for d, (metrics, log_path) in runs.items():
            trades = json.loads(log_path.read_text()).get("tradeHistory") or []
            _populate_day(result, d, metrics, trades)
    return result


def _populate_day(result: TraderResult, d: int, metrics: dict, trades: list) -> None:
    result.totals_by_day[d] = float(metrics["final_pnl_total"])
    result.per_product_by_day[d] = {
        p: float(v) for p, v in metrics["final_pnl_by_product"].items()
    }
    own_n = bot_n = 0
    own_by_p_dict: dict[str, int] = {}
    bot_by_p_dict: dict[str, int] = {}
    for trade in trades:
        ours = trade.get("buyer") == "SUBMISSION" or trade.get("seller") == "SUBMISSION"
        sym = trade.get("symbol")
        if ours:
            own_n += 1
            if sym is not None:
                own_by_p_dict[sym] = own_by_p_dict.get(sym, 0) + 1
        else:
            bot_n += 1
            if sym is not None:
                bot_by_p_dict[sym] = bot_by_p_dict.get(sym, 0) + 1
    result.own_trades_by_day[d] = own_n
    result.own_trades_by_day_per_product[d] = own_by_p_dict
    result.bot_trades_by_day[d] = bot_n
    result.bot_trades_by_day_per_product[d] = bot_by_p_dict


# ─── Rendering ───────────────────────────────────────────────────────────────

def _render(results: list[TraderResult], days: list[int], round_dir: Path,
            pnl_by_day, trades_by_day, bots_by_day) -> None:
    headers = (
        ["rank", "trader", "total", "avg", "min_day", "trades", "bots"]
        + [f"d{d}" for d in days] + [f"t{d}" for d in days] + [f"b{d}" for d in days]
    )
    rows = []
    for i, r in enumerate(results, 1):
        pnl = pnl_by_day[r.trader_path]
        tr = trades_by_day[r.trader_path]
        bt = bots_by_day[r.trader_path]
        total = sum(pnl.values())
        rows.append([
            str(i), r.trader_path.relative_to(round_dir).as_posix(),
            f"{total:.1f}", f"{total / max(1, len(pnl)):.1f}",
            f"{min(pnl.values(), default=0.0):.1f}",
            str(sum(tr.values())), str(sum(bt.values())),
            *[f"{pnl[d]:.1f}" for d in days],
            *[str(tr[d]) for d in days],
            *[str(bt[d]) for d in days],
        ])
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    fmt = lambda row: "  ".join(v.ljust(widths[i]) for i, v in enumerate(row))
    print(fmt(headers))
    print(fmt(["-" * w for w in widths]))
    for row in rows:
        print(fmt(row))


def print_table(results: list[TraderResult], days: list[int], round_dir: Path) -> None:
    _render(
        results, days, round_dir,
        {r.trader_path: r.totals_by_day for r in results},
        {r.trader_path: r.own_trades_by_day for r in results},
        {r.trader_path: r.bot_trades_by_day for r in results},
    )


def print_per_product_tables(results: list[TraderResult], days: list[int], round_dir: Path) -> None:
    products: list[str] = []
    seen: set[str] = set()
    for r in results:
        for d_prods in r.per_product_by_day.values():
            for p in d_prods:
                if p not in seen:
                    seen.add(p)
                    products.append(p)
    products.sort()

    for product in products:
        pnl = {r.trader_path: {d: float(r.per_product_by_day.get(d, {}).get(product, 0.0))
                               for d in days} for r in results}
        tr = {r.trader_path: {d: int(r.own_trades_by_day_per_product.get(d, {}).get(product, 0))
                              for d in days} for r in results}
        bt = {r.trader_path: {d: int(r.bot_trades_by_day_per_product.get(d, {}).get(product, 0))
                              for d in days} for r in results}
        ranked = sorted(
            results,
            key=lambda r, pbd=pnl: (sum(pbd[r.trader_path].values()),
                                    min(pbd[r.trader_path].values(), default=0.0)),
            reverse=True,
        )
        print(f"\n== {product} ==")
        _render(ranked, days, round_dir, pnl, tr, bt)


# ─── Cache cleanup ───────────────────────────────────────────────────────────

# Recognised run-dir name shapes:
#   rank_round{N}_{stem}_{hash}-round-{N}-day{±D}    (multi-day)
#   rank_round{N}_{stem}_{hash}_d{D}                 (single-day)
#   rank_round{N}_{stem}_{hash}                      (carry: one dir, all days)
RUN_DIR_RE = re.compile(
    r"^rank_round(?P<round>\d+)_(?P<stem>.+)_(?P<hash>[0-9a-f]{8})"
    r"(?:-round-\d+-day[+-]?\d+|_d-?\d+)?$"
)


def _current_hashes() -> dict[tuple[int, str], str]:
    """Hash every trader (top-level AND compiled/) so `--clean stale`
    only flags genuinely orphan cache dirs."""
    out: dict[tuple[int, str], str] = {}
    for round_dir in ROOT.glob("traders/ROUND_*"):
        m = re.match(r"ROUND_(\d+)$", round_dir.name)
        if not m:
            continue
        scan_dirs = [round_dir]
        compiled = round_dir / "compiled"
        if compiled.is_dir():
            scan_dirs.append(compiled)
        for scan_dir in scan_dirs:
            for trader in discover_traders(scan_dir):
                try:
                    out[(int(m.group(1)), trader.stem)] = trader_hash(trader)
                except OSError:
                    pass
    return out


def do_clean(mode: str) -> int:
    if not RUNS_DIR.is_dir():
        print("No runs/ directory yet.")
        return 0
    candidates = [p for p in RUNS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")]

    if mode == "stale":
        cur = _current_hashes()
        targets = []
        for p in candidates:
            m = RUN_DIR_RE.match(p.name)
            if not m:
                targets.append(("unknown", p))
                continue
            key = (int(m.group("round")), m.group("stem"))
            if cur.get(key) != m.group("hash"):
                targets.append(("stale", p))
        if not targets:
            print("Nothing stale to clean.")
            return 0
        for kind, p in targets:
            shutil.rmtree(p)
            print(f"  [{kind}] rm  {p.relative_to(ROOT)}")
        print(f"Removed {len(targets)} dir(s).")
        return 0

    if mode == "all":
        for p in candidates:
            shutil.rmtree(p)
            print(f"  rm  {p.relative_to(ROOT)}")
        print(f"Removed {len(candidates)} dir(s).")
        return 0

    # glob pattern
    matched = [p for p in candidates if p.match(mode)]
    if not matched:
        print(f"No run dirs match: {mode}")
        return 0
    for p in matched:
        shutil.rmtree(p)
        print(f"  rm  {p.relative_to(ROOT)}")
    print(f"Removed {len(matched)} dir(s).")
    return 0


# ─── Argparse + main ─────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="rank_traders",
        description=(
            "Concurrent-safe trader ranking via content-addressed cache.\n\n"
            "Examples:\n"
            "  uv run rank --round 3                          # all top-level traders/ROUND_3/ traders\n"
            "  uv run rank --round 3 --compiled               # all traders/ROUND_3/compiled/ traders\n"
            "  uv run rank --round 3 --trader trader_CRAZY.py # one specific trader\n"
            "  uv run rank --round 3 --trader sub/trader_X.py # path is relative to traders/ROUND_N/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-r", "--round", type=int, default=DEFAULT_ROUND, metavar="N",
                   required=DEFAULT_ROUND is None,
                   help="round number (default: %(default)s)")
    p.add_argument("--day", type=int, default=None, metavar="DAY",
                   help="restrict to one day; default = all days in the round")
    p.add_argument("--trader", action="append", dest="traders", metavar="PATH",
                   help="trader file relative to traders/ROUND_N/ (or traders/ROUND_N/compiled/ "
                        "with --compiled). Repeatable. No fuzzy matching: must exist exactly.")
    p.add_argument("--compiled", action="store_true",
                   help="evaluate traders in traders/ROUND_N/compiled/ instead of traders/ROUND_N/. "
                        "Errors if the directory does not exist.")
    p.add_argument("--show-per-product", action="store_true",
                   help="after the main table, print one table per product")
    p.add_argument("--no-cache", action="store_true",
                   help="ignore cached results and force a fresh backtester run")
    p.add_argument("--carry", action="store_true",
                   help="carry positions across days in one combined backtest. "
                        "Per-day pnl breakdowns are derived from the activitiesLog. "
                        "Cannot be combined with --day.")
    p.add_argument("--clean", nargs="?", const="stale", default=None, metavar="MODE",
                   help="clean cache: MODE = stale (default), all, or a glob pattern")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.clean is not None:
        return do_clean(args.clean)

    round_dir = ROOT / "traders" / f"ROUND_{args.round}"
    data_dir = ROOT / "data" / f"ROUND_{args.round}"

    for label, path in [("Backtester", BACKTESTER), ("Round", round_dir), ("Data", data_dir)]:
        if not path.exists():
            print(f"{label} not found: {path}")
            return 1

    if args.compiled:
        scan_dir = round_dir / "compiled"
        if not scan_dir.is_dir():
            print(f"--compiled requested, but {scan_dir} does not exist")
            return 1
    else:
        scan_dir = round_dir

    if args.traders:
        try:
            traders = [resolve_trader(scan_dir, t) for t in args.traders]
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
    else:
        traders = discover_traders(scan_dir)

    if args.carry and args.day is not None:
        print("--carry cannot be combined with --day")
        return 1

    available = discover_days(data_dir, args.round)
    if args.day is not None and args.day not in available:
        print(f"Requested day not found in {data_dir}: {args.day}")
        return 1
    days = [args.day] if args.day is not None else available

    if not traders:
        print(f"No trader scripts found in {scan_dir.relative_to(ROOT)}/."); return 1
    if not days:
        print("No datasets found."); return 1

    print(f"Discovered {len(traders)} trader script(s) in {scan_dir.relative_to(ROOT)}/ "
          f"and {len(days)} day(s): {', '.join(map(str, days))}")

    results: list[TraderResult] = []
    for i, trader in enumerate(traders, 1):
        h = trader_hash(trader)
        print(f"[{i}/{len(traders)}] Evaluating {trader.relative_to(scan_dir).as_posix()}  (hash {h})")
        results.append(evaluate_trader(trader, args.round, data_dir, args.day, args.no_cache, args.carry))

    results.sort(key=lambda r: (r.total_pnl, r.avg_pnl, r.min_day_pnl), reverse=True)
    print()
    print_table(results, days, scan_dir)
    if args.show_per_product:
        print_per_product_tables(results, days, scan_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
