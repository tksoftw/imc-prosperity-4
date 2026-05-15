"""ROUND_3: IVSMILE_STACK.

Hybrid answer to the "not just full long/short" concern:

Use the strong VELVET mean-reversion fair as the forward move, but each
voucher only joins when Black-Scholes/smile says its own price is worth
the delta exposure.

Option score:

    score = delta(K) * expected_spot_move - smile_richness(K)

where `smile_richness = option_mid - smile_fair`. Positive score means
the option is cheap enough for the expected spot rebound; negative score
means it is rich enough to short. This is still aggressive, but it is an
IV/smile-gated stack instead of blind all-in by product.
"""

import math
from typing import Dict, List, Optional

from datamodel import Order, TradingState

from traders.ROUND_3 import trader_BUGALPHA as bug
from traders.ROUND_3 import trader_FLIPVOL as base


ENTRY = {
    "VEV_4000": 7.0,
    "VEV_4500": 7.0,
    "VEV_5000": 5.0,
    "VEV_5100": 4.0,
    "VEV_5200": 3.0,
    "VEV_5300": 2.5,
    "VEV_5400": 1.4,
    "VEV_5500": 0.9,
}

ANCHOR_FAIR = {
    5300: 47.0,
    5400: 16.0,
    5500: 6.5,
}

RICHNESS_WEIGHT = {
    "VEV_5000": 0.45,
    "VEV_5100": 0.65,
    "VEV_5200": 0.65,
    "VEV_5300": 0.65,
    "VEV_5400": 0.75,
    "VEV_5500": 0.65,
}


class Trader(bug.Trader):
    def _bs_fair_delta(self, product: str, strike: int, S: float, smile_lv: Optional[float]) -> tuple[float, float]:
        if strike in (4000, 4500):
            return max(S - strike, 0.0), 1.0
        if strike in ANCHOR_FAIR:
            fair = max(S - strike, 0.0) + ANCHOR_FAIR[strike]
            # Anchor wings still need approximate delta for expected spot move.
            delta = 0.20 if strike == 5300 else (0.08 if strike == 5400 else 0.03)
            return fair, delta
        lv = 0.031 if smile_lv is None else smile_lv
        iv = max(base.IV_LO, min(base.IV_HI, lv + base.smile_offset((strike - S) / 100.0)))
        return base.bs_call(S, strike, base.TTE, iv), base.bs_delta(S, strike, base.TTE, iv)

    def _ivsmile_target(
        self,
        state: TradingState,
        product: str,
        strike: int,
        S: float,
        smile_lv: Optional[float],
    ) -> int:
        q = base.quote_from(state.order_depths.get(product))
        edge = float(getattr(self, "_swing_edge", 0.0))
        if q.mid is None:
            return self.position(state, product)

        if strike in (4000, 4500):
            # Deep ITM smile has delta≈1 and near-zero vega. The IV/smile
            # no-arb fair is intrinsic, so the clean signal is just the
            # VELVET-source forward move, not the voucher's noisy mid.
            score = edge
            entry = ENTRY[product]
            if score > entry:
                return base.POS_LIMITS[product]
            if score < -entry:
                return -base.POS_LIMITS[product]
            return self.position(state, product)

        fair, delta = self._bs_fair_delta(product, strike, S, smile_lv)
        richness = q.mid - fair
        score = delta * edge - RICHNESS_WEIGHT.get(product, 0.65) * richness

        # Wider books need more evidence; this stops pretty mid residuals
        # from becoming spread donations.
        if q.spread is not None:
            score -= 0.15 * q.spread * (1 if score > 0 else -1)

        entry = ENTRY[product]
        if score > entry:
            return base.POS_LIMITS[product]
        if score < -entry:
            return -base.POS_LIMITS[product]
        return self.position(state, product)

    def _trade_product_to_iv_target(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        S: float,
        smile_lv: Optional[float],
    ) -> None:
        target = self._ivsmile_target(state, product, strike, S, smile_lv)
        self._trade_to_target(state, result, product, target)

    def trade_itm_flash(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        strike: int,
        S: float,
        surface_shift: Optional[float],
        wing_signal: bool,
    ) -> None:
        if strike == 4000:
            # Keep executable 4000 flash arb first, then IV/smile target.
            base.Trader.trade_itm_flash(self, state, result, product, strike, S, surface_shift, wing_signal)
        self._trade_product_to_iv_target(state, result, product, strike, S, None)

    def trade_5000(self, state, store, result, S, spot_edge):
        smile_lv = float(store["emas"].get("smile_level", 0.031))
        self._trade_product_to_iv_target(state, result, "VEV_5000", 5000, S, smile_lv)

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
        self._trade_product_to_iv_target(state, result, product, strike, S, smile_lv)

    def trade_5300(self, state, result, S, vev_dir):
        self._trade_product_to_iv_target(state, result, "VEV_5300", 5300, S, None)

    def trade_5400(self, state, store, result, S):
        self._trade_product_to_iv_target(state, result, "VEV_5400", 5400, S, None)

    def trade_5500(self, state, store, result, S, basket_active):
        self._trade_product_to_iv_target(state, result, "VEV_5500", 5500, S, None)
