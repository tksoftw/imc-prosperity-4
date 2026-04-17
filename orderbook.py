from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from collections import defaultdict
import numpy as np


class Side(Enum):
    BUY = 'BUY'
    SELL = 'SELL'


@dataclass
class Order:
    symbol: str
    side: Side
    price: int
    quantity: int
    is_user: bool = False
    timestamp: Optional[int] = None


@dataclass
class AuctionConfig:
    symbol: str
    exit_price: float
    fee_per_unit: float = 0.0
    description: str = ''

    def profit_per_unit(self, order_side: Side, clearing_price: float) -> float:
        if order_side == Side.BUY:
            return self.exit_price - self.fee_per_unit - clearing_price
        return clearing_price - self.exit_price - self.fee_per_unit


@dataclass
class AuctionRules:
    select_clearing_price: Callable[[List[Order], List[Order]], Tuple[int, int]]
    allocate_fills: Callable[[List[Order], List[Order], int], Dict[str, int]]

    @staticmethod
    def default() -> 'AuctionRules':
        return AuctionRules(
            select_clearing_price=AuctionRules._maximize_volume_then_price,
            allocate_fills=AuctionRules._price_time_priority_allocation,
        )

    @staticmethod
    def _maximize_volume_then_price(bids: List[Order], asks: List[Order]) -> Tuple[int, int]:
        prices = sorted({order.price for order in bids + asks})
        best_price = 0
        best_volume = 0

        for price in prices:
            demand = sum(order.quantity for order in bids if order.price >= price)
            supply = sum(order.quantity for order in asks if order.price <= price)
            volume = min(demand, supply)

            if volume > best_volume or (volume == best_volume and price > best_price):
                best_volume = volume
                best_price = price

        return best_price, best_volume

    @staticmethod
    def _price_time_priority_allocation(bids: List[Order], asks: List[Order], clearing_price: int) -> Dict[str, object]:
        buyer_book = sorted(
            ((index, order) for index, order in enumerate(bids) if order.price >= clearing_price),
            key=lambda item: (-item[1].price, item[1].timestamp if item[1].timestamp is not None else 0),
        )
        seller_book = sorted(
            ((index, order) for index, order in enumerate(asks) if order.price <= clearing_price),
            key=lambda item: (item[1].price, item[1].timestamp if item[1].timestamp is not None else 0),
        )

        total_buy = sum(order.quantity for _, order in buyer_book)
        total_sell = sum(order.quantity for _, order in seller_book)
        executed = min(total_buy, total_sell)

        bid_fills: Dict[int, int] = {}
        ask_fills: Dict[int, int] = {}

        remaining = executed
        for idx, order in buyer_book:
            if remaining <= 0:
                break
            filled = min(order.quantity, remaining)
            bid_fills[idx] = filled
            remaining -= filled

        remaining = executed
        for idx, order in seller_book:
            if remaining <= 0:
                break
            filled = min(order.quantity, remaining)
            ask_fills[idx] = filled
            remaining -= filled

        return {
            'bid_fills': bid_fills,
            'ask_fills': ask_fills,
            'total_executed': executed,
        }


class FrozenCallAuction:
    def __init__(self, config: AuctionConfig, rules: Optional[AuctionRules] = None):
        self.config = config
        self.rules = rules or AuctionRules.default()
        self.bids: List[Order] = []
        self.asks: List[Order] = []
        self._timestamp_counter = 0

    def add_order(self, side: Side, price: int, quantity: int, is_user: bool = False, timestamp: Optional[int] = None) -> None:
        if timestamp is None:
            timestamp = self._timestamp_counter
            self._timestamp_counter += 1

        order = Order(symbol=self.config.symbol, side=side, price=price, quantity=quantity, is_user=is_user, timestamp=timestamp)
        if side == Side.BUY:
            self.bids.append(order)
        else:
            self.asks.append(order)

    def add_orders(self, orders: Iterable[Order]) -> None:
        for order in orders:
            self.add_order(order.side, order.price, order.quantity, order.is_user, order.timestamp)

    def trigger(self) -> Dict[str, float]:
        clearing_price, traded_volume = self.rules.select_clearing_price(self.bids, self.asks)
        allocation = self.rules.allocate_fills(self.bids, self.asks, clearing_price)

        user_filled = 0
        user_profit = 0.0

        for bid_idx, filled_qty in allocation['bid_fills'].items():
            order = self.bids[bid_idx]
            if order.is_user:
                user_filled += filled_qty
                user_profit += filled_qty * self.config.profit_per_unit(order.side, clearing_price)

        for ask_idx, filled_qty in allocation['ask_fills'].items():
            order = self.asks[ask_idx]
            if order.is_user:
                user_filled += filled_qty
                user_profit += filled_qty * self.config.profit_per_unit(order.side, clearing_price)

        return {
            'symbol': self.config.symbol,
            'clearing_price': clearing_price,
            'total_cleared_volume': traded_volume,
            'user_filled_volume': user_filled,
            'user_profit': round(user_profit, 2),
        }


