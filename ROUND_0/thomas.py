from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string

class Trader:

    def bid(self):
        return 15
    
    def run(self, state: TradingState):
        """Only method required. It takes all buy and sell orders for all
        symbols as an input, and outputs a list of orders to be sent."""

        print("traderData: " + state.traderData)

        # Orders to be placed on exchange matching engine
        result = {}
        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            # Compute fair value: fixed for EMERALDS, mid-price for TOMATOES
            if product == "EMERALDS":
                acceptable_price = 10000
            elif len(order_depth.buy_orders) != 0 and len(order_depth.sell_orders) != 0:
                best_bid = max(order_depth.buy_orders)
                best_ask = min(order_depth.sell_orders)
                acceptable_price = (best_bid + best_ask) // 2
            else:
                result[product] = orders
                continue

            print("Acceptable price : " + str(acceptable_price))
            print("Buy Order depth : " + str(len(order_depth.buy_orders)) + ", Sell order depth : " + str(len(order_depth.sell_orders)))

            # Market-making: post passive orders inside the spread
            orders.append(Order(product, acceptable_price - 2, 5))   # buy 5 @ fair-2
            orders.append(Order(product, acceptable_price + 2, -5))  # sell 5 @ fair+2

            result[product] = orders
    
        traderData = ""
        conversions = 0
        return result, conversions, traderData