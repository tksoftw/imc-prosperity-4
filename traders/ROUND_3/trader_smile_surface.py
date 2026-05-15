import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, TradingState


VELVET = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"
STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)
SURFACE_STRIKES = (5000, 5100, 5200, 5300, 5400, 5500)
WING_PRODUCTS = ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")

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

SMILE_LEVEL_WINDOW = 60
WING_SIGNAL_TTL = 200
TIME_TO_EXPIRY = 1.0
IV_FLOOR = 0.0001
IV_CAP = 0.08

LIMITS = {
    VELVET: 200,
    HYDROGEL: 200,
    "VEV_4000": 120,
    "VEV_4500": 90,
    "VEV_5000": 40,
    "VEV_5100": 50,
    "VEV_5200": 55,
    "VEV_5300": 12,
    "VEV_5400": 12,
    "VEV_5500": 15,
    "VEV_6000": 8,
    "VEV_6500": 8,
}

QUOTE_SIZES = {
    5000: 4,
    5100: 5,
    5200: 6,
    5300: 1,
    5400: 1,
    5500: 1,
    6000: 1,
    6500: 1,
}

DELTA_HINT = {
    4000: 1.0,
    4500: 1.0,
    5000: 0.9,
    5100: 0.8,
    5200: 0.7,
    5300: 0.5,
    5400: 0.3,
    5500: 0.2,
    6000: 0.02,
    6500: 0.0,
}

# Historical smile offsets in IV-space, indexed by moneyness = (K - S) / 100.
SMILE_KNOTS = (
    (-2.5, -0.0006),
    (-1.5, -0.0012),
    (-0.5, -0.0003),
    (0.5, 0.0),
    (1.5, -0.0019),
    (2.5, 0.0005),
    (7.5, -0.0040),
    (12.5, -0.0060),
)


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


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call(spot: float, strike: int, tte: float, sigma: float) -> float:
    intrinsic = max(spot - strike, 0.0)
    if tte <= 0 or sigma <= 0:
        return intrinsic
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * math.sqrt(tte))
    d2 = d1 - sigma * math.sqrt(tte)
    return spot * norm_cdf(d1) - strike * norm_cdf(d2)


def bs_vega(spot: float, strike: int, tte: float, sigma: float) -> float:
    if tte <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * math.sqrt(tte))
    return spot * norm_pdf(d1) * math.sqrt(tte)


def bs_delta(spot: float, strike: int, tte: float, sigma: float) -> float:
    intrinsic = 1.0 if spot > strike else 0.0
    if tte <= 0 or sigma <= 0:
        return intrinsic
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * math.sqrt(tte))
    return norm_cdf(d1)


def implied_volatility(spot: float, strike: int, price: float, tte: float = TIME_TO_EXPIRY) -> float:
    intrinsic = max(spot - strike, 0.0)
    if price <= intrinsic + 1e-6:
        return IV_FLOOR

    sigma = 0.03
    for _ in range(30):
        theo = bs_call(spot, strike, tte, sigma)
        diff = theo - price
        if abs(diff) < 1e-6:
            break
        vega = bs_vega(spot, strike, tte, sigma)
        if vega < 1e-8:
            break
        sigma = max(IV_FLOOR, min(IV_CAP, sigma - diff / vega))
    return sigma


