"""ROUND_3: IVREGIME.

BUGALPHA backbone plus a narrow IV/smile regime overlay:

* Compute the live smile level from the existing Black-Scholes ladder.
* Track a slow EMA of that level.
* If surface IV is unusually cheap, place one-sided passive bids in the
  liquid ATM options to own gamma.
* If surface IV is unusually rich, place one-sided passive offers.

No aggressive crossing. No two-sided churn. The goal is to capture the
IV/gamma effect only when the surface itself is stretched.
"""

import math
from typing import Dict, List

from datamodel import Order, TradingState

from ROUND_3 import trader_BUGALPHA as bug
from ROUND_3 import trader_FLIPVOL as base


IV_LOW = -0.0012
IV_HIGH = 0.0012


class Trader(bug.Trader):
    def smile_level(self, state: TradingState, store: Dict, S: float) -> float:
        lv = super().smile_level(state, store, S)
        slow = base.update_ema(store["emas"], "ivregime_slow", lv, 600)
        store["ivregime_dev"] = lv - slow
        store["ivregime_lv"] = lv
        return lv

    def _passive_iv_regime(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        product: str,
        size: int,
    ) -> None:
        dev = float(store.get("ivregime_dev", 0.0))
        if abs(dev) < (IV_HIGH if dev > 0 else abs(IV_LOW)):
            return
        q = base.quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None or q.spread is None:
            return
        if q.spread < 2:
            return
        orders = result.setdefault(product, [])
        pos = self.position(state, product)

        if dev <= IV_LOW and pos < base.POS_LIMITS[product] - size:
            # Cheap vol: join/improve bid, never cross.
            price = q.bid + 1 if q.bid + 1 < q.ask else q.bid
            self.buy(state, product, orders, price, size)
        elif dev >= IV_HIGH and pos > -base.POS_LIMITS[product] + size:
            # Rich vol: join/improve ask, never cross.
            price = q.ask - 1 if q.ask - 1 > q.bid else q.ask
            self.sell(state, product, orders, price, size)

    def trade_5000(self, state, store, result, S, spot_edge):
        super().trade_5000(state, store, result, S, spot_edge)
        self._passive_iv_regime(state, store, result, "VEV_5000", 4)

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
        super().trade_smile_atm(state, store, result, smile_lv, product, strike, S, v_active, spot_edge)
        self._passive_iv_regime(state, store, result, product, 5 if strike == 5200 else 4)
