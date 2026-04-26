"""Caveman per-day check — show PnL stability across each day to detect
single-day overfit. A robust trader should be positive every day and
have low day-to-day variance. Big positive day-1 + flat day-2 = warning.
"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"

TRADERS = [
    "trader_ff.py",
    "trader_MS.py",            # MADSCIENTIST
    "trader_final_OPTIMUM.py",
    "trader_EXPERIMENTS.py",
    "trader_CRAZY.py",
    "trader_final_3.py",
]

print(f"{'trader':<28}  {'d0':>10} {'d1':>10} {'d2':>10}  {'min':>10} {'range':>10}  {'cv%':>6}")
print("-" * 90)

for name in TRADERS:
    path = ROOT / "ROUND_3" / name
    run_id = f"stab_{path.stem}"
    out = ROOT / "runs"
    cmd = [
        str(BACKTESTER), "--trader", str(path.relative_to(ROOT)),
        "--dataset", "data/ROUND_3", "--run-id", run_id,
        "--output-root", "runs", "--artifact-mode", "metrics",
        "--products", "full",
    ]
    cmd[cmd.index("--artifact-mode") + 1] = "submission"
    try:
        subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    except Exception as e:
        print(f"{name:<28}  FAILED: {e}")
        continue

    days_pnl = {}
    for d in (0, 1, 2):
        sign = "-" if d == 0 else "+"
        run_dir = out / f"{run_id}-round-3-day{sign}{abs(d)}"
        m = run_dir / "metrics.json"
        if m.exists():
            data = json.loads(m.read_text())
            days_pnl[d] = float(data["final_pnl_total"])

    if len(days_pnl) != 3:
        print(f"{name:<28}  incomplete days")
        continue

    vals = [days_pnl[0], days_pnl[1], days_pnl[2]]
    mean = sum(vals) / 3
    rng = max(vals) - min(vals)
    cv = (rng / mean * 100) if mean else 0
    print(
        f"{name:<28}  {vals[0]:>10,.0f} {vals[1]:>10,.0f} {vals[2]:>10,.0f}  "
        f"{min(vals):>10,.0f} {rng:>10,.0f}  {cv:>5.1f}%"
    )
