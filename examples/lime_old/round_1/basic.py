from datamodel import Order, Product, TradingState
from typing import Callable, Dict, List

from functools import wraps




def is_buy_order(order: Order) -> bool:
    return order.quantity > 0
def is_sell_order(order: Order) -> bool:
    return order.quantity < 0


class ProductAnalysis:
    def __init__(self, state: TradingState, product: Product):
        self.state = state
        self.product = product
        self.order_depth = state.order_depths.get(product, None)
        self.listing = state.listings.get(product, None)
        self.own_trades = state.own_trades.get(product, [])
        self.market_trades = state.market_trades.get(product, [])
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
        if self.order_depth is None or len(self.order_depth.buy_orders) == 0:
            return None
        return min(self.order_depth.buy_orders.keys())
    @property
    def max_buy_price(self) -> int|None:
        if self.order_depth is None or len(self.order_depth.buy_orders) == 0:
            return None 
        return max(self.order_depth.buy_orders.keys())
    @property
    def min_sell_price(self) -> int|None:
        if self.order_depth is None or len(self.order_depth.sell_orders) == 0:
            return None
        return min(self.order_depth.sell_orders.keys())
    @property
    def max_sell_price(self) -> int|None:
        if self.order_depth is None or len(self.order_depth.sell_orders) == 0:
            return None
        return max(self.order_depth.sell_orders.keys())
    @property
    def mid_price(self) -> int|None:
        if self.min_sell_price is None or self.max_buy_price is None:
            return None
        return (self.min_sell_price + self.max_buy_price) // 2
    @property
    def total_buy_volume(self) -> int:
        return sum(self.order_depth.buy_orders.values()) if self.order_depth is not None else 0
    @property
    def total_sell_volume(self) -> int:
        return sum(self.order_depth.sell_orders.values()) if self.order_depth is not None else 0
    @property
    def moving_average_buy_price(self) -> float|None:
        if self.order_depth is None or self.total_buy_volume == 0:
            return None
        return self.total_value_of_orders(self.order_depth.buy_orders) / self.total_buy_volume
    @property
    def moving_average_sell_price(self) -> float|None:
        if self.order_depth is None or self.total_sell_volume == 0:
            return None
        return self.total_value_of_orders(self.order_depth.sell_orders) / self.total_sell_volume

ORDER_INC = 10 #number of orders to do at a time
MAX_POS = 80
PRODUCTS = ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]


class Trader:

    def bid(self):
        return 15
    
    def run(self, state: TradingState):
        """Simple algorithm: buy anything below 10k, sell anything above 10k"""
        result: Dict[str, List[Order]] = {product: [] for product in PRODUCTS}
        osmium_analysis = ProductAnalysis(state, "ASH_COATED_OSMIUM")
        osmium_true_value = 10_000

        def ashroot_price_model(timestamp: int) -> float:
            # Simple linear fair value estimate from prior regression.
            coeff = 0.00100055
            intercept = 12006.78338719
            return coeff * timestamp + intercept

        def adjusted_timestamp(timestamp):
            #prices["combinedtimestamp"] = (prices["day"] * 1_000_000 + prices["timestamp"]).astype('int64')
            return timestamp 
        osmium_current_position = osmium_analysis.position
        osmium_remaining_buy_capacity = max(0, MAX_POS - osmium_current_position)
        osmium_remaining_sell_capacity = max(0, MAX_POS + osmium_current_position)

        if osmium_analysis.order_depth is not None:
            for price, volume in sorted(osmium_analysis.order_depth.sell_orders.items()):
                if price < osmium_true_value and osmium_remaining_buy_capacity > 0:
                    available = max(0, -volume)
                    buy_volume = min(available, osmium_remaining_buy_capacity)
                    if buy_volume == 0:
                        continue
                    result["ASH_COATED_OSMIUM"].append(Order("ASH_COATED_OSMIUM", price, buy_volume))
                    osmium_remaining_buy_capacity -= buy_volume
            
            for price, volume in sorted(osmium_analysis.order_depth.buy_orders.items(), reverse=True):
                if price > osmium_true_value and osmium_remaining_sell_capacity > 0:
                    sell_volume = min(volume, osmium_remaining_sell_capacity)
                    if sell_volume == 0:
                        continue
                    result["ASH_COATED_OSMIUM"].append(Order("ASH_COATED_OSMIUM", price, -sell_volume))
                    osmium_remaining_sell_capacity -= sell_volume
            
         #buy every ashroot near the predicted price
        ashroot_analysis = ProductAnalysis(state, "INTARIAN_PEPPER_ROOT")
        ashroot_remaining_buy_capacity = max(0, MAX_POS - ashroot_analysis.position)
        ashroot_fair_value = ashroot_price_model(adjusted_timestamp(state.timestamp))
        if ashroot_analysis.order_depth is not None:
            for price, volume in sorted(ashroot_analysis.order_depth.sell_orders.items()):
                if price < ashroot_fair_value + 1 and ashroot_remaining_buy_capacity > 0:
                    available = max(0, -volume)
                    buy_volume = min(available, ashroot_remaining_buy_capacity)
                    if buy_volume == 0:
                        continue
                    result["INTARIAN_PEPPER_ROOT"].append(Order("INTARIAN_PEPPER_ROOT", price, buy_volume))
                    ashroot_remaining_buy_capacity -= buy_volume
                        
        traderData = ""
        conversions = 0
        return result, conversions, traderData