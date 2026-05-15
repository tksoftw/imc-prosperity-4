"""Caveman parse: pull final PnL per product from each real submission log."""
import json, sys
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "imc_logs" / "ROUND_3"

def parse(log: Path):
    payload = json.loads(log.read_text())
    activities = payload.get("activitiesLog", "")
    rows = activities.strip().split("\n")
    if not rows:
        return None
    header = rows[0].split(";")
    idx = {h: i for i, h in enumerate(header)}
    final_by_product = {}
    last_ts = -1
    for row in rows[1:]:
        cols = row.split(";")
        if len(cols) < len(header):
            continue
        product = cols[idx["product"]]
        ts = int(cols[idx["timestamp"]])
        try:
            pnl = float(cols[idx["profit_and_loss"]])
        except ValueError:
            continue
        # Take last seen pnl per product per day
        day = int(cols[idx["day"]])
        key = (day, product)
        final_by_product[key] = (ts, pnl)
    # Aggregate latest pnl per product across days
    by_product = {}
    by_product_per_day = {}
    days = set()
    for (day, product), (_, pnl) in final_by_product.items():
        by_product[product] = by_product.get(product, 0.0) + pnl
        by_product_per_day.setdefault(product, {})[day] = pnl
        days.add(day)
    return by_product, sorted(days), by_product_per_day

if __name__ == "__main__":
    files = sorted(LOG_DIR.glob("*.log"))
    print(f"{'log':30}  total       per-product:")
    aggregates = {}
    for f in files:
        try:
            res = parse(f)
            if res is None:
                continue
            by_product, days, _ = res
            tot = sum(by_product.values())
            aggregates[f.name] = by_product
            print(f"\n=== {f.name} (days={days}) total = {tot:,.0f} ===")
            for p in sorted(by_product):
                print(f"  {p:25s} {by_product[p]:>10,.0f}")
        except Exception as e:
            print(f"  {f.name}: ERROR {e}")
