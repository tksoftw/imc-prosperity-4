from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict
import statistics

class Trader:
    """
    Intro strategy: Group-specific market makers based on spread + volume characteristics.

    Each product group gets parameters tuned to its market characteristics:
    - High spread groups: tighter spreads, larger volumes
    - Low spread groups: selective entry, momentum overlay
    """

    # Product group definitions
    PRODUCT_GROUPS = {
        'SNACKPACK': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'],
        'GALAXY_SOUNDS': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'],
        'UV_VISOR': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'],
        'OXYGEN_SHAKE': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'],
        'PEBBLES': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'],
        'PANEL': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'],
        'SLEEP_POD': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'],
        'MICROCHIP': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'],
        'TRANSLATOR': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'],
        'ROBOT': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'],
    }

    # Strategy parameters by group: (bid_offset, ask_offset, max_position, volume_scale)
    STRATEGY_PARAMS = {
        'SNACKPACK': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 100, 'vol_scale': 1.0},      # High spread, high vol - tight maker
        'GALAXY_SOUNDS': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9},    # Good spread, good vol
        'UV_VISOR': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9},         # Similar to GALAXY_SOUNDS
        'OXYGEN_SHAKE': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9},     # Good spread, good vol
        'PEBBLES': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.8},          # Slightly lower vol
        'PANEL': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 60, 'vol_scale': 0.8},            # Tighter spread, selective
        'SLEEP_POD': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.7},        # Lower volume group
        'MICROCHIP': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 40, 'vol_scale': 0.6},        # Tight spread, low vol - selective
        'TRANSLATOR': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 60, 'vol_scale': 0.7},       # Tight spread, medium vol
        'ROBOT': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 40, 'vol_scale': 0.5},            # Tightest spread, lowest vol - very selective
    }

    def __init__(self):
        self.position = {}  # Track position per product
        self.mid_price_history = {}  # Track mid prices for momentum

    def bid(self):
        return 15

    def get_product_group(self, product: str) -> str:
        """Find which group a product belongs to."""
        for group, products in self.PRODUCT_GROUPS.items():
            if product in products:
                return group
        return None

    def calculate_bid_ask_volume(self, order_depth: OrderDepth, mid_price: float, product: str) -> tuple:
        """Calculate bid/ask levels and volumes based on market conditions."""
        group = self.get_product_group(product)
        params = self.STRATEGY_PARAMS.get(group, self.STRATEGY_PARAMS['ROBOT'])

        # Get best bid/ask from market
        best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else mid_price - 5
        best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else mid_price + 5

        # Calculate our desired levels
        bid_price = int(mid_price) + params['bid_offset']
        ask_price = int(mid_price) + params['ask_offset']

        # Scale volume based on available liquidity
        market_bid_vol = order_depth.buy_orders.get(best_bid, 0) if order_depth.buy_orders else 10
        market_ask_vol = order_depth.sell_orders.get(best_ask, 0) if order_depth.sell_orders else 10

        bid_volume = max(1, int(market_bid_vol * params['vol_scale']))
        ask_volume = max(1, int(market_ask_vol * params['vol_scale']))

        return bid_price, ask_price, bid_volume, ask_volume

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        # Initialize positions if needed
        for product in state.order_depths.keys():
            if product not in self.position:
                self.position[product] = 0

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            group = self.get_product_group(product)

            if not group:
                continue

            params = self.STRATEGY_PARAMS[group]
            orders = []

            # Calculate mid price
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
            mid_price = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask

            # Get position
            current_pos = self.position[product]

            # Calculate bid/ask
            bid_price, ask_price, bid_volume, ask_volume = self.calculate_bid_ask_volume(order_depth, mid_price, product)

            # Only trade if position allows
            if current_pos < params['max_pos']:
                orders.append(Order(product, bid_price, bid_volume))

            if current_pos > -params['max_pos']:
                orders.append(Order(product, ask_price, ask_volume))

            result[product] = orders

        return result, conversions, ""
