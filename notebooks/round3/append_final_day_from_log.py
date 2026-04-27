"""Extract the final real-log day into data/ROUND_3 as day 3.

This lets the Round 3 research treat the public three days plus the
submission log as one continuous four-day tape. Trades written here
exclude SUBMISSION fills so the CSV resembles the public market-trade
files.
"""

from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "imc_logs" / "ROUND_3" / "ff_FINAL.log"
DATA_DIR = ROOT / "data" / "ROUND_3"
DAY = 3


def main() -> None:
    payload = json.loads(LOG_PATH.read_text())

    activities = payload["activitiesLog"].strip()
    rows = list(csv.DictReader(StringIO(activities), delimiter=";"))
    if not rows:
        raise RuntimeError(f"No activities rows in {LOG_PATH}")

    price_path = DATA_DIR / f"prices_round_3_day_{DAY}.csv"
    with price_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    trade_rows = []
    for tr in payload.get("tradeHistory") or []:
        if tr.get("buyer") == "SUBMISSION" or tr.get("seller") == "SUBMISSION":
            continue
        trade_rows.append({
            "timestamp": int(tr.get("timestamp", 0)),
            "buyer": tr.get("buyer") or "",
            "seller": tr.get("seller") or "",
            "symbol": tr.get("symbol") or "",
            "currency": tr.get("currency") or "XIRECS",
            "price": tr.get("price"),
            "quantity": int(tr.get("quantity", 0)),
        })

    trade_rows.sort(key=lambda r: (r["timestamp"], r["symbol"], r["price"], r["quantity"]))
    trade_path = DATA_DIR / f"trades_round_3_day_{DAY}.csv"
    with trade_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity"],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(trade_rows)

    print(f"Wrote {len(rows)} price rows -> {price_path}")
    print(f"Wrote {len(trade_rows)} market trades -> {trade_path}")


if __name__ == "__main__":
    main()
