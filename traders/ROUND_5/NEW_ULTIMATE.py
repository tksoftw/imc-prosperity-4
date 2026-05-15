"""Filtered meta-strategy fork of ``codex_ULTIMATE``: same sub-strategy pool, but
(1) per-product inventory is clipped to ``POSITION_CAP`` (±10) when converting
child orders into submissions, individual order sides are clipped to ±10 qty,
and (2) instruments in ``SKIP_PRODUCTS`` are left flat — no delegated orders."""

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
    POSITION_CAP = 10
    ORDER_QTY_CAP = 10
    MAX_ORDER_LINES_PER_PRODUCT = 10

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
        "PEBBLES_L": "base",
        "PEBBLES_M": "base",
        "PEBBLES_S": "base",
        "PEBBLES_XL": "base",
        "PEBBLES_XS": "base",
        "ROBOT_DISHES": "intro_group",
        "ROBOT_IRONING": "intro_group",
        "ROBOT_LAUNDRY": "intro_group",
        "SNACKPACK_PISTACHIO": "intro_group",
        "SNACKPACK_RASPBERRY": "intro_group",
        "SNACKPACK_STRAWBERRY": "intro_group",
        "SNACKPACK_CHOCOLATE": "group",
        "SNACKPACK_VANILLA": "group",
        "OXYGEN_SHAKE_CHOCOLATE": "group",
        "PANEL_1X2": "group",
        "PANEL_1X4": "group",
        "TRANSLATOR_ECLIPSE_CHARCOAL": "group",
        "UV_VISOR_AMBER": "group",
        "GALAXY_SOUNDS_DARK_MATTER": "intro_market",
        "OXYGEN_SHAKE_MINT": "intro_market",
        "TRANSLATOR_VOID_BLUE": "intro_market",
        "UV_VISOR_YELLOW": "intro_market",
        "SLEEP_POD_POLYESTER": "v2",
        "UV_VISOR_MAGENTA": "v2",
        "PANEL_2X4": "v3",
        "UV_VISOR_RED": "v3",
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

    # Instruments with strictly negative summed carry-PnL in the baseline run (skipped flat).
    SKIP_PRODUCTS: Tuple[str, ...] = (
        "MICROCHIP_OVAL",
        "ROBOT_VACUUMING",
        "GALAXY_SOUNDS_SOLAR_WINDS",
        "MICROCHIP_TRIANGLE",
        "TRANSLATOR_SPACE_GRAY",
        "MICROCHIP_RECTANGLE",
        "MICROCHIP_CIRCLE",
        "TRANSLATOR_ASTRO_BLACK",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "SLEEP_POD_NYLON",
        "PANEL_4X4",
        "SNACKPACK_PISTACHIO",
    )

    OXYGEN_MORNING = "OXYGEN_SHAKE_MORNING_BREATH"
    OXYGEN_GROUP = (
        "OXYGEN_SHAKE_CHOCOLATE",
        "OXYGEN_SHAKE_EVENING_BREATH",
        "OXYGEN_SHAKE_GARLIC",
        "OXYGEN_SHAKE_MINT",
        "OXYGEN_SHAKE_MORNING_BREATH",
    )

    def __init__(self):
        self._skip = frozenset(self.SKIP_PRODUCTS)
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

        result: Dict[str, List[Order]] = {}
        for product in state.order_depths:
            if product in self._skip:
                continue
            source = self.PRODUCT_SOURCE.get(product, self.DEFAULT_PRODUCT_SOURCE)
            orders = outputs.get(source, {}).get(product, [])
            capped = self.cap_orders(product, orders, state)
            if capped:
                result[product] = capped

        morning_orders: List[Order] = []
        oxygen_memory = memory.get("om", {})
        if self.OXYGEN_MORNING not in self._skip:
            morning_orders, oxygen_memory = self.trade_oxygen_morning(state, oxygen_memory)
        if morning_orders:
            result[self.OXYGEN_MORNING] = self.cap_orders(self.OXYGEN_MORNING, morning_orders, state)

        next_memory = {"s": next_sub_memory, "om": oxygen_memory}
        return result, 0, json.dumps(next_memory, separators=(",", ":"))

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
        if not orders:
            return []
        position = state.position.get(product, 0)
        buy_room = max(0, self.POSITION_CAP - position)
        sell_room = max(0, self.POSITION_CAP + position)
        clipped: List[Order] = []

        for order in orders:
            q = order.quantity
            if q > 0:
                take = min(q, self.ORDER_QTY_CAP, buy_room)
                if take > 0:
                    clipped.append(Order(product, order.price, take))
                    buy_room -= take
                    position += take
            elif q < 0:
                take = min(-q, self.ORDER_QTY_CAP, sell_room)
                if take > 0:
                    clipped.append(Order(product, order.price, -take))
                    sell_room -= take
                    position -= take

        return self.merge_order_lines(product, clipped)

    def merge_order_lines(self, product: str, orders: List[Order]) -> List[Order]:
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

        out: List[Order] = []
        for price, side in order_keys:
            qty = merged[(price, side)]
            if qty:
                out.append(Order(product, price, qty))
            if len(out) >= self.MAX_ORDER_LINES_PER_PRODUCT:
                break
        return out

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
        limit = self.POSITION_CAP
        qty = min(self.ORDER_QTY_CAP, max(1, spread // 3))
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
