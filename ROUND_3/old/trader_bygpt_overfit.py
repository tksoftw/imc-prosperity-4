from datamodel import Order, TradingState
from typing import Dict, List, Optional, Tuple
import math
import json

VELVET = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"

STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)
VEV_PRODUCTS = tuple(f"VEV_{k}" for k in STRIKES)
PRODUCTS = (VELVET, HYDROGEL) + VEV_PRODUCTS

MAX_POS = {
    VELVET: 200,
    HYDROGEL: 200,
    **{p: 300 for p in VEV_PRODUCTS},
}

SOFT_POS = {
    VELVET: 200,
    HYDROGEL: 200,
    "VEV_4000": 80,
    "VEV_4500": 60,
    "VEV_5000": 40,
    "VEV_5100": 0,
    "VEV_5200": 0,
    "VEV_5300": 70,
    "VEV_5400": 0,
    "VEV_5500": 0,
    "VEV_6000": 0,
    "VEV_6500": 0,
}

EXTRINSIC = {
    4000: 0.0,
    4500: 0.0,
    5300: 47.0,
}


def best_bid_ask(order_depth) -> Tuple[Optional[int], int, Optional[int], int]:
    best_bid = None
    bid_vol = 0
    best_ask = None
    ask_vol = 0

    if order_depth is not None and order_depth.buy_orders:
        best_bid = max(order_depth.buy_orders)
        bid_vol = order_depth.buy_orders[best_bid]

    if order_depth is not None and order_depth.sell_orders:
        best_ask = min(order_depth.sell_orders)
        ask_vol = order_depth.sell_orders[best_ask]

    return best_bid, bid_vol, best_ask, ask_vol


def mid_price(order_depth) -> Optional[float]:
    bid, _, ask, _ = best_bid_ask(order_depth)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def spread(order_depth) -> Optional[int]:
    bid, _, ask, _ = best_bid_ask(order_depth)
    if bid is None or ask is None:
        return None
    return ask - bid


