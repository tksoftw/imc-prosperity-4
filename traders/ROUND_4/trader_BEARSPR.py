"""ROUND_4: BEARSPR — HYPER + bearish spread signal.

From r4_alpha_tight_spread.py:
  spread=3: SR=-0.660, P(up)=15.9% (BEARISH)
  spread=4: SR=-1.118, P(up)=9.6%  (BEARISH, stronger)

When the VELVET spread is 3-4 (partially compressed from normal 5), price
drops with high probability over the next tick.  This is the opposite of the
tight-spread signal — when spread compresses TO 3-4 (not all the way to 1-2),
sellers are lowering asks to attract buyers, a sign of selling pressure.

Important caveat: spread=3-4 can also occur as a TRANSITION during a Mark 67
sweep (5→3→1 as aggressive buying narrows the spread).  Applying a bearish
tilt in those cases would be wrong.  To mitigate:
  - Only apply bearish tilt when bot_tilt <= 0 (no active bullish bot signal)
  - Keep the tilt modest (-2.0) so it doesn't dominate

Spread=3-4 fires on ~3.5% of ticks = ~4200 events/3days, vs 202 bot signals.
Expected events where bot_tilt=0 AND spread=3-4: majority of those 4200.
"""

from __future__ import annotations

from typing import Dict

from datamodel import TradingState

from traders.ROUND_3 import trader_FLIPVOL as flip
from traders.ROUND_4 import trader_HYPER as base


BEAR_SPREAD_MIN = 3
BEAR_SPREAD_MAX = 4
BEAR_TILT = 2.0
BEAR_TTL = 500


class Trader(base.Trader):

    def update_signals(self, state: TradingState, store: Dict):
        result = super().update_signals(state, store)

        od = state.order_depths.get(flip.VELVET)
        if od and od.buy_orders and od.sell_orders:
            spread = min(od.sell_orders) - max(od.buy_orders)
            if BEAR_SPREAD_MIN <= spread <= BEAR_SPREAD_MAX:
                store["bear_spread_til"] = state.timestamp + BEAR_TTL

        return result

    def _velvet_tilt(self, state: TradingState, store: Dict) -> float:
        bot_tilt = super()._velvet_tilt(state, store)

        # Only apply bearish tilt when no strong bullish bot signal is active.
        # If Mark 67 is buying (bot_tilt > 0), the spread=3-4 is a transition
        # toward tighter spread — don't dampen that signal.
        if bot_tilt <= 0 and state.timestamp <= int(store.get("bear_spread_til", -1)):
            return bot_tilt - BEAR_TILT

        return bot_tilt
