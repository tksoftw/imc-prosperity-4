"""Agent1 candidate: same as HYDRO12 with a more aggressive HYDRO tilt."""

from typing import Dict, List, Tuple

from datamodel import Order, TradingState

from ROUND_3 import trader_FLIPVOL as flip
from ROUND_4 import trader_BOTVELVET as base


HYDRO_BOT_ALPHA = {
    "Mark 14": +1.0,
    "Mark 38": -1.0,
    "Mark 22": -1.5,
}
HYDRO_MIN_WEIGHT = 8.0
HYDRO_TTL = 2_500
HYDRO_TILT = 14.0


def _score_bots(trades, alpha: Dict[str, float]) -> float:
    score = 0.0
    for tr in trades or []:
        qty = abs(int(getattr(tr, "quantity", 0) or 0))
        buyer = getattr(tr, "buyer", None)
        seller = getattr(tr, "seller", None)
        if buyer != "SUBMISSION":
            score += alpha.get(buyer, 0.0) * qty
        if seller != "SUBMISSION":
            score -= alpha.get(seller, 0.0) * qty
    return score


def _all_trades(state: TradingState, product: str):
    out = list(state.market_trades.get(product, []) or [])
    out.extend(state.own_trades.get(product, []) or [])
    return out


class Trader(base.Trader):
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        hydro_score = _score_bots(_all_trades(state, flip.HYDRO), HYDRO_BOT_ALPHA)
        if abs(hydro_score) >= HYDRO_MIN_WEIGHT:
            store["agent1_hydro_dir"] = 1 if hydro_score > 0 else -1
            store["agent1_hydro_til"] = state.timestamp + HYDRO_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _hydro_signal(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("agent1_hydro_til", -1)):
            return 0
        return int(store.get("agent1_hydro_dir", 0))

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
        edge += HYDRO_TILT * self._hydro_signal(state, store)

        current = self.position(state, flip.HYDRO)
        if edge > 14.0:
            target = 200
        elif edge < -14.0:
            target = -200
        else:
            target = current

        self._trade_to_target(state, result, flip.HYDRO, target)
