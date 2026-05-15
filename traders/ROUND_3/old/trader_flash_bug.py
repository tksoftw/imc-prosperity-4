import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, TradingState


VELVET = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"
WING_PRODUCTS = ("VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
ITM_STRIKES = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
}
VELVET_MEAN = 5255.0
VELVET_SIGNAL_TTL = 1000
VELVET_INSIDER_TILT = 1.5
HYDRO_ANCHOR = 9991.0
HYDRO_FAST_WINDOW = 30
HYDRO_SLOW_WINDOW = 120
HYDRO_TAKE_EDGE = 7.0
HYDRO_QUOTE_EDGE = 4.0
HYDRO_QUOTE_SIZE = 18
HYDRO_MIN_SPREAD = 10
WING_SIGNAL_TTL = 200
VEV_5300_BASE_EXTRINSIC = 47.0
VEV_5300_MIN_EXTRINSIC = 42.0
VEV_5300_MAX_EXTRINSIC = 50.0
VEV_5300_TAKE_EDGE = 2.0
VEV_5300_EXIT_EDGE = 1.0
ENABLE_VEV_5300 = False

LIMITS = {
    VELVET: 200,
    HYDROGEL: 200,
    "VEV_4000": 120,
    "VEV_4500": 90,
    "VEV_5300": 40,
}


def best_bid_ask(order_depth) -> Tuple[Optional[int], int, Optional[int], int]:
    best_bid = None
    bid_volume = 0
    best_ask = None
    ask_volume = 0

    if order_depth is not None and order_depth.buy_orders:
        best_bid = max(order_depth.buy_orders)
        bid_volume = order_depth.buy_orders[best_bid]

    if order_depth is not None and order_depth.sell_orders:
        best_ask = min(order_depth.sell_orders)
        ask_volume = order_depth.sell_orders[best_ask]

    return best_bid, bid_volume, best_ask, ask_volume


def mid_price(order_depth) -> Optional[float]:
    best_bid, _, best_ask, _ = best_bid_ask(order_depth)
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2.0


