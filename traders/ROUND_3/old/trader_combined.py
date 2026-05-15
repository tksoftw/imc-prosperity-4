"""ROUND_3: combined "trade everything" strategy.

Pulls the best per-product piece from each existing strategy and adds a
generic IV-fit market maker for the strikes that nobody else trades.

Per-product source of edge (mapped from `uv run rank --show-per-product`):

  HYDROGEL_PACK
    - flash_bug HYDRO: 0.20*mid + 0.25*fast_ema + 0.40*slow_ema + 0.15*anchor.
      Take when book strays >= 7 from fair, quote inside when spread >= 10.

  VELVETFRUIT_EXTRACT
    - flash_bug VELVET: fast EMA blended with mean (5255), insider tilt
      whenever a >=9 contract trade prints (Accumulator), inventory skew.

  VEV_4000 / VEV_4500
    - flash_bug deep-ITM flash arb. Fair = max(S-K, 0). Buys whenever the
      shared surface shift across both strikes flashes negative or the
      individual ask drops below intrinsic; passive quotes inside the
      wide book otherwise.

  VEV_5300
    - claudecarry extrinsic mean-reversion with obs_ext_guard (current
      observed extrinsic must be at/above EMA - 1.5 to fire a buy). This
      kills the EMA-lag bug that bled VEV_5200 in earlier versions.

  VEV_5000 / VEV_5100 / VEV_5200  (NEW)
    - ATM-ish strikes carrying real extrinsic. Use a per-strike empirical
      extrinsic anchor (calibrated from notebook conclusions) blended with
      a small spot-EMA correction. Quote 1 inside the book on each side
      with conservative size + inventory skew.

  VEV_5400 / VEV_5500 / VEV_6000 / VEV_6500  (NEW)
    - Wing-Seller dump zone. Books are 1 tick wide so we cannot quote
      inside, but we can SCALP CHEAP ASKS when the floor (intrinsic) is
      far below the printed mid. Specifically: if the option ask is below
      the empirical extrinsic floor, we lift it; symmetric on the bid.
      Sizing is tiny — these are pure mispricing scalps.

Position limits per the rust_backtester (200 base, 300 wing) are obeyed
via soft caps to keep HYDRO/VELVET inventory available for the heavy MM
strategies.
"""

import json
import math
from typing import Dict, List, Optional, Tuple

from datamodel import Order, TradingState


VELVET = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"

ITM_STRIKES = {
    "VEV_4000": 4000,
    "VEV_4500": 4500,
}

# ATM strikes carrying meaningful extrinsic; we quote both sides.
# Empirical extrinsic anchors come from notebooks/round3/conclusions.ipynb
# (mid-round average, days 0-2). Re-tune as data arrives.
ATM_STRIKES = {
    "VEV_5000": 5000,
    "VEV_5100": 5100,
    "VEV_5200": 5200,
    "VEV_5300": 5300,
}
ATM_EXTRINSIC = {
    5000: 13.0,
    5100: 7.0,
    5200: 50.0 - 5200 + 5200,  # placeholder — replaced below
    5300: 47.0,
}
# Hand-calibrated. 5200 is right next to ATM so extrinsic is biggest.
ATM_EXTRINSIC = {
    5000: 13.0,
    5100: 8.0,
    5200: 52.0,
    5300: 47.0,
}

# Wing strikes — we only opportunistically lift cheap asks / hit rich bids.
WING_STRIKES = {
    "VEV_5400": 5400,
    "VEV_5500": 5500,
    "VEV_6000": 6000,
    "VEV_6500": 6500,
}
WING_EXTRINSIC = {
    5400: 16.0,
    5500: 6.0,
    6000: 1.0,
    6500: 0.5,
}

WING_PRODUCTS = tuple(WING_STRIKES.keys())

# ── Tuning constants ─────────────────────────────────────────────────────────

VELVET_MEAN = 5255.0
VELVET_SIGNAL_TTL = 1000
VELVET_INSIDER_TILT = 1.5

HYDRO_ANCHOR = 9991.0
HYDRO_FAST_WINDOW = 30
HYDRO_SLOW_WINDOW = 120
HYDRO_TAKE_EDGE = 7.0
HYDRO_QUOTE_EDGE = 4.0
HYDRO_QUOTE_SIZE = 18
HYDRO_MIN_SPREAD = 10

