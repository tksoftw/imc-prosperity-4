import json
import math
from typing import Dict, List

from datamodel import Order, TradingState

MAX_POS = 80
PEPPER_DRIFT_PER_TICK = 0.1001
OSMIUM_CLEAR_POS = 6
OSMIUM_UNWIND_POS = 2
OSMIUM_JOIN_OFFSET = 1
OSMIUM_FAIR_BLEND = 0.75
OSMIUM_FAIR_CLAMP = 8.0
OSMIUM_TAKE_EDGE = 1.75
OSMIUM_QUOTE_EDGE = 2.25
OSMIUM_ORDER_SIZE = 15
OSMIUM_MIN_SPREAD = 10
OSMIUM_SKEW = 0.06
OSMIUM_SIGNAL_SCALE = 1.45
ONE_SIDED_EDGE = 0.25
OSMIUM_COEFFS = {'intercept': 0.17752501230081807, 'spread': -0.108328, 'imbalance_1': 4.599323, 'micro_gap': -1.742129, 'mid_ret_lag_1': -1.322975, 'mid_ret_lag_2': -1.007575, 'mid_ret_lag_5': -0.449999, 'imbalance_1_lag_1': 2.866034, 'imbalance_1_lag_2': 2.241078, 'imbalance_1_lag_5': 1.190819, 'micro_gap_lag_1': -1.091608, 'micro_gap_lag_2': -0.898534, 'micro_gap_lag_5': -0.517795}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lag(values: List[float], offset: int) -> float:
    return values[-offset] if len(values) >= offset else 0.0


class Trader:
    def run(self, state: TradingState):
        store = json.loads(state.traderData) if state.traderData else {}
        result: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            orders: List[Order] = []
            buy_orders = order_depth.buy_orders
            sell_orders = order_depth.sell_orders
            position = int(state.position.get(product, 0))
            last_fair = store.get("fair", {}).get(product)

            if product == "INTARIAN_PEPPER_ROOT":
                continue
                tick = state.timestamp // 100
                remaining_ticks = max(0, 9999 - tick)
                best_ask = min(sell_orders) if sell_orders else None
                if best_ask is not None:
                    predicted_end_mid = best_ask + PEPPER_DRIFT_PER_TICK * remaining_ticks
                    if predicted_end_mid > best_ask and position < MAX_POS:
                        take = min(-sell_orders[best_ask], MAX_POS - position)
                        if take > 0:
                            orders.append(Order(product, best_ask, take))
                result[product] = orders
                continue

            if product != "ASH_COATED_OSMIUM":
                result[product] = []
                continue

            best_bid = max(buy_orders) if buy_orders else None
            best_ask = min(sell_orders) if sell_orders else None

            if best_bid is None and best_ask is None:
                result[product] = []
                continue

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

                if spread >= OSMIUM_MIN_SPREAD:
                    adjusted_fair = fair - OSMIUM_SKEW * position
                    buy_quote = min(
                        best_bid + OSMIUM_JOIN_OFFSET,
                        math.floor(adjusted_fair - OSMIUM_QUOTE_EDGE),
                    )
                    sell_quote = max(
                        best_ask - OSMIUM_JOIN_OFFSET,
                        math.ceil(adjusted_fair + OSMIUM_QUOTE_EDGE),
                    )
                    if buy_quote < sell_quote:
                        buy_size = min(OSMIUM_ORDER_SIZE, MAX_POS - position)
                        sell_size = min(OSMIUM_ORDER_SIZE, position + MAX_POS)
                        if buy_size > 0:
                            orders.append(Order(product, int(buy_quote), buy_size))
                        if sell_size > 0:
                            orders.append(Order(product, int(sell_quote), -sell_size))

                mids.append(mid)
                imbalances.append(imbalance)
                micro_gaps.append(micro_gap)
                history["mid"] = mids[-8:]
                history["imbalance"] = imbalances[-8:]
                history["micro_gap"] = micro_gaps[-8:]
                store["osmium"] = history
            else:
                if best_ask is not None and fair - best_ask >= ONE_SIDED_EDGE and position < MAX_POS:
                    take = min(-sell_orders[best_ask], MAX_POS - position)
                    if take > 0:
                        orders.append(Order(product, best_ask, take))
                if best_bid is not None and best_bid - fair >= ONE_SIDED_EDGE and position > -MAX_POS:
                    take = min(buy_orders[best_bid], MAX_POS + position)
                    if take > 0:
                        orders.append(Order(product, best_bid, -take))

            fair_store = store.get("fair", {})
            fair_store[product] = fair
            store["fair"] = fair_store
            result[product] = orders

        return result, 0, json.dumps(store, separators=(",", ":"))
