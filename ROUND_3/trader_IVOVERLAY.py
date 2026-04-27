"""ROUND_3: IVOVERLAY.

Keep the high-performing BUGALPHA/stack backbone, but add the original
Black-Scholes smile market-making layer before moving to the structural
target. This lets the trader collect IV/smile mispricings passively while
still using the full option book when the VELVET/option delta edge is big.
"""

from typing import Dict, List

from datamodel import Order, TradingState

from ROUND_3 import trader_BUGALPHA as bug
from ROUND_3 import trader_FLIPVOL as base


class Trader(bug.Trader):
    def trade_5000(self, state, store, result, S, spot_edge):
        base.Trader.trade_5000(self, state, store, result, S, spot_edge)
        super().trade_5000(state, store, result, S, spot_edge)

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
        base.Trader.trade_smile_atm(
            self, state, store, result, smile_lv, product, strike, S, v_active, spot_edge
        )
        super().trade_smile_atm(state, store, result, smile_lv, product, strike, S, v_active, spot_edge)

    def trade_5300(self, state, result, S, vev_dir):
        base.Trader.trade_5300(self, state, result, S, vev_dir)
        super().trade_5300(state, result, S, vev_dir)

    def trade_5400(self, state, store, result, S):
        base.Trader.trade_5400(self, state, store, result, S)
        super().trade_5400(state, store, result, S)

    def trade_5500(self, state, store, result, S, basket_active):
        base.Trader.trade_5500(self, state, store, result, S, basket_active)
        super().trade_5500(state, store, result, S, basket_active)
