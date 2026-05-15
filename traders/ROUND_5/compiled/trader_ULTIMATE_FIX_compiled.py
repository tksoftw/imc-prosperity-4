# `uv run compile`; edit source & recompile. Needs datamodel on sys.path.

class __CompiledModule:
    pass

traders = __CompiledModule()
traders.ROUND_5 = __CompiledModule()

#+traders/ROUND_5/codex_group_starter.py
def __build_codex_group_starter():
    from datamodel import OrderDepth, TradingState, Order
    from typing import Dict, List

    class Trader:
        GROUPS = {'GALAXY_SOUNDS': ['GALAXY_SOUNDS_PLANETARY_RINGS'], 'MICROCHIP': ['MICROCHIP_TRIANGLE'], 'OXYGEN_SHAKE': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_MINT'], 'PANEL': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X4'], 'PEBBLES': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S'], 'ROBOT': ['ROBOT_DISHES', 'ROBOT_LAUNDRY'], 'SLEEP_POD': ['SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_SUEDE'], 'SNACKPACK': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_RASPBERRY', 'SNACKPACK_VANILLA'], 'TRANSLATOR': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL'], 'UV_VISOR': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA']}
        FULL_GROUPS = {'GALAXY_SOUNDS': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'MICROCHIP': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'OXYGEN_SHAKE': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'PANEL': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'PEBBLES': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'ROBOT': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'SLEEP_POD': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'SNACKPACK': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'TRANSLATOR': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'UV_VISOR': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW']}
        PRODUCT_GROUP = {product: group for group, products in FULL_GROUPS.items() for product in products}
        ENABLED = {product for products in GROUPS.values() for product in products}
        GROUP_DIRECTION = {'GALAXY_SOUNDS': 1, 'MICROCHIP': -1, 'OXYGEN_SHAKE': -1, 'PANEL': 1, 'PEBBLES': -1, 'ROBOT': -1, 'SLEEP_POD': -1, 'SNACKPACK': -1, 'TRANSLATOR': -1, 'UV_VISOR': 1}
        GROUP_SCALE = {'GALAXY_SOUNDS': 520.0, 'MICROCHIP': 900.0, 'OXYGEN_SHAKE': 620.0, 'PANEL': 620.0, 'PEBBLES': 1100.0, 'ROBOT': 620.0, 'SLEEP_POD': 650.0, 'SNACKPACK': 240.0, 'TRANSLATOR': 460.0, 'UV_VISOR': 650.0}
        GROUP_LIMIT = {'GALAXY_SOUNDS': 14, 'MICROCHIP': 14, 'OXYGEN_SHAKE': 14, 'PANEL': 14, 'PEBBLES': 16, 'ROBOT': 14, 'SLEEP_POD': 14, 'SNACKPACK': 16, 'TRANSLATOR': 16, 'UV_VISOR': 14}
        ORDER_SIZE = 4

        def run(self, state: TradingState):
            result: Dict[str, List[Order]] = {}
            mids = self.current_mids(state.order_depths)
            group_mid: Dict[str, float] = {}
            for group, products in self.FULL_GROUPS.items():
                available = [mids[p] for p in products if p in mids]
                if available:
                    group_mid[group] = sum(available) / len(available)
            for product in self.ENABLED:
                depth = state.order_depths.get(product)
                if depth is None or product not in mids:
                    continue
                group = self.PRODUCT_GROUP[product]
                if group not in group_mid:
                    continue
                orders = self.passive_quotes(product, group, mids[product], group_mid[group], depth, state)
                if orders:
                    result[product] = orders
            return (result, 0, '')

        def passive_quotes(self, product: str, group: str, mid: float, family_mid: float, depth: OrderDepth, state: TradingState) -> List[Order]:
            if not depth.buy_orders or not depth.sell_orders:
                return []
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            spread = best_ask - best_bid
            if spread < 3:
                return []
            residual = mid - family_mid
            bias = self.GROUP_DIRECTION[group] * residual / self.GROUP_SCALE[group]
            position = state.position.get(product, 0)
            limit = self.GROUP_LIMIT[group]
            orders: List[Order] = []
            buy_room = limit - position
            sell_room = limit + position
            buy_qty = min(self.ORDER_SIZE, buy_room, abs(depth.sell_orders[best_ask]))
            sell_qty = min(self.ORDER_SIZE, sell_room, abs(depth.buy_orders[best_bid]))
            if buy_qty > 0 and bias > -0.5:
                orders.append(Order(product, best_bid + 1, buy_qty))
            if sell_qty > 0 and bias < 0.5:
                orders.append(Order(product, best_ask - 1, -sell_qty))
            return orders

        def current_mids(self, depths: Dict[str, OrderDepth]) -> Dict[str, float]:
            mids: Dict[str, float] = {}
            for product, depth in depths.items():
                if depth.buy_orders and depth.sell_orders:
                    mids[product] = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
            return mids
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
codex_group_starter=__build_codex_group_starter()
setattr(traders.ROUND_5,'codex_group_starter',codex_group_starter)

#+traders/ROUND_5/codex_pair_regression_robot_alpha_capped.py
def __build_codex_pair_regression_robot_alpha_capped():
    from datamodel import OrderDepth, TradingState, Order
    from typing import Dict, List, Tuple
    import json

    class Trader:
        PEBBLES = ('PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS')
        BASE_TARGETS = {'GALAXY_SOUNDS_BLACK_HOLES': 20, 'GALAXY_SOUNDS_DARK_MATTER': 12, 'GALAXY_SOUNDS_PLANETARY_RINGS': 16, 'GALAXY_SOUNDS_SOLAR_FLAMES': 16, 'GALAXY_SOUNDS_SOLAR_WINDS': 8, 'MICROCHIP_CIRCLE': -10, 'MICROCHIP_OVAL': -20, 'MICROCHIP_RECTANGLE': -16, 'MICROCHIP_SQUARE': 20, 'MICROCHIP_TRIANGLE': -14, 'OXYGEN_SHAKE_EVENING_BREATH': -16, 'OXYGEN_SHAKE_GARLIC': 20, 'PANEL_1X2': -16, 'PANEL_1X4': -10, 'PANEL_2X2': -12, 'PANEL_2X4': 20, 'PANEL_4X4': -10, 'ROBOT_IRONING': -20, 'ROBOT_LAUNDRY': -10, 'ROBOT_MOPPING': 18, 'ROBOT_VACUUMING': -16, 'SLEEP_POD_COTTON': 18, 'SLEEP_POD_LAMB_WOOL': 12, 'SLEEP_POD_NYLON': -8, 'SLEEP_POD_POLYESTER': 20, 'SLEEP_POD_SUEDE': 20, 'SNACKPACK_CHOCOLATE': -8, 'SNACKPACK_PISTACHIO': -16, 'SNACKPACK_RASPBERRY': 8, 'SNACKPACK_STRAWBERRY': 16, 'SNACKPACK_VANILLA': 6, 'TRANSLATOR_ASTRO_BLACK': -14, 'TRANSLATOR_ECLIPSE_CHARCOAL': -8, 'TRANSLATOR_GRAPHITE_MIST': 4, 'TRANSLATOR_SPACE_GRAY': -16, 'TRANSLATOR_VOID_BLUE': 18, 'UV_VISOR_AMBER': -20, 'UV_VISOR_MAGENTA': 18, 'UV_VISOR_ORANGE': -4, 'UV_VISOR_RED': 16, 'UV_VISOR_YELLOW': 16}
        PAIR_MODELS: Dict[str, List[Tuple[str, float, float]]] = {'PEBBLES_S': [('PEBBLES_XL', -0.3914, 194.7), ('PEBBLES_XS', 0.4589, 123.35)], 'PEBBLES_M': [('PEBBLES_XS', -0.3293, -591.52), ('PEBBLES_S', -0.5082, -279.36)], 'PEBBLES_L': [('PEBBLES_M', 0.3035, 94.23)], 'PEBBLES_XL': [('PEBBLES_S', -1.7788, 1326.43), ('PEBBLES_XS', -1.0143, 593.22)], 'PEBBLES_XS': [('PEBBLES_XL', -0.6752, -417.3), ('PEBBLES_S', 1.3886, -1112.78)]}
        SCALE = 1.15
        CAP = 25
        MAX_ORDER = 10
        ROBOT_DISHES_BY_DAY = {2: 50, 3: 100, 4: 240}
        PAIR_LIMIT = 120
        PAIR_ORDER_SIZE = 8
        PAIR_EDGE = 4.0
        LONG_CAPS_BY_DAY = {2: {'PEBBLES_S': -60, 'PEBBLES_XS': -120}, 3: {'PEBBLES_S': -60, 'PEBBLES_XS': -120}, 4: {'PEBBLES_S': -180, 'PEBBLES_XS': -240}}
        PEBBLES_TARGETS_BY_DAY = {4: {'PEBBLES_S': -180, 'PEBBLES_XS': -230}}
        PAIR_SKIP_BY_DAY = {4: {'PEBBLES_S', 'PEBBLES_XS'}}

        def run(self, state: TradingState):
            memory = self.load_state(state.traderData)
            day = int(memory.get('day', 2))
            inferred_day = self.infer_day_from_pebbles_regime(state)
            if inferred_day is not None and inferred_day > day:
                day = inferred_day
            result: Dict[str, List[Order]] = {}
            for product, target in self.make_targets(day).items():
                orders = self.rebalance(product, target, state)
                if orders:
                    result[product] = orders
            pair_orders = self.pair_regression_orders(state, day)
            for product, orders in pair_orders.items():
                result[product] = result.get(product, []) + orders
            trader_data = json.dumps({'day': day}, separators=(',', ':'))
            return (result, 0, trader_data)

        def make_targets(self, day: int) -> Dict[str, int]:
            targets: Dict[str, int] = {}
            for product, raw_target in self.BASE_TARGETS.items():
                target = int(round(raw_target * self.SCALE))
                targets[product] = max(-self.CAP, min(self.CAP, target))
            targets['ROBOT_DISHES'] = self.ROBOT_DISHES_BY_DAY.get(day, 100)
            targets.update(self.PEBBLES_TARGETS_BY_DAY.get(day, {}))
            return targets

        def pair_regression_orders(self, state: TradingState, day: int) -> Dict[str, List[Order]]:
            mids = self.mids(state.order_depths)
            center = self.pebbles_center(mids)
            if center is None:
                return {}
            result: Dict[str, List[Order]] = {}
            skip_products = self.PAIR_SKIP_BY_DAY.get(day, set())
            for product, models in self.PAIR_MODELS.items():
                if product in skip_products:
                    continue
                depth = state.order_depths.get(product)
                if depth is None or not depth.buy_orders or (not depth.sell_orders):
                    continue
                fairs = []
                for reference, beta, intercept in models:
                    ref_mid = mids.get(reference)
                    if ref_mid is not None:
                        fairs.append(center + intercept + beta * (ref_mid - center))
                if not fairs:
                    continue
                fair = sum(fairs) / len(fairs)
                best_bid = max(depth.buy_orders)
                best_ask = min(depth.sell_orders)
                bid_price = best_bid + 1
                ask_price = best_ask - 1
                position = state.position.get(product, 0)
                long_cap = self.long_cap(product, day)
                buy_room = long_cap - position
                sell_room = self.PAIR_LIMIT + position
                orders: List[Order] = []
                if fair - bid_price >= self.PAIR_EDGE and buy_room > 0:
                    orders.append(Order(product, bid_price, min(self.PAIR_ORDER_SIZE, buy_room)))
                if ask_price - fair >= self.PAIR_EDGE and sell_room > 0:
                    orders.append(Order(product, ask_price, -min(self.PAIR_ORDER_SIZE, sell_room)))
                if orders:
                    result[product] = orders
            return result

        def long_cap(self, product: str, day: int) -> int:
            day_caps = self.LONG_CAPS_BY_DAY.get(day, self.LONG_CAPS_BY_DAY[3])
            return day_caps.get(product, self.PAIR_LIMIT)

        def infer_day_from_pebbles_regime(self, state: TradingState) -> int | None:
            mids = self.mids(state.order_depths)
            if any((mids.get(product) is None for product in self.PEBBLES)):
                return None
            ranking = sorted(self.PEBBLES, key=lambda product: mids[product], reverse=True)
            top_three = set(ranking[:3])
            bottom_two = set(ranking[-2:])
            if top_three == {'PEBBLES_L', 'PEBBLES_M', 'PEBBLES_XL'} and bottom_two == {'PEBBLES_S', 'PEBBLES_XS'}:
                return 4
            if ranking[0] == 'PEBBLES_XL' and ranking[-1] == 'PEBBLES_XS' and (ranking.index('PEBBLES_L') < min(ranking.index('PEBBLES_M'), ranking.index('PEBBLES_S'))):
                return 3
            return None

        def pebbles_center(self, mids: Dict[str, float]) -> float | None:
            values = [mids.get(product) for product in self.PEBBLES]
            if any((value is None for value in values)):
                return None
            return sum((float(value) for value in values)) / len(values)

        def mids(self, depths: Dict[str, OrderDepth]) -> Dict[str, float]:
            mids: Dict[str, float] = {}
            for product in self.PAIR_MODELS:
                depth = depths.get(product)
                if depth is not None and depth.buy_orders and depth.sell_orders:
                    mids[product] = (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
            return mids

        def rebalance(self, product: str, target: int, state: TradingState) -> List[Order]:
            depth = state.order_depths.get(product)
            if depth is None or not depth.buy_orders or (not depth.sell_orders):
                return []
            position = state.position.get(product, 0)
            delta = target - position
            if delta == 0:
                return []
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            if delta > 0:
                quantity = min(delta, self.MAX_ORDER, abs(depth.sell_orders[best_ask]))
                return [Order(product, best_ask, quantity)] if quantity > 0 else []
            quantity = min(-delta, self.MAX_ORDER, abs(depth.buy_orders[best_bid]))
            return [Order(product, best_bid, -quantity)] if quantity > 0 else []

        def load_state(self, trader_data: str) -> Dict:
            if not trader_data:
                return {}
            try:
                parsed = json.loads(trader_data)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
codex_pair_regression_robot_alpha_capped=__build_codex_pair_regression_robot_alpha_capped()
setattr(traders.ROUND_5,'codex_pair_regression_robot_alpha_capped',codex_pair_regression_robot_alpha_capped)

#+traders/ROUND_5/intro_group_strategy.py
def __build_intro_group_strategy():
    from datamodel import OrderDepth, TradingState, Order
    from typing import Dict, List, Optional, Tuple
    import json
    import math

    class Trader:
        LIMIT = 250
        MAX_ORDER = 20
        PEBBLES = ('PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS')
        PEBBLES_TRADE = ('PEBBLES_XL',)
        PEBBLES_SUM = 50000.0
        PEBBLES_TAKE_EDGE = 4.0
        PEBBLES_QUOTE_EDGE = 6.0
        SNACK_PAIRS = (('SNACKPACK_CHOCOLATE', 'SNACKPACK_VANILLA', 19940.67), ('SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 19573.66))
        SNACK_TAKE_EDGE = 80.0
        SNACK_QUOTE_EDGE = 110.0
        STRAW_BASKET_PRODUCT = 'SNACKPACK_STRAWBERRY'
        STRAW_BASKET_COEFS = {'SNACKPACK_CHOCOLATE': -1.8195, 'SNACKPACK_PISTACHIO': -1.0093, 'SNACKPACK_RASPBERRY': -1.3033, 'SNACKPACK_VANILLA': -1.62}
        STRAW_BASKET_INTERCEPT = 67692.39
        STRAW_TAKE_EDGE = 110.0
        STRAW_QUOTE_EDGE = 150.0
        DISHES = 'ROBOT_DISHES'
        DISHES_REVERSION = 0.3
        DISHES_VOL_THRESHOLD = 7.0
        DISHES_TAKE_EDGE = 5.0
        DISHES_VOL_DECAY = 0.93
        PASSIVE_QTY = 3
        PASSIVE_INVENTORY = 12
        PASSIVE_MIN_SPREAD = 2
        IRONING = 'ROBOT_IRONING'
        IRONING_REVERSION = 0.15
        IRONING_THRESHOLD = 1.5
        LAUNDRY = 'ROBOT_LAUNDRY'
        FLOW_DECAY = 0.88
        FLOW_THRESHOLD = 0.6
        FLOW_FADE_WEIGHT = -0.5

        @staticmethod
        def best_prices(depth: OrderDepth) -> Optional[Tuple[int, int, int, int]]:
            if not depth.buy_orders or not depth.sell_orders:
                return None
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            if best_bid >= best_ask:
                return None
            return (best_bid, depth.buy_orders[best_bid], best_ask, depth.sell_orders[best_ask])

        @staticmethod
        def load_state(raw: str) -> Dict:
            if not raw:
                return {}
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        def take_against_fair(self, product: str, depth: OrderDepth, fair: float, position: int, take_edge: float, quote_edge: float) -> List[Order]:
            prices = self.best_prices(depth)
            if prices is None:
                return []
            best_bid, bid_size, best_ask, ask_size = prices
            orders: List[Order] = []
            buy_room = self.LIMIT - position
            sell_room = self.LIMIT + position
            if buy_room > 0 and best_ask <= fair - take_edge:
                qty = min(buy_room, abs(ask_size), self.MAX_ORDER)
                if qty > 0:
                    orders.append(Order(product, best_ask, qty))
                    buy_room -= qty
            if sell_room > 0 and best_bid >= fair + take_edge:
                qty = min(sell_room, bid_size, self.MAX_ORDER)
                if qty > 0:
                    orders.append(Order(product, best_bid, -qty))
                    sell_room -= qty
            bid_price = int(math.floor(fair - quote_edge))
            ask_price = int(math.ceil(fair + quote_edge))
            if buy_room > 0 and bid_price < best_ask and (bid_price >= best_bid):
                orders.append(Order(product, bid_price, min(buy_room, self.MAX_ORDER)))
            if sell_room > 0 and ask_price > best_bid and (ask_price <= best_ask):
                orders.append(Order(product, ask_price, -min(sell_room, self.MAX_ORDER)))
            return orders

        def quote_inside_spread(self, product: str, depth: OrderDepth, position: int, signal: float, threshold: float) -> List[Order]:
            prices = self.best_prices(depth)
            if prices is None:
                return []
            best_bid, _, best_ask, _ = prices
            if best_ask - best_bid < self.PASSIVE_MIN_SPREAD:
                return []
            if abs(signal) < threshold:
                return []
            cap = self.PASSIVE_INVENTORY
            if signal > 0 and position < cap:
                qty = min(self.PASSIVE_QTY, cap - position)
                return [Order(product, best_bid + 1, qty)]
            if signal < 0 and position > -cap:
                qty = min(self.PASSIVE_QTY, cap + position)
                return [Order(product, best_ask - 1, -qty)]
            return []

        def trade_pebbles(self, state: TradingState, mids: Dict[str, float]) -> Dict[str, List[Order]]:
            if not all((p in mids for p in self.PEBBLES)):
                return {}
            result: Dict[str, List[Order]] = {}
            for product in self.PEBBLES_TRADE:
                others_mid = sum((mids[p] for p in self.PEBBLES if p != product))
                implied = self.PEBBLES_SUM - others_mid
                position = state.position.get(product, 0)
                depth = state.order_depths[product]
                orders = self.take_against_fair(product, depth, implied, position, self.PEBBLES_TAKE_EDGE, self.PEBBLES_QUOTE_EDGE)
                if orders:
                    result[product] = orders
            return result

        def trade_snack_pairs(self, state: TradingState, mids: Dict[str, float]) -> Dict[str, List[Order]]:
            result: Dict[str, List[Order]] = {}
            for left, right, pair_sum in self.SNACK_PAIRS:
                if left not in mids or right not in mids:
                    continue
                for product, partner in ((left, right), (right, left)):
                    fair = pair_sum - mids[partner]
                    position = state.position.get(product, 0)
                    depth = state.order_depths[product]
                    orders = self.take_against_fair(product, depth, fair, position, self.SNACK_TAKE_EDGE, self.SNACK_QUOTE_EDGE)
                    if orders:
                        result[product] = orders
            return result

        def trade_straw_basket(self, state: TradingState, mids: Dict[str, float]) -> Dict[str, List[Order]]:
            product = self.STRAW_BASKET_PRODUCT
            if product not in mids:
                return {}
            if not all((p in mids for p in self.STRAW_BASKET_COEFS)):
                return {}
            fair = self.STRAW_BASKET_INTERCEPT
            for leg, coef in self.STRAW_BASKET_COEFS.items():
                fair += coef * mids[leg]
            position = state.position.get(product, 0)
            depth = state.order_depths[product]
            orders = self.take_against_fair(product, depth, fair, position, self.STRAW_TAKE_EDGE, self.STRAW_QUOTE_EDGE)
            return {product: orders} if orders else {}

        def trade_robot_dishes(self, state: TradingState, mids: Dict[str, float], memory: Dict) -> Tuple[List[Order], Dict]:
            product = self.DISHES
            if product not in mids:
                return ([], {'prev_mid': memory.get('prev_mid'), 'vol': memory.get('vol', 0.0)})
            mid = mids[product]
            prev_mid = memory.get('prev_mid')
            prev_vol = float(memory.get('vol', 0.0))
            if prev_mid is None:
                return ([], {'prev_mid': mid, 'vol': prev_vol})
            move = mid - prev_mid
            vol = self.DISHES_VOL_DECAY * prev_vol + (1 - self.DISHES_VOL_DECAY) * abs(move)
            next_state = {'prev_mid': mid, 'vol': vol}
            if vol < self.DISHES_VOL_THRESHOLD or abs(move) < self.DISHES_VOL_THRESHOLD:
                return ([], next_state)
            fair = mid - self.DISHES_REVERSION * move
            position = state.position.get(product, 0)
            depth = state.order_depths[product]
            orders = self.take_against_fair(product, depth, fair, position, self.DISHES_TAKE_EDGE, self.DISHES_TAKE_EDGE + 3.0)
            return (orders, next_state)

        def trade_robot_ironing(self, state: TradingState, mids: Dict[str, float], memory: Dict) -> Tuple[List[Order], Dict]:
            product = self.IRONING
            if product not in mids:
                return ([], {'prev_mid': memory.get('prev_mid')})
            mid = mids[product]
            prev_mid = memory.get('prev_mid')
            next_state = {'prev_mid': mid}
            if prev_mid is None:
                return ([], next_state)
            move = mid - prev_mid
            signal = -self.IRONING_REVERSION * move
            position = state.position.get(product, 0)
            orders = self.quote_inside_spread(product, state.order_depths[product], position, signal, self.IRONING_THRESHOLD)
            return (orders, next_state)

        def update_flow_signal(self, product: str, mid: float, market_trades: List, prev_signal: float, weight: float) -> float:
            signal = self.FLOW_DECAY * prev_signal
            for trade in market_trades:
                side = 1 if trade.price > mid else -1 if trade.price < mid else 0
                signal += weight * side * min(3, trade.quantity)
            return signal

        def trade_robot_flow(self, state: TradingState, mids: Dict[str, float], memory: Dict) -> Tuple[Dict[str, List[Order]], Dict]:
            result: Dict[str, List[Order]] = {}
            next_signals: Dict[str, float] = {}
            configs = ((self.LAUNDRY, self.FLOW_FADE_WEIGHT),)
            for product, weight in configs:
                if product not in mids:
                    continue
                mid = mids[product]
                prev_signal = float(memory.get(product, 0.0))
                signal = self.update_flow_signal(product, mid, state.market_trades.get(product, []), prev_signal, weight)
                next_signals[product] = signal
                position = state.position.get(product, 0)
                orders = self.quote_inside_spread(product, state.order_depths[product], position, signal, self.FLOW_THRESHOLD)
                if orders:
                    result[product] = orders
            return (result, next_signals)

        def run(self, state: TradingState):
            memory = self.load_state(state.traderData)
            mids: Dict[str, float] = {}
            for product, depth in state.order_depths.items():
                prices = self.best_prices(depth)
                if prices is None:
                    continue
                best_bid, _, best_ask, _ = prices
                mids[product] = (best_bid + best_ask) / 2
            result: Dict[str, List[Order]] = {}
            result.update(self.trade_pebbles(state, mids))
            result.update(self.trade_snack_pairs(state, mids))
            result.update(self.trade_straw_basket(state, mids))
            dishes_orders, dishes_state = self.trade_robot_dishes(state, mids, memory.get('dishes', {}))
            if dishes_orders:
                result[self.DISHES] = dishes_orders
            ironing_orders, ironing_state = self.trade_robot_ironing(state, mids, memory.get('ironing', {}))
            if ironing_orders:
                result[self.IRONING] = ironing_orders
            flow_orders, flow_state = self.trade_robot_flow(state, mids, memory.get('flow', {}))
            for product, orders in flow_orders.items():
                result.setdefault(product, []).extend(orders)
            next_memory = {'dishes': dishes_state, 'ironing': ironing_state, 'flow': flow_state}
            return (result, 0, json.dumps(next_memory, separators=(',', ':')))
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
intro_group_strategy=__build_intro_group_strategy()
setattr(traders.ROUND_5,'intro_group_strategy',intro_group_strategy)

#+traders/ROUND_5/intro_market_maker.py
def __build_intro_market_maker():
    from datamodel import OrderDepth, UserId, TradingState, Order
    from typing import List, Dict
    import statistics

    class Trader:
        PRODUCT_GROUPS = {'SNACKPACK': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'GALAXY_SOUNDS': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'UV_VISOR': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'OXYGEN_SHAKE': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'PEBBLES': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'PANEL': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'SLEEP_POD': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'MICROCHIP': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'TRANSLATOR': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'ROBOT': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING']}
        STRATEGY_PARAMS = {'SNACKPACK': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 100, 'vol_scale': 1.0}, 'GALAXY_SOUNDS': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9}, 'UV_VISOR': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9}, 'OXYGEN_SHAKE': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9}, 'PEBBLES': {'bid_offset': -3, 'ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.8}, 'PANEL': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 60, 'vol_scale': 0.8}, 'SLEEP_POD': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.7}, 'MICROCHIP': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 40, 'vol_scale': 0.6}, 'TRANSLATOR': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 60, 'vol_scale': 0.7}, 'ROBOT': {'bid_offset': -2, 'ask_offset': 2, 'max_pos': 40, 'vol_scale': 0.5}}

        def __init__(self):
            self.position = {}
            self.mid_price_history = {}

        def bid(self):
            return 15

        def get_product_group(self, product: str) -> str:
            for group, products in self.PRODUCT_GROUPS.items():
                if product in products:
                    return group
            return None

        def calculate_bid_ask_volume(self, order_depth: OrderDepth, mid_price: float, product: str) -> tuple:
            group = self.get_product_group(product)
            params = self.STRATEGY_PARAMS.get(group, self.STRATEGY_PARAMS['ROBOT'])
            best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else mid_price - 5
            best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else mid_price + 5
            bid_price = int(mid_price) + params['bid_offset']
            ask_price = int(mid_price) + params['ask_offset']
            market_bid_vol = order_depth.buy_orders.get(best_bid, 0) if order_depth.buy_orders else 10
            market_ask_vol = order_depth.sell_orders.get(best_ask, 0) if order_depth.sell_orders else 10
            bid_volume = max(1, int(market_bid_vol * params['vol_scale']))
            ask_volume = max(1, int(market_ask_vol * params['vol_scale']))
            return (bid_price, ask_price, bid_volume, ask_volume)

        def run(self, state: TradingState):
            result = {}
            conversions = 0
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
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
                mid_price = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask
                current_pos = self.position[product]
                bid_price, ask_price, bid_volume, ask_volume = self.calculate_bid_ask_volume(order_depth, mid_price, product)
                if current_pos < params['max_pos']:
                    orders.append(Order(product, bid_price, bid_volume))
                if current_pos > -params['max_pos']:
                    orders.append(Order(product, ask_price, ask_volume))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
intro_market_maker=__build_intro_market_maker()
setattr(traders.ROUND_5,'intro_market_maker',intro_market_maker)

#+traders/ROUND_5/strategy_v2_tuned.py
def __build_strategy_v2_tuned():
    from datamodel import OrderDepth, UserId, TradingState, Order
    from typing import List, Dict
    import statistics

    class Trader:
        FAMILIES = {'GALAXY_SOUNDS': {'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'base_bid_offset': -3, 'base_ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9}, 'MICROCHIP': {'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'base_bid_offset': -2, 'base_ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.8}, 'OXYGEN_SHAKE': {'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'base_bid_offset': -3, 'base_ask_offset': 3, 'max_pos': 80, 'vol_scale': 0.9}, 'PANEL': {'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'base_bid_offset': -2, 'base_ask_offset': 2, 'max_pos': 60, 'vol_scale': 0.8}, 'PEBBLES': {'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'base_bid_offset': -3, 'base_ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.8}, 'ROBOT': {'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'base_bid_offset': -2, 'base_ask_offset': 2, 'max_pos': 40, 'vol_scale': 0.5}, 'SLEEP_POD': {'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'base_bid_offset': -3, 'base_ask_offset': 3, 'max_pos': 50, 'vol_scale': 0.7}, 'SNACKPACK': {'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'base_bid_offset': -2, 'base_ask_offset': 2, 'max_pos': 80, 'vol_scale': 1.0}, 'TRANSLATOR': {'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'base_bid_offset': -2, 'base_ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.7}, 'UV_VISOR': {'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'base_bid_offset': -3, 'base_ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.8}}
        PRODUCT_OVERRIDES = {'SLEEP_POD_LAMB_WOOL': {'bid_offset_mult': 0.8, 'ask_offset_mult': 1.2}, 'SLEEP_POD_NYLON': {'bid_offset_mult': 0.8, 'ask_offset_mult': 1.2}, 'SNACKPACK_CHOCOLATE': {'bid_offset_mult': 1.0, 'ask_offset_mult': 1.5}, 'SNACKPACK_PISTACHIO': {'bid_offset_mult': 1.0, 'ask_offset_mult': 1.5}, 'TRANSLATOR_ASTRO_BLACK': {'bid_offset_mult': 0.7, 'ask_offset_mult': 1.3}, 'TRANSLATOR_SPACE_GRAY': {'bid_offset_mult': 0.7, 'ask_offset_mult': 1.3}, 'UV_VISOR_AMBER': {'bid_offset_mult': 0.7, 'ask_offset_mult': 1.3}, 'UV_VISOR_ORANGE': {'bid_offset_mult': 0.9, 'ask_offset_mult': 1.2}}

        def __init__(self):
            self.position = {}
            self.mid_prices = {}

        def bid(self):
            return 15

        def get_family_and_params(self, product: str) -> tuple:
            for family_name, family_data in self.FAMILIES.items():
                if product in family_data['products']:
                    return (family_name, family_data)
            return (None, None)

        def run(self, state: TradingState):
            result = {}
            conversions = 0
            for product in state.order_depths.keys():
                if product not in self.position:
                    self.position[product] = 0
                    self.mid_prices[product] = []
            for product in state.order_depths.keys():
                order_depth = state.order_depths[product]
                family_name, family_params = self.get_family_and_params(product)
                if not family_params:
                    continue
                orders = []
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
                mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask
                self.mid_prices[product].append(mid)
                if len(self.mid_prices[product]) > 10:
                    self.mid_prices[product].pop(0)
                trend = 0
                if len(self.mid_prices[product]) >= 3:
                    recent_avg = sum(self.mid_prices[product][-3:]) / 3
                    older_avg = sum(self.mid_prices[product][:3]) / 3
                    trend = 1 if recent_avg > older_avg else -1 if recent_avg < older_avg else 0
                bid_offset = family_params['base_bid_offset']
                ask_offset = family_params['base_ask_offset']
                if product in self.PRODUCT_OVERRIDES:
                    override = self.PRODUCT_OVERRIDES[product]
                    bid_offset = int(bid_offset * override.get('bid_offset_mult', 1.0))
                    ask_offset = int(ask_offset * override.get('ask_offset_mult', 1.0))
                if trend > 0:
                    bid_offset = max(bid_offset - 1, -5)
                elif trend < 0:
                    ask_offset = min(ask_offset + 1, 5)
                bid_price = int(mid) + bid_offset
                ask_price = int(mid) + ask_offset
                market_bid_vol = list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10
                market_ask_vol = list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10
                bid_vol = max(1, int(market_bid_vol * family_params['vol_scale']))
                ask_vol = max(1, int(market_ask_vol * family_params['vol_scale']))
                current_pos = self.position[product]
                max_pos = family_params['max_pos']
                if current_pos < max_pos:
                    orders.append(Order(product, bid_price, bid_vol))
                if current_pos > -max_pos:
                    orders.append(Order(product, ask_price, ask_vol))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
strategy_v2_tuned=__build_strategy_v2_tuned()
setattr(traders.ROUND_5,'strategy_v2_tuned',strategy_v2_tuned)

#+traders/ROUND_5/strategy_v3_aggressive_spread.py
def __build_strategy_v3_aggressive_spread():
    from datamodel import OrderDepth, UserId, TradingState, Order
    from typing import Dict

    class Trader:
        GROUPS = {'SNACKPACK': {'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'bid_offset': -1, 'ask_offset': 1, 'max_pos': 120, 'vol_scale': 1.2, 'level': 'ULTRA_LIQUID'}, 'GALAXY_SOUNDS': {'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'bid_offset': -4, 'ask_offset': 4, 'max_pos': 90, 'vol_scale': 1.0, 'level': 'HIGH_LIQUID'}, 'OXYGEN_SHAKE': {'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'bid_offset': -4, 'ask_offset': 4, 'max_pos': 90, 'vol_scale': 1.0, 'level': 'HIGH_LIQUID'}, 'UV_VISOR': {'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'bid_offset': -4, 'ask_offset': 4, 'max_pos': 85, 'vol_scale': 0.95, 'level': 'HIGH_LIQUID'}, 'PEBBLES': {'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'bid_offset': -4, 'ask_offset': 4, 'max_pos': 80, 'vol_scale': 0.9, 'level': 'MED_LIQUID'}, 'PANEL': {'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'bid_offset': -3, 'ask_offset': 3, 'max_pos': 70, 'vol_scale': 0.85, 'level': 'MED_LIQUID'}, 'SLEEP_POD': {'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'bid_offset': -3, 'ask_offset': 3, 'max_pos': 60, 'vol_scale': 0.75, 'level': 'MED_LIQUID'}, 'MICROCHIP': {'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'bid_offset': -2, 'ask_offset': 2, 'max_pos': 50, 'vol_scale': 0.7, 'level': 'LOW_LIQUID'}, 'TRANSLATOR': {'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'bid_offset': -2, 'ask_offset': 2, 'max_pos': 55, 'vol_scale': 0.65, 'level': 'LOW_LIQUID'}, 'ROBOT': {'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'bid_offset': -2, 'ask_offset': 2, 'max_pos': 45, 'vol_scale': 0.55, 'level': 'LOW_LIQUID'}}

        def __init__(self):
            self.position = {}

        def bid(self):
            return 15

        def get_group(self, product: str) -> Dict:
            for group_name, group_data in self.GROUPS.items():
                if product in group_data['products']:
                    return group_data
            return self.GROUPS['ROBOT']

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
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
                mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask
                market_bid_vol = list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10
                market_ask_vol = list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10
                bid_vol = max(1, int(market_bid_vol * group['vol_scale']))
                ask_vol = max(1, int(market_ask_vol * group['vol_scale']))
                bid_price = int(mid) + group['bid_offset']
                ask_price = int(mid) + group['ask_offset']
                current_pos = self.position[product]
                max_pos = group['max_pos']
                if current_pos < max_pos:
                    orders.append(Order(product, bid_price, bid_vol))
                if current_pos > -max_pos:
                    orders.append(Order(product, ask_price, ask_vol))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
strategy_v3_aggressive_spread=__build_strategy_v3_aggressive_spread()
setattr(traders.ROUND_5,'strategy_v3_aggressive_spread',strategy_v3_aggressive_spread)

#+traders/ROUND_5/strategy_v4_momentum_selective.py
def __build_strategy_v4_momentum_selective():
    from datamodel import OrderDepth, UserId, TradingState, Order
    from typing import Dict, List

    class Trader:
        FAMILIES = {'SNACKPACK': {'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 100, 'vol_scale': 1.1, 'trend_sensitivity': 2}, 'GALAXY_SOUNDS': {'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 95, 'vol_scale': 1.0, 'trend_sensitivity': 1.5}, 'OXYGEN_SHAKE': {'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 90, 'vol_scale': 1.0, 'trend_sensitivity': 1.5}, 'UV_VISOR': {'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 85, 'vol_scale': 1.0, 'trend_sensitivity': 1.5}, 'PEBBLES': {'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 80, 'vol_scale': 0.95, 'trend_sensitivity': 1.5}, 'PANEL': {'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 70, 'vol_scale': 0.9, 'trend_sensitivity': 1.0}, 'SLEEP_POD': {'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 65, 'vol_scale': 0.85, 'trend_sensitivity': 1.2}, 'MICROCHIP': {'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 50, 'vol_scale': 0.75, 'trend_sensitivity': 0.8}, 'TRANSLATOR': {'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 55, 'vol_scale': 0.75, 'trend_sensitivity': 0.9}, 'ROBOT': {'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 45, 'vol_scale': 0.6, 'trend_sensitivity': 0.7}}
        PROBLEM_PRODUCTS = {'SLEEP_POD_LAMB_WOOL': {'max_pos_mult': 0.6, 'vol_mult': 0.5, 'offset_mult': 1.5}, 'SLEEP_POD_NYLON': {'max_pos_mult': 0.6, 'vol_mult': 0.5, 'offset_mult': 1.5}, 'SNACKPACK_CHOCOLATE': {'max_pos_mult': 0.7, 'vol_mult': 0.7, 'offset_mult': 1.3}, 'SNACKPACK_PISTACHIO': {'max_pos_mult': 0.7, 'vol_mult': 0.7, 'offset_mult': 1.3}, 'TRANSLATOR_ASTRO_BLACK': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'TRANSLATOR_SPACE_GRAY': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'UV_VISOR_AMBER': {'max_pos_mult': 0.5, 'vol_mult': 0.5, 'offset_mult': 1.5}, 'UV_VISOR_ORANGE': {'max_pos_mult': 0.7, 'vol_mult': 0.7, 'offset_mult': 1.2}}

        def __init__(self):
            self.position = {}
            self.prices = {}

        def bid(self):
            return 15

        def get_family(self, product: str) -> Dict:
            for family_name, family_data in self.FAMILIES.items():
                if product in family_data['products']:
                    return family_data
            return self.FAMILIES['ROBOT']

        def calculate_momentum(self, product: str, current_mid: float) -> int:
            if product not in self.prices:
                self.prices[product] = []
            self.prices[product].append(current_mid)
            if len(self.prices[product]) > 15:
                self.prices[product].pop(0)
            if len(self.prices[product]) < 5:
                return 0
            recent = sum(self.prices[product][-3:]) / 3
            prev = sum(self.prices[product][-6:-3]) / 3
            trend = 1 if recent > prev else -1 if recent < prev else 0
            if len(self.prices[product]) >= 9:
                accel_recent = sum(self.prices[product][-3:]) / 3
                accel_mid = sum(self.prices[product][-6:-3]) / 3
                accel_old = sum(self.prices[product][-9:-6]) / 3
                accel = accel_recent - accel_mid - (accel_mid - accel_old)
                if accel > 0.5 and trend > 0:
                    return 2
                elif accel < -0.5 and trend < 0:
                    return -2
            return trend

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
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
                mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask
                momentum = self.calculate_momentum(product, mid)
                bid_offset = family['base_bid']
                ask_offset = family['base_ask']
                max_pos = family['max_pos']
                vol_scale = family['vol_scale']
                if product in self.PROBLEM_PRODUCTS:
                    adj = self.PROBLEM_PRODUCTS[product]
                    max_pos = int(max_pos * adj['max_pos_mult'])
                    vol_scale = vol_scale * adj['vol_mult']
                    bid_offset = int(bid_offset * adj['offset_mult'])
                    ask_offset = int(ask_offset * adj['offset_mult'])
                trend_sens = family['trend_sensitivity']
                if momentum > 0:
                    bid_offset = max(bid_offset - int(trend_sens), -5)
                elif momentum < 0:
                    ask_offset = min(ask_offset + int(trend_sens), 5)
                bid_vol = max(1, int((list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10) * vol_scale))
                ask_vol = max(1, int((list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10) * vol_scale))
                bid_price = int(mid) + bid_offset
                ask_price = int(mid) + ask_offset
                current_pos = self.position[product]
                if current_pos < max_pos:
                    orders.append(Order(product, bid_price, bid_vol))
                if current_pos > -max_pos:
                    orders.append(Order(product, ask_price, ask_vol))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
strategy_v4_momentum_selective=__build_strategy_v4_momentum_selective()
setattr(traders.ROUND_5,'strategy_v4_momentum_selective',strategy_v4_momentum_selective)

#+traders/ROUND_5/strategy_v6_final_optimized.py
def __build_strategy_v6_final_optimized():
    from datamodel import OrderDepth, UserId, TradingState, Order
    from typing import Dict

    class Trader:
        FAMILIES = {'SNACKPACK': {'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 120, 'vol_scale': 1.2}, 'GALAXY_SOUNDS': {'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 105, 'vol_scale': 1.05}, 'OXYGEN_SHAKE': {'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 100, 'vol_scale': 1.05}, 'UV_VISOR': {'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 95, 'vol_scale': 1.05}, 'PEBBLES': {'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 90, 'vol_scale': 1.0}, 'PANEL': {'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 75, 'vol_scale': 0.95}, 'SLEEP_POD': {'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 70, 'vol_scale': 0.9}, 'MICROCHIP': {'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 55, 'vol_scale': 0.8}, 'TRANSLATOR': {'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 60, 'vol_scale': 0.8}, 'ROBOT': {'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 50, 'vol_scale': 0.65}}
        WINNER_PRODUCTS = {'SNACKPACK_RASPBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'SNACKPACK_STRAWBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'SNACKPACK_VANILLA': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'TRANSLATOR_ECLIPSE_CHARCOAL': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'TRANSLATOR_VOID_BLUE': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_MAGENTA': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_RED': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_YELLOW': {'max_pos_mult': 1.1, 'vol_mult': 1.1}}
        LOSER_PRODUCTS = {'SLEEP_POD_LAMB_WOOL': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'SLEEP_POD_NYLON': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'SNACKPACK_CHOCOLATE': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'SNACKPACK_PISTACHIO': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'TRANSLATOR_ASTRO_BLACK': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6}, 'TRANSLATOR_SPACE_GRAY': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6}, 'UV_VISOR_AMBER': {'max_pos_mult': 0.3, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'UV_VISOR_ORANGE': {'max_pos_mult': 0.5, 'vol_mult': 0.5, 'offset_mult': 1.5}}

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
            if product not in self.prices:
                self.prices[product] = []
            self.prices[product].append(current_mid)
            if len(self.prices[product]) > 20:
                self.prices[product].pop(0)
            if len(self.prices[product]) < 6:
                return 0
            recent_3 = sum(self.prices[product][-3:]) / 3
            prev_3 = sum(self.prices[product][-6:-3]) / 3
            base_trend = 1 if recent_3 > prev_3 else -1 if recent_3 < prev_3 else 0
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
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else 10000
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else 10000
                mid = (best_bid + best_ask) / 2 if order_depth.buy_orders and order_depth.sell_orders else best_bid if order_depth.buy_orders else best_ask
                momentum = self.calculate_momentum(product, mid)
                bid_offset = family['base_bid']
                ask_offset = family['base_ask']
                max_pos = family['max_pos']
                vol_scale = family['vol_scale']
                if product in self.WINNER_PRODUCTS:
                    adj = self.WINNER_PRODUCTS[product]
                    max_pos = int(max_pos * adj['max_pos_mult'])
                    vol_scale = vol_scale * adj['vol_mult']
                elif product in self.LOSER_PRODUCTS:
                    adj = self.LOSER_PRODUCTS[product]
                    max_pos = int(max_pos * adj['max_pos_mult'])
                    vol_scale = vol_scale * adj['vol_mult']
                    bid_offset = int(bid_offset * adj['offset_mult'])
                    ask_offset = int(ask_offset * adj['offset_mult'])
                if momentum >= 2:
                    bid_offset = max(bid_offset - 2, -6)
                    ask_offset = max(ask_offset - 1, -4)
                elif momentum == 1:
                    bid_offset = max(bid_offset - 1, -5)
                elif momentum <= -2:
                    ask_offset = min(ask_offset + 2, 6)
                    bid_offset = min(bid_offset + 1, 4)
                elif momentum == -1:
                    ask_offset = min(ask_offset + 1, 5)
                bid_vol = max(1, int((list(order_depth.buy_orders.values())[0] if order_depth.buy_orders else 10) * vol_scale))
                ask_vol = max(1, int((list(order_depth.sell_orders.values())[0] if order_depth.sell_orders else 10) * vol_scale))
                bid_price = int(mid) + bid_offset
                ask_price = int(mid) + ask_offset
                current_pos = self.position[product]
                if current_pos < max_pos:
                    orders.append(Order(product, bid_price, bid_vol))
                if current_pos > -max_pos:
                    orders.append(Order(product, ask_price, ask_vol))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
strategy_v6_final_optimized=__build_strategy_v6_final_optimized()
setattr(traders.ROUND_5,'strategy_v6_final_optimized',strategy_v6_final_optimized)

#+traders/ROUND_5/trader_combined_strats.py
def __build_trader_combined_strats():
    from datamodel import OrderDepth, TradingState, Order
    from typing import Dict, List, Tuple
    PairModel = Tuple[str, str, float, float, float, float]

    class Trader:
        PAIR_MODELS: List[PairModel] = [('SNACKPACK_CHOCOLATE', 'SNACKPACK_VANILLA', -1.0411, 20356.14, 75.84, 0.96), ('SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', -0.1932, 12146.24, 154.61, 0.94), ('SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', -0.5492, 15030.23, 162.66, 0.88), ('SNACKPACK_PISTACHIO', 'SNACKPACK_STRAWBERRY', -0.2276, 11932.18, 168.25, 0.86), ('PANEL_2X2', 'PANEL_4X4', -0.7295, 16783.08, 586.67, 0.42), ('PANEL_1X2', 'PANEL_2X4', +0.121, 7560.04, 585.01, 0.32), ('TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_GRAPHITE_MIST', -0.1887, 11287.81, 480.59, 0.32), ('TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_SPACE_GRAY', -0.1067, 10820.59, 351.56, 0.29), ('UV_VISOR_AMBER', 'UV_VISOR_ORANGE', -1.2867, 21327.68, 701.38, 0.3), ('UV_VISOR_MAGENTA', 'UV_VISOR_RED', +0.3266, 7498.44, 582.75, 0.26), ('UV_VISOR_ORANGE', 'UV_VISOR_RED', +0.3898, 6114.56, 500.69, 0.2), ('GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_SOLAR_FLAMES', -0.2493, 14232.51, 951.84, 0.28), ('GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_SOLAR_WINDS', +0.8243, 2863.48, 848.33, 0.26), ('MICROCHIP_CIRCLE', 'MICROCHIP_RECTANGLE', +0.0981, 8358.32, 527.37, 0.24), ('MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', -0.1426, 10381.11, 484.36, 0.22), ('MICROCHIP_RECTANGLE', 'MICROCHIP_TRIANGLE', +0.3055, 5773.67, 707.62, 0.2), ('OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', -0.0569, 9951.05, 396.11, 0.23), ('SLEEP_POD_COTTON', 'SLEEP_POD_SUEDE', +0.6914, 3647.01, 633.09, 0.21)]
        Z_ENTRY = 1.0
        Z_MAX = 3.0
        OVERLAY_PER_PAIR = 30
        FAMILIES: Dict[str, Dict] = {'SNACKPACK': {'products': ['SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 120, 'vol_scale': 1.2}, 'GALAXY_SOUNDS': {'products': ['GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 105, 'vol_scale': 1.05}, 'OXYGEN_SHAKE': {'products': ['OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 100, 'vol_scale': 1.05}, 'UV_VISOR': {'products': ['UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 95, 'vol_scale': 1.05}, 'PEBBLES': {'products': ['PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS'], 'base_bid': -4, 'base_ask': 4, 'max_pos': 90, 'vol_scale': 1.0}, 'PANEL': {'products': ['PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 75, 'vol_scale': 0.95}, 'SLEEP_POD': {'products': ['SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE'], 'base_bid': -3, 'base_ask': 3, 'max_pos': 70, 'vol_scale': 0.9}, 'MICROCHIP': {'products': ['MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 55, 'vol_scale': 0.8}, 'TRANSLATOR': {'products': ['TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 60, 'vol_scale': 0.8}, 'ROBOT': {'products': ['ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING'], 'base_bid': -2, 'base_ask': 2, 'max_pos': 50, 'vol_scale': 0.65}}
        WINNER_PRODUCTS = {'SNACKPACK_RASPBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'SNACKPACK_STRAWBERRY': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'SNACKPACK_VANILLA': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'TRANSLATOR_ECLIPSE_CHARCOAL': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'TRANSLATOR_VOID_BLUE': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_MAGENTA': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_RED': {'max_pos_mult': 1.1, 'vol_mult': 1.1}, 'UV_VISOR_YELLOW': {'max_pos_mult': 1.1, 'vol_mult': 1.1}}
        LOSER_PRODUCTS = {'SLEEP_POD_LAMB_WOOL': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'SLEEP_POD_NYLON': {'max_pos_mult': 0.4, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'SNACKPACK_CHOCOLATE': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'SNACKPACK_PISTACHIO': {'max_pos_mult': 0.6, 'vol_mult': 0.6, 'offset_mult': 1.4}, 'TRANSLATOR_ASTRO_BLACK': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6}, 'TRANSLATOR_SPACE_GRAY': {'max_pos_mult': 0.4, 'vol_mult': 0.4, 'offset_mult': 1.6}, 'UV_VISOR_AMBER': {'max_pos_mult': 0.3, 'vol_mult': 0.3, 'offset_mult': 1.8}, 'UV_VISOR_ORANGE': {'max_pos_mult': 0.5, 'vol_mult': 0.5, 'offset_mult': 1.5}}

        def __init__(self):
            self.prices: Dict[str, List[float]] = {}

        def get_family(self, product: str) -> Dict:
            for cfg in self.FAMILIES.values():
                if product in cfg['products']:
                    return cfg
            return self.FAMILIES['ROBOT']

        def calculate_momentum(self, product: str, current_mid: float) -> int:
            history = self.prices.setdefault(product, [])
            history.append(current_mid)
            if len(history) > 20:
                history.pop(0)
            if len(history) < 6:
                return 0
            recent_3 = sum(history[-3:]) / 3
            prev_3 = sum(history[-6:-3]) / 3
            base = 1 if recent_3 > prev_3 else -1 if recent_3 < prev_3 else 0
            if len(history) >= 12 and base != 0:
                prev_6 = sum(history[-12:-6]) / 6
                curr_6 = sum(history[-6:]) / 6
                if (curr_6 - prev_6) * base > 0:
                    return base + (1 if base > 0 else -1)
            return base

        @staticmethod
        def _mid_from_depth(depth: OrderDepth) -> float | None:
            if depth.buy_orders and depth.sell_orders:
                return (max(depth.buy_orders) + min(depth.sell_orders)) / 2.0
            if depth.buy_orders:
                return float(max(depth.buy_orders))
            if depth.sell_orders:
                return float(min(depth.sell_orders))
            return None

        def _compute_overlay(self, mids: Dict[str, float]) -> Dict[str, int]:
            bias: Dict[str, float] = {}
            for a, b, beta, alpha, std, weight in self.PAIR_MODELS:
                ma, mb = (mids.get(a), mids.get(b))
                if ma is None or mb is None or std <= 0:
                    continue
                z = (ma - beta * mb - alpha) / std
                if abs(z) < self.Z_ENTRY:
                    continue
                zc = max(-self.Z_MAX, min(self.Z_MAX, z))
                contrib = self.OVERLAY_PER_PAIR * weight * zc / self.Z_MAX
                bias[a] = bias.get(a, 0.0) - contrib
                bias[b] = bias.get(b, 0.0) + contrib * (1.0 if beta > 0 else -1.0)
            return {p: int(round(v)) for p, v in bias.items()}

        def run(self, state: TradingState):
            result: Dict[str, List[Order]] = {}
            conversions = 0
            mids: Dict[str, float] = {}
            for product, depth in state.order_depths.items():
                m = self._mid_from_depth(depth)
                if m is not None:
                    mids[product] = m
            overlay = self._compute_overlay(mids)
            for product, depth in state.order_depths.items():
                family = self.get_family(product)
                orders: List[Order] = []
                best_bid = max(depth.buy_orders) if depth.buy_orders else 10000
                best_ask = min(depth.sell_orders) if depth.sell_orders else 10000
                mid = mids.get(product, (best_bid + best_ask) / 2.0)
                momentum = self.calculate_momentum(product, mid)
                bid_offset = family['base_bid']
                ask_offset = family['base_ask']
                max_pos = family['max_pos']
                vol_scale = family['vol_scale']
                if product in self.WINNER_PRODUCTS:
                    adj = self.WINNER_PRODUCTS[product]
                    max_pos = int(max_pos * adj['max_pos_mult'])
                    vol_scale = vol_scale * adj['vol_mult']
                elif product in self.LOSER_PRODUCTS:
                    adj = self.LOSER_PRODUCTS[product]
                    max_pos = int(max_pos * adj['max_pos_mult'])
                    vol_scale = vol_scale * adj['vol_mult']
                    bid_offset = int(bid_offset * adj['offset_mult'])
                    ask_offset = int(ask_offset * adj['offset_mult'])
                if momentum >= 2:
                    bid_offset = max(bid_offset - 2, -6)
                    ask_offset = max(ask_offset - 1, -4)
                elif momentum == 1:
                    bid_offset = max(bid_offset - 1, -5)
                elif momentum <= -2:
                    ask_offset = min(ask_offset + 2, 6)
                    bid_offset = min(bid_offset + 1, 4)
                elif momentum == -1:
                    ask_offset = min(ask_offset + 1, 5)
                bid_vol = max(1, int((list(depth.buy_orders.values())[0] if depth.buy_orders else 10) * vol_scale))
                ask_vol = max(1, int((list(depth.sell_orders.values())[0] if depth.sell_orders else 10) * vol_scale))
                bid_price = int(mid) + bid_offset
                ask_price = int(mid) + ask_offset
                current_pos = state.position.get(product, 0)
                extra = overlay.get(product, 0)
                if current_pos < max_pos:
                    orders.append(Order(product, bid_price, bid_vol))
                if current_pos > -max_pos:
                    orders.append(Order(product, ask_price, ask_vol))
                if extra > 0:
                    size = min(extra, max_pos - current_pos)
                    if size > 0:
                        overlay_bid = bid_price + 1
                        if overlay_bid < best_ask:
                            orders.append(Order(product, overlay_bid, size))
                elif extra < 0:
                    size = min(-extra, current_pos + max_pos)
                    if size > 0:
                        overlay_ask = ask_price - 1
                        if overlay_ask > best_bid:
                            orders.append(Order(product, overlay_ask, -size))
                result[product] = orders
            return (result, conversions, '')
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
trader_combined_strats=__build_trader_combined_strats()
setattr(traders.ROUND_5,'trader_combined_strats',trader_combined_strats)

#+traders/ROUND_5/trader_ULTIMATE_FIX.py
def __build_trader_ULTIMATE_FIX():
    from datamodel import OrderDepth, TradingState, Order
    from typing import Dict, List, Tuple
    import json
    import math
    PebblesRobotTrader = globals()['codex_pair_regression_robot_alpha_capped'].Trader
    GroupStarterTrader = globals()['codex_group_starter'].Trader
    IntroGroupTrader = globals()['intro_group_strategy'].Trader
    IntroMarketMakerTrader = globals()['intro_market_maker'].Trader
    StrategyV2Trader = globals()['strategy_v2_tuned'].Trader
    StrategyV3Trader = globals()['strategy_v3_aggressive_spread'].Trader
    StrategyV4Trader = globals()['strategy_v4_momentum_selective'].Trader
    StrategyV6Trader = globals()['strategy_v6_final_optimized'].Trader
    CombinedTrader = globals()['trader_combined_strats'].Trader

    class Trader:
        SOURCES = ('base', 'group', 'intro_group', 'intro_market', 'v2', 'v3', 'v4', 'v6', 'combined')
        _PEBBLES = ('PEBBLES_L', 'PEBBLES_M', 'PEBBLES_S', 'PEBBLES_XL', 'PEBBLES_XS')
        DEFAULT_PRODUCT_SOURCE = 'combined'
        PRODUCT_SOURCE: Dict[str, str] = {'PEBBLES_L': 'base', 'PEBBLES_M': 'base', 'PEBBLES_S': 'base', 'PEBBLES_XL': 'base', 'PEBBLES_XS': 'base', 'ROBOT_DISHES': 'intro_group', 'ROBOT_IRONING': 'intro_group', 'ROBOT_LAUNDRY': 'intro_group', 'SNACKPACK_PISTACHIO': 'intro_group', 'SNACKPACK_RASPBERRY': 'intro_group', 'SNACKPACK_STRAWBERRY': 'intro_group', 'SNACKPACK_CHOCOLATE': 'group', 'SNACKPACK_VANILLA': 'group', 'OXYGEN_SHAKE_CHOCOLATE': 'group', 'PANEL_1X2': 'group', 'PANEL_1X4': 'group', 'TRANSLATOR_ECLIPSE_CHARCOAL': 'group', 'UV_VISOR_AMBER': 'group', 'GALAXY_SOUNDS_DARK_MATTER': 'intro_market', 'OXYGEN_SHAKE_MINT': 'intro_market', 'TRANSLATOR_VOID_BLUE': 'intro_market', 'UV_VISOR_YELLOW': 'intro_market', 'SLEEP_POD_POLYESTER': 'v2', 'UV_VISOR_MAGENTA': 'v2', 'PANEL_2X4': 'v3', 'UV_VISOR_RED': 'v3', 'GALAXY_SOUNDS_BLACK_HOLES': 'combined', 'GALAXY_SOUNDS_PLANETARY_RINGS': 'combined', 'GALAXY_SOUNDS_SOLAR_FLAMES': 'combined', 'GALAXY_SOUNDS_SOLAR_WINDS': 'combined', 'MICROCHIP_CIRCLE': 'combined', 'MICROCHIP_OVAL': 'combined', 'MICROCHIP_RECTANGLE': 'combined', 'MICROCHIP_SQUARE': 'combined', 'MICROCHIP_TRIANGLE': 'combined', 'OXYGEN_SHAKE_EVENING_BREATH': 'combined', 'OXYGEN_SHAKE_GARLIC': 'combined', 'OXYGEN_SHAKE_MORNING_BREATH': 'combined', 'PANEL_2X2': 'combined', 'PANEL_4X4': 'combined', 'ROBOT_MOPPING': 'combined', 'ROBOT_VACUUMING': 'combined', 'SLEEP_POD_COTTON': 'combined', 'SLEEP_POD_LAMB_WOOL': 'combined', 'SLEEP_POD_NYLON': 'combined', 'SLEEP_POD_SUEDE': 'combined', 'TRANSLATOR_ASTRO_BLACK': 'combined', 'TRANSLATOR_GRAPHITE_MIST': 'combined', 'TRANSLATOR_SPACE_GRAY': 'combined', 'UV_VISOR_ORANGE': 'combined'}
        _ALL_UNDERLYINGS: Tuple[str, ...] = ('GALAXY_SOUNDS_BLACK_HOLES', 'GALAXY_SOUNDS_DARK_MATTER', 'GALAXY_SOUNDS_PLANETARY_RINGS', 'GALAXY_SOUNDS_SOLAR_FLAMES', 'GALAXY_SOUNDS_SOLAR_WINDS', 'MICROCHIP_CIRCLE', 'MICROCHIP_OVAL', 'MICROCHIP_RECTANGLE', 'MICROCHIP_SQUARE', 'MICROCHIP_TRIANGLE', 'OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH', 'PANEL_1X2', 'PANEL_1X4', 'PANEL_2X2', 'PANEL_2X4', 'PANEL_4X4', *_PEBBLES, 'ROBOT_DISHES', 'ROBOT_IRONING', 'ROBOT_LAUNDRY', 'ROBOT_MOPPING', 'ROBOT_VACUUMING', 'SLEEP_POD_COTTON', 'SLEEP_POD_LAMB_WOOL', 'SLEEP_POD_NYLON', 'SLEEP_POD_POLYESTER', 'SLEEP_POD_SUEDE', 'SNACKPACK_CHOCOLATE', 'SNACKPACK_PISTACHIO', 'SNACKPACK_RASPBERRY', 'SNACKPACK_STRAWBERRY', 'SNACKPACK_VANILLA', 'TRANSLATOR_ASTRO_BLACK', 'TRANSLATOR_ECLIPSE_CHARCOAL', 'TRANSLATOR_GRAPHITE_MIST', 'TRANSLATOR_SPACE_GRAY', 'TRANSLATOR_VOID_BLUE', 'UV_VISOR_AMBER', 'UV_VISOR_MAGENTA', 'UV_VISOR_ORANGE', 'UV_VISOR_RED', 'UV_VISOR_YELLOW')
        OXYGEN_MORNING = 'OXYGEN_SHAKE_MORNING_BREATH'
        OXYGEN_GROUP = ('OXYGEN_SHAKE_CHOCOLATE', 'OXYGEN_SHAKE_EVENING_BREATH', 'OXYGEN_SHAKE_GARLIC', 'OXYGEN_SHAKE_MINT', 'OXYGEN_SHAKE_MORNING_BREATH')
        POSITION_LIMIT = 10
        MAX_ORDERS_PER_PRODUCT = 10
        DEFAULT_MAX_ORDER_SIZE = 3
        SOURCE_MAX_ORDER_SIZE = {'base': 4, 'group': 2, 'intro_group': 3, 'intro_market': 2, 'v2': 2, 'v3': 2, 'v4': 2, 'v6': 2, 'combined': 3, 'oxygen_morning': 2}
        MAKER_VOL_SCALE = 0.18
        PAIR_OVERLAY_SIZE = 3

        def __init__(self):
            self.traders = {'base': PebblesRobotTrader(), 'group': GroupStarterTrader(), 'intro_group': IntroGroupTrader(), 'intro_market': IntroMarketMakerTrader(), 'v2': StrategyV2Trader(), 'v3': StrategyV3Trader(), 'v4': StrategyV4Trader(), 'v6': StrategyV6Trader(), 'combined': CombinedTrader()}
            self.configure_child_traders()

        def configure_child_traders(self) -> None:
            base = self.traders['base']
            base.CAP = self.POSITION_LIMIT
            base.MAX_ORDER = 4
            base.ROBOT_DISHES_BY_DAY = {2: 10, 3: 10, 4: 10}
            base.PAIR_LIMIT = self.POSITION_LIMIT
            base.PAIR_ORDER_SIZE = 3
            base.LONG_CAPS_BY_DAY = {2: {'PEBBLES_S': -10, 'PEBBLES_XS': -10}, 3: {'PEBBLES_S': -10, 'PEBBLES_XS': -10}, 4: {'PEBBLES_S': -10, 'PEBBLES_XS': -10}}
            base.PEBBLES_TARGETS_BY_DAY = {4: {'PEBBLES_S': -10, 'PEBBLES_XS': -10}}
            group = self.traders['group']
            group.GROUP_LIMIT = {name: self.POSITION_LIMIT for name in group.GROUP_LIMIT}
            group.ORDER_SIZE = 2
            intro_group = self.traders['intro_group']
            intro_group.LIMIT = self.POSITION_LIMIT
            intro_group.MAX_ORDER = 3
            intro_group.PASSIVE_QTY = 2
            intro_group.PASSIVE_INVENTORY = 8
            intro_market = self.traders['intro_market']
            intro_market.STRATEGY_PARAMS = self.limit_param_dicts(intro_market.STRATEGY_PARAMS, max_vol_scale=self.MAKER_VOL_SCALE)
            for name in ('v2', 'v4', 'v6', 'combined'):
                child = self.traders[name]
                child.FAMILIES = self.limit_param_dicts(child.FAMILIES, max_vol_scale=self.MAKER_VOL_SCALE)
            v3 = self.traders['v3']
            v3.GROUPS = self.limit_param_dicts(v3.GROUPS, max_vol_scale=self.MAKER_VOL_SCALE)
            for name in ('v6', 'combined'):
                child = self.traders[name]
                if hasattr(child, 'WINNER_PRODUCTS'):
                    child.WINNER_PRODUCTS = self.cap_winner_multipliers(child.WINNER_PRODUCTS)
            combined = self.traders['combined']
            combined.OVERLAY_PER_PAIR = self.PAIR_OVERLAY_SIZE
            combined.Z_ENTRY = 1.25

        def limit_param_dicts(self, configs: Dict[str, Dict], max_vol_scale: float) -> Dict[str, Dict]:
            limited: Dict[str, Dict] = {}
            for name, config in configs.items():
                tuned = dict(config)
                if 'max_pos' in tuned:
                    tuned['max_pos'] = min(self.POSITION_LIMIT, int(tuned['max_pos']))
                if 'vol_scale' in tuned:
                    tuned['vol_scale'] = min(max_vol_scale, max(0.05, float(tuned['vol_scale']) * 0.25))
                limited[name] = tuned
            return limited

        def cap_winner_multipliers(self, configs: Dict[str, Dict]) -> Dict[str, Dict]:
            limited: Dict[str, Dict] = {}
            for product, config in configs.items():
                tuned = dict(config)
                tuned['max_pos_mult'] = min(1.0, float(tuned.get('max_pos_mult', 1.0)))
                tuned['vol_mult'] = min(1.0, float(tuned.get('vol_mult', 1.0)))
                limited[product] = tuned
            return limited

        def run(self, state: TradingState):
            memory = self.load_state(state.traderData)
            sub_memory = memory.get('s', {})
            outputs: Dict[str, Dict[str, List[Order]]] = {}
            next_sub_memory: Dict[str, str] = {}
            for name in self.SOURCES:
                orders, trader_data = self.run_child(name, state, sub_memory.get(name, ''))
                outputs[name] = orders
                next_sub_memory[name] = trader_data
            result: Dict[str, List[Order]] = {}
            for product in state.order_depths:
                source = self.PRODUCT_SOURCE.get(product, self.DEFAULT_PRODUCT_SOURCE)
                orders = outputs.get(source, {}).get(product, [])
                safe_orders = self.cap_orders(product, orders, state, source)
                if safe_orders:
                    result[product] = safe_orders
            morning_orders, oxygen_memory = self.trade_oxygen_morning(state, memory.get('om', {}))
            if morning_orders:
                result[self.OXYGEN_MORNING] = self.cap_orders(self.OXYGEN_MORNING, morning_orders, state, 'oxygen_morning')
            next_memory = {'s': next_sub_memory, 'om': oxygen_memory}
            return (result, 0, json.dumps(next_memory, separators=(',', ':')))

        def run_child(self, name: str, state: TradingState, trader_data: str) -> Tuple[Dict[str, List[Order]], str]:
            original_data = state.traderData
            state.traderData = trader_data if isinstance(trader_data, str) else ''
            try:
                child = self.traders[name]
                if hasattr(child, 'position') and isinstance(child.position, dict):
                    child.position = {product: state.position.get(product, 0) for product in state.order_depths}
                orders, _, next_data = child.run(state)
            except Exception:
                orders, next_data = ({}, trader_data if isinstance(trader_data, str) else '')
            finally:
                state.traderData = original_data
            return (orders if isinstance(orders, dict) else {}, next_data if isinstance(next_data, str) else '')

        def cap_orders(self, product: str, orders: List[Order], state: TradingState, source: str='') -> List[Order]:
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
                quantity = merged[price, side]
                if quantity:
                    result.append(Order(orders[0].symbol, price, quantity))
                if len(result) >= self.MAX_ORDERS_PER_PRODUCT:
                    break
            return result

        def trade_oxygen_morning(self, state: TradingState, memory: Dict) -> Tuple[List[Order], Dict]:
            mids = self.mids(state.order_depths)
            product = self.OXYGEN_MORNING
            depth = state.order_depths.get(product)
            if product not in mids or depth is None or (not depth.buy_orders) or (not depth.sell_orders):
                return ([], memory if isinstance(memory, dict) else {})
            if not all((product_name in mids for product_name in self.OXYGEN_GROUP)):
                return ([], memory if isinstance(memory, dict) else {})
            values = [mids[product_name] for product_name in self.OXYGEN_GROUP]
            mean = sum(values) / len(values)
            variance = sum(((value - mean) ** 2 for value in values)) / len(values)
            std = math.sqrt(variance)
            if std <= 0:
                return ([], memory if isinstance(memory, dict) else {})
            z = (mids[product] - mean) / std
            prev = float(memory.get('p', mids[product])) if isinstance(memory, dict) else mids[product]
            move = mids[product] - prev
            signal = -z - 0.2 * move / max(std, 1.0)
            next_memory = {'p': mids[product]}
            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            spread = best_ask - best_bid
            if spread < 3 or abs(signal) < 0.8:
                return ([], next_memory)
            position = state.position.get(product, 0)
            limit = self.POSITION_LIMIT
            qty = min(2, max(1, spread // 4))
            if signal > 0 and position < limit:
                return ([Order(product, best_bid + 1, min(qty, limit - position))], next_memory)
            if signal < 0 and position > -limit:
                return ([Order(product, best_ask - 1, -min(qty, limit + position))], next_memory)
            return ([], next_memory)

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
    __m=__CompiledModule()
    for __k,__v in list(locals().items()):
        if not __k.startswith('__'):setattr(__m,__k,__v)
    return __m
trader_ULTIMATE_FIX=__build_trader_ULTIMATE_FIX()
setattr(traders.ROUND_5,'trader_ULTIMATE_FIX',trader_ULTIMATE_FIX)

Trader = trader_ULTIMATE_FIX.Trader

__all__ = ['Trader']
