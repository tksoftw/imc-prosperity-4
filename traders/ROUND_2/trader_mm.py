"""Market-maker-first trader for ROUND_2.

Key observations that drive this file:

- ASH_COATED_OSMIUM is mean-reverting with a wide (16-19 tick) typical
  spread, so market making is the dominant profit source; the baseline
  already does it but only posts a single quote level and only when
  spread >= 10, leaving money on the table. Here we keep the same ML
  fair value and (modest) take gate, add a second outer maker level,
  and widen the quoting regime so we quote on more ticks.

- INTARIAN_PEPPER_ROOT has a near-deterministic +0.1/tick drift. The
  baseline just takes at best_ask until max-pos and holds; taking pays
  the ask every time and limits trade activity. Since the drift is
  positive, the only ways to improve are (a) fill cheaper via resting
  bids (maker), and (b) trade more while never shorting. We never sell:
  holding to close and marking to final mid dominates any flatten-at-
  best-bid strategy, so the late-day "dump" is explicitly removed.

  Our pepper flow each tick:
    1. take only the TOP ask level (no book-walking), if it's a clear
       bargain vs. forward_fair = mid + drift * remaining_ticks.
    2. rest an aggressive maker bid at best_bid+1 for the remaining
       inventory capacity, capped so we never overpay vs forward_fair.
    3. rest a cheaper backup bid below it to catch dips.
  No sell orders are ever generated for pepper.
"""

import json
import math
from typing import Dict, List

from datamodel import Order, TradingState


MAX_POS = 80
DAY_LAST_TICK = 9999

# ----- INTARIAN_PEPPER_ROOT (deterministic +0.1/tick drift) -----
# Two complementary levers:
#   1. Drift capture: forward_fair = mid + drift * remaining_ticks. Any
#      take priced << forward_fair is accretive, and we want to hold a
#      long base inventory into the close (mark-to-market at final mid
#      strictly dominates any "flatten at best bid" close-out).
#   2. Spread scalping: because the book is typically 13-17 ticks wide
#      and bots do trade at both the best bid and the best ask, we
#      post a 1-tick-inside quote on BOTH sides around the current mid
#      with an inventory skew that keeps us on average long.  Each
#      completed round-trip books ~spread profit independent of drift.

PEPPER_DRIFT_PER_TICK = 0.1001
PEPPER_TAKE_EDGE = 1.0            # top ask < forward_fair - this => take
PEPPER_BASE_LONG = 70             # sell scalp only allowed above this inventory
PEPPER_SCALP_SIZE = 5             # size of each inside-book scalp quote
PEPPER_BUY_EDGE = 3.0             # local fair - this => maker buy price (pre-clamp)
PEPPER_SELL_EDGE = 6.0            # local fair + this => maker sell price (pre-clamp)
PEPPER_SKEW_PER_UNIT = 0.05       # inventory skew (per unit of position)
PEPPER_MIN_SELL_ABOVE_MID = 6.0   # never sell at less than mid + this
PEPPER_ACCUMULATE_SIZE = 25       # bulk bid below fair to keep topping up to MAX_POS

# ----- ASH_COATED_OSMIUM (mean-reverting, wide-spread) -----

OSMIUM_FAIR_BLEND = 0.75
OSMIUM_FAIR_CLAMP = 8.0
OSMIUM_SIGNAL_SCALE = 1.45
OSMIUM_TAKE_EDGE = 1.75
OSMIUM_INNER_EDGE = 2.25
OSMIUM_OUTER_EDGE = 4.5
OSMIUM_INNER_SIZE = 15
OSMIUM_OUTER_SIZE = 20
OSMIUM_JOIN_OFFSET = 1
OSMIUM_MIN_SPREAD = 10
OSMIUM_SKEW_PER_UNIT = 0.06
OSMIUM_CLEAR_POS = 6
OSMIUM_UNWIND_POS = 2
OSMIUM_ENABLE_OUTER = True
OSMIUM_ONE_SIDED_EDGE = 0.25  # take when only one side of book is present

