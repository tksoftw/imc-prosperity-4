"""tools/check_overfit.py — overfitting risk audit for a single trader.

Walk-forward + held-out validation + economic-sanity + regularization audit.
Reuses the `rank_traders.evaluate_trader` pipeline so we get the SAME metrics
the leaderboard uses.

What we compute:

  Walk-forward CV
    Score the trader on each calendar day independently. Use the LAST day as
    the held-out validation set (gap = 0 — we run train and val back-to-back,
    matching what the live submission actually does).

  Train ↔ validation gap
    `gap = (validation - train_mean) / max(|train_mean|, 1)`. Large negative
    gap = train wins by a lot ⇒ likely overfit. Positive gap = validation
    beats train ⇒ no overfit signal.

  Per-day & per-product stability (coefficient of variation)
    `cv = std / |mean|` across the train days for total PnL and for every
    product. Anything > 0.6 is a "shaky leg" of the strategy.

  Economic sanity
    - PnL per trade (extreme = either luck-driven or noise-trading)
    - Trades per day (very high turnover = pattern-matching micro-features)
    - Bot diversity per product (concentrated edge on one bot = fragile)
    - Win rate (fraction of train days with positive PnL)
    - Sharpe-like ratio (mean / std across train days)

  Regularization audit (static analysis of the trader source)
    Counts hardcoded magic numbers, log-/submission-derived constants, and
    file size. More magic numbers ⇒ more places that could be fit to one day.

The final score is a weighted sum of the worst factors, mapped to 0..100.
This is a *risk* score (higher = worse), not a quality score.

Usage:
  uv run check_overfit ROUND_3/trader_ff.py
  uv run check_overfit ROUND_3/trader_ff.py --validation-day 3
  uv run check_overfit ROUND_3/trader_ff.py --report-only-economic
"""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Reuse the rank_traders evaluation pipeline.
from tools.rank_traders import (
    DEFAULT_ROUND,
    ROOT,
    discover_days,
    discover_traders,
    evaluate_trader,
)


# ── Risk thresholds ─────────────────────────────────────────────────────────

# Each metric has thresholds for [LOW, MEDIUM, HIGH] risk regions. Tuned to
# match the historical spread of healthy traders we've already shipped.

VALIDATION_GAP_RISK = (-0.10, -0.30, -0.50)  # validation < train mean by these fractions
DAY_CV_RISK = (0.30, 0.60, 1.00)              # std/|mean| of total PnL across train days
PRODUCT_CV_RISK = (0.50, 0.90, 1.50)          # same for per-product PnL
WIN_RATE_RISK = (0.85, 0.65, 0.50)            # fraction of train days profitable
SHARPE_RISK = (2.5, 1.0, 0.5)                 # mean/std of train-day total PnL
TURNOVER_RISK = (3000, 6000, 10000)           # avg trades/day (HIGH turnover = pattern matching)
PER_TRADE_PNL_RISK = (50.0, 20.0, 5.0)        # |avg PnL per own trade| — too small = noise
MAGIC_NUMBER_RISK = (15, 30, 60)              # count of hardcoded floats > 0.001 in the trader source

WEIGHTS = {
    "validation_gap": 25,
    "day_cv":         15,
    "product_cv":     15,
    "win_rate":       10,
    "sharpe":         10,
    "turnover":        5,
    "per_trade_pnl":   5,
    "magic_numbers":  10,
    "submission_log_calibration": 5,
}


# ── Helpers ─────────────────────────────────────────────────────────────────


@dataclass
class RiskFactor:
    name: str
    value: float
    risk_level: str           # "LOW" | "MEDIUM" | "HIGH"
    risk_pts: float           # 0..weight
    note: str = ""


@dataclass
class OverfitReport:
    trader: Path
    train_days: list[int]
    val_day: int
    train_pnls: dict[int, float]
    val_pnl: float
    factors: list[RiskFactor] = field(default_factory=list)
    economic: dict = field(default_factory=dict)
    static: dict = field(default_factory=dict)

    @property
    def total_risk(self) -> float:
        # Each factor contributes 0..weight; sum and clamp 0..100.
        return min(100.0, sum(f.risk_pts for f in self.factors))


