from datamodel import TradingState, Order
from typing import Dict, List


class Trader:
    TARGETS = {
        "PEBBLES_L": 30,
        "PEBBLES_M": 172,
        "PEBBLES_S": -67,
        "PEBBLES_XL": 106,
        "PEBBLES_XS": -240,
    }
    MAX_ORDER = 10

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        for product, target in self.TARGETS.items():
            depth = state.order_depths.get(product)
            if depth is None or not depth.buy_orders or not depth.sell_orders:
                continue
            position = state.position.get(product, 0)
            delta = target - position
            if delta == 0:
                continue
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            if delta > 0:
                qty = min(delta, self.MAX_ORDER, abs(depth.sell_orders[best_ask]))
                if qty > 0:
                    result[product] = [Order(product, best_ask, qty)]
            else:
                qty = min(-delta, self.MAX_ORDER, abs(depth.buy_orders[best_bid]))
                if qty > 0:
                    result[product] = [Order(product, best_bid, -qty)]
        return result, 0, ""
