"""ROUND_3: best-of-each per product, refactored.

Each block below is the highest-scoring strategy for its product, lifted
from the rank table and refactored to share a small set of helpers.

  HYDROGEL_PACK         <- trader_overfit
      Static fair = 10_000, take only when book strays >= 7. No MM, no
      anchor smoothing, large size. The simplest winner.

  VELVETFRUIT_EXTRACT   <- trader_smile_surface / flash_bug / combined
      Fast EMA(15) of wall_mid blended with mean 5255, insider tilt on
      >=9-lot prints, saturated MM at floor(fair-2) / ceil(fair+2).

  VEV_4000 / VEV_4500   <- trader_smile_surface
      Flash-arb on the deep-ITM dislocation. Fair = max(S-K, 0). Lifts
      sub-intrinsic asks, sells over-intrinsic bids.

  VEV_5000              <- trader_combined
      ATM extrinsic-anchor MM. EMA-smoothed observed extrinsic, capped
      to anchor +-6.

  VEV_5100 / 5200       <- trader_smile_surface
      BS pricing with smile interpolation; Newton-Raphson IV solver per
      strike, EMA-smoothed and spread-weighted into a single smile level.

  VEV_5300              <- trader_bygpt_overfit
      Static extrinsic = 47, take/quote MM, +/-2 fair tilt from filtered
      vev_dir signal (only fires when both 5200 and 5300 spreads <= 2).

  VEV_5400              <- trader_strats
      Static extrinsic = 16, MM gated on spread >= 2.

  VEV_5500              <- trader_combined
      Wing scalp around extrinsic anchor 6.

  VEV_6000 / VEV_6500   skipped — floored at 0.5, nobody scores here.

Allowed libs (per submission rules): pandas, NumPy, statistics, math,
typing, jsonpickle. We use statistics.NormalDist for BS, math for the
log/sqrt/exp work, jsonpickle for state.
"""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

import jsonpickle

from datamodel import Order, TradingState


# ── Black-Scholes (statistics.NormalDist, no scipy needed) ──────────────────

NORMAL = NormalDist()

# Time to expiry baked into the BS pricing. Using 1.0 keeps the model
# numerically stable; the absolute level is absorbed by the smoothed IV.
TTE = 1.0
IV_LO, IV_HI = 1e-4, 0.08


def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    return S * NORMAL.cdf(d1) - K * NORMAL.cdf(d2)


