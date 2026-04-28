"""ROUND_3 experiment: per-strike hybrid of the current best beasts."""

from ROUND_3 import trader_DELTABEAST as delta
from ROUND_3 import trader_SWINGBEAST as swing
from ROUND_3 import trader_MS as ms


class Trader(delta.Trader):
    def trade_hydro(self, state, store, result):
        # Public-data monster hydro: ultra-soft skew from MADSCIENTIST.
        return ms.Trader.trade_hydro(self, state, store, result)

    def trade_itm_flash(self, state, result, product, strike, S, surface_shift, wing_signal):
        # SWING wins on the deep-ITM delta vouchers with fewer churn trades.
        return swing.Trader.trade_itm_flash(
            self, state, result, product, strike, S, surface_shift, wing_signal
        )

    def trade_5000(self, state, store, result, S, spot_edge):
        # SWING wins 5000 on every public day.
        return swing.Trader.trade_5000(self, state, store, result, S, spot_edge)