class Trader:
    def load_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"emas": {}, "velvet_until": -1, "wing_until": -1}
        try:
            data = json.loads(trader_data)
        except Exception:
            return {"emas": {}, "velvet_until": -1, "wing_until": -1}
        if "emas" not in data or not isinstance(data["emas"], dict):
            data["emas"] = {}
        if "velvet_until" not in data:
            data["velvet_until"] = -1
        if "wing_until" not in data:
            data["wing_until"] = -1
        return data

    def save_state(self, data: Dict) -> str:
        return json.dumps(data, separators=(",", ":"))

    def ema(self, data: Dict, key: str, value: float, window: int) -> float:
        emas = data.setdefault("emas", {})
        if key not in emas:
            emas[key] = value
        else:
            alpha = 2.0 / (window + 1.0)
            emas[key] = alpha * value + (1.0 - alpha) * emas[key]
        return float(emas[key])

    def pos(self, state: TradingState, product: str) -> int:
        return int(state.position.get(product, 0))

    def buy_room(self, state: TradingState, product: str, orders: List[Order]) -> int:
        used = sum(max(0, order.quantity) for order in orders)
        return max(0, LIMITS[product] - self.pos(state, product) - used)

    def sell_room(self, state: TradingState, product: str, orders: List[Order]) -> int:
        used = sum(max(0, -order.quantity) for order in orders)
        return max(0, LIMITS[product] + self.pos(state, product) - used)

    def add_buy(
        self,
        state: TradingState,
        product: str,
        orders: List[Order],
        price: int,
        quantity: int,
    ) -> None:
        quantity = min(max(0, int(quantity)), self.buy_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), quantity))

    def add_sell(
        self,
        state: TradingState,
        product: str,
        orders: List[Order],
        price: int,
        quantity: int,
    ) -> None:
        quantity = min(max(0, int(quantity)), self.sell_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), -quantity))

    def wing_flow_active(self, state: TradingState) -> bool:
        for product in WING_PRODUCTS:
            if state.market_trades.get(product):
                return True
        return False

    def update_signals(self, state: TradingState, data: Dict) -> Tuple[bool, bool]:
        if any(abs(int(trade.quantity)) >= 9 for trade in state.market_trades.get(VELVET, [])):
            data["velvet_until"] = state.timestamp + VELVET_SIGNAL_TTL

        if any(state.market_trades.get(product) for product in ("VEV_5300",) + WING_PRODUCTS):
            data["wing_until"] = state.timestamp + WING_SIGNAL_TTL

        velvet_signal = state.timestamp <= int(data.get("velvet_until", -1))
        wing_signal = state.timestamp <= int(data.get("wing_until", -1))
        return velvet_signal, wing_signal

    def deep_itm_surface_shift(self, state: TradingState, spot_mid: float) -> Optional[float]:
        shifts: List[float] = []
        for product, strike in ITM_STRIKES.items():
            depth = state.order_depths.get(product)
            option_mid = mid_price(depth)
            if option_mid is None:
                continue
            shifts.append(option_mid - max(spot_mid - strike, 0.0))
        if not shifts:
            return None
        return sum(shifts) / len(shifts)

    def trade_hydrogel(self, state: TradingState, data: Dict, result: Dict[str, List[Order]]) -> None:
        product = HYDROGEL
        depth = state.order_depths.get(product)
        if depth is None:
            return

        orders = result.setdefault(product, [])
        best_bid, bid_volume, best_ask, ask_volume = best_bid_ask(depth)
        mid = mid_price(depth)
        if best_bid is None or best_ask is None or mid is None:
            return

        position = self.pos(state, product)
        spread = best_ask - best_bid

        fast = self.ema(data, f"{product}_fast", mid, HYDRO_FAST_WINDOW)
        slow = self.ema(data, f"{product}_slow", mid, HYDRO_SLOW_WINDOW)
        fair = 0.20 * mid + 0.25 * fast + 0.40 * slow + 0.15 * HYDRO_ANCHOR
        fair -= 0.05 * position

        if best_ask <= fair - HYDRO_TAKE_EDGE:
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, 28))

        if best_bid >= fair + HYDRO_TAKE_EDGE:
            self.add_sell(state, product, orders, best_bid, min(bid_volume, 28))

        if spread >= HYDRO_MIN_SPREAD:
            target_bid = int(min(best_bid + 1, math.floor(fair - HYDRO_QUOTE_EDGE)))
            target_ask = int(max(best_ask - 1, math.ceil(fair + HYDRO_QUOTE_EDGE)))
            if target_bid < target_ask:
                self.add_buy(state, product, orders, target_bid, HYDRO_QUOTE_SIZE)
                self.add_sell(state, product, orders, target_ask, HYDRO_QUOTE_SIZE)

    def trade_velvet(
        self,
        state: TradingState,
        data: Dict,
        result: Dict[str, List[Order]],
        velvet_signal: bool,
    ) -> None:
        product = VELVET
        depth = state.order_depths.get(product)
        if depth is None:
            return

        best_bid, bid_volume, best_ask, ask_volume = best_bid_ask(depth)
        mid = mid_price(depth)
        if best_bid is None or best_ask is None or mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        fast = self.ema(data, f"{product}_fast", mid, 15)
        fair = 0.7 * fast + 0.3 * VELVET_MEAN
        fair -= 0.015 * position
        if velvet_signal:
            fair += VELVET_INSIDER_TILT

        if velvet_signal and best_ask <= fair:
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, 25))

        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))
        self.add_buy(state, product, orders, target_bid, LIMITS[product])
        self.add_sell(state, product, orders, target_ask, LIMITS[product])

    def trade_flash_itm(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        spot_mid: float,
        surface_shift: Optional[float],
        wing_flow: bool,
    ) -> None:
        depth = state.order_depths.get(product)
        if depth is None:
            return

        best_bid, bid_volume, best_ask, ask_volume = best_bid_ask(depth)
        option_mid = mid_price(depth)
        if best_bid is None or best_ask is None or option_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        fair = max(spot_mid - strike, 0.0)
        spread = best_ask - best_bid
        local_shift = option_mid - fair
        shared_shift = surface_shift if surface_shift is not None else local_shift

        flash_down = shared_shift <= -4.0
        flash_up = shared_shift >= 4.0
        actual_cheap = best_ask <= fair - 2
        cheap_touch = best_ask <= fair

        take_size = 12 if product == "VEV_4000" else 8
        unload_size = 10 if product == "VEV_4000" else 7

        if flash_down and cheap_touch:
            size = take_size
            if actual_cheap:
                size += 4
            if wing_flow or spread <= 10:
                size += 3
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, size))

        if actual_cheap:
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, take_size + 4))

        if position > 0:
            if best_bid >= fair + 1 or (flash_up and best_bid >= fair):
                self.add_sell(state, product, orders, best_bid, min(position, bid_volume, unload_size))

            target_ask = int(max(best_ask - 1, math.ceil(fair + 1.0)))
            if target_ask > best_bid:
                self.add_sell(state, product, orders, target_ask, min(position, 5))

        # We only use rich books to lighten inventory. Fresh shorts were
        # less reliable than buying the down-flashes in the data.
        if position <= 0 and spread >= 8 and not flash_up:
            target_bid = int(min(best_bid + 1, math.floor(fair - 2.0)))
            if target_bid > 0 and target_bid < best_ask:
                passive_size = 2 if product == "VEV_4500" else 3
                self.add_buy(state, product, orders, target_bid, passive_size)

    def trade_vev_5300(
        self,
        state: TradingState,
        data: Dict,
        result: Dict[str, List[Order]],
        spot_mid: float,
        velvet_signal: bool,
        wing_signal: bool,
    ) -> None:
        product = "VEV_5300"
        depth = state.order_depths.get(product)
        if depth is None:
            return

        best_bid, bid_volume, best_ask, ask_volume = best_bid_ask(depth)
        option_mid = mid_price(depth)
        if best_bid is None or best_ask is None or option_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        intrinsic = max(spot_mid - 5300, 0.0)
        observed_extrinsic = option_mid - intrinsic
        extrinsic = self.ema(data, f"{product}_extrinsic", observed_extrinsic, 100)
        extrinsic = max(VEV_5300_MIN_EXTRINSIC, min(VEV_5300_MAX_EXTRINSIC, extrinsic))

        spot_fast = self.ema(data, f"{VELVET}_fast", spot_mid, 15)
        spot_slow = self.ema(data, f"{VELVET}_slow", spot_mid, 80)
        spot_dev = spot_mid - spot_slow

        fair = intrinsic + 0.6 * extrinsic + 0.4 * VEV_5300_BASE_EXTRINSIC - 0.05 * position
        if velvet_signal:
            fair += 1.0

        spread = best_ask - best_bid
        bullish_state = velvet_signal or wing_signal or spot_dev <= -2.0
        bearish_state = spot_dev >= 2.0 and not velvet_signal

        if best_ask <= fair - 3.0 and bullish_state:
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, 8))
        if best_bid >= fair + 3.0 and bearish_state:
            self.add_sell(state, product, orders, best_bid, min(bid_volume, 8))

        if position > 0 and (best_bid >= fair + VEV_5300_EXIT_EDGE or bearish_state):
            self.add_sell(state, product, orders, best_bid, min(position, bid_volume, 8))
        if position < 0 and (best_ask <= fair - VEV_5300_EXIT_EDGE or bullish_state):
            self.add_buy(state, product, orders, best_ask, min(-position, -ask_volume, 8))

        if spread >= 2:
            if bullish_state:
                target_bid = int(min(best_bid + 1, math.floor(fair - 1.0)))
                if target_bid < best_ask:
                    self.add_buy(state, product, orders, target_bid, 4)
            elif bearish_state:
                target_ask = int(max(best_ask - 1, math.ceil(fair + 1.0)))
                if target_ask > best_bid:
                    self.add_sell(state, product, orders, target_ask, 4)
            elif position > 0:
                target_ask = int(max(best_ask - 1, math.ceil(fair + 1.0)))
                if target_ask > best_bid:
                    self.add_sell(state, product, orders, target_ask, min(position, 4))
            elif position < 0:
                target_bid = int(min(best_bid + 1, math.floor(fair - 1.0)))
                if target_bid < best_ask:
                    self.add_buy(state, product, orders, target_bid, min(-position, 4))

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        data = self.load_state(state.traderData)
        velvet_signal, wing_signal = self.update_signals(state, data)

        self.trade_hydrogel(state, data, result)
        self.trade_velvet(state, data, result, velvet_signal)

        spot_mid = mid_price(state.order_depths.get(VELVET))
        if spot_mid is not None:
            surface_shift = self.deep_itm_surface_shift(state, spot_mid)
            wing_flow = wing_signal or self.wing_flow_active(state)
            for product, strike in ITM_STRIKES.items():
                self.trade_flash_itm(
                    state,
                    result,
                    product,
                    strike,
                    spot_mid,
                    surface_shift,
                    wing_flow,
                )
            if ENABLE_VEV_5300:
                self.trade_vev_5300(state, data, result, spot_mid, velvet_signal, wing_signal)

        result = {product: orders for product, orders in result.items() if orders}
        return result, 0, self.save_state(data)