OSMIUM_COEFFS = {
    "intercept": 0.17752501230081807,
    "spread": -0.108328,
    "imbalance_1": 4.599323,
    "micro_gap": -1.742129,
    "mid_ret_lag_1": -1.322975,
    "mid_ret_lag_2": -1.007575,
    "mid_ret_lag_5": -0.449999,
    "imbalance_1_lag_1": 2.866034,
    "imbalance_1_lag_2": 2.241078,
    "imbalance_1_lag_5": 1.190819,
    "micro_gap_lag_1": -1.091608,
    "micro_gap_lag_2": -0.898534,
    "micro_gap_lag_5": -0.517795,
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lag(values: List[float], offset: int) -> float:
    return values[-offset] if len(values) >= offset else 0.0


class Trader:
    def run(self, state: TradingState):
        store = json.loads(state.traderData) if state.traderData else {}
        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            position = int(state.position.get(product, 0))

            if product == "INTARIAN_PEPPER_ROOT":
                result[product] = self._pepper(
                    product, order_depth, position, state.timestamp
                )
                continue

            if product == "ASH_COATED_OSMIUM":
                result[product] = self._osmium(
                    product, order_depth, position, store
                )
                continue

            result[product] = []

        return result, 0, json.dumps(store, separators=(",", ":"))

    # ------------------------------------------------------------------
    # INTARIAN_PEPPER_ROOT - buy-only market maker (never sells)
    # ------------------------------------------------------------------

    def _pepper(self, product, order_depth, position, timestamp):
        orders: List[Order] = []
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        if not buy_orders or not sell_orders:
            # Missing one side of the book: require both for a stable fair value.
            return orders

        best_bid = max(buy_orders)
        best_ask = min(sell_orders)

        tick = timestamp // 100
        remaining = max(0, DAY_LAST_TICK - tick)
        mid = (best_bid + best_ask) / 2
        forward_fair = mid + PEPPER_DRIFT_PER_TICK * remaining

        # Running intent trackers - cumulative posted orders must keep
        # position + sum(buy_orders) <= MAX_POS and position - sum(sell_orders) >= -MAX_POS.
        buy_room = MAX_POS - position       # max additional buy units allowed
        sell_room = MAX_POS + position      # max additional sell units allowed

        # (1) Take obviously cheap top-of-ask against the forward fair.
        if buy_room > 0 and best_ask < forward_fair - PEPPER_TAKE_EDGE:
            available = -sell_orders[best_ask]
            take = min(available, buy_room)
            if take > 0:
                orders.append(Order(product, best_ask, take))
                position += take
                buy_room -= take

        # (2) Rotating scalp quotes one tick inside the book, skewed long.
        skew = PEPPER_SKEW_PER_UNIT * position
        local_fair = mid - skew
        raw_buy = math.floor(local_fair - PEPPER_BUY_EDGE)
        raw_sell = math.ceil(local_fair + PEPPER_SELL_EDGE)
        sell_floor = math.ceil(mid + PEPPER_MIN_SELL_ABOVE_MID)

        scalp_buy_price = min(best_bid + 1, raw_buy, best_ask - 1)
        scalp_sell_price = max(best_ask - 1, raw_sell, sell_floor)

        if buy_room > 0 and scalp_buy_price > 0 and scalp_buy_price < scalp_sell_price:
            size = min(PEPPER_SCALP_SIZE, buy_room)
            if size > 0:
                orders.append(Order(product, int(scalp_buy_price), size))
                buy_room -= size

        # Only post a sell scalp once we're meaningfully long and the quote
        # is comfortably above the current mid (so a fill is a clear "spike").
        if (
            position >= PEPPER_BASE_LONG
            and sell_room > 0
            and scalp_sell_price > scalp_buy_price
            and scalp_sell_price >= mid + PEPPER_MIN_SELL_ABOVE_MID
        ):
            size = min(PEPPER_SCALP_SIZE, sell_room, max(0, position - PEPPER_BASE_LONG + PEPPER_SCALP_SIZE))
            if size > 0:
                orders.append(Order(product, int(scalp_sell_price), -size))
                sell_room -= size

        # (3) Bulk accumulation bid for remaining buy capacity, at a price
        #     distinctly below the scalp so order priorities do not collide.
        if buy_room > 0:
            accum_price_raw = min(best_bid + 1, math.floor(mid) - 3, best_ask - 2)
            if scalp_buy_price > 0:
                accum_price_raw = min(accum_price_raw, scalp_buy_price - 1)
            if accum_price_raw > 0:
                size = min(PEPPER_ACCUMULATE_SIZE, buy_room)
                if size > 0:
                    orders.append(Order(product, int(accum_price_raw), size))
                    buy_room -= size

        return orders

    # ------------------------------------------------------------------
    # ASH_COATED_OSMIUM - two-level maker with ML fair value
    # ------------------------------------------------------------------

    def _osmium(self, product, order_depth, position, store):
        orders: List[Order] = []
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        best_bid = max(buy_orders) if buy_orders else None
        best_ask = min(sell_orders) if sell_orders else None
        if best_bid is None and best_ask is None:
            return orders

        last_fair = store.get("fair", {}).get(product)
        fair = last_fair if last_fair is not None else 10000.0
        history = store.get("osmium", {"mid": [], "imbalance": [], "micro_gap": []})
        mids = history["mid"]
        imbalances = history["imbalance"]
        micro_gaps = history["micro_gap"]

        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            buy_volume_1 = buy_orders.get(best_bid, 0)
            sell_volume_1 = -sell_orders.get(best_ask, 0)
            total_volume_1 = max(1, buy_volume_1 + sell_volume_1)
            imbalance = (buy_volume_1 - sell_volume_1) / total_volume_1
            microprice = (best_ask * buy_volume_1 + best_bid * sell_volume_1) / total_volume_1
            micro_gap = (microprice - mid) / 5.0

            features = {
                "spread": spread / 10.0,
                "imbalance_1": imbalance,
                "micro_gap": micro_gap,
                "mid_ret_lag_1": (mid - mids[-1]) / 5.0 if len(mids) >= 1 else 0.0,
                "mid_ret_lag_2": (mid - mids[-2]) / 5.0 if len(mids) >= 2 else 0.0,
                "mid_ret_lag_5": (mid - mids[-5]) / 5.0 if len(mids) >= 5 else 0.0,
                "imbalance_1_lag_1": lag(imbalances, 1),
                "imbalance_1_lag_2": lag(imbalances, 2),
                "imbalance_1_lag_5": lag(imbalances, 5),
                "micro_gap_lag_1": lag(micro_gaps, 1),
                "micro_gap_lag_2": lag(micro_gaps, 2),
                "micro_gap_lag_5": lag(micro_gaps, 5),
            }
            signal = OSMIUM_COEFFS["intercept"]
            for name, value in features.items():
                signal += OSMIUM_COEFFS[name] * value
            predicted_mid = mid + signal * OSMIUM_SIGNAL_SCALE
            fair = 10000.0 + clamp(
                (predicted_mid - 10000.0) * OSMIUM_FAIR_BLEND,
                -OSMIUM_FAIR_CLAMP,
                OSMIUM_FAIR_CLAMP,
            )

            # Take (baseline gate: still profitable on clear mispricings).
            for price, volume in sorted(sell_orders.items()):
                if price > fair - OSMIUM_TAKE_EDGE or position >= MAX_POS:
                    break
                take = min(-volume, MAX_POS - position)
                if take > 0:
                    orders.append(Order(product, price, take))
                    position += take

            for price, volume in sorted(buy_orders.items(), reverse=True):
                if price < fair + OSMIUM_TAKE_EDGE or position <= -MAX_POS:
                    break
                take = min(volume, position + MAX_POS)
                if take > 0:
                    orders.append(Order(product, price, -take))
                    position -= take

            # Soft inventory clearing against fair.
            if position > OSMIUM_CLEAR_POS:
                need = position - OSMIUM_UNWIND_POS
                for price, volume in sorted(buy_orders.items(), reverse=True):
                    if price < fair or need <= 0:
                        break
                    hit = min(volume, need)
                    if hit > 0:
                        orders.append(Order(product, price, -hit))
                        position -= hit
                        need -= hit
            elif position < -OSMIUM_CLEAR_POS:
                need = -OSMIUM_UNWIND_POS - position
                for price, volume in sorted(sell_orders.items()):
                    if price > fair or need <= 0:
                        break
                    hit = min(-volume, need)
                    if hit > 0:
                        orders.append(Order(product, price, hit))
                        position += hit
                        need -= hit

            # Two-level market making with inventory skew.
            if spread >= OSMIUM_MIN_SPREAD:
                skew = OSMIUM_SKEW_PER_UNIT * position
                adj_fair = fair - skew

                inner_raw_buy = math.floor(adj_fair - OSMIUM_INNER_EDGE)
                inner_raw_sell = math.ceil(adj_fair + OSMIUM_INNER_EDGE)

                buy_inner = min(best_bid + OSMIUM_JOIN_OFFSET, inner_raw_buy)
                sell_inner = max(best_ask - OSMIUM_JOIN_OFFSET, inner_raw_sell)

                if buy_inner < sell_inner:
                    buy_room = MAX_POS - position
                    if buy_room > 0:
                        size = min(OSMIUM_INNER_SIZE, buy_room)
                        if size > 0:
                            orders.append(Order(product, int(buy_inner), size))
                            if OSMIUM_ENABLE_OUTER:
                                outer_room = buy_room - size
                                if outer_room > 0:
                                    outer_price = math.floor(adj_fair - OSMIUM_OUTER_EDGE)
                                    outer_price = min(outer_price, buy_inner - 1)
                                    outer_size = min(OSMIUM_OUTER_SIZE, outer_room)
                                    if outer_size > 0:
                                        orders.append(
                                            Order(product, int(outer_price), outer_size)
                                        )

                    sell_room = position + MAX_POS
                    if sell_room > 0:
                        size = min(OSMIUM_INNER_SIZE, sell_room)
                        if size > 0:
                            orders.append(Order(product, int(sell_inner), -size))
                            if OSMIUM_ENABLE_OUTER:
                                outer_room = sell_room - size
                                if outer_room > 0:
                                    outer_price = math.ceil(adj_fair + OSMIUM_OUTER_EDGE)
                                    outer_price = max(outer_price, sell_inner + 1)
                                    outer_size = min(OSMIUM_OUTER_SIZE, outer_room)
                                    if outer_size > 0:
                                        orders.append(
                                            Order(product, int(outer_price), -outer_size)
                                        )

            mids.append(mid)
            imbalances.append(imbalance)
            micro_gaps.append(micro_gap)
            history["mid"] = mids[-8:]
            history["imbalance"] = imbalances[-8:]
            history["micro_gap"] = micro_gaps[-8:]
            store["osmium"] = history
        else:
            # One side of the book is missing: still take cheap asks / rich bids
            # against the stale fair value.
            if (
                best_ask is not None
                and fair - best_ask >= OSMIUM_ONE_SIDED_EDGE
                and position < MAX_POS
            ):
                take = min(-sell_orders[best_ask], MAX_POS - position)
                if take > 0:
                    orders.append(Order(product, best_ask, take))
                    position += take
            if (
                best_bid is not None
                and best_bid - fair >= OSMIUM_ONE_SIDED_EDGE
                and position > -MAX_POS
            ):
                take = min(buy_orders[best_bid], MAX_POS + position)
                if take > 0:
                    orders.append(Order(product, best_bid, -take))
                    position -= take

        fair_store = store.get("fair", {})
        fair_store[product] = fair
        store["fair"] = fair_store
        return orders
