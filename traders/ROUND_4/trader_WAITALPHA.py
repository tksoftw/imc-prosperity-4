"""ROUND_4: tape-wait + BUGALPHA.

Hold all logic until the public tape is moving, then run the same stack as
BUGALPHA. Rationale: early-tick quotes can lean on thin snapshots; arming
after a print (or a short time cap) reduces noise from the opening book.
"""

import jsonpickle
from datamodel import TradingState
from typing import Dict

from traders.ROUND_4 import trader_BUGALPHA as base

VELVET = "VELVETFRUIT_EXTRACT"
# In state.timestamp units (same as the backtester). Arm anyway if still quiet.
MAX_WAIT = 4_000


class Trader(base.Trader):
    def _tape_ready(self, state, store: Dict) -> bool:
        if store.get("tape_ready"):
            return True
        if int(state.timestamp) >= MAX_WAIT:
            store["tape_ready"] = True
            return True
        for p in (VELVET, "VEV_5200", "HYDROGEL_PACK"):
            if state.market_trades.get(p):
                store["tape_ready"] = True
                return True
        return False

    def run(self, state: TradingState):
        store: Dict = self.load_state(state.traderData)
        self.mark_pnl(state, store)
        if not self._tape_ready(state, store):
            return {}, 0, jsonpickle.encode(store)
        return super().run(state)