def bs_vega(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    return S * NORMAL.pdf(d1) * sqrt_t


def bs_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    return NORMAL.cdf(d1)


def implied_vol(S: float, K: float, T: float, market_price: float,
                sigma_init: float = 0.03, iters: int = 30) -> float:
    """Newton-Raphson IV solver, clamped to [IV_LO, IV_HI]."""
    if market_price <= max(S - K, 0.0) + 1e-6:
        return IV_LO
    sigma = sigma_init
    for _ in range(iters):
        diff = bs_call(S, K, T, sigma) - market_price
        if abs(diff) < 1e-6:
            break
        v = bs_vega(S, K, T, sigma)
        if v < 1e-8:
            break
        sigma = max(IV_LO, min(IV_HI, sigma - diff / v))
    return sigma


# ── Smile shape: IV offset by moneyness (K-S)/100, linear interpolation ─────

SMILE_KNOTS: Tuple[Tuple[float, float], ...] = (
    (-2.5, -0.0006),
    (-1.5, -0.0012),
    (-0.5, -0.0003),
    (0.5,   0.0),
    (1.5,  -0.0019),
    (2.5,   0.0005),
    (7.5,  -0.0040),
    (12.5, -0.0060),
)


def smile_offset(moneyness: float) -> float:
    if moneyness <= SMILE_KNOTS[0][0]:
        return SMILE_KNOTS[0][1]
    if moneyness >= SMILE_KNOTS[-1][0]:
        return SMILE_KNOTS[-1][1]
    for (x0, y0), (x1, y1) in zip(SMILE_KNOTS, SMILE_KNOTS[1:]):
        if x0 <= moneyness <= x1:
            return y0 + (y1 - y0) * (moneyness - x0) / (x1 - x0)
    return 0.0


# ── Quote helpers ───────────────────────────────────────────────────────────


@dataclass
class Quote:
    """Top-of-book + wall-of-book snapshot.

    `wall_mid` averages the highest-volume bid level with the highest-
    volume ask level. The hedgehogs writeup uses this as a more stable
    estimator of true fair than raw (best_bid + best_ask) / 2 — the
    walls are where designated MM bots park their liquidity.
    """
    bid: Optional[int] = None
    bid_vol: int = 0
    ask: Optional[int] = None
    ask_vol: int = 0
    bid_wall: Optional[int] = None
    ask_wall: Optional[int] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2.0

    @property
    def wall_mid(self) -> Optional[float]:
        # Fall back to top-of-book if either wall is missing.
        bw = self.bid_wall if self.bid_wall is not None else self.bid
        aw = self.ask_wall if self.ask_wall is not None else self.ask
        if bw is None or aw is None:
            return None
        return (bw + aw) / 2.0

    @property
    def spread(self) -> Optional[int]:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid


def quote_from(order_depth) -> Quote:
    q = Quote()
    if order_depth is None:
        return q
    if order_depth.buy_orders:
        q.bid = max(order_depth.buy_orders)
        q.bid_vol = order_depth.buy_orders[q.bid]
        # Wall = level with greatest displayed liquidity.
        q.bid_wall = max(order_depth.buy_orders, key=lambda p: order_depth.buy_orders[p])
    if order_depth.sell_orders:
        q.ask = min(order_depth.sell_orders)
        q.ask_vol = order_depth.sell_orders[q.ask]
        q.ask_wall = min(order_depth.sell_orders, key=lambda p: -order_depth.sell_orders[p])
    return q


def update_ema(emas: Dict[str, float], key: str, value: float, window: int) -> float:
    if key not in emas:
        emas[key] = value
        return value
    alpha = 2.0 / (window + 1.0)
    emas[key] = alpha * value + (1.0 - alpha) * emas[key]
    return emas[key]


# ── Per-product config ──────────────────────────────────────────────────────

VELVET = "VELVETFRUIT_EXTRACT"
HYDRO = "HYDROGEL_PACK"

POS_LIMITS = {
    VELVET: 200,
    HYDRO: 200,
    "VEV_4000": 120,
    "VEV_4500": 90,
    "VEV_5000": 60,
    "VEV_5100": 50,
    "VEV_5200": 50,
    "VEV_5300": 70,
    "VEV_5400": 50,
    "VEV_5500": 30,
}


# ── Trader ──────────────────────────────────────────────────────────────────


class Trader:
    # ---- state ----
    def load_state(self, td: str) -> Dict:
        default = {"emas": {}, "v_signal_til": -1, "vev_dir": 0, "vev_dir_til": -1}
        if not td:
            return default
        try:
            data = jsonpickle.decode(td)
        except Exception:
            return default
        for k, v in default.items():
            data.setdefault(k, v)
        return data

    # ---- order helpers (track in-flight orders to respect position limits) ----
    @staticmethod
    def position(state: TradingState, product: str) -> int:
        return int(state.position.get(product, 0))

    def buy_room(self, state, product, orders) -> int:
        used = sum(o.quantity for o in orders if o.quantity > 0)
        return max(0, POS_LIMITS[product] - self.position(state, product) - used)

    def sell_room(self, state, product, orders) -> int:
        used = sum(-o.quantity for o in orders if o.quantity < 0)
        return max(0, POS_LIMITS[product] + self.position(state, product) - used)

    def buy(self, state, product, orders, price, qty):
        qty = min(max(0, int(qty)), self.buy_room(state, product, orders))
        if qty > 0:
            orders.append(Order(product, int(price), qty))

    def sell(self, state, product, orders, price, qty):
        qty = min(max(0, int(qty)), self.sell_room(state, product, orders))
        if qty > 0:
            orders.append(Order(product, int(price), -qty))

    # ---- Signals ----
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int]:
        """Detect:
          (a) VELVET Accumulator: any market trade with quantity >= 9.
          (b) vev_dir: directional score on VEV_5200/5300 trades, but only
              when BOTH spreads are <= 2 (the alpha-tip filter).
        Both signals decay over time via a TTL stored in `store`.
        """
        for tr in state.market_trades.get(VELVET, []):
            if abs(int(tr.quantity)) >= 9:
                store["v_signal_til"] = state.timestamp + 1500
                break

        q5200 = quote_from(state.order_depths.get("VEV_5200"))
        q5300 = quote_from(state.order_depths.get("VEV_5300"))
        tight = (
            q5200.spread is not None and q5200.spread <= 2
            and q5300.spread is not None and q5300.spread <= 2
        )
        if tight:
            score = 0
            for prod in ("VEV_5200", "VEV_5300"):
                q = quote_from(state.order_depths.get(prod))
                if q.mid is None:
                    continue
                for tr in state.market_trades.get(prod, []):
                    px = int(tr.price)
                    n = abs(int(tr.quantity))
                    if q.ask is not None and px >= q.ask:
                        score += n
                    elif q.bid is not None and px <= q.bid:
                        score -= n
                    elif px > q.mid:
                        score += n
                    elif px < q.mid:
                        score -= n
            if score > 0:
                store["vev_dir"] = 1
                store["vev_dir_til"] = state.timestamp + 1200
            elif score < 0:
                store["vev_dir"] = -1
                store["vev_dir_til"] = state.timestamp + 1200

        v_active = state.timestamp <= int(store.get("v_signal_til", -1))
        vev_dir = (
            int(store.get("vev_dir", 0))
            if state.timestamp <= int(store.get("vev_dir_til", -1))
            else 0
        )
        return v_active, vev_dir

    # ---- HYDROGEL_PACK: rolling-fair MM ----
    # Anchor 9991 is the historical mean midpoint (between 9990 hist and 9979
    # submission). At 15% weight it barely biases fair (~1.8 ticks error even
    # in the 9979 regime), so no blowup. The fast+slow EMA blend does most of
    # the work; including raw mid (20%) adds responsiveness on big moves.
    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        q = quote_from(state.order_depths.get(HYDRO))
        wall = q.wall_mid
        if wall is None:
            return
        orders = result.setdefault(HYDRO, [])
        position = self.position(state, HYDRO)

        ANCHOR = 9991.0
        fast = update_ema(store["emas"], f"{HYDRO}_fast", wall, 30)
        slow = update_ema(store["emas"], f"{HYDRO}_slow", wall, 120)
        fair = 0.20 * wall + 0.25 * fast + 0.40 * slow + 0.15 * ANCHOR
        fair -= 0.05 * position

        TAKE_EDGE = 7.0
        QUOTE_EDGE = 4.0
        QUOTE_SIZE = 18

        if q.ask is not None and q.ask <= fair - TAKE_EDGE:
            self.buy(state, HYDRO, orders, q.ask, min(-q.ask_vol, 35))
        if q.bid is not None and q.bid >= fair + TAKE_EDGE:
            self.sell(state, HYDRO, orders, q.bid, min(q.bid_vol, 35))

        if q.spread is not None and q.spread >= 10:
            target_bid = int(min(q.bid + 1, math.floor(fair - QUOTE_EDGE)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + QUOTE_EDGE)))
            if target_bid < target_ask:
                self.buy(state, HYDRO, orders, target_bid, QUOTE_SIZE)
                self.sell(state, HYDRO, orders, target_ask, QUOTE_SIZE)

    # ---- VELVETFRUIT_EXTRACT (smile_surface): EMA + insider tilt + saturated MM
    # Uses wall_mid (more stable than raw mid) as the EMA source, per hedgehogs.
    # Note: vev_dir is intentionally NOT used here. It hurts VELVET (-$60K vs
    # winner) while helping VEV_5300 — consumed only in trade_5300.
    def trade_velvet(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        v_active: bool,
    ) -> None:
        q = quote_from(state.order_depths.get(VELVET))
        wall = q.wall_mid
        if wall is None:
            return
        orders = result.setdefault(VELVET, [])
        position = self.position(state, VELVET)
        fast = update_ema(store["emas"], f"{VELVET}_fast", wall, 15)

        fair = 0.7 * fast + 0.3 * 5255.0
        fair -= 0.015 * position
        if v_active:
            fair += 1.5

        if v_active and q.ask is not None and q.ask <= fair:
            self.buy(state, VELVET, orders, q.ask, min(-q.ask_vol, 25))

        # Saturated MM (no cross-book guard — matches the winner)
        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))
        self.buy(state, VELVET, orders, target_bid, POS_LIMITS[VELVET])
        self.sell(state, VELVET, orders, target_ask, POS_LIMITS[VELVET])

    # ---- VEV_4000 / VEV_4500: flash-arb deep ITM (smile_surface) ----
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
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0)
        spread = q.spread
        local_shift = q.mid - fair
        shift = surface_shift if surface_shift is not None else local_shift

        flash_down = shift <= -4.0
        flash_up = shift >= 4.0
        actual_cheap = q.ask <= fair - 2

        if flash_down and q.ask <= fair:
            size = 12 if strike == 4000 else 8
            if actual_cheap:
                size += 4
            if wing_signal or spread <= 10:
                size += 3
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))

        if actual_cheap:
            size = 16 if strike == 4000 else 10
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size))

        if position > 0:
            if q.bid >= fair + 1 or (flash_up and q.bid >= fair):
                size = 10 if strike == 4000 else 7
                self.sell(state, product, orders, q.bid,
                          min(position, q.bid_vol, size))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.0)))
            if target_ask > q.bid:
                self.sell(state, product, orders, target_ask, min(position, 5))

        if position <= 0 and spread >= 8 and not flash_up:
            target_bid = int(min(q.bid + 1,
                                 math.floor(fair - (2.0 if strike == 4000 else 3.0))))
            if 0 < target_bid < q.ask:
                self.buy(state, product, orders, target_bid, 3 if strike == 4000 else 2)

    # ---- VEV_5000: ATM extrinsic-anchor MM (trader_combined) ----
    def trade_5000(self, state, store, result, S):
        product, strike, anchor = "VEV_5000", 5000, 13.0
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        # Cap drift around the anchor so a flash tick can't whip fair value.
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend - 0.05 * position

        if q.ask <= fair - 3.0:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 8))
        if q.bid >= fair + 3.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 8))

        if q.spread >= 3:
            target_bid = int(min(q.bid + 1, math.floor(fair - 1.5)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if target_bid < target_ask:
                if target_bid > 0:
                    self.buy(state, product, orders, target_bid, 5)
                self.sell(state, product, orders, target_ask, 5)

    # ---- VEV_5100 / 5200: BS-with-smile IV MM (smile_surface) ----
    # The smile fit was tuned against raw mid: switching it to wall_mid
    # subtly shifts implied vols and bleeds PnL. Keep raw mid here.
    def smile_level(self, state: TradingState, store: Dict, S: float) -> float:
        weighted = total_w = 0.0
        for strike in (5000, 5100, 5200, 5300, 5400, 5500):
            q = quote_from(state.order_depths.get(f"VEV_{strike}"))
            if q.mid is None or q.spread is None:
                continue
            iv_obs = implied_vol(S, strike, TTE, q.mid)
            iv_smooth = update_ema(store["emas"], f"iv_{strike}", iv_obs, 60)
            level = iv_smooth - smile_offset((strike - S) / 100.0)
            w = 1.0 / max(1, q.spread)
            weighted += w * level
            total_w += w
        if total_w == 0:
            return update_ema(store["emas"], "smile_level_default", 0.033, 60)
        level = update_ema(store["emas"], "smile_level", weighted / total_w, 60)
        return max(0.026, min(0.037, level))

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
    ) -> None:
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        spread = q.spread

        moneyness = (strike - S) / 100.0
        fair_iv = max(IV_LO, min(IV_HI, smile_lv + smile_offset(moneyness)))
        fair = bs_call(S, strike, TTE, fair_iv)
        if v_active:
            fair += 0.8 * bs_delta(S, strike, TTE, fair_iv)
        fair -= (0.07 if strike <= 5200 else 0.05) * position

        take_edge = 1.5 if spread >= 2 else 1.0
        quote_edge = 0.8 if spread >= 4 else 0.5
        size = {5100: 5, 5200: 6}[strike]

        if q.ask <= fair - take_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size * 2))
        if q.bid >= fair + take_edge:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, size * 2))

        if spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if target_bid < target_ask:
                self.buy(state, product, orders, target_bid, size)
                self.sell(state, product, orders, target_ask, size)

    # ---- VEV_5300: extrinsic + filtered vev_dir tilt (bygpt_overfit) ----
    def trade_5300(self, state, result, S, vev_dir):
        product, strike = "VEV_5300", 5300
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0) + 47.0
        fair -= 0.06 * position
        if vev_dir != 0:
            fair += 2.0 * vev_dir

        if q.ask < fair - 2.0:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 10))
        if q.bid > fair + 2.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 10))

        if q.spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - 1.0)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.0)))
            if target_bid < target_ask:
                self.buy(state, product, orders, target_bid, 5)
                self.sell(state, product, orders, target_ask, 5)

    # ---- VEV_5400: extrinsic = 16 OTM MM (trader_strats, no take-size cap) ----
    def trade_5400(self, state, result, S):
        product, strike = "VEV_5400", 5400
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0) + 16.0
        fair -= 0.05 * position

        # Take entire available top-of-book volume (matching trader_strats).
        if q.ask < fair - 2.0:
            self.buy(state, product, orders, q.ask, -q.ask_vol)
        if q.bid > fair + 2.0:
            self.sell(state, product, orders, q.bid, q.bid_vol)

        if q.spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - 1.5)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if target_bid < target_ask:
                self.buy(state, product, orders, target_bid, 5)
                self.sell(state, product, orders, target_ask, 5)

    # ---- VEV_5500: wing scalp around extrinsic anchor (trader_combined) ----
    def trade_5500(self, state, store, result, S):
        product, strike = "VEV_5500", 5500
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        anchor = 6.0
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 120)
        ext_blend = max(anchor - 4.0, min(anchor + 4.0, 0.4 * ext_ema + 0.6 * anchor))
        fair = intrinsic + ext_blend

        if q.ask <= fair - 3.0:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 4))
        if q.bid >= fair + 3.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 4))

        if position > 0:
            target_ask = int(max(q.ask, math.ceil(fair + 1.0)))
            if target_ask > q.bid:
                self.sell(state, product, orders, target_ask, min(position, 4))
        elif position < 0:
            target_bid = int(min(q.bid, math.floor(fair - 1.0)))
            if 0 < target_bid < q.ask:
                self.buy(state, product, orders, target_bid, min(-position, 4))

    # ---- main ----
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        store = self.load_state(state.traderData)
        v_active, vev_dir = self.update_signals(state, store)

        self.trade_hydro(state, store, result)
        self.trade_velvet(state, store, result, v_active)

        # The flash-arb / smile-surface strategies were tuned to raw mid;
        # only the VELVET MM benefits from wall_mid (already used inside
        # trade_velvet). Use raw mid for everything downstream.
        velvet_q = quote_from(state.order_depths.get(VELVET))
        S = velvet_q.mid
        if S is None:
            result = {p: o for p, o in result.items() if o}
            return result, 0, jsonpickle.encode(store)

        # Surface shift across deep-ITM strikes (flash-bug detector).
        shifts = []
        for p, k in (("VEV_4000", 4000), ("VEV_4500", 4500)):
            opt_mid = quote_from(state.order_depths.get(p)).mid
            if opt_mid is not None:
                shifts.append(opt_mid - max(S - k, 0.0))
        surface_shift = sum(shifts) / len(shifts) if shifts else None

        wing_signal = any(
            state.market_trades.get(p)
            for p in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
        )

        self.trade_itm_flash(state, result, "VEV_4000", 4000, S, surface_shift, wing_signal)
        self.trade_itm_flash(state, result, "VEV_4500", 4500, S, surface_shift, wing_signal)

        smile_lv = self.smile_level(state, store, S)
        self.trade_5000(state, store, result, S)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5100", 5100, S, v_active)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5200", 5200, S, v_active)

        self.trade_5300(state, result, S, vev_dir)
        self.trade_5400(state, result, S)
        self.trade_5500(state, store, result, S)
        # VEV_6000, VEV_6500 deliberately untouched.

        result = {p: o for p, o in result.items() if o}
        return result, 0, jsonpickle.encode(store)
