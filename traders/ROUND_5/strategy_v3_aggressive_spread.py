from datamodel import OrderDepth, UserId, TradingState, Order
from typing import Dict

class Trader:
    """
    V3: Aggressive spread-harvesting strategy.

    Key insight from baseline analysis:
    - High spread products (SNACKPACK $16.79, GALAXY_SOUNDS $13.73, etc.) are excellent targets
    - Wide spreads allow profitable middle-placement
    - Volume concentrations can be harvested with position tracking

    Strategy:
    1. Exploit wide spreads by placing tighter bids/asks
    2. Scale position limits by group liquidity
    3. Aggressive position taking in liquid products
    """

    # Classify products by spread magnitude
    GROUPS = {
        # Ultra-wide spread, very liquid: SNACKPACK
        'SNACKPACK': {
            'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY',
                        'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'],
            'bid_offset': -1, 'ask_offset': 1,  # Very tight - capture spread
            'max_pos': 120, 'vol_scale': 1.2,
            'level': 'ULTRA_LIQUID'
        },
        # Wide spread, liquid
        'GALAXY_SOUNDS': {
            'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER',
                        'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'],
            'bid_offset': -4, 'ask_offset': 4, 'max_pos': 90, 'vol_scale': 1.0,
            'level': 'HIGH_LIQUID'
        },
        'OXYGEN_SHAKE': {
            'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH',
                        'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'],
            'bid_offset': -4, 'ask_offset': 4, 'max_pos': 90, 'vol_scale': 1.0,
            'level': 'HIGH_LIQUID'
        },
        'UV_VISOR': {
            'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE',
                        'UV_VISOR_RED', 'UV_VISOR_YELLOW'],
            'bid_offset': -4, 'ask_offset': 4, 'max_pos': 85, 'vol_scale': 0.95,
            'level': 'HIGH_LIQUID'
        },
        'PEBBLES': {
            'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'],
            'bid_offset': -4, 'ask_offset': 4, 'max_pos': 80, 'vol_scale': 0.9,
            'level': 'MED_LIQUID'
        },
        # Medium spread, medium liquid
        'PANEL': {
            'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'],
            'bid_offset': -3, 'ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.85,
            'level': 'MED_LIQUID'
        },
        'SLEEP_POD': {
            'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON',
                        'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'],
            'bid_offset': -3, 'ask_offset': 3, 'max_pos': 60, 'vol_scale': 0.75,
            'level': 'MED_LIQUID'
        },
        # Tight spread, need selection
        'MICROCHIP': {
            'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE',
                        'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'],
            'bid_offset': -2, 'ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.7,
            'level': 'LOW_LIQUID'
        },
        'TRANSLATOR': {
            'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL',
                        'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'],
            'bid_offset': -2, 'ask_offset': 2, 'max_pos': 55, 'vol_scale': 0.65,
            'level': 'LOW_LIQUID'
        },
        'ROBOT': {
            'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY',
                        'ROBOT_MOPPING', 'ROBOT_VACUUMING'],
            'bid_offset': -2, 'ask_offset': 2, 'max_pos': 45, 'vol_scale': 0.55,
            'level': 'LOW_LIQUID'
        },
    }

    def __init__(self):
        self.position = {}

    def bid(self):
        return 15

    def get_group(self, product: str) -> Dict:
        """Find product group."""
        for group_name, group_data in self.GROUPS.items():
            if product in group_data['products']:
                return group_data
        return self.GROUPS['ROBOT']  # Default fallback

    def run(self, state: TradingState):
        result = {}
        conversions = 0

        for product in state.order_depths.keys():
            if product not in self.position:
                self.position[product] = 0

        for product in state.order_depths.keys():
            order_depth = state.order_depths[product]
            group = self.get_group(product)
            orders = []

            # Calculate mid price
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
            mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask

            # Get volumes
            market_bid_vol = list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10
            market_ask_vol = list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10

            bid_vol = max(1, int(market_bid_vol * group['vol_scale']))
            ask_vol = max(1, int(market_ask_vol * group['vol_scale']))

            # Place orders
            bid_price = int(mid) + group['bid_offset']
            ask_price = int(mid) + group['ask_offset']

            current_pos = self.position[product]
            max_pos = group['max_pos']

            if current_pos < max_pos:
                orders.append(Order(product, bid_price, bid_vol))

            if current_pos > -max_pos:
                orders.append(Order(product, ask_price, ask_vol))

            result[product] = orders

        return result, conversions, ""