def interpolate_smile_offset(moneyness: float) -> float:
    if moneyness <= SMILE_KNOTS[0][0]:
        return SMILE_KNOTS[0][1]
    if moneyness >= SMILE_KNOTS[-1][0]:
        return SMILE_KNOTS[-1][1]

    for (x0, y0), (x1, y1) in zip(SMILE_KNOTS, SMILE_KNOTS[1:]):
        if x0 <= moneyness <= x1:
            weight = (moneyness - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return 0.0


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

    def add_buy(self, state: TradingState, product: str, orders: List[Order], price: int, quantity: int) -> None:
        quantity = min(max(0, int(quantity)), self.buy_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), quantity))

    def add_sell(self, state: TradingState, product: str, orders: List[Order], price: int, quantity: int) -> None:
        quantity = min(max(0, int(quantity)), self.sell_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), -quantity))

    def update_signals(self, state: TradingState, data: Dict) -> Tuple[bool, bool]:
        if any(abs(int(trade.quantity)) >= 9 for trade in state.market_trades.get(VELVET, [])):
            data["velvet_until"] = state.timestamp + VELVET_SIGNAL_TTL

        if any(state.market_trades.get(product) for product in WING_PRODUCTS):
            data["wing_until"] = state.timestamp + WING_SIGNAL_TTL

        velvet_signal = state.timestamp <= int(data.get("velvet_until", -1))
        wing_signal = state.timestamp <= int(data.get("wing_until", -1))
        return velvet_signal, wing_signal

    def deep_itm_surface_shift(self, state: TradingState, spot_mid: float) -> Optional[float]:
        shifts: List[float] = []
        for strike in (4000, 4500):
            depth = state.order_depths.get(f"VEV_{strike}")
            option_mid = mid_price(depth)
            if option_mid is None:
                continue
            shifts.append(option_mid - max(spot_mid - strike, 0.0))
        if not shifts:
            return None
        return sum(shifts) / len(shifts)

    def compute_smile_level(self, state: TradingState, data: Dict, spot_mid: float, wing_signal: bool) -> float:
        weighted_sum = 0.0
        total_weight = 0.0

        for strike in SURFACE_STRIKES:
            depth = state.order_depths.get(f"VEV_{strike}")
            option_mid = mid_price(depth)
            best_bid, _, best_ask, _ = best_bid_ask(depth)
            if option_mid is None or best_bid is None or best_ask is None:
                continue

            spread = max(1, best_ask - best_bid)
            iv_obs = implied_volatility(spot_mid, strike, option_mid)
            iv_smoothed = self.ema(data, f"iv_{strike}", iv_obs, SMILE_LEVEL_WINDOW)
            offset = interpolate_smile_offset((strike - spot_mid) / 100.0)
            level_candidate = iv_smoothed - offset

            if wing_signal and strike >= 5300:
                weight = 0.3 / spread
            else:
                weight = 1.0 / spread
            weighted_sum += weight * level_candidate
            total_weight += weight

        default_level = self.ema(data, "smile_level_default", 0.033, SMILE_LEVEL_WINDOW)
        if total_weight == 0:
            return default_level

        level = weighted_sum / total_weight
        level = self.ema(data, "smile_level", level, SMILE_LEVEL_WINDOW)
        return max(0.026, min(0.037, level))

    def option_delta_exposure(self, state: TradingState, spot_mid: float, smile_level: float) -> float:
        exposure = 0.0
        for strike in STRIKES:
            product = f"VEV_{strike}"
            position = self.pos(state, product)
            if position == 0:
                continue

            if strike in (4000, 4500):
                delta = DELTA_HINT[strike]
            elif strike in (6000, 6500):
                delta = DELTA_HINT[strike]
            else:
                fair_iv = max(IV_FLOOR, min(IV_CAP, smile_level + interpolate_smile_offset((strike - spot_mid) / 100.0)))
                delta = bs_delta(spot_mid, strike, TIME_TO_EXPIRY, fair_iv)
            exposure += delta * position
        return exposure

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
        option_delta: float,
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

    def trade_deep_itm(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        spot_mid: float,
        shared_shift: Optional[float],
        wing_signal: bool,
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
        shift = shared_shift if shared_shift is not None else local_shift

        flash_down = shift <= -4.0
        flash_up = shift >= 4.0
        actual_cheap = best_ask <= fair - 2

        if flash_down and best_ask <= fair:
            size = 12 if strike == 4000 else 8
            if actual_cheap:
                size += 4
            if wing_signal or spread <= 10:
                size += 3
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, size))

        if actual_cheap:
            size = 16 if strike == 4000 else 10
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, size))

        if position > 0:
            if best_bid >= fair + 1 or (flash_up and best_bid >= fair):
                size = 10 if strike == 4000 else 7
                self.add_sell(state, product, orders, best_bid, min(position, bid_volume, size))
            target_ask = int(max(best_ask - 1, math.ceil(fair + 1.0)))
            if target_ask > best_bid:
                self.add_sell(state, product, orders, target_ask, min(position, 5))

        if position <= 0 and spread >= 8 and not flash_up:
            target_bid = int(min(best_bid + 1, math.floor(fair - (2.0 if strike == 4000 else 3.0))))
            if target_bid > 0 and target_bid < best_ask:
                self.add_buy(state, product, orders, target_bid, 3 if strike == 4000 else 2)

    def trade_surface_option(
        self,
        state: TradingState,
        data: Dict,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        spot_mid: float,
        smile_level: float,
        velvet_signal: bool,
        wing_signal: bool,
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
        spread = best_ask - best_bid
        moneyness = (strike - spot_mid) / 100.0
        fair_iv = max(IV_FLOOR, min(IV_CAP, smile_level + interpolate_smile_offset(moneyness)))
        fair = bs_call(spot_mid, strike, TIME_TO_EXPIRY, fair_iv)
        if strike in (6000, 6500):
            fair = max(0.5, fair)

        delta = bs_delta(spot_mid, strike, TIME_TO_EXPIRY, fair_iv)
        fair += 0.8 * delta if velvet_signal else 0.0

        position_skew = 0.07 if strike <= 5200 else 0.05
        fair -= position_skew * position

        take_edge = 1.5 if spread >= 2 else 1.0
        quote_edge = 0.8 if spread >= 4 else 0.5
        quote_size = QUOTE_SIZES[strike]
        allow_open_buy = True
        allow_open_sell = True
        allow_quote_bid = spread >= 2
        allow_quote_ask = spread >= 2

        if wing_signal and strike >= 5300:
            take_edge += 1.0
            quote_edge += 1.0
            quote_size = max(1, quote_size - 1)
            allow_quote_bid = False

        if strike == 5300:
            take_edge += 0.75
            quote_edge += 0.75
            allow_open_buy = velvet_signal and not wing_signal
            allow_quote_bid = allow_quote_bid and velvet_signal
        elif strike == 5400:
            take_edge += 0.5
            if wing_signal:
                allow_open_buy = False
                allow_quote_bid = False
        elif strike == 5500:
            take_edge += 0.25

        if allow_open_buy and best_ask <= fair - take_edge:
            self.add_buy(state, product, orders, best_ask, min(-ask_volume, quote_size * 2))
        if allow_open_sell and best_bid >= fair + take_edge:
            self.add_sell(state, product, orders, best_bid, min(bid_volume, quote_size * 2))

        if spread >= 2:
            target_bid = int(min(best_bid + 1, math.floor(fair - quote_edge)))
            target_ask = int(max(best_ask - 1, math.ceil(fair + quote_edge)))
            if target_bid < target_ask:
                if allow_quote_bid:
                    self.add_buy(state, product, orders, target_bid, quote_size)
                if allow_quote_ask:
                    self.add_sell(state, product, orders, target_ask, quote_size)
        else:
            if strike in (5400, 5500) and best_ask <= fair - 1.0:
                self.add_buy(state, product, orders, best_ask, min(-ask_volume, quote_size))
            if strike in (5400, 5500) and best_bid >= fair + 1.0:
                self.add_sell(state, product, orders, best_bid, min(bid_volume, quote_size))
            if strike in (6000, 6500):
                if best_ask == 0:
                    self.add_buy(state, product, orders, best_ask, 1)
                if position > 0 and best_bid >= 1:
                    self.add_sell(state, product, orders, best_bid, min(position, 1))

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        data = self.load_state(state.traderData)
        velvet_signal, wing_signal = self.update_signals(state, data)

        self.trade_hydrogel(state, data, result)

        spot_mid = mid_price(state.order_depths.get(VELVET))
        smile_level = 0.033
        option_delta = 0.0

        if spot_mid is not None:
            smile_level = self.compute_smile_level(state, data, spot_mid, wing_signal)
            option_delta = self.option_delta_exposure(state, spot_mid, smile_level)

        self.trade_velvet(state, data, result, velvet_signal, option_delta)

        if spot_mid is None:
            return {product: orders for product, orders in result.items() if orders}, 0, self.save_state(data)

        shared_shift = self.deep_itm_surface_shift(state, spot_mid)
        self.trade_deep_itm(state, result, "VEV_4000", 4000, spot_mid, shared_shift, wing_signal)
        self.trade_deep_itm(state, result, "VEV_4500", 4500, spot_mid, shared_shift, wing_signal)

        for strike in SURFACE_STRIKES:
            self.trade_surface_option(
                state,
                data,
                result,
                f"VEV_{strike}",
                strike,
                spot_mid,
                smile_level,
                velvet_signal,
                wing_signal,
            )

        for strike in (6000, 6500):
            self.trade_surface_option(
                state,
                data,
                result,
                f"VEV_{strike}",
                strike,
                spot_mid,
                smile_level,
                velvet_signal,
                wing_signal,
            )

        result = {product: orders for product, orders in result.items() if orders}
        return result, 0, self.save_state(data)