class Trader:
    def bid(self):
        return 15

    def load_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"emas": {}, "velvet_until": -1, "vev_until": -1, "vev_dir": 0}
        try:
            data = json.loads(trader_data)
            if "emas" not in data:
                data["emas"] = {}
            if "velvet_until" not in data:
                data["velvet_until"] = -1
            if "vev_until" not in data:
                data["vev_until"] = -1
            if "vev_dir" not in data:
                data["vev_dir"] = 0
            return data
        except Exception:
            return {"emas": {}, "velvet_until": -1, "vev_until": -1, "vev_dir": 0}

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

    def buy_room(self, state: TradingState, product: str, orders: List[Order], soft: bool = True) -> int:
        limit = SOFT_POS.get(product, MAX_POS.get(product, 0)) if soft else MAX_POS.get(product, 0)
        already = sum(max(0, o.quantity) for o in orders)
        return max(0, limit - self.pos(state, product) - already)

    def sell_room(self, state: TradingState, product: str, orders: List[Order], soft: bool = True) -> int:
        limit = SOFT_POS.get(product, MAX_POS.get(product, 0)) if soft else MAX_POS.get(product, 0)
        already = sum(max(0, -o.quantity) for o in orders)
        return max(0, limit + self.pos(state, product) - already)

    def add_buy(self, state: TradingState, product: str, orders: List[Order], price: int, qty: int, soft: bool = True) -> None:
        qty = min(max(0, int(qty)), self.buy_room(state, product, orders, soft))
        if qty > 0:
            orders.append(Order(product, int(price), qty))

    def add_sell(self, state: TradingState, product: str, orders: List[Order], price: int, qty: int, soft: bool = True) -> None:
        qty = min(max(0, int(qty)), self.sell_room(state, product, orders, soft))
        if qty > 0:
            orders.append(Order(product, int(price), -qty))

    def tight_vev_surface(self, state: TradingState) -> bool:
        d5200 = state.order_depths.get("VEV_5200")
        d5300 = state.order_depths.get("VEV_5300")
        s5200 = spread(d5200)
        s5300 = spread(d5300)
        return s5200 is not None and s5300 is not None and s5200 <= 2 and s5300 <= 2

    def update_signals(self, data: Dict, state: TradingState) -> Tuple[bool, int]:
        for tr in state.market_trades.get(VELVET, []):
            if abs(int(tr.quantity)) >= 9:
                data["velvet_until"] = state.timestamp + 1800
                break

        if self.tight_vev_surface(state):
            score = 0
            for product in ("VEV_5200", "VEV_5300"):
                depth = state.order_depths.get(product)
                mid = mid_price(depth)
                if mid is None:
                    continue
                bid, _, ask, _ = best_bid_ask(depth)

                for tr in state.market_trades.get(product, []):
                    px = int(tr.price)
                    q = abs(int(tr.quantity))

                    if ask is not None and px >= ask:
                        score += q
                    elif bid is not None and px <= bid:
                        score -= q
                    elif px > mid:
                        score += q
                    elif px < mid:
                        score -= q

            if score > 0:
                data["vev_dir"] = 1
                data["vev_until"] = state.timestamp + 1200
            elif score < 0:
                data["vev_dir"] = -1
                data["vev_until"] = state.timestamp + 1200

        velvet_signal = state.timestamp <= int(data.get("velvet_until", -1))
        vev_dir = int(data.get("vev_dir", 0)) if state.timestamp <= int(data.get("vev_until", -1)) else 0
        return velvet_signal, vev_dir

    def trade_hydrogel(self, state: TradingState, result: Dict[str, List[Order]]) -> None:
        product = HYDROGEL
        depth = state.order_depths.get(product)
        if depth is None:
            return

        orders = result.setdefault(product, [])
        fair = 10000

        for price, volume in sorted(depth.sell_orders.items()):
            if price <= fair - 7:
                self.add_buy(state, product, orders, price, min(-volume, 35), soft=True)

        for price, volume in sorted(depth.buy_orders.items(), reverse=True):
            if price >= fair + 7:
                self.add_sell(state, product, orders, price, min(volume, 35), soft=True)

    def trade_velvet(self, state: TradingState, data: Dict, result: Dict[str, List[Order]], velvet_signal: bool, vev_dir: int) -> None:
        product = VELVET
        depth = state.order_depths.get(product)
        if depth is None:
            return

        bid, bid_vol, ask, ask_vol = best_bid_ask(depth)
        mid = mid_price(depth)
        if bid is None or ask is None or mid is None:
            return

        orders = result.setdefault(product, [])

        fast = self.ema(data, f"{product}_fast", mid, 15)
        pos = self.pos(state, product)

        fair = fast
        fair -= 0.015 * pos

        if velvet_signal:
            fair += 2.5

        if vev_dir != 0:
            fair += 1.5 * vev_dir

        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))

        if velvet_signal and ask <= fair:
            self.add_buy(state, product, orders, ask, min(-ask_vol, 25), soft=True)
        if vev_dir > 0 and ask <= fair:
            self.add_buy(state, product, orders, ask, min(-ask_vol, 15), soft=True)
        if vev_dir < 0 and bid >= fair:
            self.add_sell(state, product, orders, bid, min(bid_vol, 15), soft=True)

        if target_bid < ask:
            self.add_buy(state, product, orders, target_bid, 200, soft=True)
        if target_ask > bid:
            self.add_sell(state, product, orders, target_ask, 200, soft=True)

    def trade_deep_itm(self, state: TradingState, result: Dict[str, List[Order]], product: str, strike: int, S: float) -> None:
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bid, bid_vol, ask, ask_vol = best_bid_ask(depth)
        if bid is None or ask is None:
            return

        orders = result.setdefault(product, [])
        fair = max(S - strike, 0.0)

        take_edge = 3.0 if strike == 4000 else 4.0
        quote_edge = 2.0 if strike == 4000 else 3.0

        if ask < fair - take_edge:
            self.add_buy(state, product, orders, ask, min(-ask_vol, 8), soft=True)
        if bid > fair + take_edge:
            self.add_sell(state, product, orders, bid, min(bid_vol, 8), soft=True)

        sp = ask - bid
        if sp >= 4:
            buy_px = min(bid + 1, int(math.floor(fair - quote_edge)))
            sell_px = max(ask - 1, int(math.ceil(fair + quote_edge)))
            if buy_px < sell_px:
                self.add_buy(state, product, orders, buy_px, 3, soft=True)
                self.add_sell(state, product, orders, sell_px, 3, soft=True)

    def trade_vev_5300(self, state: TradingState, result: Dict[str, List[Order]], S: float, vev_dir: int) -> None:
        product = "VEV_5300"
        strike = 5300
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bid, bid_vol, ask, ask_vol = best_bid_ask(depth)
        if bid is None or ask is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        sp = ask - bid

        fair = max(S - strike, 0.0) + EXTRINSIC[strike]
        fair -= 0.06 * position

        if vev_dir != 0:
            fair += 2.0 * vev_dir

        take_edge = 2.0
        quote_edge = 1.0

        if ask < fair - take_edge:
            self.add_buy(state, product, orders, ask, min(-ask_vol, 10), soft=True)
        if bid > fair + take_edge:
            self.add_sell(state, product, orders, bid, min(bid_vol, 10), soft=True)

        if sp >= 2:
            buy_px = min(bid + 1, int(math.floor(fair - quote_edge)))
            sell_px = max(ask - 1, int(math.ceil(fair + quote_edge)))
            if buy_px < sell_px:
                self.add_buy(state, product, orders, buy_px, 5, soft=True)
                self.add_sell(state, product, orders, sell_px, 5, soft=True)

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        data = self.load_state(state.traderData)

        velvet_signal, vev_dir = self.update_signals(data, state)

        self.trade_hydrogel(state, result)
        self.trade_velvet(state, data, result, velvet_signal, vev_dir)

        S = mid_price(state.order_depths.get(VELVET))
        if S is not None:
            self.trade_deep_itm(state, result, "VEV_4000", 4000, S)
            self.trade_deep_itm(state, result, "VEV_4500", 4500, S)
            self.trade_vev_5300(state, result, S, vev_dir)

        result = {p: orders for p, orders in result.items() if orders}
        return result, 0, self.save_state(data)