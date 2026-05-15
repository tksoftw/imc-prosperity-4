"""ROUND_4: named-bot tilt only where it survives product attribution.

Early tests confirmed the bot tape is real for VELVET/HYDRO, but tilting
the shared swing edge bleeds across the option stack.  This variant keeps
WAITALPHA's option targets untouched and uses bot flow only for:

* VELVET target selection.
* HYDRO target selection.
"""

from typing import Dict, List, Tuple

from datamodel import Order, TradingState

from traders.ROUND_3 import trader_FLIPVOL as flip
from traders.ROUND_4 import trader_WAITALPHA as base


VELVET_BOT_ALPHA = {
    "Mark 67": 3.0,
    "Mark 55": 1.0,
    "Mark 49": -2.0,
    "Mark 22": -1.5,
}
HYDRO_BOT_ALPHA = {
    "Mark 14": 1.0,
    "Mark 38": -1.0,
    "Mark 22": -1.5,
}

VELVET_BOT_TTL = 2_500
HYDRO_BOT_TTL = 5_000
VELVET_BOT_TILT = 2.5
HYDRO_BOT_TILT = 4.0


def _score_bot_sides(trades, alpha: Dict[str, float]) -> float:
    score = 0.0
    for tr in trades or []:
        buyer = getattr(tr, "buyer", None)
        seller = getattr(tr, "seller", None)
        if buyer == "SUBMISSION" or seller == "SUBMISSION":
            continue
        qty = abs(int(getattr(tr, "quantity", 0) or 0))
        score += alpha.get(buyer, 0.0) * qty
        score -= alpha.get(seller, 0.0) * qty
    return score


class Trader(base.Trader):
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        velvet_score = _score_bot_sides(
            state.market_trades.get(flip.VELVET, []),
            VELVET_BOT_ALPHA,
        )
        if abs(velvet_score) >= 8.0:
            store["velvet_bot_dir"] = 1 if velvet_score > 0 else -1
            store["velvet_bot_mag"] = min(1.5, abs(velvet_score) / 60.0)
            store["velvet_bot_til"] = state.timestamp + VELVET_BOT_TTL

        hydro_score = _score_bot_sides(
            state.market_trades.get(flip.HYDRO, []),
            HYDRO_BOT_ALPHA,
        )
        if abs(hydro_score) >= 8.0:
            store["hydro_bot_dir"] = 1 if hydro_score > 0 else -1
            store["hydro_bot_mag"] = min(1.5, abs(hydro_score) / 80.0)
            store["hydro_bot_til"] = state.timestamp + HYDRO_BOT_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _bot_tilt(self, state: TradingState, store: Dict, key: str, scale: float) -> float:
        if state.timestamp > int(store.get(f"{key}_bot_til", -1)):
            return 0.0
        direction = int(store.get(f"{key}_bot_dir", 0))
        magnitude = float(store.get(f"{key}_bot_mag", 1.0))
        return scale * direction * magnitude

    def trade_velvet(self, state, store, result, v_active, implied_tilt, v4_dir):
        edge = self._update_swing_edge(state, store)
        if edge is None:
            return
        bot_edge = max(
            -25.0,
            min(25.0, edge + self._bot_tilt(state, store, "velvet", VELVET_BOT_TILT)),
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
        edge += self._bot_tilt(state, store, "hydro", HYDRO_BOT_TILT)
        current = self.position(state, flip.HYDRO)
        if edge > 14.0:
            target = 200
        elif edge < -14.0:
            target = -200
        else:
            target = current
        self._trade_to_target(state, result, flip.HYDRO, target)
