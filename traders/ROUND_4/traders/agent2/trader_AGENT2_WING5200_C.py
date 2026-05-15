"""agent2 variant C: add wing-bot directional overlay to VEV_5200 only.

Alpha mined from ROUND_4 trades:
- Mark 22 sells in VEV_5300/5400/5500 -> VEV_5200 drops ~0.7-0.8 within 100.
- Mark 01 buys in same wings -> VEV_5200 rises ~0.7-0.8 within 100.

Build on HYPER, keep all existing logic, and only tilt 5200 target sizing.
"""

from typing import Dict, List, Tuple

from datamodel import TradingState

from traders.ROUND_4 import trader_HYPER as hyper

WING_PRODUCTS = ("VEV_5300", "VEV_5400", "VEV_5500")
WING_5200_ALPHA = {
    "Mark 22": -1.0,
    "Mark 01": +1.0,
    "Mark 14": +0.5,
}
WING_5200_MIN_WEIGHT = 8.0
WING_5200_TTL = 1_600
WING_5200_EDGE = 12.0


def _wing_score(trades) -> float:
    s = 0.0
    for tr in trades or []:
        qty = abs(int(getattr(tr, "quantity", 0) or 0))
        b = getattr(tr, "buyer", None)
        se = getattr(tr, "seller", None)
        if b != "SUBMISSION":
            s += WING_5200_ALPHA.get(b, 0.0) * qty
        if se != "SUBMISSION":
            s -= WING_5200_ALPHA.get(se, 0.0) * qty
    return s


class Trader(hyper.Trader):
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        score = 0.0
        for p in WING_PRODUCTS:
            score += _wing_score(state.market_trades.get(p, []))

        if abs(score) >= WING_5200_MIN_WEIGHT:
            store["wing5200_dir"] = 1 if score > 0 else -1
            store["wing5200_til"] = state.timestamp + WING_5200_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _wing5200_dir(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("wing5200_til", -1)):
            return 0
        return int(store.get("wing5200_dir", 0))

    def trade_smile_atm(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List],
        smile_lv: float,
        product: str,
        strike: int,
        S: float,
        v_active: bool,
        spot_edge: float,
    ) -> None:
        if product != "VEV_5200":
            return super().trade_smile_atm(
                state, store, result, smile_lv, product, strike, S, v_active, spot_edge
            )

        edge = float(getattr(self, "_swing_edge", spot_edge))
        edge += WING_5200_EDGE * self._wing5200_dir(state, store)
        edge = max(-25.0, min(25.0, edge))
        target = self._swing_target(product, edge, self.position(state, product))
        self._trade_to_target(state, result, product, target)
