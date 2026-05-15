"""ROUND_3 experiment: stack VELVET mean-reversion into delta vouchers.

This is intentionally thin: it reuses trader_FLIPVOL and only changes the
missing class of edge suggested by the high-PnL target, namely using
VEV_4000..5200 as additional delta capacity for the underlying signal.
"""

import math
from typing import Dict, List, Optional

from datamodel import Order, TradingState

from traders.ROUND_3 import trader_FLIPVOL as base


class Trader(base.Trader):
    def update_low_iv_trail(self, store: Dict, total_pnl: float, smile_lv: float) -> None:
        # Public target keeps harvesting options; do not switch into the
        # conservative day-3 risk-off mode while hunting this edge.
        return

    def velvet_spot_edge(
        self,
        state: TradingState,
        store: Dict,
        S: float,
        v_active: bool,
        implied_tilt: float,
        v4_dir: int,
    ) -> float:
        fast = float(store["emas"].get(f"{base.VELVET}_fast", S))
        fair = 0.7 * fast + 0.3 * 5255.0
        fair -= 0.002 * self.position(state, base.VELVET)
        if v_active:
            fair += 1.5
        fair += implied_tilt
        fair += 1.0 * v4_dir
        edge = max(-18.0, min(18.0, fair - S))
        self._spot_edge = edge
        return edge

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
        super().trade_itm_flash(state, result, product, strike, S, surface_shift, wing_signal)

        q = base.quote_from(state.order_depths.get(product))
        if q.mid is None or q.bid is None or q.ask is None or q.spread is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        spot_edge = float(getattr(self, "_spot_edge", 0.0))
        if abs(spot_edge) < 1.25:
            return

        # Deep ITM vouchers are almost delta-one VELVET with a wider book.
        # Quote only when the underlying signal is strong enough to pay toll.
        fair = max(S - strike, 0.0) + spot_edge - 0.006 * position
        take_edge = 3.0 if strike == 4000 else 2.5
        quote_edge = 2.0 if strike == 4000 else 1.5
        size = 18 if strike == 4000 else 16

        if q.ask <= fair - take_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))
        if q.bid >= fair + take_edge:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, size))

        if q.spread >= 5:
            target_bid = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if target_bid < target_ask:
                if spot_edge > -4.0:
                    self.buy(state, product, orders, target_bid, 10)
                if spot_edge < 4.0:
                    self.sell(state, product, orders, target_ask, 10)

    def trade_5000(self, state, store, result, S, spot_edge: float):
        product, strike, anchor = "VEV_5000", 5000, 13.0
        q = base.quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = base.update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend + 1.05 * spot_edge - 0.008 * position
        fair += self.option_micro_reversion(store, product, q, S, 0.92, 1.0)

        if q.ask <= fair - 1.6:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 18))
        if q.bid >= fair + 1.6:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 18))

        if q.spread >= 3:
            target_bid = int(min(q.bid + 1, math.floor(fair - 0.8)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 0.8)))
            if target_bid < target_ask:
                if spot_edge >= -4.0:
                    self.buy(state, product, orders, target_bid, 12)
                if spot_edge <= 4.0:
                    self.sell(state, product, orders, target_ask, 12)

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
        if q.mid is None or q.spread is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        moneyness = (strike - S) / 100.0
        fair_iv = max(base.IV_LO, min(base.IV_HI, smile_lv + base.smile_offset(moneyness)))
        delta = base.bs_delta(S, strike, base.TTE, fair_iv)
        fair = base.bs_call(S, strike, base.TTE, fair_iv)
        # Core change: full underlying mean-reversion edge, especially 5200.
        fair += 1.25 * delta * spot_edge
        fair += self.option_micro_reversion(store, product, q, S, delta, 0.65)
        fair -= (0.010 if strike == 5100 else 0.008) * position

        take_edge = 1.2 if strike == 5100 else 1.0
        quote_edge = 0.6 if strike == 5100 else 0.5
        size = 14 if strike == 5100 else 16

        if q.ask <= fair - take_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))
        if q.bid >= fair + take_edge:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, size))

        if q.spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if target_bid < target_ask:
                if spot_edge >= -4.0:
                    self.buy(state, product, orders, target_bid, size)
                if spot_edge <= 4.0:
                    self.sell(state, product, orders, target_ask, size)
