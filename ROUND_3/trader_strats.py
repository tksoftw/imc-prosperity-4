"""ROUND_3: per-product strategy book.

Every strategy here is grounded in `notebooks/round3/`:
  - data_analysis_round_3.ipynb
  - conclusions.ipynb
  - trader_identification.ipynb

The notebooks identified 5 distinct trader profiles and characterized
each instrument. Strategies follow directly:

  VELVETFRUIT_EXTRACT
    - Mean-revert spot anchored ~5250, σ ≈ 14 ticks/day, 5-tick spread
    - VELVET Accumulator (insider) buys in sizes >= 9 with 83% hit rate
      on +500-tick mid moves => when we see a >=9 BUY print in the
      market_trades tape, we tilt our fair UP so we lean long.

  HYDROGEL_PACK
    - Independent of VELVET (return corr ~ 0), 16-tick spread, balanced
      two-way flow. Pure MM anchored ~10000.

  VEV_4000 (and any deep-ITM strike with empirical extrinsic ~ 0)
    - Fair = max(S-K, 0). Pure arb against VELVET. Take obvious
      mispricings, quote tight inside the wide ITM book.

  VEV_5300
    - OTM near-ATM. Empirical extrinsic ~ 47, stable across days,
      low delta -> static fair holds up. MM at fair +/- 1.

  VEV_5000 / VEV_5100 / VEV_5200 (ITM near-ATM)
    - High delta means static-fair gets picked off by VELVET drift.
      Without delta hedging in VELVET they bleed money. Skipped here.

  VEV_5400 / VEV_5500 (OTM, Wing-Seller's dump zone)
    - 1-tick book leaves no room to quote inside. Wing Seller's flow
      is interesting but capturing it requires a delta-hedged
      absorber strategy that's out of scope for this v1.

  VEV_6000 / VEV_6500
    - Floored at 0.5; effectively worthless. Skipped.
"""

import math
from typing import Dict, List

from datamodel import Order, TradingState


UND = "VELVETFRUIT_EXTRACT"
HYDRO = "HYDROGEL_PACK"

POS_LIMIT = 50

# ----- VELVETFRUIT_EXTRACT --------------------------------------------------
# Anchor calibrated to span historical (~5250) and submission (~5262) means.
# Mean reversion still helps; we just use a value closer to the truth.
VELVET_MEAN = 5255.0
VELVET_BLEND = 0.7            # heavier weight on current mid -> less anchor drag
VELVET_MM_EDGE = 1.0
VELVET_MM_SIZE = 5
VELVET_SKEW = 0.05
VELVET_INSIDER_SIZE = 9       # market-trade quantity that flags the Accumulator

# Voucher-surface gating for the insider signal. The Accumulator can only
# hedge into a tight options market, so its t-stat collapses when the VEV
# 5200 or 5300 spread widens past 2 ticks. Conditioning on BOTH being <= 2
# at the same time roughly doubles the per-event edge with way fewer false
# fires (n drops, signal stronger).
VELVET_TIGHT_SPREAD = 2
VELVET_TIGHT_TILT = 4.0       # tilt fair UP when Accumulator + tight surface
VELVET_LIFT_SIZE = 5          # also lift the ask directly on confirmation

# ----- HYDROGEL_PACK --------------------------------------------------------
# True fair sits BELOW 10000: ~9990 in historical, ~9979 in the submission
# log. Hardcoding 10000 systematically over-pays for HYDRO. Anchor at the
# midpoint of those two regimes and lean on current mid via a 0.7 blend.
HYDRO_MEAN = 9985.0
HYDRO_BLEND = 0.7
HYDRO_MM_EDGE = 4.0
HYDRO_MM_SIZE = 5
HYDRO_SKEW = 0.04
HYDRO_TAKE_EDGE = 6.0
HYDRO_MIN_SPREAD = 6

# ----- VEV options ----------------------------------------------------------
# Submission has ~5 DTE remaining (vs the 3-day historical sample), so
# extrinsic at the wings is HIGHER than what historical averages suggest.
# Empirical from imc_logs/ROUND_3/447290.log:
#   K=4000  extrinsic ~ 0    (pure intrinsic)
#   K=4500  extrinsic ~ 0
#   K=5300  extrinsic ~ 50
#   K=5400  extrinsic ~ 16   (now has 2-tick book spread -> MM room)
VEV_EXTRINSIC = {
    4000: 0.0,
    5300: 50.0,
    5400: 16.0,
}

