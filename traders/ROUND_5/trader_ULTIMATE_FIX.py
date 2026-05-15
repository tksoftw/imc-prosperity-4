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
    """Meta-router across sub-strategies, retuned for a 10-unit product cap.

    The imported children were originally sized for much larger inventories
    (40-250 units). This wrapper keeps the same routing but rewrites each
    child's limits, max order sizes, maker volume scales, and overlay sizes so
    the child logic natively behaves like a 10-limit strategy before the final
    hard cap runs.
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
    POSITION_LIMIT = 10
    MAX_ORDERS_PER_PRODUCT = 10
    DEFAULT_MAX_ORDER_SIZE = 3
    SOURCE_MAX_ORDER_SIZE = {
        "base": 4,
        "group": 2,
        "intro_group": 3,
        "intro_market": 2,
        "v2": 2,
        "v3": 2,
        "v4": 2,
        "v6": 2,
        "combined": 3,
        "oxygen_morning": 2,
    }
    MAKER_VOL_SCALE = 0.18
    PAIR_OVERLAY_SIZE = 3

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
        self.configure_child_traders()

    def configure_child_traders(self) -> None:
        base = self.traders["base"]
        base.CAP = self.POSITION_LIMIT
        base.MAX_ORDER = 4
        base.ROBOT_DISHES_BY_DAY = {2: 10, 3: 10, 4: 10}
        base.PAIR_LIMIT = self.POSITION_LIMIT
        base.PAIR_ORDER_SIZE = 3
        base.LONG_CAPS_BY_DAY = {
            2: {"PEBBLES_S": -10, "PEBBLES_XS": -10},
            3: {"PEBBLES_S": -10, "PEBBLES_XS": -10},
            4: {"PEBBLES_S": -10, "PEBBLES_XS": -10},
        }
        base.PEBBLES_TARGETS_BY_DAY = {4: {"PEBBLES_S": -10, "PEBBLES_XS": -10}}

        group = self.traders["group"]
        group.GROUP_LIMIT = {name: self.POSITION_LIMIT for name in group.GROUP_LIMIT}
        group.ORDER_SIZE = 2

        intro_group = self.traders["intro_group"]
        intro_group.LIMIT = self.POSITION_LIMIT
        intro_group.MAX_ORDER = 3
        intro_group.PASSIVE_QTY = 2
        intro_group.PASSIVE_INVENTORY = 8

        intro_market = self.traders["intro_market"]
        intro_market.STRATEGY_PARAMS = self.limit_param_dicts(
            intro_market.STRATEGY_PARAMS,
            max_vol_scale=self.MAKER_VOL_SCALE,
        )

        for name in ("v2", "v4", "v6", "combined"):
            child = self.traders[name]
            child.FAMILIES = self.limit_param_dicts(child.FAMILIES, max_vol_scale=self.MAKER_VOL_SCALE)

        v3 = self.traders["v3"]
        v3.GROUPS = self.limit_param_dicts(v3.GROUPS, max_vol_scale=self.MAKER_VOL_SCALE)

        for name in ("v6", "combined"):
            child = self.traders[name]
            if hasattr(child, "WINNER_PRODUCTS"):
                child.WINNER_PRODUCTS = self.cap_winner_multipliers(child.WINNER_PRODUCTS)

        combined = self.traders["combined"]
        combined.OVERLAY_PER_PAIR = self.PAIR_OVERLAY_SIZE
        combined.Z_ENTRY = 1.25

    def limit_param_dicts(self, configs: Dict[str, Dict], max_vol_scale: float) -> Dict[str, Dict]:
        limited: Dict[str, Dict] = {}
        for name, config in configs.items():
            tuned = dict(config)
            if "max_pos" in tuned:
                tuned["max_pos"] = min(self.POSITION_LIMIT, int(tuned["max_pos"]))
            if "vol_scale" in tuned:
                tuned["vol_scale"] = min(max_vol_scale, max(0.05, float(tuned["vol_scale"]) * 0.25))
            limited[name] = tuned
        return limited

    def cap_winner_multipliers(self, configs: Dict[str, Dict]) -> Dict[str, Dict]:
        limited: Dict[str, Dict] = {}
        for product, config in configs.items():
            tuned = dict(config)
            tuned["max_pos_mult"] = min(1.0, float(tuned.get("max_pos_mult", 1.0)))
            tuned["vol_mult"] = min(1.0, float(tuned.get("vol_mult", 1.0)))
            limited[product] = tuned
        return limited

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
            source = self.PRODUCT_SOURCE.get(product, self.DEFAULT_PRODUCT_SOURCE)
            orders = outputs.get(source, {}).get(product, [])
            safe_orders = self.cap_orders(product, orders, state, source)
            if safe_orders:
                result[product] = safe_orders

        morning_orders, oxygen_memory = self.trade_oxygen_morning(state, memory.get("om", {}))
        if morning_orders:
            result[self.OXYGEN_MORNING] = self.cap_orders(
                self.OXYGEN_MORNING,
                morning_orders,
                state,
                "oxygen_morning",
            )

        next_memory = {"s": next_sub_memory, "om": oxygen_memory}
        return result, 0, json.dumps(next_memory, separators=(",", ":"))

    def run_child(self, name: str, state: TradingState, trader_data: str) -> Tuple[Dict[str, List[Order]], str]:
        original_data = state.traderData
        state.traderData = trader_data if isinstance(trader_data, str) else ""
        try:
            child = self.traders[name]
            if hasattr(child, "position") and isinstance(child.position, dict):
                child.position = {product: state.position.get(product, 0) for product in state.order_depths}
            orders, _, next_data = child.run(state)
        except Exception:
            orders, next_data = {}, trader_data if isinstance(trader_data, str) else ""
        finally:
            state.traderData = original_data
        return orders if isinstance(orders, dict) else {}, next_data if isinstance(next_data, str) else ""

    def cap_orders(
        self,
        product: str,
        orders: List[Order],
        state: TradingState,
        source: str = "",
    ) -> List[Order]:
        position = state.position.get(product, 0)
        max_order_size = self.SOURCE_MAX_ORDER_SIZE.get(source, self.DEFAULT_MAX_ORDER_SIZE)
        buy_room = min(max_order_size, max(0, self.POSITION_LIMIT - position))
        sell_room = min(max_order_size, max(0, self.POSITION_LIMIT + position))
        capped: List[Order] = []

        for order in orders:
            quantity = order.quantity
            if quantity > 0 and buy_room > 0:
                allowed = min(quantity, buy_room, max_order_size)
                capped.append(Order(product, order.price, allowed))
                buy_room -= allowed
            elif quantity < 0 and sell_room > 0:
                allowed = min(-quantity, sell_room, max_order_size)
                capped.append(Order(product, order.price, -allowed))
                sell_room -= allowed

        return self.merge_and_trim_orders(capped)

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
        limit = self.POSITION_LIMIT
        qty = min(2, max(1, spread // 4))
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
