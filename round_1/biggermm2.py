from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict

ORDER_INC = 9 #number of orders to do at a time
MAX_POS = 75

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
            if(not order_depth.buy_orders or not order_depth.sell_orders):
                result[product] = []
                continue

            bid = max(order_depth.buy_orders)
            ask = min(order_depth.sell_orders)

            spread = ask-bid
            if(spread<=0): #DO NOT TRADE.
                result[product] = []
                continue
            

            #Tomatoes are risky, man.
            if(product=="TOMATOES" and spread>=4):
                buyprice = bid+1
                sellprice = ask-1
            elif spread>=2 and product!="TOMATOES":
                buyprice = bid+1
                sellprice = ask-1
            else:
                continue

            cur_position = int(state.position.get(product, 0))


            #buy x in [0,maxpossible]
            #sell x in [-maxpossible,0]
            numbuy = max(0, min(ORDER_INC, MAX_POS - cur_position))
            numsell = min(0, max(-ORDER_INC, -MAX_POS-cur_position))
            
            if(numbuy):
                orders.append(Order(product, buyprice, numbuy))  
            if(numsell):
                orders.append(Order(product, sellprice, numsell))

            result[product] = orders
    
        traderData = "" #what is this for?
        conversions = 0 #arbitrage? what is this for?
        return result, conversions, traderData
