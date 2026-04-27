"""ROUND_4: TIGHTEDGE — HYPER + tight-spread hold filter.

Tight-spread analysis (notebooks/round4/r4_alpha_tight_spread.py):
  spread=1: SR=1.78, P(up)=89%, mean mid-change=+1.87
  spread=2: SR=0.75, P(up)=80%, mean mid-change=+1.13

Problem with original tilt approach:
  - max(bot_tilt, +3) overrides BEARISH bot signals (Mark 55 sell → -1, but max(-1,3)=+3)
  - Tight spread fires as/after Mark 67 lifts the ask → entering at the peak
  - Extra entries cause 12k regression on VELVET vs HYPER

This version uses tight spread as a HOLD FILTER only:
  - When spread ≤ 2 AND we hold a long position AND swing edge says exit, stay.
  - Does NOT open new positions due to tight spread.
  - Does NOT suppress bearish bot signals.
"""

from __future__ import annotations

from typing import Dict, List

from datamodel import Order, TradingState

from ROUND_3 import trader_FLIPVOL as flip
from ROUND_4 import trader_HYPER as base


TIGHT_SPREAD_MAX = 2   # ≤ this triggers the hold filter
TIGHT_TTL = 500        # ms; hold extension after last tight tick


class Trader(base.Trader):

    def update_signals(self, state: TradingState, store: Dict):
        result = super().update_signals(state, store)

        od = state.order_depths.get(flip.VELVET)
        if od and od.buy_orders and od.sell_orders:
            spread = min(od.sell_orders) - max(od.buy_orders)
            if spread <= TIGHT_SPREAD_MAX:
                store["tight_spread_til"] = state.timestamp + TIGHT_TTL

        return result

    def trade_velvet(self, state: TradingState, store: Dict, result: Dict[str, List[Order]],
                     v_active, implied_tilt, v4_dir):
        edge = self._update_swing_edge(state, store)
        if edge is None:
            return

        tilt = self._velvet_tilt(state, store)  # pure HYPER bot tilt, no spread tilt
        bot_edge = max(-25.0, min(25.0, edge + tilt))
        current = self.position(state, flip.VELVET)
        target = self._swing_target(flip.VELVET, bot_edge, current)

        # Hold filter: don't reduce a long position while tight spread is active.
        # Price is 89% likely to rise next tick — premature exit loses that gain.
        tight_active = state.timestamp <= int(store.get("tight_spread_til", -1))
        if tight_active and current > 0 and target < current:
            target = current

        self._trade_to_target(state, result, flip.VELVET, target)
        self._swing_edge = edge
