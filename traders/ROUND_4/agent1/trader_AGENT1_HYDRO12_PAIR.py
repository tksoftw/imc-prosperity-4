"""Agent1 candidate: HYDRO12 plus rare Mark38/Mark22 HYDRO pair boost."""

from typing import Dict, List, Tuple

from datamodel import Order, TradingState

from traders.ROUND_3 import trader_FLIPVOL as flip
from traders.ROUND_4.agent1 import trader_AGENT1_HYDRO12 as hyd


PAIR_TTL = 500
PAIR_TILT = 8.0


class Trader(hyd.Trader):
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        pair_score = 0
        for tr in hyd._all_trades(state, flip.HYDRO):
            buyer = getattr(tr, "buyer", None)
            seller = getattr(tr, "seller", None)
            qty = abs(int(getattr(tr, "quantity", 0) or 0))
            if buyer == "Mark 38" and seller == "Mark 22":
                pair_score += qty
            elif buyer == "Mark 22" and seller == "Mark 38":
                pair_score -= qty
        if abs(pair_score) >= 4:
            store["agent1_hydro_pair_dir"] = 1 if pair_score > 0 else -1
            store["agent1_hydro_pair_til"] = state.timestamp + PAIR_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _pair_signal(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("agent1_hydro_pair_til", -1)):
            return 0
        return int(store.get("agent1_hydro_pair_dir", 0))

    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        q = flip.quote_from(state.order_depths.get(flip.HYDRO))
        if q.bid is None or q.ask is None or q.mid is None:
            return
        fast = flip.update_ema(store["emas"], "hydro_swing_fast", q.mid, 10)
        edge = 0.7 * fast + 0.3 * 9980.0 - q.mid
        edge += hyd.HYDRO_TILT * self._hydro_signal(state, store)
        edge += PAIR_TILT * self._pair_signal(state, store)

        current = self.position(state, flip.HYDRO)
        if edge > 14.0:
            target = 200
        elif edge < -14.0:
            target = -200
        else:
            target = current

        self._trade_to_target(state, result, flip.HYDRO, target)
