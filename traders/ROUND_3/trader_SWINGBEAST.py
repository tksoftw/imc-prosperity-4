"""ROUND_3 experiment: one-sided delta swing stack.

Compared with DELTABEAST, this stops quoting both sides in the high-delta
vouchers. It only adds option exposure when the underlying mean-reversion
edge is strong enough to justify paying the option spread.
"""

import math
from typing import Dict, List, Optional

from datamodel import Order, TradingState

from traders.ROUND_3 import trader_FLIPVOL as base
from traders.ROUND_3 import trader_DELTABEAST as delta


class Trader(delta.Trader):
    def trade_itm_flash(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        S: float,
        surface_shift: Optional[float],
        wing_signal: bool,
    ) -> None:
        # Keep original flash-arb, not DELTABEAST's two-sided overlay.
        base.Trader.trade_itm_flash(self, state, result, product, strike, S, surface_shift, wing_signal)

        q = base.quote_from(state.order_depths.get(product))
        if q.mid is None or q.bid is None or q.ask is None or q.spread is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        spot_edge = float(getattr(self, "_spot_edge", 0.0))
        if abs(spot_edge) < 2.0:
            return

        fair = max(S - strike, 0.0) + spot_edge - 0.004 * position
        take_edge = 2.0 if strike == 4500 else 2.5
        quote_edge = 1.0 if strike == 4500 else 1.5
        size = 28 if strike == 4500 else 24

        if spot_edge > 0:
            if q.ask <= fair - take_edge:
                self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))
            if q.spread >= 5:
                bid = int(min(q.bid + 1, math.floor(fair - quote_edge)))
                if 0 < bid < q.ask:
                    self.buy(state, product, orders, bid, size)
        else:
            if q.bid >= fair + take_edge:
                self.sell(state, product, orders, q.bid, min(q.bid_vol, size))
            if q.spread >= 5:
                ask = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
                if ask > q.bid:
                    self.sell(state, product, orders, ask, size)

    def trade_5000(self, state, store, result, S, spot_edge: float):
        product, strike, anchor = "VEV_5000", 5000, 13.0
        q = base.quote_from(state.order_depths.get(product))
        if q.mid is None or q.bid is None or q.ask is None or q.spread is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = base.update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend + 1.10 * spot_edge - 0.006 * position
        fair += self.option_micro_reversion(store, product, q, S, 0.92, 0.75)

        if spot_edge > 1.6:
            if q.ask <= fair - 1.2:
                self.buy(state, product, orders, q.ask, min(-q.ask_vol, 26))
            if q.spread >= 3:
                bid = int(min(q.bid + 1, math.floor(fair - 0.5)))
                if 0 < bid < q.ask:
                    self.buy(state, product, orders, bid, 22)
        elif spot_edge < -1.6:
            if q.bid >= fair + 1.2:
                self.sell(state, product, orders, q.bid, min(q.bid_vol, 26))
            if q.spread >= 3:
                ask = int(max(q.ask - 1, math.ceil(fair + 0.5)))
                if ask > q.bid:
                    self.sell(state, product, orders, ask, 22)

    def trade_smile_atm(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        smile_lv: float,
        product: str,
        strike: int,
        S: float,
        v_active: bool,
        spot_edge: float,
    ) -> None:
        q = base.quote_from(state.order_depths.get(product))
        if q.mid is None or q.bid is None or q.ask is None or q.spread is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        moneyness = (strike - S) / 100.0
        fair_iv = max(base.IV_LO, min(base.IV_HI, smile_lv + base.smile_offset(moneyness)))
        delta_val = base.bs_delta(S, strike, base.TTE, fair_iv)
        fair = base.bs_call(S, strike, base.TTE, fair_iv)
        mult = 1.15 if strike == 5100 else 1.45
        fair += mult * delta_val * spot_edge
        fair += self.option_micro_reversion(store, product, q, S, delta_val, 0.50)
        fair -= (0.007 if strike == 5100 else 0.005) * position

        threshold = 1.5 if strike == 5100 else 1.2
        size = 22 if strike == 5100 else 26
        if spot_edge > threshold:
            if q.ask <= fair - 0.8:
                self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))
            if q.spread >= 2:
                bid = int(min(q.bid + 1, math.floor(fair - 0.3)))
                if 0 < bid < q.ask:
                    self.buy(state, product, orders, bid, size)
        elif spot_edge < -threshold:
            if q.bid >= fair + 0.8:
                self.sell(state, product, orders, q.bid, min(q.bid_vol, size))
            if q.spread >= 2:
                ask = int(max(q.ask - 1, math.ceil(fair + 0.3)))
                if ask > q.bid:
                    self.sell(state, product, orders, ask, size)