class AuctionMarket:
    def __init__(self, configs: List[AuctionConfig], rules: Optional[AuctionRules] = None):
        self.rules = rules or AuctionRules.default()
        self.books: Dict[str, FrozenCallAuction] = {
            config.symbol: FrozenCallAuction(config, self.rules)
            for config in configs
        }

    def add_order(self, order: Order) -> None:
        self.books[order.symbol].add_order(order.side, order.price, order.quantity, order.is_user, order.timestamp)

    def add_orders(self, orders: Iterable[Order]) -> None:
        for order in orders:
            self.add_order(order)

    def trigger_all(self) -> List[Dict[str, float]]:
        return [book.trigger() for book in self.books.values()]


def find_optimal_order_numpy(bids: Dict[int, int], asks: Dict[int, int], exit_price: float, fee: float = 0.0, side: Side = Side.BUY):
    all_prices = sorted(list(set(bids.keys()) | set(asks.keys())))
    if not all_prices:
        return {'optimal_price': 0, 'optimal_quantity': 0, 'clearing_price': 0, 'max_profit': 0.0}
    
    prices = np.array(all_prices)
    P = len(prices)

    ask_vols = np.array([asks.get(p, 0) for p in prices])
    bid_vols = np.array([bids.get(p, 0) for p in prices])

    S = np.cumsum(ask_vols)
    D = np.cumsum(bid_vols[::-1])[::-1]

    if side == Side.BUY:
        diffs = (S.reshape(1, -1) - D.reshape(-1, 1)).flatten()
        base_qtys = np.unique(np.clip(diffs, 0, None))
        qtys = np.unique(np.concatenate([base_qtys, base_qtys - 1, base_qtys + 1, [1, int(S[-1])]]))
        qtys = qtys[qtys > 0].astype(int)
    else:
        diffs = (D.reshape(1, -1) - S.reshape(-1, 1)).flatten()
        base_qtys = np.unique(np.clip(diffs, 0, None))
        qtys = np.unique(np.concatenate([base_qtys, base_qtys - 1, base_qtys + 1, [1, int(D[0])]]))
        qtys = qtys[qtys > 0].astype(int)

    best_profit, best_price, best_qty, best_cp = 0.0, 0, 0, 0

    for px_idx, px in enumerate(prices):
        Q = qtys.reshape(-1, 1)
        
        if side == Side.BUY:
            mask = (prices <= px).astype(int)
            V_new = np.minimum(D + Q * mask, S)
            clear_idx = P - 1 - np.argmax(V_new[:, ::-1], axis=1)
            P_c = prices[clear_idx]
            fill = np.minimum(qtys, np.maximum(0, S[clear_idx] - D[px_idx]))
            fill = np.where(px >= P_c, fill, 0)
            profit = fill * (exit_price - P_c - fee)
        else:
            mask = (prices >= px).astype(int)
            V_new = np.minimum(D, S + Q * mask)
            clear_idx = P - 1 - np.argmax(V_new[:, ::-1], axis=1)
            P_c = prices[clear_idx]
            fill = np.minimum(qtys, np.maximum(0, D[clear_idx] - S[px_idx]))
            fill = np.where(px <= P_c, fill, 0)
            profit = fill * (P_c - exit_price - fee)
        
        max_idx = np.argmax(profit)
        if profit[max_idx] > best_profit:
            best_profit = profit[max_idx]
            best_price = px
            best_qty = qtys[max_idx]
            best_cp = P_c[max_idx]

    return {
        'optimal_price': int(best_price),
        'optimal_quantity': int(best_qty),
        'clearing_price': int(best_cp),
        'max_profit': round(float(best_profit), 2)
    }

