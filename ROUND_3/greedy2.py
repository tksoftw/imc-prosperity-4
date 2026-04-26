from typing import List

from datamodel import OrderDepth, TradingState, Order
ORDER_INC = 10 #number of orders to do at a time
MAX_POS = {
    "HYDROGEL_PACK": 200,
    "VELVETFRUIT_EXTRACT": 200,
    "VEV_4000":300,
    "VEV_4500":300,
    "VEV_5000":300,
    "VEV_5100":300,
    "VEV_5200":300,
    "VEV_5300":300,
    "VEV_5400":300,
    "VEV_5500":300,
    "VEV_6000":300,
    "VEV_6500":300,
    

}

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

            #We can trade if spread < 0 on pepper root, in fact this works in our favor.
            # if(spread<=0): #DO NOT TRADE.
            #     result[product] = []
            #     continue
            
            cur_position = int(state.position.get(product, 0))

            if((product[:6]=='VEV_52' or product[:6]=='VEV_53') and cur_position < MAX_POS[product] and spread>6):
                buyprice = bid+2
                sellprice = ask-2
                # buyprice = ask
                # buyprice = ask
                numbuy = max(0, min(MAX_POS - cur_position, abs(available_volume)))
                numsell = min(0, max(-ORDER_INC, -MAX_POS-cur_position))
                # # numbuy = MAX_POS[product]-cur_position
                # numbuy = 0
                # numsell = -MAX_POS[product]-cur_position
                # buyprice = 0
                # sellprice = bid
            else:
                continue


            #at this point:
            #buy x in [0,maxpossible]
            #sell x in [-maxpossible,0]
            
            if numbuy and buyprice is not None:
                orders.append(Order(product, buyprice, numbuy))  
            if numsell and sellprice is not None:
                orders.append(Order(product, sellprice, numsell))

            result[product] = orders
    
        traderData = "" #what is this for?
        conversions = 0 #arbitrage? what is this for?
        return result, conversions, traderData
