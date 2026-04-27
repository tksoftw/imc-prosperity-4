"""ROUND_3: simple delta-capacity mean-reversion alpha.

Core edge discovered by vector backtest:
  edge = 0.7 * EMA15(VELVET mid) + 0.3 * 5255 - VELVET mid

When |edge| is large, VELVET mean-reverts enough to pay the spread. Use
VELVET plus the high-delta vouchers (4000..5200) as extra position
limits. Hold until the opposite threshold appears.
"""

from typing import Dict, List, Optional

from datamodel import Order, TradingState

from ROUND_3 import trader_FLIPVOL as base
ENTRY = 6.0
ENTRY_BY_PRODUCT = {
    "VEV_4000": 7.0,
    "VEV_4500": 7.0,
    "VEV_5000": 7.0,
    "VEV_5500": 7.0,
}
DELTA_STACK = {
    base.VELVET: 200,
    "VEV_4000": 300,
    "VEV_4500": 300,
    "VEV_5000": 300,
    "VEV_5100": 300,
    "VEV_5200": 300,
    "VEV_5300": 300,
    "VEV_5400": 300,
    "VEV_5500": 300,
}


class Trader(base.Trader):
    def update_low_iv_trail(self, store: Dict, total_pnl: float, smile_lv: float) -> None:
        return

    def trade_hydro(self, state, store, result):
        q = base.quote_from(state.order_depths.get(base.HYDRO))
        if q.bid is None or q.ask is None or q.mid is None:
            return
        fast = base.update_ema(store["emas"], "hydro_swing_fast", q.mid, 10)
        edge = 0.7 * fast + 0.3 * 9980.0 - q.mid
        current = self.position(state, base.HYDRO)
        if edge > 14.0:
            target = 200
        elif edge < -14.0:
            target = -200
        else:
            target = current
        self._trade_to_target(state, result, base.HYDRO, target)

    def _update_swing_edge(self, state: TradingState, store: Dict) -> Optional[float]:
        q = base.quote_from(state.order_depths.get(base.VELVET))
        if q.mid is None:
            return None
        fast = base.update_ema(store["emas"], "velvet_swing_fast", q.mid, 15)
        edge = 0.7 * fast + 0.3 * 5255.0 - q.mid
        edge = max(-25.0, min(25.0, edge))
        self._swing_edge = edge
        return edge

    def _swing_target(self, product: str, edge: float, current: int) -> int:
        entry = ENTRY_BY_PRODUCT.get(product, ENTRY)
        if edge > entry:
            return DELTA_STACK[product]
        if edge < -entry:
            return -DELTA_STACK[product]
        return current

    def _trade_to_target(self, state: TradingState, result: Dict[str, List[Order]], product: str, target: int) -> None:
        q = base.quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        delta = int(target) - self.position(state, product)
        if delta > 0:
            self.buy(state, product, orders, q.ask, min(delta, -q.ask_vol))
        elif delta < 0:
            self.sell(state, product, orders, q.bid, min(-delta, q.bid_vol))

    def trade_velvet(self, state, store, result, v_active, implied_tilt, v4_dir):
        edge = self._update_swing_edge(state, store)
        if edge is None:
            return
        target = self._swing_target(base.VELVET, edge, self.position(state, base.VELVET))
        self._trade_to_target(state, result, base.VELVET, target)

    def velvet_spot_edge(self, state, store, S, v_active, implied_tilt, v4_dir):
        edge = getattr(self, "_swing_edge", None)
        if edge is None:
            edge = self._update_swing_edge(state, store)
        return 0.0 if edge is None else float(edge)

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
        # Keep the known one-tick flash arb, then use remaining room for
        # the structural VELVET mean-reversion position.
        base.Trader.trade_itm_flash(self, state, result, product, strike, S, surface_shift, wing_signal)
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target(product, edge, self.position(state, product))
        self._trade_to_target(state, result, product, target)

    def trade_5000(self, state, store, result, S, spot_edge):
        edge = float(getattr(self, "_swing_edge", spot_edge))
        target = self._swing_target("VEV_5000", edge, self.position(state, "VEV_5000"))
        self._trade_to_target(state, result, "VEV_5000", target)

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
        edge = float(getattr(self, "_swing_edge", spot_edge))
        target = self._swing_target(product, edge, self.position(state, product))
        self._trade_to_target(state, result, product, target)

    def trade_5300(self, state, result, S, vev_dir):
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target("VEV_5300", edge, self.position(state, "VEV_5300"))
        self._trade_to_target(state, result, "VEV_5300", target)

    def trade_5400(self, state, store, result, S):
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target("VEV_5400", edge, self.position(state, "VEV_5400"))
        self._trade_to_target(state, result, "VEV_5400", target)

    def trade_5500(self, state, store, result, S, basket_active):
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target("VEV_5500", edge, self.position(state, "VEV_5500"))
        self._trade_to_target(state, result, "VEV_5500", target)