ITM_CONFIG = {
    "VEV_4000": {"take": 12, "unload": 10, "passive": 3},
    "VEV_4500": {"take": 8,  "unload": 7,  "passive": 2},
}

# VEV_5300 extrinsic mean-reversion (from claudecarry).
EXTRINSIC_5300_CONFIG = {
    "base": 47.0, "min": 42.0, "max": 50.0,
    "take_edge": 2.0, "exit_edge": 1.0,
    "ema_window": 100,
    "take_size": 12, "exit_size": 10, "passive_size": 6,
    "obs_ext_guard": 1.5,
}

# ATM (5000/5100/5200) IV-anchored MM. These strikes have wider books so
# we can post inside; sizing is moderate to keep gamma in check.
ATM_QUOTE_EDGE = 1.5
ATM_TAKE_EDGE = 3.0
ATM_QUOTE_SIZE = 5
ATM_MIN_SPREAD = 3
ATM_INVENTORY_SKEW = 0.05

# Wings: only take, don't quote. Books are usually 1 tick wide.
WING_TAKE_EDGE = 3.0
WING_TAKE_SIZE = 4

WING_SIGNAL_TTL = 200

LIMITS = {
    VELVET:    200,
    HYDROGEL:  200,
    "VEV_4000": 120,
    "VEV_4500": 90,
    "VEV_5000": 60,
    "VEV_5100": 50,
    "VEV_5200": 50,
    "VEV_5300": 40,
    "VEV_5400": 40,
    "VEV_5500": 30,
    "VEV_6000": 20,
    "VEV_6500": 20,
}


# ─── helpers ────────────────────────────────────────────────────────────────

def best_bid_ask(order_depth) -> Tuple[Optional[int], int, Optional[int], int]:
    bb = ba = None
    bv = av = 0
    if order_depth is not None and order_depth.buy_orders:
        bb = max(order_depth.buy_orders)
        bv = order_depth.buy_orders[bb]
    if order_depth is not None and order_depth.sell_orders:
        ba = min(order_depth.sell_orders)
        av = order_depth.sell_orders[ba]
    return bb, bv, ba, av


def mid_price(order_depth) -> Optional[float]:
    bb, _, ba, _ = best_bid_ask(order_depth)
    if bb is None or ba is None:
        return None
    return (bb + ba) / 2.0


# ─── Trader ─────────────────────────────────────────────────────────────────

