"""ROUND_3: BUGALPHA.

Start from SIMPLEALPHA, but handle the discovered bug honestly:

* VEV_4000 flash logic is kept; it still helps total.
* VEV_4500 flash-mid logic is disabled; rank shows the apparent bug is
  stale-snapshot churn, not executable alpha. The profitable response is
  to trust VELVET-source delta instead of the voucher's lying mid.
* VEV_6000/6500 keep a free 0-bid probe, but rank confirms queue priority
  usually prevents fills.
"""

from typing import Dict, List, Optional

from datamodel import Order, TradingState

from ROUND_3 import trader_FLIPVOL as base
from ROUND_3 import trader_SIMPLEALPHA as simple


class Trader(simple.Trader):
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
        if strike == 4000:
            simple.Trader.trade_itm_flash(
                self, state, result, product, strike, S, surface_shift, wing_signal
            )
            return

        # VEV_4500's flash-mid bug is not executable alpha in rank. It
        # mostly causes churn against stale snapshots, so the alpha is to
        # ignore the flash layer and keep only the VELVET-source signal.
        edge_signal = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target(product, edge_signal, self.position(state, product))
        self._trade_to_target(state, result, product, target)

    def trade_zero_tail(self, state, result, product, basket_active: bool):
        # Probe both queue possibilities safely: bid 0 for free, sell 1 if
        # somehow filled. Do not bid 1; that just crosses the permanent ask.
        q = base.quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        if q.bid == 0:
            self.buy(state, product, orders, 0, base.POS_LIMITS[product])
        if self.position(state, product) > 0:
            self.sell(state, product, orders, 1, min(self.position(state, product), 60))
