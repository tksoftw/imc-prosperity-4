from datamodel import Listing,Order, OrderDepth, Position, Product, Symbol, Time, UserId,Trade, TradingState
from typing import List
import string
from typing import Literal, TypedDict, Union, Callable

from functools import wraps


class StateAnalysis(TradingState):
    def __init__(self, state: TradingState):
        super().__init__(state.traderData, state.timestamp, state.listings, state.order_depths, state.own_trades, state.market_trades, state.position, state.observations)



def is_buy_order(order: Order) -> bool:
    return order.quantity > 0
def is_sell_order(order: Order) -> bool:
    return order.quantity < 0


class ProductAnalysis:
    def __init__(self, state: StateAnalysis, product: Product):
        self.state = state
        self.product = product
        self.order_depth = state.order_depths[product]
        self.listing = state.listings[product]
        self.own_trades = state.own_trades[product]
        self.market_trades = state.market_trades[product]
        self.position = state.position[product]
        
    

    @property
    def min_buy_price(self) -> int:
        if len(self.order_depth.buy_orders) == 0:
            return 0
        return min(self.order_depth.buy_orders.keys())
    @property
    def max_buy_price(self) -> int:
        if len(self.order_depth.buy_orders) == 0:
            return 0
        return max(self.order_depth.buy_orders.keys())
    @property
    def min_sell_price(self) -> int:
        if len(self.order_depth.sell_orders) == 0:
            return 0
        return min(self.order_depth.sell_orders.keys())
    @property
    def max_sell_price(self) -> int:
        if len(self.order_depth.sell_orders) == 0:
            return 0
        return max(self.order_depth.sell_orders.keys())
    @property
    def mid_price(self) -> int:
        if len(self.order_depth.buy_orders) == 0 or len(self.order_depth.sell_orders) == 0:
            return 0
        return (self.min_sell_price + self.max_buy_price) // 2
    @property
    def total_buy_volume(self) -> int:
        return sum(self.order_depth.buy_orders.values())
    @property
    def total_sell_volume(self) -> int:
        if len(self.order_depth.buy_orders) == 0 or len(self.order_depth.sell_orders) == 0:
            return 0
        return (self.min_sell_price + self.max_buy_price) // 2
class Trader:

    # def bid(self):
    #     return 15
    
    def run(self, state: TradingState):
        """Only method required. It takes all buy and sell orders for all
        symbols as an input, and outputs a list of orders to be sent."""
        state_analysis = StateAnalysis(state)
        products = "EM"
        #emerald_analysis = ProductAnalysis(state_analysis, "EMERALDS")
        if "TOMATOES" in state.order_depths:
            tomato_analysis = ProductAnalysis(state_analysis, "TOMATOES")
        else:
            tomato_analysis = None
        print("traderData: " + state.traderData)
        print("Observations: " + str(state.observations))
        # Orders to be placed on exchange matching engine
        acceptable_prices: dict[Product,int] = {
            "EMERALDS": 10000,
            "TOMATOES": tomato_analysis.mid_price
        }
        result = {}
        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []
            acceptable_price = acceptable_prices[product]  # type: ignore # Participant should calculate this value
            print("Acceptable price : " + str(acceptable_price))
            print("Buy Order depth : " + str(len(order_depth.buy_orders)) + ", Sell order depth : " + str(len(order_depth.sell_orders)))
    
            if len(order_depth.sell_orders) != 0:
                best_ask, best_ask_amount = list(order_depth.sell_orders.items())[0]
                if int(best_ask) < acceptable_price:
                    print("BUY", str(-best_ask_amount) + "x", best_ask)
                    orders.append(Order(product, best_ask, -best_ask_amount))
    
            if len(order_depth.buy_orders) != 0:
                best_bid, best_bid_amount = list(order_depth.buy_orders.items())[0]
                if int(best_bid) > acceptable_price:
                    print("SELL", str(best_bid_amount) + "x", best_bid)
                    orders.append(Order(product, best_bid, -best_bid_amount))
            
            result[product] = orders
    
        traderData = ""  # No state needed - we check position directly
        conversions = 0
        return result, conversions, traderData