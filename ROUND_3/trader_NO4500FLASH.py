"""Control test: SIMPLEALPHA with VEV_4500 flash-arb disabled."""

from typing import Dict, List, Optional

from datamodel import Order, TradingState

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
        if strike == 4500:
            edge = float(getattr(self, "_swing_edge", 0.0))
            target = self._swing_target(product, edge, self.position(state, product))
            self._trade_to_target(state, result, product, target)
            return
        simple.Trader.trade_itm_flash(
            self, state, result, product, strike, S, surface_shift, wing_signal
        )
