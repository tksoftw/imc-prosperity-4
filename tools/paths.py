"""Path definitions for trader tools."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
BACKTESTER = Path.home() / ".cargo" / "bin" / "rust_backtester"


def traders_dir(round_num: int) -> Path:
    return ROOT / "traders" / f"ROUND_{round_num}"


def data_dir(round_num: int) -> Path:
    return ROOT / "data" / f"ROUND_{round_num}"


def default_round() -> int | None:
    """Discover the highest ROUND_N directory that exists."""
    rounds = [
        int(p.name.split("_")[1])
        for p in ROOT.glob("traders/ROUND_*")
        if p.is_dir()
    ]
    return max(rounds, default=None)
