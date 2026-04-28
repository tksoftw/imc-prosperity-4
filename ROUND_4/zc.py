"""ROUND 4 champion variant with aggressive VEV_6000/VEV_6500 zero-tail spam.

This keeps the champion + Thomas Hydrogel stack intact and overrides only the
tail voucher and Hydrogel behavior:

* Always bid 0 for as much room as the position limit allows.
* If long, try to unload the full current position at 4 first.
* If that long position survives into another call, unload at 1 until flat.
* Trade HYDROGEL_PACK as a 10,000-anchor mean-reversion product.
"""

import math
from typing import Dict, List

from datamodel import Order, TradingState
from ROUND_4.round4_champion_with_thomas_hydro_strategy_commented import (
    HYDRO,
    POS_LIMITS,
    Trader as ChampionTrader,
    quote_from,
    update_ema,
)


ZERO_TAIL_PRODUCTS = ("VEV_6000", "VEV_6500")
HYDRO_ANCHOR = 10_000.0


class Trader(ChampionTrader):
    """Champion trader with spammy zero-tail voucher harvesting."""

    def _zero_tail_ask_attempts(self) -> Dict[str, int]:
        attempts = getattr(self, "_zero_tail_attempt_counts", None)
        if attempts is None:
            attempts = {}
            self._zero_tail_attempt_counts = attempts
        return attempts

    def trade_zero_tail(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        basket_active: bool,
    ) -> None:
        if product not in ZERO_TAIL_PRODUCTS:
            return super().trade_zero_tail(state, result, product, basket_active)

        orders = result.setdefault(product, [])

        # Spam full-room 0 bids regardless of the current book state.
        self.buy(state, product, orders, 0, POS_LIMITS[product])

        position = self.position(state, product)
        attempts = self._zero_tail_ask_attempts()
        if position <= 0:
            attempts.pop(product, None)
            return

        # If this process has no memory for the product but the latest fills show
        # we just bought it, give price 4 one chance. Otherwise treat the long as
        # stale and immediately fall back to the faster exit at 1.
        bought_recently = any(
            getattr(tr, "buyer", None) == "SUBMISSION"
            and abs(int(getattr(tr, "quantity", 0) or 0)) > 0
            for tr in state.own_trades.get(product, []) or []
        )
        attempt_count = attempts.get(product)
        if attempt_count is None:
            attempt_count = 0 if bought_recently else 1

        ask_price = 4 if attempt_count == 0 else 1
        self.sell(state, product, orders, ask_price, position)
        attempts[product] = attempt_count + 1

    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        q = quote_from(state.order_depths.get(HYDRO))
        mid = q.wall_mid if q.wall_mid is not None else q.mid
        if q.bid is None or q.ask is None or mid is None:
            return

        orders = result.setdefault(HYDRO, [])
        position = self.position(state, HYDRO)
        limit = POS_LIMITS[HYDRO]

        fast = update_ema(store["emas"], "hydro_10000_fast", mid, 10)
        slow = update_ema(store["emas"], "hydro_10000_slow", mid, 60)
        fair = 0.55 * fast + 0.20 * slow + 0.25 * HYDRO_ANCHOR
        fair += self.THOMAS_HYDRO_TILT * self._thomas_hydro_signal(state, store)

        # Inventory-aware edge: still mean-revert to 10k, but avoid getting stuck
        # quoting too aggressively in the same direction as an existing position.
        edge = fair - mid - 0.025 * position
        if abs(edge) < 3.0:
            target = 0
        else:
            target = int(max(-limit, min(limit, round(edge * 12.0))))

        delta = target - position
        if delta > 0 and q.ask <= fair - 2.0:
            self.buy(state, HYDRO, orders, q.ask, min(delta, max(0, -q.ask_vol)))
        elif delta < 0 and q.bid >= fair + 2.0:
            self.sell(state, HYDRO, orders, q.bid, min(-delta, max(0, q.bid_vol)))

        if q.spread is None or q.spread < 2:
            return

        # Post inside the spread toward the desired inventory instead of only
        # crossing the book; this should reduce paid spread on slower reversion.
        if target > position:
            bid_price = min(q.bid + 1, int(math.floor(fair - 1.0)))
            if bid_price < q.ask:
                self.buy(state, HYDRO, orders, bid_price, min(target - position, 30))
        elif target < position:
            ask_price = max(q.ask - 1, int(math.ceil(fair + 1.0)))
            if ask_price > q.bid:
                self.sell(state, HYDRO, orders, ask_price, min(position - target, 30))

