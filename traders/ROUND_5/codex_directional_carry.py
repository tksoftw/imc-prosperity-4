from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List


class Trader:
    TARGETS = {
        "GALAXY_SOUNDS_BLACK_HOLES": 20,
        "GALAXY_SOUNDS_DARK_MATTER": 12,
        "GALAXY_SOUNDS_PLANETARY_RINGS": 16,
        "GALAXY_SOUNDS_SOLAR_FLAMES": 16,
        "GALAXY_SOUNDS_SOLAR_WINDS": 8,
        "MICROCHIP_CIRCLE": -10,
        "MICROCHIP_OVAL": -20,
        "MICROCHIP_RECTANGLE": -16,
        "MICROCHIP_SQUARE": 20,
        "MICROCHIP_TRIANGLE": -14,
        "OXYGEN_SHAKE_EVENING_BREATH": -16,
        "OXYGEN_SHAKE_GARLIC": 20,
        "PANEL_1X2": -16,
        "PANEL_1X4": -10,
        "PANEL_2X2": -12,
        "PANEL_2X4": 20,
        "PANEL_4X4": -10,
        "PEBBLES_L": 6,
        "PEBBLES_M": 8,
        "PEBBLES_S": -16,
        "PEBBLES_XL": 20,
        "PEBBLES_XS": -20,
        "ROBOT_DISHES": 10,
        "ROBOT_IRONING": -20,
        "ROBOT_LAUNDRY": -10,
        "ROBOT_MOPPING": 18,
        "ROBOT_VACUUMING": -16,
        "SLEEP_POD_COTTON": 18,
        "SLEEP_POD_LAMB_WOOL": 12,
        "SLEEP_POD_NYLON": -8,
        "SLEEP_POD_POLYESTER": 20,
        "SLEEP_POD_SUEDE": 20,
        "SNACKPACK_CHOCOLATE": -8,
        "SNACKPACK_PISTACHIO": -16,
        "SNACKPACK_RASPBERRY": 8,
        "SNACKPACK_STRAWBERRY": 16,
        "SNACKPACK_VANILLA": 6,
        "TRANSLATOR_ASTRO_BLACK": -14,
        "TRANSLATOR_ECLIPSE_CHARCOAL": -8,
        "TRANSLATOR_GRAPHITE_MIST": 4,
        "TRANSLATOR_SPACE_GRAY": -16,
        "TRANSLATOR_VOID_BLUE": 18,
        "UV_VISOR_AMBER": -20,
        "UV_VISOR_MAGENTA": 18,
        "UV_VISOR_ORANGE": -4,
        "UV_VISOR_RED": 16,
        "UV_VISOR_YELLOW": 16,
    }
    SCALE = 1.15
    CAP = 25
    MAX_ORDER = 10

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        for product, raw_target in self.TARGETS.items():
            target = int(round(raw_target * self.SCALE))
            target = max(-self.CAP, min(self.CAP, target))
            depth = state.order_depths.get(product)
            if depth is None or not depth.buy_orders or not depth.sell_orders:
                continue
            position = state.position.get(product, 0)
            delta = target - position
            if delta == 0:
                continue
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            if delta > 0:
                quantity = min(delta, self.MAX_ORDER, abs(depth.sell_orders[best_ask]))
                if quantity > 0:
                    result[product] = [Order(product, best_ask, quantity)]
            else:
                quantity = min(-delta, self.MAX_ORDER, abs(depth.buy_orders[best_bid]))
                if quantity > 0:
                    result[product] = [Order(product, best_bid, -quantity)]
        return result, 0, ""
