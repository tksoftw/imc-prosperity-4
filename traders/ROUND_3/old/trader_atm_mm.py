"""ROUND_3: ATM option market maker + deep-ITM arbitrage.

Strategy is grounded in the analysis in notebooks/round3/.

Two distinct edges, one file:

1. Market-make near-ATM VEV calls (K = 5000, 5100, 5200, 5300)
   - These options carry the highest extrinsic value (peaks at K=5200/5300)
     yet are almost untouched by other participants (1-120 trades over 3
     days). The "Wing Seller" dumps OTM strikes; the "Accumulator" only
     touches VELVET; nobody is quoting fair value here.
   - Fair = max(S-K, 0) + extrinsic_K, where extrinsic_K is calibrated
     to empirical mid-round averages (see notebook). We quote one tick
     inside the existing top of book on both sides, with an inventory
     skew that pushes us back toward zero.

2. Pure arbitrage on deep-ITM VEV_4000 / VEV_4500
   - Empirical extrinsic ~0; fair = max(S-K, 0). Whenever the book strays
     more than ITM_TAKE_EDGE from intrinsic we lift / hit, otherwise we
     post tight quotes inside the (wide) book.

Strikes outside [4000, 5300] are intentionally untouched. VEV_5400+ is
the Wing Seller's basket dump zone and deserves its own dedicated
absorber strategy. Underlying VELVET is also left alone here - the
Accumulator is the alpha there and a separate copy-trader belongs in
its own file.
"""

import math
from typing import Dict, List

from datamodel import Order, TradingState


UNDERLYING = "VELVETFRUIT_EXTRACT"

# Empirical extrinsic value (time value) by strike.
# Source: notebooks/round3/conclusions.ipynb, mid-round average across days 0-2.
# We deliberately only trade strikes where a static fair value works:
#   - Deep-ITM (4000, 4500): extrinsic ~0, fair = pure intrinsic.
#   - VEV_5300 (closest OTM): low delta -> fair stable as VELVET moves.
# ITM-ATM strikes 5000/5100/5200 were tried in v1 and bled hard from
# gamma risk: their delta is high, so static fair gets picked off as
# VELVET drifts. They need either (a) delta-hedging in VELVET or (b)
# an intraday extrinsic decay model. Out of scope for this v2 cut.
VEV_EXTRINSIC = {
    4000: 0.0,
    4500: 0.0,
    5300: 47.0,
}

ATM_STRIKES = (5300,)
ITM_STRIKES = (4000, 4500)

# Position limits per option (conservative initial value; revisit per
# actual game limit once a backtest run pings them).
POS_LIMIT = 50

# ATM market-making knobs
ATM_TAKE_EDGE = 1.5        # mispricing required to cross the spread
ATM_QUOTE_EDGE = 1.0       # offset from fair when posting quotes
ATM_QUOTE_SIZE = 5         # size per quote level
ATM_SKEW_PER_UNIT = 0.05   # tilt fair against inventory
ATM_MIN_SPREAD = 2         # only post quotes when book is at least this wide

# Deep-ITM arbitrage knobs
ITM_TAKE_EDGE = 2.0
ITM_QUOTE_EDGE = 1.5
ITM_QUOTE_SIZE = 3
ITM_MIN_SPREAD = 3


def get_mid(order_depth):
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    best_bid = max(order_depth.buy_orders)
    best_ask = min(order_depth.sell_orders)
    return (best_bid + best_ask) / 2


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        underlying_depth = state.order_depths.get(UNDERLYING)
        S = get_mid(underlying_depth) if underlying_depth is not None else None

        for product, order_depth in state.order_depths.items():
            position = int(state.position.get(product, 0))

            if not product.startswith("VEV_"):
                result[product] = []
                continue

            try:
                strike = int(product.split("_", 1)[1])
            except ValueError:
                result[product] = []
                continue

            if strike not in VEV_EXTRINSIC or S is None:
                result[product] = []
                continue

            fair = max(S - strike, 0.0) + VEV_EXTRINSIC[strike]

            if strike in ITM_STRIKES:
                result[product] = self._itm_arb(product, order_depth, position, fair)
            elif strike in ATM_STRIKES:
                result[product] = self._atm_make(product, order_depth, position, fair)
            else:
                result[product] = []

        return result, 0, ""

    # ------------------------------------------------------------------
    # ATM market making — quote inside the book around extrinsic-aware fair
    # ------------------------------------------------------------------

    def _atm_make(self, product, order_depth, position, fair):
        orders: List[Order] = []
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        if not buy_orders or not sell_orders:
            return orders

        best_bid = max(buy_orders)
        best_ask = min(sell_orders)
        spread = best_ask - best_bid

        adj_fair = fair - ATM_SKEW_PER_UNIT * position

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        if buy_room > 0 and best_ask < adj_fair - ATM_TAKE_EDGE:
            available = -sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        if sell_room > 0 and best_bid > adj_fair + ATM_TAKE_EDGE:
            available = buy_orders[best_bid]
            take = min(available, sell_room)
            if take > 0:
                orders.append(Order(product, best_bid, -take))
                position -= take
                sell_room -= take

        if spread >= ATM_MIN_SPREAD:
            adj_fair = fair - ATM_SKEW_PER_UNIT * position
            buy_price = min(best_bid + 1, math.floor(adj_fair - ATM_QUOTE_EDGE))
            sell_price = max(best_ask - 1, math.ceil(adj_fair + ATM_QUOTE_EDGE))
            if buy_price < sell_price:
                if buy_room > 0 and buy_price > 0:
                    size = min(ATM_QUOTE_SIZE, buy_room)
                    orders.append(Order(product, int(buy_price), size))
                if sell_room > 0:
                    size = min(ATM_QUOTE_SIZE, sell_room)
                    orders.append(Order(product, int(sell_price), -size))

        return orders

    # ------------------------------------------------------------------
    # Deep-ITM arbitrage — fair ≈ intrinsic, lift/hit any sub-intrinsic print
    # ------------------------------------------------------------------

    def _itm_arb(self, product, order_depth, position, fair):
        orders: List[Order] = []
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        if not buy_orders or not sell_orders:
            return orders

        best_bid = max(buy_orders)
        best_ask = min(sell_orders)
        spread = best_ask - best_bid

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        if buy_room > 0 and best_ask < fair - ITM_TAKE_EDGE:
            available = -sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        if sell_room > 0 and best_bid > fair + ITM_TAKE_EDGE:
            available = buy_orders[best_bid]
            take = min(available, sell_room)
            if take > 0:
                orders.append(Order(product, best_bid, -take))
                position -= take
                sell_room -= take

        if spread >= ITM_MIN_SPREAD:
            buy_price = min(best_bid + 1, math.floor(fair - ITM_QUOTE_EDGE))
            sell_price = max(best_ask - 1, math.ceil(fair + ITM_QUOTE_EDGE))
            if buy_price < sell_price:
                if buy_room > 0 and buy_price > 0:
                    size = min(ITM_QUOTE_SIZE, buy_room)
                    orders.append(Order(product, int(buy_price), size))
                if sell_room > 0:
                    size = min(ITM_QUOTE_SIZE, sell_room)
                    orders.append(Order(product, int(sell_price), -size))

        return orders
