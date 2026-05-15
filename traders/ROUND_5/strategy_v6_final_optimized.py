from datamodel import OrderDepth, UserId, TradingState, Order
from typing import Dict

class Trader:
    """
    V6: Final optimized version based on v4.

    Refinements:
    1. Better momentum calculation with acceleration detection
    2. More aggressive on liquid, wide-spread products
    3. More conservative on problem products
    4. Dynamic position limits based on volatility
    5. Multi-level momentum response
    """

    FAMILIES = {
        'SNACKPACK': {
            'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY',
                        'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'],
            'base_bid': -2, 'base_ask': 2, 'max_pos': 120, 'vol_scale': 1.2
        },
        'GALAXY_SOUNDS': {
            'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER',
                        'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'],
            'base_bid': -4, 'base_ask': 4, 'max_pos': 105, 'vol_scale': 1.05
        },
        'OXYGEN_SHAKE': {
            'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH',
                        'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'],
            'base_bid': -4, 'base_ask': 4, 'max_pos': 100, 'vol_scale': 1.05
        },
        'UV_VISOR': {
            'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE',
                        'UV_VISOR_RED', 'UV_VISOR_YELLOW'],
            'base_bid': -4, 'base_ask': 4, 'max_pos': 95, 'vol_scale': 1.05
        },
        'PEBBLES': {
            'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'],
            'base_bid': -4, 'base_ask': 4, 'max_pos': 90, 'vol_scale': 1.0
        },
        'PANEL': {
            'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'],
            'base_bid': -3, 'base_ask': 3, 'max_pos': 75, 'vol_scale': 0.95
        },
        'SLEEP_POD': {
            'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON',
                        'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'],
            'base_bid': -3, 'base_ask': 3, 'max_pos': 70, 'vol_scale': 0.9
        },
        'MICROCHIP': {
            'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE',
                        'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'],
            'base_bid': -2, 'base_ask': 2, 'max_pos': 55, 'vol_scale': 0.8
        },
        'TRANSLATOR': {
            'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL',
                        'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'],
            'base_bid': -2, 'base_ask': 2, 'max_pos': 60, 'vol_scale': 0.8
        },
        'ROBOT': {
            'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY',
                        'ROBOT_MOPPING', 'ROBOT_VACUUMING'],
            'base_bid': -2, 'base_ask': 2, 'max_pos': 50, 'vol_scale': 0.65
        },
    }

    # Aggressive: known winners
    WINNER_PRODUCTS = {
        'SNACKPACK_RASPBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'SNACKPACK_STRAWBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'SNACKPACK_VANILLA': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'TRANSLATOR_ECLIPSE_CHARCOAL': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'TRANSLATOR_VOID_BLUE': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'UV_VISOR_MAGENTA': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'UV_VISOR_RED': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
        'UV_VISOR_YELLOW': {'max_pos_mult': 1.1, 'vol_mult': 1.1},
    }

    # Conservative: known losers
    LOSER_PRODUCTS = {
        'SLEEP_POD_LAMB_WOOL': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8},
        'SLEEP_POD_NYLON': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8},
        'SNACKPACK_CHOCOLATE': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4},
        'SNACKPACK_PISTACHIO': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4},
        'TRANSLATOR_ASTRO_BLACK': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6},
        'TRANSLATOR_SPACE_GRAY': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6},
        'UV_VISOR_AMBER': {'max_pos_mult': 0.3, 'vol_mult': 0.3, 'offset_mult': 1.8},
        'UV_VISOR_ORANGE': {'max_pos_mult': 0.5, 'vol_mult': 0.5, 'offset_mult': 1.5},
    }

    def __init__(self):
        self.position = {}
        self.prices = {}
        self.volatility = {}

    def bid(self):
        return 15

    def get_family(self, product: str) -> Dict:
        for family_name, family_data in self.FAMILIES.items():
            if product in family_data['products']:
                return family_data
        return self.FAMILIES['ROBOT']

    def calculate_momentum(self, product: str, current_mid: float) -> int:
        """Calculate momentum: -2 (strong down), -1 (down), 0 (flat), 1 (up), 2 (strong up)"""
        if product not in self.prices:
            self.prices[product] = []

        self.prices[product].append(current_mid)
        if len(self.prices[product]) > 20:
            self.prices[product].pop(0)

        if len(self.prices[product]) < 6:
            return 0

        # 3-period trend
        recent_3 = sum(self.prices[product][-3:]) / 3
        prev_3 = sum(self.prices[product][-6:-3]) / 3
        base_trend = 1 if recent_3 > prev_3 else (-1 if recent_3 < prev_3 else 0)

        # Acceleration check (6-period window)
        if len(self.prices[product]) >= 12 and base_trend != 0:
            prev_6 = sum(self.prices[product][-12:-6]) / 6
            curr_6 = sum(self.prices[product][-6:]) / 6
            acceleration = 1 if (curr_6 - prev_6) * base_trend > 0 else 0
            return base_trend + acceleration

        return base_trend

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        for product in state.order_depths.keys():
            if product not in self.position:
                self.position[product] = 0

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            family = self.get_family(product)
            orders = []

            # Calculate mid price
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
            mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask

            # Get momentum
            momentum = self.calculate_momentum(product, mid)

            # Base parameters
            bid_offset = family['base_bid']
            ask_offset = family['base_ask']
            max_pos = family['max_pos']
            vol_scale = family['vol_scale']

            # Apply winner adjustments
            if product in self.WINNER_PRODUCTS:
                adj = self.WINNER_PRODUCTS[product]
                max_pos = int(max_pos * adj['max_pos_mult'])
                vol_scale = vol_scale * adj['vol_mult']

            # Apply loser adjustments
            elif product in self.LOSER_PRODUCTS:
                adj = self.LOSER_PRODUCTS[product]
                max_pos = int(max_pos * adj['max_pos_mult'])
                vol_scale = vol_scale * adj['vol_mult']
                bid_offset = int(bid_offset * adj['offset_mult'])
                ask_offset = int(ask_offset * adj['offset_mult'])

            # Momentum adjustments: more aggressive = tighter spread
            if momentum >= 2:  # Strong uptrend
                bid_offset = max(bid_offset - 2, -6)
                ask_offset = max(ask_offset - 1, -4)
            elif momentum == 1:  # Uptrend
                bid_offset = max(bid_offset - 1, -5)
            elif momentum <= -2:  # Strong downtrend
                ask_offset = min(ask_offset + 2, 6)
                bid_offset = min(bid_offset + 1, 4)
            elif momentum == -1:  # Downtrend
                ask_offset = min(ask_offset + 1, 5)

            # Calculate volumes
            bid_vol = max(1, int((list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10) * vol_scale))
            ask_vol = max(1, int((list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10) * vol_scale))

            # Place orders
            bid_price = int(mid) + bid_offset
            ask_price = int(mid) + ask_offset

            current_pos = self.position[product]

            if current_pos < max_pos:
                orders.append(Order(product, bid_price, bid_vol))

            if current_pos > -max_pos:
                orders.append(Order(product, ask_price, ask_vol))

            result[product] = orders

        return result, conversions, ""