class Trader:
    # ── State ──────────────────────────────────────────────────────────────

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

    def pos(self, state: TradingState, product: str) -> int:
        return int(state.position.get(product, 0))

    def buy_room(self, state, product, orders) -> int:
        used = sum(max(0, o.quantity) for o in orders)
        return max(0, LIMITS[product] - self.pos(state, product) - used)

    def sell_room(self, state, product, orders) -> int:
        used = sum(max(0, -o.quantity) for o in orders)
        return max(0, LIMITS[product] + self.pos(state, product) - used)

    def add_buy(self, state, product, orders, price, qty):
        qty = min(max(0, int(qty)), self.buy_room(state, product, orders))
        if qty > 0:
            orders.append(Order(product, int(price), qty))

    def add_sell(self, state, product, orders, price, qty):
        qty = min(max(0, int(qty)), self.sell_room(state, product, orders))
        if qty > 0:
            orders.append(Order(product, int(price), -qty))

    # ── Signals ────────────────────────────────────────────────────────────

    def update_signals(self, state, data) -> Tuple[bool, bool]:
        if any(abs(int(t.quantity)) >= 9 for t in state.market_trades.get(VELVET, [])):
            data["velvet_until"] = state.timestamp + VELVET_SIGNAL_TTL
        if any(state.market_trades.get(p) for p in ("VEV_5300",) + WING_PRODUCTS):
            data["wing_until"] = state.timestamp + WING_SIGNAL_TTL
        velvet_signal = state.timestamp <= int(data.get("velvet_until", -1))
        wing_signal = state.timestamp <= int(data.get("wing_until", -1))
        return velvet_signal, wing_signal

    def wing_flow_active(self, state) -> bool:
        return any(state.market_trades.get(p) for p in WING_PRODUCTS)

    def deep_itm_surface_shift(self, state, spot_mid) -> Optional[float]:
        shifts = []
        for product, strike in ITM_STRIKES.items():
            depth = state.order_depths.get(product)
            opt_mid = mid_price(depth)
            if opt_mid is None:
                continue
            shifts.append(opt_mid - max(spot_mid - strike, 0.0))
        return sum(shifts) / len(shifts) if shifts else None

    # ── HYDROGEL_PACK (flash_bug) ──────────────────────────────────────────

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
        fair -= 0.05 * position

        if ba <= fair - HYDRO_TAKE_EDGE:
            self.add_buy(state, HYDROGEL, orders, ba, min(-av, 28))
        if bb >= fair + HYDRO_TAKE_EDGE:
            self.add_sell(state, HYDROGEL, orders, bb, min(bv, 28))

        if spread >= HYDRO_MIN_SPREAD:
            tb = int(min(bb + 1, math.floor(fair - HYDRO_QUOTE_EDGE)))
            ta = int(max(ba - 1, math.ceil(fair + HYDRO_QUOTE_EDGE)))
            if tb < ta:
                self.add_buy(state, HYDROGEL, orders, tb, HYDRO_QUOTE_SIZE)
                self.add_sell(state, HYDROGEL, orders, ta, HYDRO_QUOTE_SIZE)

    # ── VELVETFRUIT_EXTRACT (flash_bug) ────────────────────────────────────

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
        fair = 0.7 * fast + 0.3 * VELVET_MEAN
        fair -= 0.015 * position
        if velvet_signal:
            fair += VELVET_INSIDER_TILT

        if velvet_signal and ba <= fair:
            self.add_buy(state, VELVET, orders, ba, min(-av, 25))

        tb = int(math.floor(fair - 2.0))
        ta = int(math.ceil(fair + 2.0))
        self.add_buy(state, VELVET, orders, tb, LIMITS[VELVET])
        self.add_sell(state, VELVET, orders, ta, LIMITS[VELVET])

    # ── VEV_4000 / VEV_4500 (flash_bug) ────────────────────────────────────

    def trade_flash_itm(self, state, result, product, strike, spot_mid, surface_shift, wing_flow):
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        opt_mid = mid_price(depth)
        if bb is None or ba is None or opt_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        fair = max(spot_mid - strike, 0.0)
        shared_shift = surface_shift if surface_shift is not None else (opt_mid - fair)
        cfg = ITM_CONFIG[product]

        flash_down = shared_shift <= -4.0
        flash_up = shared_shift >= 4.0
        actual_cheap = ba <= fair - 2
        cheap_touch = ba <= fair

        if flash_down and cheap_touch:
            size = cfg["take"]
            if actual_cheap:
                size += 4
            if wing_flow or (ba - bb) <= 10:
                size += 3
            self.add_buy(state, product, orders, ba, min(-av, size))

        if actual_cheap:
            self.add_buy(state, product, orders, ba, min(-av, cfg["take"] + 4))

        if position > 0:
            if bb >= fair + 1 or (flash_up and bb >= fair):
                self.add_sell(state, product, orders, bb,
                              min(position, bv, cfg["unload"]))
            ta = int(max(ba - 1, math.ceil(fair + 1.0)))
            if ta > bb:
                self.add_sell(state, product, orders, ta, min(position, 5))

        if position <= 0 and (ba - bb) >= 8 and not flash_up:
            tb = int(min(bb + 1, math.floor(fair - 2.0)))
            if 0 < tb < ba:
                self.add_buy(state, product, orders, tb, cfg["passive"])

    # ── VEV_5300 (claudecarry extrinsic mean-revert) ───────────────────────

    def trade_vev_5300(self, state, data, result, spot_mid, velvet_signal, wing_signal):
        product = "VEV_5300"
        strike = 5300
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        opt_mid = mid_price(depth)
        if bb is None or ba is None or opt_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)
        cfg = EXTRINSIC_5300_CONFIG

        intrinsic = max(spot_mid - strike, 0.0)
        observed_extrinsic = opt_mid - intrinsic
        ext_ema = self.ema(data, f"{product}_extrinsic", observed_extrinsic, cfg["ema_window"])
        extrinsic = max(cfg["min"], min(cfg["max"], ext_ema))

        spot_slow = self.ema(data, f"{VELVET}_slow", spot_mid, 80)
        spot_dev = spot_mid - spot_slow

        fair = intrinsic + 0.6 * extrinsic + 0.4 * cfg["base"] - 0.05 * position
        if velvet_signal:
            fair += 1.0

        obs_ext_ok = observed_extrinsic >= ext_ema - cfg["obs_ext_guard"]
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
            self.add_sell(state, product, orders, bb,
                          min(position, bv, cfg["exit_size"]))
        if position > 0:
            ta = int(max(ba - 1, math.ceil(fair + 1.0 + (1.0 if top_state else 0.0))))
            if ta > bb:
                self.add_sell(state, product, orders, ta,
                              min(position, cfg["passive_size"]))

    # ── VEV_5000 / VEV_5100 / VEV_5200 (NEW: ATM extrinsic-anchor MM) ──────

    def trade_atm_make(self, state, data, result, product, strike, spot_mid):
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        opt_mid = mid_price(depth)
        if bb is None or ba is None or opt_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)

        intrinsic = max(spot_mid - strike, 0.0)
        observed_extrinsic = opt_mid - intrinsic

        # Blend stable empirical extrinsic with observed extrinsic so that
        # we adapt to the current vol regime but don't get whipped by noise.
        anchor = ATM_EXTRINSIC[strike]
        ext_ema = self.ema(data, f"{product}_extrinsic", observed_extrinsic, 80)
        # Cap drift to +/- 6 from the historical anchor; keeps fair sane
        # even when one print causes a temporary distortion.
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend - ATM_INVENTORY_SKEW * position

        spread = ba - bb

        # Take obvious mispricings.
        if ba <= fair - ATM_TAKE_EDGE:
            self.add_buy(state, product, orders, ba, min(-av, 8))
        if bb >= fair + ATM_TAKE_EDGE:
            self.add_sell(state, product, orders, bb, min(bv, 8))

        # Quote inside when the book is wide enough.
        if spread >= ATM_MIN_SPREAD:
            tb = int(min(bb + 1, math.floor(fair - ATM_QUOTE_EDGE)))
            ta = int(max(ba - 1, math.ceil(fair + ATM_QUOTE_EDGE)))
            if tb < ta:
                if tb > 0:
                    self.add_buy(state, product, orders, tb, ATM_QUOTE_SIZE)
                self.add_sell(state, product, orders, ta, ATM_QUOTE_SIZE)

    # ── VEV_5400+ (NEW: wing scalp) ────────────────────────────────────────

    def trade_wing_scalp(self, state, data, result, product, strike, spot_mid):
        depth = state.order_depths.get(product)
        if depth is None:
            return
        bb, bv, ba, av = best_bid_ask(depth)
        opt_mid = mid_price(depth)
        if bb is None or ba is None or opt_mid is None:
            return

        orders = result.setdefault(product, [])
        position = self.pos(state, product)

        intrinsic = max(spot_mid - strike, 0.0)
        anchor = WING_EXTRINSIC[strike]
        observed_extrinsic = opt_mid - intrinsic
        ext_ema = self.ema(data, f"{product}_extrinsic", observed_extrinsic, 120)
        ext_blend = max(anchor - 4.0, min(anchor + 4.0, 0.4 * ext_ema + 0.6 * anchor))
        fair = intrinsic + ext_blend

        # Only act on clear mispricings — wings have 1-tick books and
        # constant Wing-Seller flow.
        if ba <= fair - WING_TAKE_EDGE:
            size = min(-av, WING_TAKE_SIZE)
            self.add_buy(state, product, orders, ba, size)
        if bb >= fair + WING_TAKE_EDGE:
            size = min(bv, WING_TAKE_SIZE)
            self.add_sell(state, product, orders, bb, size)

        # Inventory unwind: if we sit long/short a wing, ladder out at +/- 1
        # from fair.
        if position > 0:
            ta = int(max(ba, math.ceil(fair + 1.0)))
            if ta > bb:
                self.add_sell(state, product, orders, ta,
                              min(position, WING_TAKE_SIZE))
        elif position < 0:
            tb = int(min(bb, math.floor(fair - 1.0)))
            if 0 < tb < ba:
                self.add_buy(state, product, orders, tb,
                             min(-position, WING_TAKE_SIZE))

    # ── Main ───────────────────────────────────────────────────────────────

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

            self.trade_vev_5300(state, data, result, spot_mid, velvet_signal, wing_signal)

            for product, strike in ATM_STRIKES.items():
                if product == "VEV_5300":
                    continue
                self.trade_atm_make(state, data, result, product, strike, spot_mid)

            for product, strike in WING_STRIKES.items():
                self.trade_wing_scalp(state, data, result, product, strike, spot_mid)

        result = {p: orders for p, orders in result.items() if orders}
        return result, 0, self.save_state(data)
