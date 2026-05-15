from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple
import json
import math

from traders.ROUND_5.codex_pair_regression_robot_alpha_capped import Trader as PebblesRobotTrader
from traders.ROUND_5.codex_group_starter import Trader as GroupStarterTrader
from traders.ROUND_5.intro_group_strategy import Trader as IntroGroupTrader
from traders.ROUND_5.intro_market_maker import Trader as IntroMarketMakerTrader
from traders.ROUND_5.strategy_v2_tuned import Trader as StrategyV2Trader
from traders.ROUND_5.strategy_v3_aggressive_spread import Trader as StrategyV3Trader
from traders.ROUND_5.strategy_v4_momentum_selective import Trader as StrategyV4Trader
from traders.ROUND_5.strategy_v6_final_optimized import Trader as StrategyV6Trader
from traders.ROUND_5.trader_combined_strats import Trader as CombinedTrader


class Trader:
    """Meta-router across sub-strategies.

    Previously, any product omitted from PRODUCT_SOURCE defaulted to ``base``
    (:class:`codex_pair_regression_robot_alpha_capped.Trader`), which **taker-rebalances**
    toward fixed inventory targets across ~40 books. That is strong on **pebbles**
    plus pair MM there, but it is a blunt hammer on ROBOT legs, most PANEL/SLEEP legs,
    etc.—consistent with miserable web logs on those names while pebbles still print.

    Unmapped symbols now default to ``combined`` (MM + benign pair overlays). Only
    the five ``PEBBLES_*`` symbols use ``base`` unless overridden below.

    ``codex_group_starter`` only ENABLES tiny subsets per venue (e.g. a single galaxy
    name); routing a whole family’s weak leg there (e.g. only PLANETARY_RINGS) amplified
    drawdowns—those names are pushed to ``combined``.
    """

    SOURCES = (
        "base",
        "group",
        "intro_group",
        "intro_market",
        "v2",
        "v3",
        "v4",
        "v6",
        "combined",
    )

    _PEBBLES = (
        "PEBBLES_L",
        "PEBBLES_M",
        "PEBBLES_S",
        "PEBBLES_XL",
        "PEBBLES_XS",
    )
    DEFAULT_PRODUCT_SOURCE = "combined"

    PRODUCT_SOURCE: Dict[str, str] = {
        # Pebbles (base = pair regression robot)
        "PEBBLES_L": "base",
        "PEBBLES_M": "base",
        "PEBBLES_S": "base",
        "PEBBLES_XL": "base",
        "PEBBLES_XS": "base",
        # intro_group
        "ROBOT_DISHES": "intro_group",
        "ROBOT_IRONING": "intro_group",
        "ROBOT_LAUNDRY": "intro_group",
        "SNACKPACK_PISTACHIO": "intro_group",
        "SNACKPACK_RASPBERRY": "intro_group",
        "SNACKPACK_STRAWBERRY": "intro_group",
        # group starter
        "SNACKPACK_CHOCOLATE": "group",
        "SNACKPACK_VANILLA": "group",
        "OXYGEN_SHAKE_CHOCOLATE": "group",
        "PANEL_1X2": "group",
        "PANEL_1X4": "group",
        "TRANSLATOR_ECLIPSE_CHARCOAL": "group",
        "UV_VISOR_AMBER": "group",
        # intro_market
        "GALAXY_SOUNDS_DARK_MATTER": "intro_market",
        "OXYGEN_SHAKE_MINT": "intro_market",
        "TRANSLATOR_VOID_BLUE": "intro_market",
        "UV_VISOR_YELLOW": "intro_market",
        # v2
        "SLEEP_POD_POLYESTER": "v2",
        "UV_VISOR_MAGENTA": "v2",
        # v3
        "PANEL_2X4": "v3",
        "UV_VISOR_RED": "v3",
        # Combined (all others)
        "GALAXY_SOUNDS_BLACK_HOLES": "combined",
        "GALAXY_SOUNDS_PLANETARY_RINGS": "combined",
        "GALAXY_SOUNDS_SOLAR_FLAMES": "combined",
        "GALAXY_SOUNDS_SOLAR_WINDS": "combined",
        "MICROCHIP_CIRCLE": "combined",
        "MICROCHIP_OVAL": "combined",
        "MICROCHIP_RECTANGLE": "combined",
        "MICROCHIP_SQUARE": "combined",
        "MICROCHIP_TRIANGLE": "combined",
        "OXYGEN_SHAKE_EVENING_BREATH": "combined",
        "OXYGEN_SHAKE_GARLIC": "combined",
        "OXYGEN_SHAKE_MORNING_BREATH": "combined",
        "PANEL_2X2": "combined",
        "PANEL_4X4": "combined",
        "ROBOT_MOPPING": "combined",
        "ROBOT_VACUUMING": "combined",
        "SLEEP_POD_COTTON": "combined",
        "SLEEP_POD_LAMB_WOOL": "combined",
        "SLEEP_POD_NYLON": "combined",
        "SLEEP_POD_SUEDE": "combined",
        "TRANSLATOR_ASTRO_BLACK": "combined",
        "TRANSLATOR_GRAPHITE_MIST": "combined",
        "TRANSLATOR_SPACE_GRAY": "combined",
        "UV_VISOR_ORANGE": "combined",
    }

    # Every symbol that appears on the ROUND_5 submission book (50 total).
    _ALL_UNDERLYINGS: Tuple[str, ...] = (
        "GALAXY_SOUNDS_BLACK_HOLES",
        "GALAXY_SOUNDS_DARK_MATTER",
        "GALAXY_SOUNDS_PLANETARY_RINGS",
        "GALAXY_SOUNDS_SOLAR_FLAMES",
        "GALAXY_SOUNDS_SOLAR_WINDS",
        "MICROCHIP_CIRCLE",
        "MICROCHIP_OVAL",
        "MICROCHIP_RECTANGLE",
        "MICROCHIP_SQUARE",
        "MICROCHIP_TRIANGLE",
        "OXYGEN_SHAKE_CHOCOLATE",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "OXYGEN_SHAKE_GARLIC",
        "OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_MORNING_BREATH",
        "PANEL_1X2",
        "PANEL_1X4",
        "PANEL_2X2",
        "PANEL_2X4",
        "PANEL_4X4",
        *_PEBBLES,
        "ROBOT_DISHES",
        "ROBOT_IRONING",
        "ROBOT_LAUNDRY",
        "ROBOT_MOPPING",
        "ROBOT_VACUUMING",
        "SLEEP_POD_COTTON",
        "SLEEP_POD_LAMB_WOOL",
        "SLEEP_POD_NYLON",
        "SLEEP_POD_POLYESTER",
        "SLEEP_POD_SUEDE",
        "SNACKPACK_CHOCOLATE",
        "SNACKPACK_PISTACHIO",
        "SNACKPACK_RASPBERRY",
        "SNACKPACK_STRAWBERRY",
        "SNACKPACK_VANILLA",
        "TRANSLATOR_ASTRO_BLACK",
        "TRANSLATOR_ECLIPSE_CHARCOAL",
        "TRANSLATOR_GRAPHITE_MIST",
        "TRANSLATOR_SPACE_GRAY",
        "TRANSLATOR_VOID_BLUE",
        "UV_VISOR_AMBER",
        "UV_VISOR_MAGENTA",
        "UV_VISOR_ORANGE",
        "UV_VISOR_RED",
        "UV_VISOR_YELLOW",
    )

    OXYGEN_MORNING = "OXYGEN_SHAKE_MORNING_BREATH"
    OXYGEN_GROUP = (
        "OXYGEN_SHAKE_CHOCOLATE",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "OXYGEN_SHAKE_GARLIC",
        "OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_MORNING_BREATH",
    )
    ROBOT_GROUP = (
        "ROBOT_DISHES",
        "ROBOT_IRONING",
        "ROBOT_LAUNDRY",
        "ROBOT_MOPPING",
        "ROBOT_VACUUMING",
    )
    POSITION_LIMIT = 10
    MAX_ORDERS_PER_PRODUCT = 10
    NO_TRADE_ALWAYS = {
        "GALAXY_SOUNDS_SOLAR_WINDS",
        "ROBOT_VACUUMING",
        "SNACKPACK_PISTACHIO",
        "TRANSLATOR_ASTRO_BLACK",
    }
    RESCUE_ALWAYS = {
        "MICROCHIP_TRIANGLE",
        "PANEL_1X2",
        "TRANSLATOR_SPACE_GRAY",
    }
    RESCUE_NORMAL = {
        "OXYGEN_SHAKE_MORNING_BREATH",
    }
    RESCUE_LATE_REGIME = {
        "GALAXY_SOUNDS_PLANETARY_RINGS",
        "MICROCHIP_OVAL",
        "MICROCHIP_SQUARE",
        "PANEL_4X4",
        "ROBOT_MOPPING",
        "TRANSLATOR_GRAPHITE_MIST",
        "UV_VISOR_ORANGE",
        "UV_VISOR_YELLOW",
    }
    NO_TRADE_LATE_REGIME = {
        "GALAXY_SOUNDS_DARK_MATTER",
        "GALAXY_SOUNDS_SOLAR_FLAMES",
        "PANEL_1X4",
        "SLEEP_POD_LAMB_WOOL",
        "SLEEP_POD_POLYESTER",
        "SLEEP_POD_SUEDE",
        "OXYGEN_SHAKE_MORNING_BREATH",
    }

    def __init__(self):
        self.traders = {
            "base": PebblesRobotTrader(),
            "group": GroupStarterTrader(),
            "intro_group": IntroGroupTrader(),
            "intro_market": IntroMarketMakerTrader(),
            "v2": StrategyV2Trader(),
            "v3": StrategyV3Trader(),
            "v4": StrategyV4Trader(),
            "v6": StrategyV6Trader(),
            "combined": CombinedTrader(),
        }

    def run(self, state: TradingState):
        memory = self.load_state(state.traderData)
        sub_memory = memory.get("s", {})
        outputs: Dict[str, Dict[str, List[Order]]] = {}
        next_sub_memory: Dict[str, str] = {}

        for name in self.SOURCES:
            orders, trader_data = self.run_child(name, state, sub_memory.get(name, ""))
            outputs[name] = orders
            next_sub_memory[name] = trader_data

        mids = self.mids(state.order_depths)
        if isinstance(memory.get("late"), bool):
            late_regime = bool(memory["late"])
        else:
            late_regime = self.is_late_regime(mids)
        rescue_memory = memory.get("r", {}) if isinstance(memory.get("r", {}), dict) else {}
        next_rescue_memory: Dict[str, Dict[str, float]] = {}

        result: Dict[str, List[Order]] = {}
        for product in state.order_depths:
            if self.should_skip_product(product, late_regime):
                continue
            if self.should_rescue_product(product, late_regime):
                rescue_orders, product_memory = self.trade_rescue_product(
                    product,
                    state,
                    mids,
                    rescue_memory.get(product, {}),
                )
                next_rescue_memory[product] = product_memory
                if rescue_orders:
                    result[product] = self.cap_orders(product, rescue_orders, state)
                continue
            source = self.PRODUCT_SOURCE.get(product, self.DEFAULT_PRODUCT_SOURCE)
            orders = outputs.get(source, {}).get(product, [])
            safe_orders = self.cap_orders(product, orders, state)
            if safe_orders:
                result[product] = safe_orders

        morning_orders: List[Order] = []
        oxygen_memory = memory.get("om", {})
        if not self.should_rescue_product(self.OXYGEN_MORNING, late_regime):
            morning_orders, oxygen_memory = self.trade_oxygen_morning(state, oxygen_memory)
        if morning_orders:
            result[self.OXYGEN_MORNING] = self.cap_orders(self.OXYGEN_MORNING, morning_orders, state)

        next_memory = {"s": next_sub_memory, "om": oxygen_memory, "r": next_rescue_memory, "late": late_regime}
        return result, 0, json.dumps(next_memory, separators=(",", ":"))

    def should_rescue_product(self, product: str, late_regime: bool) -> bool:
        return (
            product in self.RESCUE_ALWAYS
            or (not late_regime and product in self.RESCUE_NORMAL)
            or (late_regime and product in self.RESCUE_LATE_REGIME)
        )

    def should_skip_product(self, product: str, late_regime: bool) -> bool:
        return product in self.NO_TRADE_ALWAYS or (
            late_regime and product in self.NO_TRADE_LATE_REGIME
        )

    def run_child(self, name: str, state: TradingState, trader_data: str) -> Tuple[Dict[str, List[Order]], str]:
        original_data = state.traderData
        state.traderData = trader_data if isinstance(trader_data, str) else ""
        try:
            orders, _, next_data = self.traders[name].run(state)
        except Exception:
            orders, next_data = {}, trader_data if isinstance(trader_data, str) else ""
        finally:
            state.traderData = original_data
        return orders if isinstance(orders, dict) else {}, next_data if isinstance(next_data, str) else ""

    def cap_orders(self, product: str, orders: List[Order], state: TradingState) -> List[Order]:
        position = state.position.get(product, 0)
        buy_room = max(0, self.POSITION_LIMIT - position)
        sell_room = max(0, self.POSITION_LIMIT + position)
        capped: List[Order] = []

        for order in orders:
            quantity = order.quantity
            if quantity > 0 and buy_room > 0:
                allowed = min(quantity, buy_room)
                capped.append(Order(product, order.price, allowed))
                buy_room -= allowed
            elif quantity < 0 and sell_room > 0:
                allowed = min(-quantity, sell_room)
                capped.append(Order(product, order.price, -allowed))
                sell_room -= allowed

        return self.merge_and_trim_orders(capped)

    def is_late_regime(self, mids: Dict[str, float]) -> bool:
        robot_mids = [mids[product] for product in self.ROBOT_GROUP if product in mids]
        if len(robot_mids) < len(self.ROBOT_GROUP):
            return False
        mean = sum(robot_mids) / len(robot_mids)
        if mean <= 0:
            return False
        spread_ratio = (max(robot_mids) - min(robot_mids)) / mean
        return spread_ratio > 0.12

    def trade_rescue_product(
        self,
        product: str,
        state: TradingState,
        mids: Dict[str, float],
        memory: Dict,
    ) -> Tuple[List[Order], Dict[str, float]]:
        mid = mids.get(product)
        depth = state.order_depths.get(product)
        if mid is None or depth is None or not depth.buy_orders or not depth.sell_orders:
            return [], memory if isinstance(memory, dict) else {}

        anchor = float(memory.get("a", mid)) if isinstance(memory, dict) else mid
        ema = float(memory.get("e", mid)) if isinstance(memory, dict) else mid
        vol = float(memory.get("v", 0.0)) if isinstance(memory, dict) else 0.0
        prev = float(memory.get("p", mid)) if isinstance(memory, dict) else mid
        move = mid - prev
        next_vol = 0.94 * vol + 0.06 * abs(move)
        next_ema = 0.985 * ema + 0.015 * mid
        next_memory = {"a": anchor, "e": next_ema, "v": next_vol, "p": mid}

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid
        if spread < 2:
            return [], next_memory

        trend = mid - anchor
        short_trend = mid - next_ema
        threshold = max(18.0, 2.2 * max(next_vol, 1.0))
        if abs(trend) < threshold and abs(short_trend) < threshold:
            return [], next_memory

        signal = trend + 0.6 * short_trend
        position = state.position.get(product, 0)
        qty = 2
        if abs(signal) > 4.0 * threshold:
            qty = 3

        if signal > 0 and position < self.POSITION_LIMIT:
            price = best_bid + 1 if best_bid + 1 < best_ask else best_bid
            return [Order(product, price, min(qty, self.POSITION_LIMIT - position))], next_memory
        if signal < 0 and position > -self.POSITION_LIMIT:
            price = best_ask - 1 if best_ask - 1 > best_bid else best_ask
            return [Order(product, price, -min(qty, self.POSITION_LIMIT + position))], next_memory
        return [], next_memory

    def merge_and_trim_orders(self, orders: List[Order]) -> List[Order]:
        if not orders:
            return []

        merged: Dict[Tuple[int, int], int] = {}
        order_keys: List[Tuple[int, int]] = []
        for order in orders:
            side = 1 if order.quantity > 0 else -1
            key = (order.price, side)
            if key not in merged:
                order_keys.append(key)
                merged[key] = 0
            merged[key] += order.quantity

        result: List[Order] = []
        for price, side in order_keys:
            quantity = merged[(price, side)]
            if quantity:
                result.append(Order(orders[0].symbol, price, quantity))
            if len(result) >= self.MAX_ORDERS_PER_PRODUCT:
                break
        return result

    def trade_oxygen_morning(self, state: TradingState, memory: Dict) -> Tuple[List[Order], Dict]:
        mids = self.mids(state.order_depths)
        product = self.OXYGEN_MORNING
        depth = state.order_depths.get(product)
        if product not in mids or depth is None or not depth.buy_orders or not depth.sell_orders:
            return [], memory if isinstance(memory, dict) else {}
        if not all(product_name in mids for product_name in self.OXYGEN_GROUP):
            return [], memory if isinstance(memory, dict) else {}

        values = [mids[product_name] for product_name in self.OXYGEN_GROUP]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        std = math.sqrt(variance)
        if std <= 0:
            return [], memory if isinstance(memory, dict) else {}

        z = (mids[product] - mean) / std
        prev = float(memory.get("p", mids[product])) if isinstance(memory, dict) else mids[product]
        move = mids[product] - prev
        signal = -z - 0.20 * move / max(std, 1.0)
        next_memory = {"p": mids[product]}

        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        spread = best_ask - best_bid
        if spread < 3 or abs(signal) < 0.8:
            return [], next_memory

        position = state.position.get(product, 0)
        limit = 35
        qty = min(4, max(1, spread // 3))
        if signal > 0 and position < limit:
            return [Order(product, best_bid + 1, min(qty, limit - position))], next_memory
        if signal < 0 and position > -limit:
            return [Order(product, best_ask - 1, -min(qty, limit + position))], next_memory
        return [], next_memory

    def mids(self, depths: Dict[str, OrderDepth]) -> Dict[str, float]:
        mids: Dict[str, float] = {}
        for product, depth in depths.items():
            if depth is not None and depth.buy_orders and depth.sell_orders:
                mids[product] = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
        return mids

    def load_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {}
        try:
            parsed = json.loads(trader_data)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
