#draft version
#for actual submission copy past this code into the file
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
        return sum(self.order_depth.sell_orders.values())