OPT_TAKE_EDGE = 2.0
OPT_QUOTE_EDGE = 1.5
OPT_QUOTE_SIZE = 5
OPT_SKEW = 0.05
OPT_MIN_SPREAD = 2
ITM_MIN_SPREAD = 3


def get_mid(order_depth):
    if not order_depth.buy_orders or not order_depth.sell_orders:
        return None
    return (max(order_depth.buy_orders) + min(order_depth.sell_orders)) / 2


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        und_depth = state.order_depths.get(UND)
        S = get_mid(und_depth) if und_depth is not None else None

        # Gate the Accumulator signal on a tight voucher surface
        tight_surface = self._tight_voucher_surface(state.order_depths)

        for product, order_depth in state.order_depths.items():
            position = int(state.position.get(product, 0))
            market_trades = state.market_trades.get(product, [])

            if product == UND:
                result[product] = self._velvet(
                    product, order_depth, position, market_trades, tight_surface
                )
            elif product == HYDRO:
                result[product] = self._hydro(product, order_depth, position)
            elif product.startswith("VEV_"):
                try:
                    strike = int(product.split("_", 1)[1])
                except ValueError:
                    result[product] = []
                    continue
                if S is None or strike not in VEV_EXTRINSIC:
                    result[product] = []
                    continue
                fair = max(S - strike, 0.0) + VEV_EXTRINSIC[strike]
                if VEV_EXTRINSIC[strike] == 0.0:
                    result[product] = self._itm_arb(product, order_depth, position, fair)
                else:
                    result[product] = self._otm_mm(product, order_depth, position, fair)
            else:
                result[product] = []

        return result, 0, ""

    # ------------------------------------------------------------------
    # Voucher surface gate
    # ------------------------------------------------------------------
    @staticmethod
    def _tight_voucher_surface(order_depths) -> bool:
        """True iff VEV_5200 spread <= 2 AND VEV_5300 spread <= 2.
        The Accumulator only fires when it can hedge into a tight surface,
        so this gate filters most false signals and roughly doubles the
        per-event edge.
        """
        for sym in ("VEV_5200", "VEV_5300"):
            d = order_depths.get(sym)
            if d is None or not d.buy_orders or not d.sell_orders:
                return False
            if min(d.sell_orders) - max(d.buy_orders) > VELVET_TIGHT_SPREAD:
                return False
        return True

    # ------------------------------------------------------------------
    # VELVETFRUIT_EXTRACT — mean-revert MM + filtered Accumulator alpha
    # ------------------------------------------------------------------
    def _velvet(self, product, order_depth, position, market_trades, tight_surface):
        orders: List[Order] = []
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders

        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)
        mid = (best_bid + best_ask) / 2

        # Accumulator detection: any recent market trade with quantity >= 9
        # whose price is at the ask side. Per the notebook, VELVET sizes >= 9
        # are 99% BUY (only the Accumulator uses them).
        accumulator_active = any(
            t.quantity >= VELVET_INSIDER_SIZE and t.price >= best_ask - 1
            for t in market_trades
        )
        # The clean alpha: only treat the signal as real when the voucher
        # surface is tight enough for the informed bot to hedge into.
        informed = accumulator_active and tight_surface

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        # Aggressive lift on confirmed signal — capture the +mid drift
        # before the spread re-widens.
        if informed and buy_room > 0:
            available = -order_depth.sell_orders[best_ask]
            lift = min(VELVET_LIFT_SIZE, buy_room, available)
            if lift > 0:
                orders.append(Order(product, best_ask, lift))
                position += lift
                buy_room -= lift

        fair = VELVET_BLEND * mid + (1 - VELVET_BLEND) * VELVET_MEAN
        fair -= VELVET_SKEW * position
        if informed:
            fair += VELVET_TIGHT_TILT

        bid_price = min(best_bid + 1, math.floor(fair - VELVET_MM_EDGE))
        ask_price = max(best_ask - 1, math.ceil(fair + VELVET_MM_EDGE))
        if bid_price >= ask_price:
            return orders

        if buy_room > 0:
            size = min(VELVET_MM_SIZE, buy_room)
            orders.append(Order(product, int(bid_price), size))
        if sell_room > 0:
            size = min(VELVET_MM_SIZE, sell_room)
            orders.append(Order(product, int(ask_price), -size))
        return orders

    # ------------------------------------------------------------------
    # HYDROGEL_PACK — pure MM around the long-run mean
    # ------------------------------------------------------------------
    def _hydro(self, product, order_depth, position):
        orders: List[Order] = []
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders

        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        fair = HYDRO_BLEND * mid + (1 - HYDRO_BLEND) * HYDRO_MEAN
        fair -= HYDRO_SKEW * position

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        if buy_room > 0 and best_ask < fair - HYDRO_TAKE_EDGE:
            available = -order_depth.sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        if sell_room > 0 and best_bid > fair + HYDRO_TAKE_EDGE:
            available = order_depth.buy_orders[best_bid]
            take = min(available, sell_room)
            if take > 0:
                orders.append(Order(product, best_bid, -take))
                position -= take
                sell_room -= take

        if spread >= HYDRO_MIN_SPREAD:
            fair = HYDRO_BLEND * mid + (1 - HYDRO_BLEND) * HYDRO_MEAN - HYDRO_SKEW * position
            bid_price = min(best_bid + 1, math.floor(fair - HYDRO_MM_EDGE))
            ask_price = max(best_ask - 1, math.ceil(fair + HYDRO_MM_EDGE))
            if bid_price < ask_price:
                if buy_room > 0:
                    size = min(HYDRO_MM_SIZE, buy_room)
                    orders.append(Order(product, int(bid_price), size))
                if sell_room > 0:
                    size = min(HYDRO_MM_SIZE, sell_room)
                    orders.append(Order(product, int(ask_price), -size))
        return orders

    # ------------------------------------------------------------------
    # VEV_4000 — deep-ITM arb, fair = max(S-K, 0)
    # ------------------------------------------------------------------
    def _itm_arb(self, product, order_depth, position, fair):
        orders: List[Order] = []
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders

        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)
        spread = best_ask - best_bid

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        if buy_room > 0 and best_ask < fair - OPT_TAKE_EDGE:
            available = -order_depth.sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        if sell_room > 0 and best_bid > fair + OPT_TAKE_EDGE:
            available = order_depth.buy_orders[best_bid]
            take = min(available, sell_room)
            if take > 0:
                orders.append(Order(product, best_bid, -take))
                position -= take
                sell_room -= take

        if spread >= ITM_MIN_SPREAD:
            bid_price = min(best_bid + 1, math.floor(fair - OPT_QUOTE_EDGE))
            ask_price = max(best_ask - 1, math.ceil(fair + OPT_QUOTE_EDGE))
            if bid_price < ask_price:
                if buy_room > 0 and bid_price > 0:
                    size = min(OPT_QUOTE_SIZE, buy_room)
                    orders.append(Order(product, int(bid_price), size))
                if sell_room > 0:
                    size = min(OPT_QUOTE_SIZE, sell_room)
                    orders.append(Order(product, int(ask_price), -size))
        return orders

    # ------------------------------------------------------------------
    # VEV_5300 — OTM MM with stable extrinsic, low delta
    # ------------------------------------------------------------------
    def _otm_mm(self, product, order_depth, position, fair):
        orders: List[Order] = []
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders

        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)
        spread = best_ask - best_bid
        adj_fair = fair - OPT_SKEW * position

        buy_room = POS_LIMIT - position
        sell_room = POS_LIMIT + position

        if buy_room > 0 and best_ask < adj_fair - OPT_TAKE_EDGE:
            available = -order_depth.sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        if sell_room > 0 and best_bid > adj_fair + OPT_TAKE_EDGE:
            available = order_depth.buy_orders[best_bid]
            take = min(available, sell_room)
            if take > 0:
                orders.append(Order(product, best_bid, -take))
                position -= take
                sell_room -= take

        if spread >= OPT_MIN_SPREAD:
            adj_fair = fair - OPT_SKEW * position
            bid_price = min(best_bid + 1, math.floor(adj_fair - OPT_QUOTE_EDGE))
            ask_price = max(best_ask - 1, math.ceil(adj_fair + OPT_QUOTE_EDGE))
            if bid_price < ask_price:
                if buy_room > 0 and bid_price > 0:
                    size = min(OPT_QUOTE_SIZE, buy_room)
                    orders.append(Order(product, int(bid_price), size))
                if sell_room > 0:
                    size = min(OPT_QUOTE_SIZE, sell_room)
                    orders.append(Order(product, int(ask_price), -size))
        return orders
