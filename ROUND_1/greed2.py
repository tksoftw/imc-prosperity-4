from datamodel import OrderDepth, UserId, TradingState, Order


ORDER_INC = 10 #number of orders to do at a time
MAX_POS = 80

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

            #Osmium is risky, man.
            
            if(product=="ASH_COATED_OSMIUM" and spread>2):
                buyprice = bid+1 if cur_position > -35 else bid+2
                sellprice = ask-1 if cur_position < 35 else ask-2
                numbuy = max(0, min(ORDER_INC, MAX_POS - cur_position))
                numsell = min(0, max(-ORDER_INC, -MAX_POS-cur_position))
            elif(product=="INTARIAN_PEPPER_ROOT" and cur_position < MAX_POS):
                # buyprice = ask
                buyprice = ask
                available_volume = order_depth.sell_orders[ask]
                # numbuy = max(0, min(MAX_POS - cur_position, abs(available_volume)))
                numbuy = MAX_POS-cur_position
                numsell = 0
            # elif spread>=2 and product!="ASH_COATED_OSMIUM":
            #     buyprice = bid+1
            #     sellprice = ask-1
            else:
                continue


            #at this point:
            #buy x in [0,maxpossible]
            #sell x in [-maxpossible,0]
            
            if(numbuy):
                orders.append(Order(product, buyprice, numbuy))  
            if(numsell):
                orders.append(Order(product, sellprice, numsell))

            result[product] = orders
    
        traderData = "" #what is this for?
        conversions = 0 #arbitrage? what is this for?
        return result, conversions, traderData