import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, TradingState


VELVET = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"

# Deep ITM — flash-arb on surface dislocations.
# VEV_5000 removed: extrinsic ~3 (noisy, near zero) poisons the shared
# surface-shift signal and the spread is too wide to trade profitably.
ITM_STRIKES = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
}

# ATM-ish — mean-reversion on extrinsic premium.
# VEV_5200 removed: EMA lag causes buys right when spot spikes and
# extrinsic compresses; we accumulate into the move and unwind at a loss.
# VEV_5100 removed: same structural issue, smaller magnitude.
EXTRINSIC_STRIKES = {
    "VEV_5300": 5300,
}

WING_PRODUCTS = ("VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")

# ── VFE ──────────────────────────────────────────────────────────────────────
# Reverted weights to 0.70/0.30 — the 0.75/0.25 split underperformed on
# the day-2 price path (early rally to 5276 caused more short fills).
# Mean updated to 5262 (actual observed mean) but weight kept conservative
# so EMA can adapt without fighting the anchor.
VELVET_MEAN = 5262.0
VELVET_SIGNAL_TTL = 1000
VELVET_INSIDER_TILT = 1.5
VELVET_QUOTE_EDGE = 2.0
VELVET_POSITION_SKEW = 0.02   # kept from v2 — inventory improved dramatically

# ── HGP ──────────────────────────────────────────────────────────────────────
# All v2 changes kept — anchor correction lifted HGP PnL by +1872.
HYDRO_ANCHOR = 9980.0
HYDRO_FAST_WINDOW = 30
HYDRO_SLOW_WINDOW = 120
HYDRO_TAKE_EDGE = 7.0
HYDRO_QUOTE_EDGE = 5.0
HYDRO_QUOTE_SIZE = 18
HYDRO_MIN_SPREAD = 10
HYDRO_POSITION_SKEW = 0.06

# ── Deep ITM flash config ─────────────────────────────────────────────────────
ITM_CONFIG = {
    "VEV_4000": {"take": 12, "unload": 10, "passive": 3},
    "VEV_4500": {"take": 8,  "unload": 7,  "passive": 2},
}

# ── VEV_5300 extrinsic config ─────────────────────────────────────────────────
# Added obs_ext_guard: only buy when current observed extrinsic is also
# cheap (not just EMA cheap). Prevents the EMA-lag buy-the-spike problem.
EXTRINSIC_CONFIG = {
    "VEV_5300": {
        "base": 47.0, "min": 42.0, "max": 50.0,
        "take_edge": 2.0, "exit_edge": 1.0,
        "ema_window": 100, "take_size": 12, "exit_size": 10, "passive_size": 6,
        "obs_ext_guard": 1.5,  # NEW: observed extrinsic must be > EMA - guard to buy
    },
}

WING_SIGNAL_TTL = 200

