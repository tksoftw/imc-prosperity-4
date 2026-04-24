import json
import math
from typing import Dict, List

from datamodel import Order, TradingState


MAX_POS = 80
MARKET_DATA_BID = 102

ROOT_PRODUCT = "INTARIAN_PEPPER_ROOT"
OSMIUM_PRODUCT = "ASH_COATED_OSMIUM"

PEPPER_DRIFT_PER_TICK = 0.1001
ROOT_HISTORY = 8
ROOT_STRESS_POS = 68
ROOT_PANIC_POS = 36
ROOT_DELEVER_SIZE = 18
ROOT_STRESS_BUY_SIZE = 15
ROOT_LOOKBACK = 5
ROOT_STRESS_DROP = -14.0
ROOT_PANIC_DROP = -20.0
ROOT_STRESS_DEV = 10.0
ROOT_PANIC_DEV = 14.0
# Flatten: when a sustained downtrend is detected, actively sell inventory
# at best_bid - BUT only if best_bid is within this many ticks of the
# (already outlier-guarded) mid. Normal spreads here are ~15-20, so this
# floor allows normal books and rejects bizarre single-level bad bids.
ROOT_FLATTEN_MAX_BELOW_MID = 25.0
ROOT_FLATTEN_STRESS_SIZE = 14
ROOT_FLATTEN_PANIC_SIZE = 22

OSMIUM_CLEAR_POS = 6
OSMIUM_UNWIND_POS = 2
OSMIUM_JOIN_OFFSET = 1
OSMIUM_FAIR_BLEND = 0.75
OSMIUM_FAIR_CLAMP = 8.0
OSMIUM_TAKE_EDGE = 1.75
OSMIUM_QUOTE_EDGE = 2.25
OSMIUM_ORDER_SIZE = 15
OSMIUM_STRESS_ORDER_SIZE = 8
OSMIUM_MIN_SPREAD = 10
OSMIUM_STRESS_MIN_SPREAD = 12
OSMIUM_SKEW = 0.06
OSMIUM_SIGNAL_SCALE = 1.45
OSMIUM_STRESS_POS = 48
OSMIUM_PANIC_POS = 20
OSMIUM_DELEVER_SIZE = 18
OSMIUM_STRESS_MOVE = 4.0
OSMIUM_PANIC_MOVE = 6.0
ONE_SIDED_EDGE = 0.25

# Single-tick outlier guard.
#
# In synthetic / degenerate datasets the top-of-book occasionally dislocates
# for a single tick (e.g. mid 10940 -> 10660 -> 10940). Any logic that reacts
# to "sudden drop" (panic, deleverage, stress) will then cross the book at
# the dislocated prices and take a guaranteed loss on the reversion tick.
# Treat any tick whose mid deviates from the rolling reference by more than
# this many ticks as an outlier: don't trade, don't pollute history.
ROOT_OUTLIER_MID_DEV = 30.0
OSMIUM_OUTLIER_MID_DEV = 20.0
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


def recent_change(values: List[float], current: float, window: int) -> float:
    return current - values[-window] if len(values) >= window else 0.0


def rolling_mean(values: List[float], current: float, window: int) -> float:
    tail = values[-window:]
    series = tail + [current]
    return sum(series) / len(series)


