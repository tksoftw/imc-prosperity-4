#draft version
#for actual submission copy past this code into the file
from datamodel import Order, Product, TradingState
from typing import Callable

from functools import wraps


class StateAnalysis(TradingState):
    def __init__(self, state: TradingState):
        super().__init__(
            state.traderData,
            state.timestamp,
            state.listings,
            state.order_depths,
            state.own_trades,
            state.market_trades,
            state.position,
        )



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
        self.position = state.position.get(product, 0)
    @staticmethod
    def total_value_of_orders(orders: dict[int, int]) -> int:
        return sum(price * volume for price, volume in orders.items())
    @staticmethod
    def ret_X_if_y_empty(X, y) -> Callable:
        def inner(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args, **kwargs):
                if len(y) == 0:
                    return X
                return func(*args, **kwargs)
            return wrapper
        return inner

    @property
    def min_buy_price(self) -> int|None:
        if len(self.order_depth.buy_orders) == 0:
            return 0
        return min(self.order_depth.buy_orders.keys())
    @property
    def max_buy_price(self) -> int|None:
        if len(self.order_depth.buy_orders) == 0:
            return None 
        return max(self.order_depth.buy_orders.keys())
    @property
    def min_sell_price(self) -> int|None:
        if len(self.order_depth.sell_orders) == 0:
            return None
        return min(self.order_depth.sell_orders.keys())
    @property
    def max_sell_price(self) -> int|None:
        if len(self.order_depth.sell_orders) == 0:
            return None
        return max(self.order_depth.sell_orders.keys())
    @property
    def mid_price(self) -> int|None:
        if self.min_sell_price is None or self.max_buy_price is None:
            return None
        return (self.min_sell_price + self.max_buy_price) // 2
    @property
    def total_buy_volume(self) -> int:
        return sum(self.order_depth.buy_orders.values())
    @property
    def total_sell_volume(self) -> int:
        return sum(self.order_depth.sell_orders.values())
    @property
    def moving_average_buy_price(self) -> float|None:
        if self.total_buy_volume == 0:
            return None
        return self.total_value_of_orders(self.order_depth.buy_orders) / self.total_buy_volume
    @property
    def moving_average_sell_price(self) -> float|None:
        if self.total_sell_volume == 0:
            return None
        return self.total_value_of_orders(self.order_depth.sell_orders) / self.total_sell_volume