def _bucket(value: float, thresholds: tuple[float, float, float], higher_is_worse: bool = True) -> tuple[str, float]:
    """Return (label, fraction) where fraction is 0 (no risk) .. 1 (worst).

    `thresholds = (low_to_med, med_to_high, max)` — going from boundary to
    boundary linearly. If higher_is_worse=False the sort is reversed.
    """
    low_med, med_high, hard_max = thresholds
    if higher_is_worse:
        if value <= low_med:
            return "LOW", max(0.0, min(1.0, value / max(low_med, 1e-9))) * 0.33
        if value <= med_high:
            return "MEDIUM", 0.33 + 0.34 * (value - low_med) / max(med_high - low_med, 1e-9)
        if value <= hard_max:
            return "HIGH", 0.67 + 0.33 * (value - med_high) / max(hard_max - med_high, 1e-9)
        return "HIGH", 1.0
    # lower_is_worse — flip
    if value >= low_med:
        return "LOW", max(0.0, 1.0 - value / max(low_med, 1e-9)) * 0.33
    if value >= med_high:
        return "MEDIUM", 0.33 + 0.34 * (low_med - value) / max(low_med - med_high, 1e-9)
    if value >= hard_max:
        return "HIGH", 0.67 + 0.33 * (med_high - value) / max(med_high - hard_max, 1e-9)
    return "HIGH", 1.0


def _factor(name: str, value: float, thresholds, higher_is_worse: bool, note: str = "") -> RiskFactor:
    label, frac = _bucket(value, thresholds, higher_is_worse)
    return RiskFactor(
        name=name, value=value, risk_level=label,
        risk_pts=frac * WEIGHTS[name], note=note,
    )


# ── Static (source) analysis ────────────────────────────────────────────────


# A "magic number" in our context = any literal float that's not 0/1 and not
# a clean integer used in `range(...)` or as an array index. We scan for
# floating literals and integer literals > 1 that are used in expressions
# the trader compares against. Comments and docstrings are excluded.
NUMERIC_RE = re.compile(r"(?<![A-Za-z_])(?:\d+\.\d+|\d{2,})(?:[eE][+-]?\d+)?")
COMMENT_RE = re.compile(r"#[^\n]*")
DOCSTRING_RE = re.compile(r'(?:[rubRUB]*)("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')')

# Sub-scores that almost always indicate "I tuned this to a single submission".
SUBMISSION_LOG_KEYWORDS = (
    "submission log", "real submission", "live deploy", "MADSCIENTIST.log",
    "ff_OLD/optimum", "real evidence wins",
)


def static_audit(trader: Path) -> dict:
    src = trader.read_text(encoding="utf-8", errors="replace")
    # Strip docstrings and comments before counting magic numbers.
    no_doc = DOCSTRING_RE.sub("", src)
    no_comment = COMMENT_RE.sub("", no_doc)
    magics = NUMERIC_RE.findall(no_comment)
    # Keep only "interesting" numbers — drop common indices (0, 1, 2, 100)
    # and durations expressed in ticks (multiples of 100).
    interesting = [m for m in magics if not (m.endswith("00") and "." not in m)]
    interesting = [m for m in interesting if m not in {"100", "200", "300", "1000"}]

    sub_log_hits = sum(src.count(k) for k in SUBMISSION_LOG_KEYWORDS)

    return {
        "loc": src.count("\n") + 1,
        "magic_numbers": len(interesting),
        "submission_log_keywords": sub_log_hits,
        "src_bytes": len(src),
    }


# ── Walk-forward + per-product stability ────────────────────────────────────


def _cv(values: Iterable[float]) -> float:
    """Coefficient of variation: std / |mean|. Returns inf if mean == 0."""
    vs = list(values)
    if len(vs) < 2:
        return 0.0
    m = statistics.mean(vs)
    if abs(m) < 1e-9:
        return float("inf")
    return statistics.stdev(vs) / abs(m)


