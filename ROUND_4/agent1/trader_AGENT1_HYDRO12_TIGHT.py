"""Agent1 candidate: HYDRO12 plus VELVET tight-spread same-tick bias."""

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
HYDRO_TILT = 12.0

TIGHT_TTL = 500
TIGHT_TILT_SPREAD_1 = 1.7
TIGHT_TILT_SPREAD_2 = 1.0


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

        q = flip.quote_from(state.order_depths.get(flip.VELVET))
        if q.spread is not None and q.spread <= 2:
            store["agent1_velvet_tight_til"] = state.timestamp + TIGHT_TTL
            store["agent1_velvet_tight_tilt"] = (
                TIGHT_TILT_SPREAD_1 if q.spread <= 1 else TIGHT_TILT_SPREAD_2
            )

        return v_active, vev_dir, basket_active, v4_dir

    def _hydro_signal(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("agent1_hydro_til", -1)):
            return 0
        return int(store.get("agent1_hydro_dir", 0))

    def _tight_tilt(self, state: TradingState, store: Dict) -> float:
        if state.timestamp > int(store.get("agent1_velvet_tight_til", -1)):
            return 0.0
        return float(store.get("agent1_velvet_tight_tilt", 0.0))

    def trade_velvet(self, state, store, result, v_active, implied_tilt, v4_dir):
        edge = self._update_swing_edge(state, store)
        if edge is None:
            return
        bot_edge = max(
            -25.0,
            min(
                25.0,
                edge
                + self._bot_tilt(state, store, "velvet", base.VELVET_BOT_TILT)
                + self._tight_tilt(state, store),
            ),
        )
        target = self._swing_target(
            flip.VELVET,
            bot_edge,
            self.position(state, flip.VELVET),
        )
        self._trade_to_target(state, result, flip.VELVET, target)
        self._swing_edge = edge

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