class AuctionMaximizer:
    def __init__(self, market: Optional[AuctionMarket] = None):
        self.base_market = market or round_1_market()

    def best_profit_grid(self, symbol: str, sides: Optional[List[Side]] = None) -> Dict[str, object]:
        if sides is None:
            sides = [Side.BUY, Side.SELL]
        
        auction = self.base_market.books[symbol]
        
        bids_dict = defaultdict(int)
        for order in auction.bids:
            bids_dict[order.price] += order.quantity
        
        asks_dict = defaultdict(int)
        for order in auction.asks:
            asks_dict[order.price] += order.quantity
        
        config = auction.config
        
        best_by_side = {}
        
        for side in sides:
            result = find_optimal_order_numpy(bids_dict, asks_dict, config.exit_price, config.fee_per_unit, side)
            
            best_by_side[side.value] = {
                'price': result['optimal_price'],
                'quantity': result['optimal_quantity'],
                'side': side.value,
                'symbol': symbol,
                'clearing_price': result['clearing_price'],
                'user_filled_volume': result['optimal_quantity'] if result['max_profit'] > 0 else 0,
                'user_profit': result['max_profit'],
            }
        
        best_overall = max(best_by_side.values(), key=lambda x: x['user_profit'])
        
        return {
            'best_overall': best_overall if best_overall['user_profit'] > 0 else None,
            'best_by_side': best_by_side,
        }

    def best_profit_across_market(self, sides: Optional[List[Side]] = None) -> Dict[str, object]:
        best_by_symbol = {}
        total_profit = 0.0
        
        for symbol in self.base_market.books:
            symbol_best = self.best_profit_grid(symbol, sides)
            best_by_symbol[symbol] = symbol_best['best_overall']
            if symbol_best['best_overall'] is not None:
                total_profit += symbol_best['best_overall']['user_profit']
        
        return {
            'best_by_symbol': best_by_symbol,
            'total_profit': total_profit,
        }


def round_1_market() -> AuctionMarket:
    configs = [
        AuctionConfig(symbol='DRYLAND_FLAX', exit_price=30.0, fee_per_unit=0.0),
        AuctionConfig(symbol='EMBER_MUSHROOM', exit_price=20.0, fee_per_unit=0.10),
    ]
    market =  AuctionMarket(configs)

    market.add_orders([
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=12, quantity=20000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=13, quantity=25000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=14, quantity=35000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=15, quantity=6000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=16, quantity=5000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=18, quantity=10000),
        Order(symbol='EMBER_MUSHROOM', side=Side.SELL, price=19, quantity=12000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=20, quantity=43000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=19, quantity=17000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=18, quantity=6000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=17, quantity=5000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=16, quantity=10000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=15, quantity=5000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=14, quantity=10000),
        Order(symbol='EMBER_MUSHROOM', side=Side.BUY, price=13, quantity=7000),
    ])

    market.add_orders([
        Order(symbol='DRYLAND_FLAX', side=Side.SELL, price=28, quantity=40_000),
        Order(symbol='DRYLAND_FLAX', side=Side.SELL, price=31, quantity=20_000),
        Order(symbol='DRYLAND_FLAX', side=Side.SELL, price=32, quantity=20_000),
        Order(symbol='DRYLAND_FLAX', side=Side.SELL, price=33, quantity=30_000),
        Order(symbol='DRYLAND_FLAX', side=Side.BUY, price=27, quantity=28000),
        Order(symbol='DRYLAND_FLAX', side=Side.BUY, price=28, quantity=12000),
        Order(symbol='DRYLAND_FLAX', side=Side.BUY, price=29, quantity=5000),
        Order(symbol='DRYLAND_FLAX', side=Side.BUY, price=30, quantity=30000),
    ])

    return market


if __name__ == '__main__':
    market = round_1_market()
    maximizer = AuctionMaximizer(market)
    
    # Let the solver find the global maximum mathematically
    result = maximizer.best_profit_across_market()
    
    print(f"--- OPTIMAL STRATEGY ---")
    for symbol, best in result['best_by_symbol'].items():
        if best:
            print(f"{symbol}: {best['side']} {best['quantity']} @ {best['price']} -> Profit: {best['user_profit']}")
        else:
            print(f"{symbol}: No profitable trade available.")
    
    print(f"\nTotal Max PNL: {result['total_profit']}")