def evaluate_overfit(trader: Path, round_num: int, validation_day: int | None) -> OverfitReport:
    data_dir = ROOT / f"data/ROUND_{round_num}"
    days = discover_days(data_dir, round_num)
    if not days:
        sys.exit(f"No days found in {data_dir}")
    if validation_day is None:
        validation_day = max(days)
    if validation_day not in days:
        sys.exit(f"Validation day {validation_day} not in {days}")
    train_days = [d for d in days if d != validation_day]
    if not train_days:
        sys.exit("Need at least one train day in addition to the validation day.")

    print(f"Evaluating {trader.name} — train={train_days}, validation={validation_day}", file=sys.stderr)
    res = evaluate_trader(trader, round_num, data_dir, day=None, no_cache=False, carry=False)

    train_pnls = {d: res.totals_by_day[d] for d in train_days if d in res.totals_by_day}
    val_pnl = res.totals_by_day.get(validation_day, 0.0)

    # Walk-forward gap.
    train_mean = statistics.mean(train_pnls.values()) if train_pnls else 0.0
    train_stdev = statistics.stdev(train_pnls.values()) if len(train_pnls) >= 2 else 0.0
    gap = (val_pnl - train_mean) / max(abs(train_mean), 1.0)

    # Per-product stability (CV across train days only).
    products = sorted({p for d in train_days for p in res.per_product_by_day.get(d, {})})
    product_cv = {}
    for p in products:
        vs = [res.per_product_by_day.get(d, {}).get(p, 0.0) for d in train_days]
        if any(abs(v) > 1e-6 for v in vs):
            product_cv[p] = _cv(vs)
    worst_product_cv = max(product_cv.values(), default=0.0)

    # Day CV (across train days).
    day_cv = _cv(train_pnls.values())

    # Win rate (train days only).
    wins = sum(1 for v in train_pnls.values() if v > 0)
    win_rate = wins / len(train_pnls) if train_pnls else 0.0

    # Sharpe.
    sharpe = (train_mean / train_stdev) if train_stdev > 1e-9 else float("inf")

    # Turnover (avg trades/day, train only).
    avg_trades = statistics.mean(res.own_trades_by_day.get(d, 0) for d in train_days) if train_days else 0
    avg_pnl = train_mean
    per_trade_pnl = abs(avg_pnl) / max(1, avg_trades)

    # Bot diversity per product (per-product, per-day distinct bot count is
    # in `bot_trades_by_day_per_product` as counts, not distinct bots — but
    # a low non-zero bots count means we're trading with a small set of bots).
    bot_count_per_day = [
        sum(res.bot_trades_by_day_per_product.get(d, {}).values()) for d in train_days
    ]
    avg_bot_trades = statistics.mean(bot_count_per_day) if bot_count_per_day else 0

    # Static audit.
    static = static_audit(trader)

    # Build factor list.
    report = OverfitReport(
        trader=trader, train_days=train_days, val_day=validation_day,
        train_pnls=train_pnls, val_pnl=val_pnl, static=static,
    )
    report.economic = {
        "train_mean": train_mean, "train_stdev": train_stdev,
        "validation": val_pnl, "gap": gap,
        "win_rate": win_rate, "sharpe": sharpe,
        "avg_trades_per_day": avg_trades, "per_trade_pnl": per_trade_pnl,
        "avg_bot_trades_per_day": avg_bot_trades,
        "product_cv": product_cv, "worst_product_cv": worst_product_cv,
        "day_cv": day_cv,
    }

    report.factors = [
        _factor("validation_gap", -gap, (-VALIDATION_GAP_RISK[0], -VALIDATION_GAP_RISK[1], -VALIDATION_GAP_RISK[2]),
                higher_is_worse=True,
                note=f"validation/train ratio = {1+gap:.2%}"),
        _factor("day_cv", day_cv, DAY_CV_RISK, True,
                note=f"day-PnL std/|mean| across train"),
        _factor("product_cv", worst_product_cv, PRODUCT_CV_RISK, True,
                note=f"worst product is volatile"),
        _factor("win_rate", win_rate, WIN_RATE_RISK, False,
                note=f"{wins}/{len(train_pnls)} train days profitable"),
        _factor("sharpe", sharpe, SHARPE_RISK, False,
                note=f"mean/std of train-day PnL"),
        _factor("turnover", avg_trades, TURNOVER_RISK, True,
                note=f"avg own trades/day"),
        _factor("per_trade_pnl", per_trade_pnl, PER_TRADE_PNL_RISK, False,
                note=f"average $/trade"),
        _factor("magic_numbers", static["magic_numbers"], MAGIC_NUMBER_RISK, True,
                note=f"hardcoded numerics in source"),
        _factor("submission_log_calibration", static["submission_log_keywords"], (1, 4, 10), True,
                note="comments referencing a specific submission log"),
    ]
    return report


