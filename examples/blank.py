from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import string

class Trader:

    def bid(self):
        return 0
    
    def run(self, state: TradingState):
        """Only method required. It takes all buy and sell orders for all
        symbols as an input, and outputs a list of orders to be sent."""

        print("traderData: " + state.traderData)
        #print("Observations: " + str(state.observations))

        # Orders to be placed on exchange matching engine
        result = {}
    
        traderData = ""  # No state needed - we check position directly
        conversions = 0
        return result, conversions, traderData