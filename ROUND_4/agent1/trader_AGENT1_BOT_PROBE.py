"""Agent1 diagnostic trader: probe real bot behavior, not profit.

Purpose:
* force tiny fills across the Round 4 products so website `own_trades` exposes
  whether bot names are present and whether they match local backtester behavior.
* keep compact counters in traderData for market-trade and own-trade bot names.

This intentionally crosses 1 lot on a rotating product schedule and then
flattens slowly. Expect mediocre or negative PnL; the signal is the bot-name
telemetry, not the account curve.
"""

import json
from collections import Counter
from typing import Dict, List

from datamodel import Order, TradingState


PRODUCTS = (
    "VELVETFRUIT_EXTRACT",
    "HYDROGEL_PACK",
    "VEV_4000",
    "VEV_4500",
    "VEV_5000",
    "VEV_5100",
    "VEV_5200",
    "VEV_5300",
    "VEV_5400",
    "VEV_5500",
)
LIMIT = 12


class Trader:
    def _load(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"market": {}, "own": {}, "seen_own": []}
        try:
            data = json.loads(trader_data)
        except Exception:
            return {"market": {}, "own": {}, "seen_own": []}
        data.setdefault("market", {})
        data.setdefault("own", {})
        data.setdefault("seen_own", [])
        return data

    def _bump(self, bucket: Dict[str, int], product: str, bot: str, role: str, qty: int) -> None:
        if not bot or bot == "SUBMISSION":
            return
        key = f"{product}|{bot}|{role}"
        bucket[key] = int(bucket.get(key, 0)) + abs(int(qty))

    def _observe(self, state: TradingState, store: Dict) -> None:
        market = store.setdefault("market", {})
        own = store.setdefault("own", {})
        seen = set(store.get("seen_own", []))

        for product, trades in state.market_trades.items():
            for tr in trades or []:
                qty = int(getattr(tr, "quantity", 0) or 0)
                self._bump(market, product, getattr(tr, "buyer", ""), "buyer", qty)
                self._bump(market, product, getattr(tr, "seller", ""), "seller", qty)

        for product, trades in state.own_trades.items():
            for idx, tr in enumerate(trades or []):
                key = (
                    f"{product}:{getattr(tr, 'timestamp', 0)}:{idx}:"
                    f"{getattr(tr, 'price', 0)}:{getattr(tr, 'quantity', 0)}:"
                    f"{getattr(tr, 'buyer', '')}:{getattr(tr, 'seller', '')}"
                )
                if key in seen:
                    continue
                seen.add(key)
                qty = int(getattr(tr, "quantity", 0) or 0)
                self._bump(own, product, getattr(tr, "buyer", ""), "buyer", qty)
                self._bump(own, product, getattr(tr, "seller", ""), "seller", qty)

        store["seen_own"] = list(seen)[-200:]
        # Keep traderData small enough for the website.
        for name in ("market", "own"):
            top = Counter(store.get(name, {})).most_common(80)
            store[name] = dict(top)

    def run(self, state: TradingState):
        store = self._load(state.traderData)
        self._observe(state, store)

        result: Dict[str, List[Order]] = {}

        # Sparse schedule: cross at phase 0, then use the next few snapshots to
        # flatten. This keeps the website test readable and limits probe losses.
        timestamp = int(state.timestamp)
        phase = timestamp % 5_000
        if phase in (100, 200, 300):
            for product in PRODUCTS:
                pos = int(state.position.get(product, 0))
                depth = state.order_depths.get(product)
                if depth is None:
                    continue
                orders = result.setdefault(product, [])
                if pos > 0 and depth.buy_orders:
                    orders.append(Order(product, max(depth.buy_orders), -1))
                elif pos < 0 and depth.sell_orders:
                    orders.append(Order(product, min(depth.sell_orders), 1))

        # Every 5,000 timestamp units, cross one lot in a rotating product. This
        # is the part that should reveal named counterparties in own_trades.
        tick = max(0, timestamp // 5_000)
        product = PRODUCTS[tick % len(PRODUCTS)]
        depth = state.order_depths.get(product)
        pos = int(state.position.get(product, 0))
        if phase == 0 and depth is not None and abs(pos) < LIMIT:
            orders = result.setdefault(product, [])
            if tick % 2 == 0 and depth.sell_orders:
                orders.append(Order(product, min(depth.sell_orders), 1))
            elif depth.buy_orders:
                orders.append(Order(product, max(depth.buy_orders), -1))

        result = {product: orders for product, orders in result.items() if orders}
        return result, 0, json.dumps(store, separators=(",", ":"))