# ── Rendering ───────────────────────────────────────────────────────────────


def _color(level: str) -> str:
    return {"LOW": "\033[32m", "MEDIUM": "\033[33m", "HIGH": "\033[31m"}.get(level, "") + level + "\033[0m"


def render(r: OverfitReport, *, only_economic: bool = False) -> None:
    print(f"\n=== Overfit Risk Report — {r.trader.name} ===")
    print(f"Train days: {r.train_days}   Validation day: {r.val_day}")
    print()

    print("Walk-forward CV (per-day):")
    for d in sorted(r.train_days):
        print(f"  Day {d}: ${r.train_pnls[d]:>12,.0f}")
    print(f"  Day {r.val_day}: ${r.val_pnl:>12,.0f}    [VALIDATION]")
    print(f"  Train mean = ${r.economic['train_mean']:,.0f},  stdev = ${r.economic['train_stdev']:,.0f}")
    print(f"  Train→Val gap: {r.economic['gap']:+.1%}")
    print()

    print("Per-product stability (CV across train days):")
    pcv = r.economic["product_cv"]
    for p in sorted(pcv, key=lambda x: -pcv[x]):
        if math.isinf(pcv[p]):
            continue
        marker = "✗" if pcv[p] > PRODUCT_CV_RISK[1] else "·"
        print(f"  {marker} {p:<25} CV = {pcv[p]:.2f}")
    print()

    print("Economic sanity:")
    e = r.economic
    print(f"  Win rate           : {e['win_rate']*100:>5.0f}%  ({sum(1 for v in r.train_pnls.values() if v > 0)}/{len(r.train_pnls)} train days)")
    print(f"  Sharpe (mean/std)  : {e['sharpe']:>6.2f}")
    print(f"  Trades / day       : {e['avg_trades_per_day']:>6.0f}")
    print(f"  $ / own trade      : {e['per_trade_pnl']:>6.2f}")
    print(f"  Bot trades / day   : {e['avg_bot_trades_per_day']:>6.0f}   (counter-party diversity proxy)")
    print()

    print("Static source audit:")
    s = r.static
    print(f"  Lines of code            : {s['loc']:>5}")
    print(f"  Hardcoded numerics       : {s['magic_numbers']:>5}")
    print(f"  Submission-log refs      : {s['submission_log_keywords']:>5}")
    print()

    if only_economic:
        return

    print("Risk factors:")
    print(f"  {'metric':<32} {'value':>12}  {'level':<8}  contribution")
    print(f"  {'-'*32:<32} {'-'*12:>12}  {'-'*8:<8}  {'-'*12:<12}")
    for f in sorted(r.factors, key=lambda f: -f.risk_pts):
        if isinstance(f.value, float):
            value_str = f"{f.value:>12.3f}"
        else:
            value_str = f"{f.value:>12}"
        print(f"  {f.name:<32} {value_str}  {_color(f.risk_level):<8}  {f.risk_pts:>5.1f} / {WEIGHTS[f.name]}     {f.note}")
    print()

    score = r.total_risk
    if score < 25:
        verdict = "\033[32mLOW\033[0m — strategy looks robust"
    elif score < 55:
        verdict = "\033[33mMEDIUM\033[0m — review the highest-risk factors"
    else:
        verdict = "\033[31mHIGH\033[0m — likely overfit to training data"
    print(f"OVERALL RISK SCORE: {score:.0f} / 100   ({verdict})")