class Trader:
    def bid(self) -> int:
        return MARKET_DATA_BID

    def run(self, state: TradingState):
        store = json.loads(state.traderData) if state.traderData else {}
        result: Dict[str, List[Order]] = {}

        tick = state.timestamp // 100
        for product, order_depth in state.order_depths.items():
            position = int(state.position.get(product, 0))
            if product == ROOT_PRODUCT:
                result[product] = self._trade_root(product, order_depth, position, store, tick)
            elif product == OSMIUM_PRODUCT:
                result[product] = self._trade_osmium(product, order_depth, position, store)
            else:
                result[product] = []

        return result, 0, json.dumps(store, separators=(",", ":"))

    def _trade_root(self, product, order_depth, position, store, tick):
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        if not buy_orders or not sell_orders:
            return []

        best_bid = max(buy_orders)
        best_ask = min(sell_orders)
        mid = (best_bid + best_ask) / 2

        root_store = store.get("root", {"mid": []})
        mids = list(root_store.get("mid", []))

        # ---- Single-tick outlier guard --------------------------------
        # Compare current mid to the MOST RECENT previous mid. A flash tick
        # is defined by a large single-tick detachment from the last normal
        # print (we never store outlier mids, so mids[-1] is always the
        # most recent normal value). Using mids[-1] instead of a short
        # rolling mean prevents the guard from mis-firing on the 4th+ tick
        # of a sustained real downtrend.
        if len(mids) >= 1:
            if abs(mid - mids[-1]) > ROOT_OUTLIER_MID_DEV:
                return []
        # ---------------------------------------------------------------

        roll_mean = rolling_mean(mids, mid, ROOT_LOOKBACK)
        move_3 = recent_change(mids, mid, 3)
        move_5 = recent_change(mids, mid, ROOT_LOOKBACK)
        panic = (
            len(mids) >= ROOT_LOOKBACK
            and move_5 <= ROOT_PANIC_DROP
            and mid < roll_mean - ROOT_PANIC_DEV
        )
        stress = panic or (
            len(mids) >= 3
            and move_3 <= ROOT_STRESS_DROP
            and mid < roll_mean - ROOT_STRESS_DEV
        )

        position_cap = ROOT_PANIC_POS if panic else (ROOT_STRESS_POS if stress else MAX_POS)
        orders: List[Order] = []

        # ---- Active flattening during sustained downtrends --------------
        # When mid has actually trended down (stress/panic), sell inventory
        # at best_bid provided best_bid is not a flash-level outlier. The
        # single-tick outlier guard above already rejects flash-crash mids,
        # so the remaining job here is just to refuse a pathological bid
        # that is e.g. 30+ points below the otherwise-normal mid.
        if (stress or panic) and position > 0:
            safe_floor = mid - ROOT_FLATTEN_MAX_BELOW_MID
            if best_bid >= safe_floor:
                size_cap = ROOT_FLATTEN_PANIC_SIZE if panic else ROOT_FLATTEN_STRESS_SIZE
                size = min(position, size_cap, buy_orders.get(best_bid, 0))
                if size > 0:
                    orders.append(Order(product, best_bid, -size))
                    position -= size

        # Aggressive drift-capture buy (matches baseline behaviour on real
        # pepper data, which has strong positive drift). Only suppressed
        # when an active downtrend has been detected: the flatten block
        # above has then already started dumping inventory, and buying on
        # the same tick would just churn.
        if position < position_cap and not panic and not stress:
            remaining = max(0, 9999 - tick)
            predicted_end_mid = best_ask + PEPPER_DRIFT_PER_TICK * remaining
            if predicted_end_mid > best_ask:
                take = min(-sell_orders.get(best_ask, 0), position_cap - position)
                if take > 0:
                    orders.append(Order(product, best_ask, take))
                    position += take

        mids.append(mid)
        root_store["mid"] = mids[-ROOT_HISTORY:]
        store["root"] = root_store
        return orders

    def _trade_osmium(self, product, order_depth, position, store):
        orders: List[Order] = []
        buy_orders = order_depth.buy_orders
        sell_orders = order_depth.sell_orders
        best_bid = max(buy_orders) if buy_orders else None
        best_ask = min(sell_orders) if sell_orders else None

        if best_bid is None and best_ask is None:
            return orders

        fair = store.get("fair", {}).get(product, 10000.0)
        history = store.get("osmium", {"mid": [], "imbalance": [], "micro_gap": []})
        mids = list(history["mid"])
        imbalances = list(history["imbalance"])
        micro_gaps = list(history["micro_gap"])

        if best_bid is not None and best_ask is not None:
            mid = (best_bid + best_ask) / 2

            # ---- Single-tick outlier guard ---------------------------
            # Compare against the last stored (non-outlier) mid. A flash
            # print detaches from the previous normal tick; a sustained
            # trend does not.
            if len(mids) >= 1:
                if abs(mid - mids[-1]) > OSMIUM_OUTLIER_MID_DEV:
                    fair_store = store.get("fair", {})
                    fair_store[product] = fair
                    store["fair"] = fair_store
                    return orders
            # ---------------------------------------------------------

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

            roll_mean = rolling_mean(mids, mid, 5)
            move_3 = recent_change(mids, mid, 3)
            move_5 = recent_change(mids, mid, 5)
            down_panic = len(mids) >= 5 and move_5 <= -OSMIUM_PANIC_MOVE and mid < roll_mean - max(5.0, spread / 2)
            up_panic = len(mids) >= 5 and move_5 >= OSMIUM_PANIC_MOVE and mid > roll_mean + max(5.0, spread / 2)
            down_stress = down_panic or (len(mids) >= 3 and move_3 <= -OSMIUM_STRESS_MOVE and mid < roll_mean - max(4.0, spread / 3))
            up_stress = up_panic or (len(mids) >= 3 and move_3 >= OSMIUM_STRESS_MOVE and mid > roll_mean + max(4.0, spread / 3))
            stress = down_stress or up_stress

            max_abs = OSMIUM_PANIC_POS if (down_panic or up_panic) else (OSMIUM_STRESS_POS if stress else MAX_POS)

            if position > max_abs:
                reduction = min(position - max_abs, OSMIUM_DELEVER_SIZE, buy_orders.get(best_bid, 0))
                if reduction > 0:
                    orders.append(Order(product, best_bid, -reduction))
                    position -= reduction
            elif position < -max_abs:
                reduction = min(-max_abs - position, OSMIUM_DELEVER_SIZE, -sell_orders.get(best_ask, 0))
                if reduction > 0:
                    orders.append(Order(product, best_ask, reduction))
                    position += reduction

            if down_panic and position > 0:
                extra = min(position, OSMIUM_DELEVER_SIZE, buy_orders.get(best_bid, 0))
                if extra > 0:
                    orders.append(Order(product, best_bid, -extra))
                    position -= extra
            elif up_panic and position < 0:
                extra = min(-position, OSMIUM_DELEVER_SIZE, -sell_orders.get(best_ask, 0))
                if extra > 0:
                    orders.append(Order(product, best_ask, extra))
                    position += extra

            if not down_panic:
                for price, volume in sorted(sell_orders.items()):
                    if price > fair - OSMIUM_TAKE_EDGE or position >= max_abs:
                        break
                    take = min(-volume, max_abs - position)
                    if take > 0:
                        orders.append(Order(product, price, take))
                        position += take

            if not up_panic:
                for price, volume in sorted(buy_orders.items(), reverse=True):
                    if price < fair + OSMIUM_TAKE_EDGE or position <= -max_abs:
                        break
                    take = min(volume, position + max_abs)
                    if take > 0:
                        orders.append(Order(product, price, -take))
                        position -= take

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

            min_spread = OSMIUM_STRESS_MIN_SPREAD if stress else OSMIUM_MIN_SPREAD
            quote_size = OSMIUM_STRESS_ORDER_SIZE if stress else OSMIUM_ORDER_SIZE

            if spread >= min_spread:
                adjusted_fair = fair - OSMIUM_SKEW * position
                buy_quote = min(
                    best_bid + OSMIUM_JOIN_OFFSET,
                    math.floor(adjusted_fair - OSMIUM_QUOTE_EDGE - (0.75 if down_stress else 0.0)),
                )
                sell_quote = max(
                    best_ask - OSMIUM_JOIN_OFFSET,
                    math.ceil(adjusted_fair + OSMIUM_QUOTE_EDGE + (0.75 if up_stress else 0.0)),
                )
                if buy_quote < sell_quote:
                    buy_size = min(quote_size, max_abs - position)
                    sell_size = min(quote_size, position + max_abs)
                    if buy_size > 0 and not down_stress:
                        orders.append(Order(product, int(buy_quote), buy_size))
                    if sell_size > 0 and not up_stress:
                        orders.append(Order(product, int(sell_quote), -sell_size))

            mids.append(mid)
            imbalances.append(imbalance)
            micro_gaps.append(micro_gap)
            history["mid"] = mids[-8:]
            history["imbalance"] = imbalances[-8:]
            history["micro_gap"] = micro_gaps[-8:]
            store["osmium"] = history
        else:
            if (
                best_ask is not None
                and fair - best_ask >= ONE_SIDED_EDGE
                and position < OSMIUM_STRESS_POS
            ):
                take = min(-sell_orders[best_ask], OSMIUM_STRESS_POS - position)
                if take > 0:
                    orders.append(Order(product, best_ask, take))
            if (
                best_bid is not None
                and best_bid - fair >= ONE_SIDED_EDGE
                and position > -OSMIUM_STRESS_POS
            ):
                take = min(buy_orders[best_bid], position + OSMIUM_STRESS_POS)
                if take > 0:
                    orders.append(Order(product, best_bid, -take))

        fair_store = store.get("fair", {})
        fair_store[product] = fair
        store["fair"] = fair_store
        return orders
