from __future__ import annotations

import csv
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .models import Snapshot


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
ROUND_FILE_RE = re.compile(r"prices_round_(\d+)_day_(-?\d+)\.csv$")


def _parse_float(raw: str) -> float | None:
    return float(raw) if raw not in ("", None) else None


def _parse_int(raw: str) -> int | None:
    return int(raw) if raw not in ("", None) else None


@lru_cache(maxsize=1)
def discover_rounds() -> list[int]:
    rounds: list[int] = []
    for path in sorted(DATA_ROOT.glob("ROUND_*")):
        try:
            rounds.append(int(path.name.split("_", 1)[1]))
        except (IndexError, ValueError):
            continue
    return sorted(set(rounds))


def round_path(round_num: int) -> Path:
    return DATA_ROOT / f"ROUND_{round_num}"


@lru_cache(maxsize=16)
def load_round(round_num: int) -> dict[str, list[Snapshot]]:
    data_dir = round_path(round_num)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Round data directory not found: {data_dir}")

    grouped: dict[str, list[Snapshot]] = defaultdict(list)
    for path in sorted(data_dir.glob(f"prices_round_{round_num}_day_*.csv")):
        match = ROUND_FILE_RE.match(path.name)
        if not match:
            continue
        day = int(match.group(2))
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                product = row["product"]
                bid = _parse_float(row.get("bid_price_1"))
                ask = _parse_float(row.get("ask_price_1"))
                mid = _parse_float(row.get("mid_price"))
                if bid is None and ask is None:
                    mid = None
                if mid is None and bid is not None and ask is not None:
                    mid = (bid + ask) / 2
                grouped[product].append(
                    Snapshot(
                        day=day,
                        timestamp=int(row["timestamp"]),
                        product=product,
                        bid_price_1=bid,
                        ask_price_1=ask,
                        bid_volume_1=_parse_int(row.get("bid_volume_1")),
                        ask_volume_1=_parse_int(row.get("ask_volume_1")),
                        mid_price=mid,
                    )
                )

    for product in grouped:
        grouped[product].sort(key=lambda snap: (snap.day, snap.timestamp))
    return dict(grouped)