# ── Multi-trader rank ───────────────────────────────────────────────────────


_GREEN, _YELLOW, _RED, _DIM, _RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _verdict(score: float) -> str:
    if score < 25: return "LOW"
    if score < 55: return "MEDIUM"
    return "HIGH"


def _verdict_color(score: float) -> str:
    return {"LOW": _GREEN, "MEDIUM": _YELLOW, "HIGH": _RED}[_verdict(score)]


def _gap_color(gap_pct: float) -> str:
    if gap_pct >= 0:    return _GREEN
    if gap_pct >= -25:  return _YELLOW
    return _RED


def _cv_color(cv: float, low: float, high: float) -> str:
    if cv <= low:  return _GREEN
    if cv <= high: return _YELLOW
    return _RED


def _wrap(s: str, color: str) -> str:
    return f"{color}{s}{_RESET}"


def render_rank(reports: list[OverfitReport], by_pnl: bool = False) -> None:
    """One-row-per-trader overfit-risk leaderboard, ascending by risk."""
    rows = []
    for r in reports:
        e = r.economic
        rows.append({
            "trader": r.trader.name,
            "score": r.total_risk,
            "gap_pct": e["gap"] * 100,
            "train_mean": e["train_mean"],
            "val": r.val_pnl,
            "day_cv": e["day_cv"],
            "worst_prod_cv": e["worst_product_cv"],
            "win": e["win_rate"] * 100,
            "sharpe": e["sharpe"],
            "magic": r.static["magic_numbers"],
            "log_refs": r.static["submission_log_keywords"],
        })
    rows.sort(key=lambda r: r["score"] if not by_pnl else -r["val"])

    headers = ("rank", "trader", "score", "verdict", "gap%", "train$", "val$",
               "day_cv", "prod_cv", "win%", "sharpe", "magic", "logs")

    # Build plain (uncolored) cell strings first — used for column widths.
    plain = []
    colored = []
    for i, row in enumerate(rows, 1):
        score_v = _verdict(row["score"])
        score_c = _verdict_color(row["score"])
        gap_str = f"{row['gap_pct']:+.1f}"
        sharpe_str = f"{row['sharpe']:>5.2f}" if math.isfinite(row['sharpe']) else "  inf"
        plain_row = [
            str(i),
            row["trader"],
            f"{row['score']:.0f}",
            score_v,
            gap_str,
            f"{row['train_mean']:>9,.0f}",
            f"{row['val']:>9,.0f}",
            f"{row['day_cv']:.2f}",
            f"{row['worst_prod_cv']:.2f}",
            f"{row['win']:.0f}",
            sharpe_str,
            str(row["magic"]),
            str(row["log_refs"]),
        ]
        plain.append(plain_row)
        colored.append({
            "score":   _wrap(plain_row[2], score_c),
            "verdict": _wrap(plain_row[3], score_c),
            "gap":     _wrap(gap_str, _gap_color(row["gap_pct"])),
            "day_cv":  _wrap(plain_row[7], _cv_color(row["day_cv"], DAY_CV_RISK[0], DAY_CV_RISK[1])),
            "prod_cv": _wrap(plain_row[8], _cv_color(row["worst_prod_cv"], PRODUCT_CV_RISK[0], PRODUCT_CV_RISK[1])),
            "magic":   _wrap(plain_row[11], _cv_color(row["magic"], MAGIC_NUMBER_RISK[0], MAGIC_NUMBER_RISK[1])),
            "logs":    _wrap(plain_row[12], _cv_color(row["log_refs"], 1, 4)),
        })

    widths = [max(len(h), *(len(r[i]) for r in plain)) if plain else len(h)
              for i, h in enumerate(headers)]

    def fmt_plain(row):
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(row))

    def fmt_colored(plain_row, c):
        # Pad on the plain string, then wrap with color codes (zero width)
        cells = []
        for i, v in enumerate(plain_row):
            pad = " " * (widths[i] - len(v))
            key = headers[i]
            color_map = {
                "score": c["score"], "verdict": c["verdict"], "gap%": c["gap"],
                "day_cv": c["day_cv"], "prod_cv": c["prod_cv"],
                "magic": c["magic"], "logs": c["logs"],
            }
            cells.append((color_map.get(key, v)) + pad)
        return "  ".join(cells)

    # ── explanation block ──
    print()
    print(f"{'═'*72}")
    print("Overfit Risk Leaderboard — what each column means")
    print(f"{'═'*72}")
    print(_wrap("score",     _DIM) + "    0–100; sum of weighted risk factors. lower = more robust")
    print(_wrap("verdict",   _DIM) + "  LOW (<25)  MEDIUM (25–55)  HIGH (≥55)")
    print(_wrap("gap%",      _DIM) + "     (validation − train_mean) / |train_mean|. positive = val beat train")
    print(_wrap("train$",    _DIM) + "   mean total PnL across the train days")
    print(_wrap("val$",      _DIM) + "     held-out validation day's total PnL")
    print(_wrap("day_cv",    _DIM) + "   stdev / |mean| of train-day total PnL — high = unstable across days")
    print(_wrap("prod_cv",   _DIM) + "  worst per-product CV across train days — high = a product is brittle")
    print(_wrap("win%",      _DIM) + "     fraction of train days the trader was profitable")
    print(_wrap("sharpe",    _DIM) + "   train_mean / train_stdev (rough)")
    print(_wrap("magic",     _DIM) + "    hardcoded numerics in the trader source (proxy for tuning)")
    print(_wrap("logs",      _DIM) + "     references to specific submission logs in code (e.g. 'MADSCIENTIST.log')")
    print(f"{'═'*72}")
    print()

    # ── leaderboard table ──
    print(fmt_plain(list(headers)))
    print("__".join("_" * w for w in widths))
    for plain_row, c in zip(plain, colored):
        print(fmt_colored(plain_row, c))


