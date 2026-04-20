from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict

ORDER_INC = 9 #number of orders to do at a time
MAX_POS = 75

class Trader:

    def bid(self):
        return 0
    
    def run(self, state: TradingState):
        """Only method required. It takes all buy and sell orders for all
        symbols as an input, and outputs a list of orders to be sent."""

        print("traderData: " + state.traderData)

        # Orders to be placed on exchange matching engine
        result = {}
        for product in state.order_depths:
            continue

            result[product] = orders
    
        traderData = "" #what is this for?
        conversions = 0 #arbitrage? what is this for?
        return result, conversions, traderData