LIMITS = {
    VELVET:   200,
    HYDROGEL: 200,
    "VEV_4000": 120,
    "VEV_4500": 90,
    "VEV_5300": 40,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def best_bid_ask(order_depth) -> Tuple[Optional[int], int, Optional[int], int]:
    best_bid = best_ask = None
    bid_volume = ask_volume = 0
    if order_depth is not None and order_depth.buy_orders:
        best_bid = max(order_depth.buy_orders)
        bid_volume = order_depth.buy_orders[best_bid]
    if order_depth is not None and order_depth.sell_orders:
        best_ask = min(order_depth.sell_orders)
        ask_volume = order_depth.sell_orders[best_ask]
    return best_bid, bid_volume, best_ask, ask_volume


def mid_price(order_depth) -> Optional[float]:
    bb, _, ba, _ = best_bid_ask(order_depth)
    if bb is None or ba is None:
        return None
    return (bb + ba) / 2.0


# ─────────────────────────────────────────────────────────────────────────────
# Trader
# ─────────────────────────────────────────────────────────────────────────────

class Trader:
    # ── State ─────────────────────────────────────────────────────────────────

    def load_state(self, trader_data: str) -> Dict:
        if not trader_data:
            return {"emas": {}, "velvet_until": -1, "wing_until": -1}
        try:
            data = json.loads(trader_data)
        except Exception:
            return {"emas": {}, "velvet_until": -1, "wing_until": -1}
        data.setdefault("emas", {})
        data.setdefault("velvet_until", -1)
        data.setdefault("wing_until", -1)
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

    # ── Position helpers ───────────────────────────────────────────────────────

    def pos(self, state: TradingState, product: str) -> int:
        return int(state.position.get(product, 0))

    def buy_room(self, state: TradingState, product: str, orders: List[Order]) -> int:
        used = sum(max(0, o.quantity) for o in orders)
        return max(0, LIMITS[product] - self.pos(state, product) - used)

    def sell_room(self, state: TradingState, product: str, orders: List[Order]) -> int:
        used = sum(max(0, -o.quantity) for o in orders)
        return max(0, LIMITS[product] + self.pos(state, product) - used)

    def add_buy(self, state, product, orders, price, quantity):
        quantity = min(max(0, int(quantity)), self.buy_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), quantity))

    def add_sell(self, state, product, orders, price, quantity):
        quantity = min(max(0, int(quantity)), self.sell_room(state, product, orders))
        if quantity > 0:
            orders.append(Order(product, int(price), -quantity))

    # ── Signal detection ───────────────────────────────────────────────────────

    def wing_flow_active(self, state: TradingState) -> bool:
        return any(state.market_trades.get(p) for p in WING_PRODUCTS)

    def update_signals(self, state: TradingState, data: Dict) -> Tuple[bool, bool]:
        if any(abs(int(t.quantity)) >= 9 for t in state.market_trades.get(VELVET, [])):
            data["velvet_until"] = state.timestamp + VELVET_SIGNAL_TTL

        wing_products_active = ("VEV_5300",) + WING_PRODUCTS
        if any(state.market_trades.get(p) for p in wing_products_active):
            data["wing_until"] = state.timestamp + WING_SIGNAL_TTL

        velvet_signal = state.timestamp <= int(data.get("velvet_until", -1))
        wing_signal = state.timestamp <= int(data.get("wing_until", -1))
        return velvet_signal, wing_signal

    def deep_itm_surface_shift(self, state: TradingState, spot_mid: float) -> Optional[float]:
        """Average extrinsic across the two clean deep ITM options.

        VEV_4000 and VEV_4500 both price near-zero extrinsic at fair value.
        A non-zero average signals a surface-wide dislocation worth trading.
        VEV_5000 is intentionally excluded — its ~3pt mean extrinsic biases
        the signal and causes phantom flash_down triggers.
        """
        shifts: List[float] = []
        for product, strike in ITM_STRIKES.items():
            depth = state.order_depths.get(product)
            option_mid = mid_price(depth)
            if option_mid is None:
                continue
            shifts.append(option_mid - max(spot_mid - strike, 0.0))
        return sum(shifts) / len(shifts) if shifts else None

    # ── Product strategies ─────────────────────────────────────────────────────

    def trade_hydrogel(self, state, data, result):
        depth = state.order_depths.get(HYDROGEL)
        if depth is None:
            return
        orders = result.setdefault(HYDROGEL, [])
        bb, bv, ba, av = best_bid_ask(depth)
        mid = mid_price(depth)
        if bb is None or ba is None or mid is None:
            return

        position = self.pos(state, HYDROGEL)
        spread = ba - bb

        fast = self.ema(data, f"{HYDROGEL}_fast", mid, HYDRO_FAST_WINDOW)
        slow = self.ema(data, f"{HYDROGEL}_slow", mid, HYDRO_SLOW_WINDOW)
        fair = 0.20 * mid + 0.25 * fast + 0.40 * slow + 0.15 * HYDRO_ANCHOR
        fair -= HYDRO_POSITION_SKEW * position

        if ba <= fair - HYDRO_TAKE_EDGE:
            self.add_buy(state, HYDROGEL, orders, ba, min(-av, 28))
        if bb >= fair + HYDRO_TAKE_EDGE:
            self.add_sell(state, HYDROGEL, orders, bb, min(bv, 28))

        if spread >= HYDRO_MIN_SPREAD:
            target_bid = int(min(bb + 1, math.floor(fair - HYDRO_QUOTE_EDGE)))
            target_ask = int(max(ba - 1, math.ceil(fair + HYDRO_QUOTE_EDGE)))
            if target_bid < target_ask:
                self.add_buy(state, HYDROGEL, orders, target_bid, HYDRO_QUOTE_SIZE)
                self.add_sell(state, HYDROGEL, orders, target_ask, HYDRO_QUOTE_SIZE)

    def trade_velvet(self, state, data, result, velvet_signal):
        depth = state.order_depths.get(VELVET)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        mid = mid_price(depth)
        if bb is None or ba is None or mid is None:
            return

        orders = result.setdefault(VELVET, [])
        position = self.pos(state, VELVET)
        fast = self.ema(data, f"{VELVET}_fast", mid, 15)
        # 0.70/0.30 split restored — outperforms 0.75/0.25 on day-2 path
        fair = 0.70 * fast + 0.30 * VELVET_MEAN
        fair -= VELVET_POSITION_SKEW * position
        if velvet_signal:
            fair += VELVET_INSIDER_TILT

        if velvet_signal and ba <= fair:
            self.add_buy(state, VELVET, orders, ba, min(-av, 25))

        target_bid = int(math.floor(fair - VELVET_QUOTE_EDGE))
        target_ask = int(math.ceil(fair + VELVET_QUOTE_EDGE))
        self.add_buy(state, VELVET, orders, target_bid, LIMITS[VELVET])
        self.add_sell(state, VELVET, orders, target_ask, LIMITS[VELVET])

    def trade_flash_itm(self, state, result, product, strike, spot_mid, surface_shift, wing_flow):
        """Deep ITM flash-arb on VEV_4000 and VEV_4500."""
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        option_mid = mid_price(depth)
        if bb is None or ba is None or option_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        fair = max(spot_mid - strike, 0.0)
        shared_shift = surface_shift if surface_shift is not None else (option_mid - fair)
        cfg = ITM_CONFIG[product]

        flash_down = shared_shift <= -4.0
        flash_up = shared_shift >= 4.0
        actual_cheap = ba <= fair - 2
        cheap_touch = ba <= fair

        take_size = cfg["take"]
        unload_size = cfg["unload"]

        if flash_down and cheap_touch:
            size = take_size
            if actual_cheap:
                size += 4
            if wing_flow or (ba - bb) <= 10:
                size += 3
            self.add_buy(state, product, orders, ba, min(-av, size))

        if actual_cheap:
            self.add_buy(state, product, orders, ba, min(-av, take_size + 4))

        if position > 0:
            if bb >= fair + 1 or (flash_up and bb >= fair):
                self.add_sell(state, product, orders, bb, min(position, bv, unload_size))
            target_ask = int(max(ba - 1, math.ceil(fair + 1.0)))
            if target_ask > bb:
                self.add_sell(state, product, orders, target_ask, min(position, 5))

        if position <= 0 and (ba - bb) >= 8 and not flash_up:
            target_bid = int(min(bb + 1, math.floor(fair - 2.0)))
            if target_bid > 0 and target_bid < ba:
                self.add_buy(state, product, orders, target_bid, cfg["passive"])

    def trade_extrinsic(self, state, data, result, product, strike, spot_mid, velvet_signal, wing_signal):
        """Extrinsic mean-reversion for VEV_5300.

        obs_ext_guard prevents buying when the current observed extrinsic is
        compressing (even if the EMA is still elevated). This is the fix for
        the EMA-lag bug that burned -598 on VEV_5200 in v2.
        """
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        option_mid = mid_price(depth)
        if bb is None or ba is None or option_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        cfg = EXTRINSIC_CONFIG[product]

        intrinsic = max(spot_mid - strike, 0.0)
        observed_extrinsic = option_mid - intrinsic
        extrinsic_ema = self.ema(data, f"{product}_extrinsic", observed_extrinsic, cfg["ema_window"])
        extrinsic = max(cfg["min"], min(cfg["max"], extrinsic_ema))

        spot_slow = self.ema(data, f"{VELVET}_slow", spot_mid, 80)
        spot_dev = spot_mid - spot_slow

        fair = intrinsic + 0.6 * extrinsic + 0.4 * cfg["base"] - 0.05 * position
        if velvet_signal:
            fair += 1.0

        # Guard: don't buy if observed extrinsic is already compressing
        # relative to EMA. EMA lag would otherwise inflate fair during spot spikes.
        obs_ext_ok = observed_extrinsic >= extrinsic_ema - cfg["obs_ext_guard"]

        cheap = ba <= fair - cfg["take_edge"] and obs_ext_ok
        wing_cheap = wing_signal and ba <= fair - 1.0 and spot_dev <= 1.0 and obs_ext_ok
        top_state = spot_dev >= 4.0 and not velvet_signal

        if cheap:
            size = cfg["take_size"] + (2 if wing_signal else 0)
            self.add_buy(state, product, orders, ba, min(-av, size))
        elif wing_cheap:
            self.add_buy(state, product, orders, ba, min(-av, 5))
        elif velvet_signal and ba <= fair and obs_ext_ok:
            self.add_buy(state, product, orders, ba, min(-av, cfg["passive_size"]))

        if position > 0 and (bb >= fair + cfg["exit_edge"] or top_state):
            self.add_sell(state, product, orders, bb, min(position, bv, cfg["exit_size"]))

        if position > 0:
            target_ask = int(max(ba - 1, math.ceil(fair + 1.0 + (1.0 if top_state else 0.0))))
            if target_ask > bb:
                self.add_sell(state, product, orders, target_ask, min(position, cfg["passive_size"]))

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        data = self.load_state(state.traderData)
        velvet_signal, wing_signal = self.update_signals(state, data)

        self.trade_hydrogel(state, data, result)
        self.trade_velvet(state, data, result, velvet_signal)

        spot_mid = mid_price(state.order_depths.get(VELVET))
        if spot_mid is not None:
            surface_shift = self.deep_itm_surface_shift(state, spot_mid)
            wing_flow = wing_signal or self.wing_flow_active(state)

            for product, strike in ITM_STRIKES.items():
                self.trade_flash_itm(
                    state, result, product, strike,
                    spot_mid, surface_shift, wing_flow,
                )

            for product, strike in EXTRINSIC_STRIKES.items():
                self.trade_extrinsic(
                    state, data, result, product, strike,
                    spot_mid, velvet_signal, wing_signal,
                )

        result = {p: orders for p, orders in result.items() if orders}
        return result, 0, self.save_state(data)