def evaluate_all(round_num: int, validation_day: int | None,
                 trader_filter: list[str] | None) -> list[OverfitReport]:
    round_dir = ROOT / f"traders/ROUND_{round_num}"
    traders = discover_traders(round_dir)
    if trader_filter:
        traders = [t for t in traders if any(f in t.name for f in trader_filter)]
    reports = []
    for i, t in enumerate(traders, 1):
        print(f"[{i}/{len(traders)}] {t.name}", file=sys.stderr)
        try:
            reports.append(evaluate_overfit(t, round_num, validation_day))
        except SystemExit as e:
            print(f"  skipped: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
    return reports


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        prog="check_overfit",
        description="Walk-forward + economic + static-analysis overfit audit for a single trader.",
    )
    p.add_argument("trader", type=Path, nargs="?",
                   help="Path to a trader script (e.g. ROUND_3/trader_ff.py). "
                        "Omit when using --all.")
    p.add_argument("-r", "--round", type=int, default=DEFAULT_ROUND,
                   help=f"Round number (default: {DEFAULT_ROUND}).")
    p.add_argument("--validation-day", type=int, default=None,
                   help="Held-out validation day (default: latest available).")
    p.add_argument("--report-only-economic", action="store_true",
                   help="Skip the per-factor risk table — print stats only.")
    p.add_argument("--all", action="store_true",
                   help="Audit every trader in ROUND_N/ and print a ranked leaderboard.")
    p.add_argument("--trader-filter", action="append", default=[],
                   help="When using --all, only include trader filenames containing this substring "
                        "(repeatable).")
    p.add_argument("--by-pnl", action="store_true",
                   help="Sort by PnL instead of risk score.")
    args = p.parse_args()

    if args.all:
        reports = evaluate_all(args.round, args.validation_day, args.trader_filter or None)
        if not reports:
            sys.exit("No traders evaluated.")
        render_rank(reports, by_pnl=args.by_pnl)
        return 0

    if args.trader is None:
        sys.exit("Specify a trader path or pass --all.")
    trader = args.trader.resolve()
    if not trader.exists():
        sys.exit(f"Trader file not found: {trader}")

    report = evaluate_overfit(trader, args.round, args.validation_day)
    render(report, only_economic=args.report_only_economic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
