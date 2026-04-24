import json
from typing import List

from datamodel import Order, OrderDepth, TradingState


OSMIUM_ORDER_INC = 15
OSMIUM_MIN_SPREAD = 6
MAX_POS = 80
PEPPER_DRIFT_PER_STEP = 0.1
PEPPER_PASSIVE_SELL_EDGE = 8
PEPPER_PASSIVE_SELL_SIZE = 10


class Trader:
    def run(self, state: TradingState):
        store = json.loads(state.traderData) if state.traderData else {}
        result = {}

        for product, order_depth in state.order_depths.items():
            orders: List[Order] = []
            if not order_depth.buy_orders or not order_depth.sell_orders:
                result[product] = []
                continue

            bid = max(order_depth.buy_orders)
            ask = min(order_depth.sell_orders)
            spread = ask - bid
            position = int(state.position.get(product, 0))

            if product == "ASH_COATED_OSMIUM":
                if spread > OSMIUM_MIN_SPREAD:
                    buy_price = bid + 1
                    sell_price = ask - 1
                    buy_qty = max(0, min(OSMIUM_ORDER_INC, MAX_POS - position))
                    sell_qty = min(0, max(-OSMIUM_ORDER_INC, -MAX_POS - position))

                    if buy_qty:
                        orders.append(Order(product, buy_price, buy_qty))
                    if sell_qty:
                        orders.append(Order(product, sell_price, sell_qty))

            elif product == "INTARIAN_PEPPER_ROOT":
                mid = (bid + ask) / 2
                step = state.timestamp / 100
                anchor = store.get("pepper_anchor")
                if anchor is None:
                    anchor = mid - PEPPER_DRIFT_PER_STEP * step

                fair = anchor + PEPPER_DRIFT_PER_STEP * step

                if position < MAX_POS:
                    buy_qty = MAX_POS - position
                    orders.append(Order(product, ask, buy_qty))
                    position += buy_qty

                # When pepper is richly quoted relative to the drift line, post
                # a small offer one tick inside the ask and recycle inventory if
                # a later taker lifts it.
                sell_quote = max(ask - 1, int(fair + PEPPER_PASSIVE_SELL_EDGE + 0.9999))
                if sell_quote > bid and position > 0:
                    orders.append(Order(product, sell_quote, -min(PEPPER_PASSIVE_SELL_SIZE, position)))

                store["pepper_anchor"] = anchor

            result[product] = orders

        return result, 0, json.dumps(store, separators=(",", ":"))
