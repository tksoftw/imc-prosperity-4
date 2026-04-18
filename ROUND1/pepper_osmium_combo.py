from typing import List

from datamodel import Order, OrderDepth, TradingState


OSMIUM_ORDER_INC = 15
MAX_POS = 80


class Trader:
    def run(self, state: TradingState):
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
                if spread <= 6:
                    result[product] = []
                    continue

                buy_price = bid + 1
                sell_price = ask - 1
                buy_qty = max(0, min(OSMIUM_ORDER_INC, MAX_POS - position))
                sell_qty = min(0, max(-OSMIUM_ORDER_INC, -MAX_POS - position))

                if buy_qty:
                    orders.append(Order(product, buy_price, buy_qty))
                if sell_qty:
                    orders.append(Order(product, sell_price, sell_qty))

            elif product == "INTARIAN_PEPPER_ROOT" and position < MAX_POS:
                buy_qty = MAX_POS - position
                orders.append(Order(product, ask, buy_qty))

            result[product] = orders

        return result, 0, ""
