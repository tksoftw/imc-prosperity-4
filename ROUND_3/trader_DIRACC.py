"""ROUND_3: trader_DIRACC — direction-aware VELVET accumulator.

This keeps the robust mechanisms from trader_ultimate, but uses priors
that come from `data/ROUND_3` instead of selecting every constant from a
single real submission log. Submission logs are smoke checks, not main
calibration fuel. Big VELVET tape is copied with inferred direction.
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
    "VEV_4000": 300,
    "VEV_4500": 300,
    "VEV_5000": 300,
    "VEV_5100": 300,
    "VEV_5200": 300,
    "VEV_5300": 300,
    "VEV_5400": 300,
    "VEV_5500": 300,
    "VEV_6000": 300,
    "VEV_6500": 300,
}


# ── Trader ──────────────────────────────────────────────────────────────────


class Trader:
    # ---- state ----
    def load_state(self, td: str) -> Dict:
        default = {
            "emas": {},
            "v_signal_til": -1,
            "v_signal_dir": 0,
            "vev_dir": 0,
            "vev_dir_til": -1,
            "basket_til": -1,
            "v4_dir": 0,
            "v4_dir_til": -1,
            "prev_S": None,
            "prev_mids": {},
        }
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
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        """Detect:
          (a) VELVET Accumulator: any market trade with quantity >= 9.
          (b) vev_dir: directional score on VEV_5200/5300 trades, but only
              when BOTH spreads are <= 2 (the alpha-tip filter).
        Both signals decay over time via a TTL stored in `store`.
        """
        vq = quote_from(state.order_depths.get(VELVET))
        v_score = 0
        for tr in state.market_trades.get(VELVET, []):
            n = abs(int(tr.quantity))
            if n < 9:
                continue
            px = int(tr.price)
            if vq.ask is not None and px >= vq.ask:
                v_score += n
            elif vq.bid is not None and px <= vq.bid:
                v_score -= n
            elif vq.mid is not None and px > vq.mid:
                v_score += n
            elif vq.mid is not None and px < vq.mid:
                v_score -= n
        if v_score != 0:
            store["v_signal_til"] = state.timestamp + 1500
            store["v_signal_dir"] = 1 if v_score > 0 else -1

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

        basket_hits = sum(
            1
            for prod in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
            if state.market_trades.get(prod)
        )
        zero_tail_hits = sum(
            1
            for prod in ("VEV_6000", "VEV_6500")
            for tr in state.market_trades.get(prod, [])
            if int(tr.price) == 0
        )
        if basket_hits >= 2:
            store["basket_til"] = state.timestamp + (500 if zero_tail_hits >= 2 else 300)

        q4000 = quote_from(state.order_depths.get("VEV_4000"))
        v4_score = 0
        if q4000.mid is not None:
            for tr in state.market_trades.get("VEV_4000", []):
                px = int(tr.price)
                n = abs(int(tr.quantity))
                if q4000.ask is not None and px >= q4000.ask:
                    v4_score += n
                elif q4000.bid is not None and px <= q4000.bid:
                    v4_score -= n
                elif px > q4000.mid:
                    v4_score += n
                elif px < q4000.mid:
                    v4_score -= n
        if v4_score:
            store["v4_dir"] = 1 if v4_score > 0 else -1
            store["v4_dir_til"] = state.timestamp + 1800

        v_active = state.timestamp <= int(store.get("v_signal_til", -1))
        if not v_active:
            store["v_signal_dir"] = 0
        vev_dir = (
            int(store.get("vev_dir", 0))
            if state.timestamp <= int(store.get("vev_dir_til", -1))
            else 0
        )
        basket_active = state.timestamp <= int(store.get("basket_til", -1))
        v4_dir = (
            int(store.get("v4_dir", 0))
            if state.timestamp <= int(store.get("v4_dir_til", -1))
            else 0
        )
        return v_active, vev_dir, basket_active, v4_dir

    def implied_spot_tilt(self, state: TradingState, store: Dict, velvet_mid: float) -> float:
        weighted = total_w = 0.0
        for strike in (5100, 5200):
            opt_q = quote_from(state.order_depths.get(f"VEV_{strike}"))
            if opt_q.mid is None or opt_q.spread is None or opt_q.spread > 3:
                continue
            iv_obs = implied_vol(velvet_mid, strike, TTE, opt_q.mid)
            iv_smooth = update_ema(store["emas"], f"iv_for_implied_{strike}", iv_obs, 60)

            lo, hi = velvet_mid - 50.0, velvet_mid + 50.0
            for _ in range(20):
                mid = 0.5 * (lo + hi)
                if bs_call(mid, strike, TTE, iv_smooth) > opt_q.mid:
                    hi = mid
                else:
                    lo = mid
            implied_s = 0.5 * (lo + hi)
            weight = 1.0 / max(1, opt_q.spread)
            weighted += weight * (implied_s - velvet_mid)
            total_w += weight

        if total_w == 0:
            return 0.0
        raw = weighted / total_w
        smooth = update_ema(store["emas"], "implied_spot_dev", raw, 60)
        return max(-0.5, min(0.5, 0.4 * (raw - smooth)))

    # ---- HYDROGEL_PACK: historical-fair adaptive mean reversion ----
    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        q = quote_from(state.order_depths.get(HYDRO))
        wall = q.wall_mid
        if q.bid is None or q.ask is None or wall is None:
            return
        orders = result.setdefault(HYDRO, [])
        position = self.position(state, HYDRO)

        # Historical center across data/ROUND_3 is ~9991. The submission
        # logs sat a bit lower, but 9988 worsened public total and min-day,
        # so this general variant gives historical data the main vote.
        fast = update_ema(store["emas"], f"{HYDRO}_fast", wall, 30)
        slow = update_ema(store["emas"], f"{HYDRO}_slow", wall, 120)
        fair = 0.20 * wall + 0.25 * fast + 0.40 * slow + 0.15 * 9991.0
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
        implied_tilt: float,
        v4_dir: int,
    ) -> None:
        q = quote_from(state.order_depths.get(VELVET))
        wall = q.wall_mid
        if wall is None:
            return
        orders = result.setdefault(VELVET, [])
        position = self.position(state, VELVET)
        fast = update_ema(store["emas"], f"{VELVET}_fast", wall, 15)

        # 5255 is not from a single submission log: it is the day-2
        # historical center and wins all public days in rank.
        fair = 0.7 * fast + 0.3 * 5255.0
        fair -= 0.005 * position
        if v_active:
            fair += 1.5 * int(store.get("v_signal_dir", 0))
        fair += implied_tilt
        fair += 1.0 * v4_dir

        if v_active and q.ask is not None and q.ask <= fair:
            self.buy(state, VELVET, orders, q.ask, min(-q.ask_vol, 25))

        # Saturated MM (no cross-book guard — matches the winner)
        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))
        self.buy(state, VELVET, orders, target_bid, POS_LIMITS[VELVET])
        self.sell(state, VELVET, orders, target_ask, POS_LIMITS[VELVET])

    def velvet_spot_edge(
        self,
        state: TradingState,
        store: Dict,
        S: float,
        v_active: bool,
        implied_tilt: float,
        v4_dir: int,
    ) -> float:
        """The same underlying fair used by VELVET, exposed to ATM options."""
        fast = float(store["emas"].get(f"{VELVET}_fast", S))
        fair = 0.7 * fast + 0.3 * 5255.0
        fair -= 0.005 * self.position(state, VELVET)
        if v_active:
            fair += 1.5 * int(store.get("v_signal_dir", 0))
        fair += implied_tilt
        fair += 1.0 * v4_dir
        edge = fair - S
        return max(-5.0, min(5.0, edge))

    def option_micro_reversion(
        self,
        store: Dict,
        product: str,
        q: Quote,
        S: float,
        delta: float,
        strength: float,
    ) -> float:
        """Fade one-tick option quote jumps after removing delta*S movement."""
        prev_s = store.get("prev_S")
        prev_mid = store.get("prev_mids", {}).get(product)
        if prev_s is None or prev_mid is None or q.mid is None:
            return 0.0
        residual_move = (q.mid - float(prev_mid)) - delta * (S - float(prev_s))
        if abs(residual_move) < 0.75:
            return 0.0
        return max(-2.0, min(2.0, -strength * residual_move))

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
        spike_bid = flash_up and q.bid >= fair + 1.0

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

        if spike_bid:
            self.sell(state, product, orders, q.bid, q.bid_vol)

        if position > 0:
            if not spike_bid and (q.bid >= fair + 1 or (flash_up and q.bid >= fair)):
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

    # ---- VEV_5000 / 5100 / 5200: spot-anchored ATM option strategies ----
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

    def trade_5000(self, state, store, result, S, spot_edge: float):
        product, strike, anchor = "VEV_5000", 5000, 13.0
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend + 0.9 * spot_edge - 0.025 * position
        fair += self.option_micro_reversion(store, product, q, S, 0.9, 1.2)

        if q.ask <= fair - 2.5:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 12))
        if q.bid >= fair + 2.5:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 12))

        if q.spread >= 3:
            target_bid = int(min(q.bid + 1, math.floor(fair - 1.5)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if target_bid < target_ask:
                if target_bid > 0 and spot_edge >= -1.5:
                    self.buy(state, product, orders, target_bid, 8)
                if spot_edge <= 1.5:
                    self.sell(state, product, orders, target_ask, 8)

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
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        spread = q.spread

        moneyness = (strike - S) / 100.0
        fair_iv = max(IV_LO, min(IV_HI, smile_lv + smile_offset(moneyness)))
        delta = bs_delta(S, strike, TTE, fair_iv)
        fair = bs_call(S, strike, TTE, fair_iv)
        spot_weight = 1.0 if strike == 5100 else 0.25
        fair += spot_weight * delta * spot_edge
        fair += self.option_micro_reversion(store, product, q, S, delta, 0.75)
        fair -= (0.03 if strike <= 5100 else 0.025) * position

        if strike == 5100:
            take_edge = 1.5 if spread >= 2 else 1.0
            quote_edge = 0.8 if spread >= 4 else 0.5
            size = 6
        else:
            take_edge = 1.2 if spread >= 2 else 0.8
            quote_edge = 0.7 if spread >= 4 else 0.5
            size = 6

        if q.ask <= fair - take_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size * 2))
        if q.bid >= fair + take_edge:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, size * 2))

        if spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if target_bid < target_ask:
                if spot_edge >= (-1.25 if strike == 5100 else -2.5):
                    self.buy(state, product, orders, target_bid, size)
                if spot_edge <= (1.25 if strike == 5100 else 2.5):
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
        fair -= 0.015 * position
        if vev_dir != 0:
            fair += 2.0 * vev_dir

        if q.ask < fair - 2.0:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 20))
        if q.bid > fair + 2.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 20))

        if q.spread >= 2:
            target_bid = int(min(q.bid + 1, math.floor(fair - 1.0)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 1.0)))
            if target_bid < target_ask:
                self.buy(state, product, orders, target_bid, 5)
                self.sell(state, product, orders, target_ask, 5)

    # ---- VEV_5400: extrinsic = 16 OTM MM (trader_strats, no take-size cap) ----
    def trade_5400(self, state, store, result, S):
        product, strike = "VEV_5400", 5400
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0) + 16.0
        fair += self.option_micro_reversion(store, product, q, S, 0.20, 0.60)
        fair -= 0.02 * position

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

    # ---- VEV_5500: basket-seller absorber plus fair-value unwind ----
    def trade_5500(self, state, store, result, S, basket_active: bool):
        product, strike = "VEV_5500", 5500
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        anchor = 6.5
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 120)
        ext_blend = max(anchor - 3.5, min(anchor + 3.5, 0.35 * ext_ema + 0.65 * anchor))
        fair = intrinsic + ext_blend
        fair += self.option_micro_reversion(store, product, q, S, 0.08, 0.50)

        if basket_active and q.ask <= fair - 0.5:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 8))
        elif q.ask <= fair - 2.5:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 5))
        if q.bid >= fair + 2.5:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 5))

        if q.spread is not None and q.spread <= 2 and q.bid <= fair - 0.5:
            size = 7 if basket_active else 3
            target_bid = q.bid
            if q.spread >= 2 and q.bid + 1 < q.ask:
                target_bid = q.bid + 1
            self.buy(state, product, orders, target_bid, size)

        if position > 0:
            target_ask = int(max(q.ask, math.ceil(fair + 1.0)))
            if target_ask > q.bid:
                self.sell(state, product, orders, target_ask, min(position, 5))
        elif position < 0:
            target_bid = int(min(q.bid, math.floor(fair - 1.0)))
            if 0 < target_bid < q.ask:
                self.buy(state, product, orders, target_bid, min(-position, 5))

    # ---- VEV_6000 / VEV_6500: absorb zero-priced basket legs ----
    def trade_zero_tail(self, state, result, product, basket_active: bool):
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        if q.bid == 0:
            size = POS_LIMITS[product] if basket_active else 40
            self.buy(state, product, orders, 0, size)

        if position > 0 and q.ask >= 1:
            self.sell(state, product, orders, 1, min(position, 20))

    # ---- main ----
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        store = self.load_state(state.traderData)
        v_active, vev_dir, basket_active, v4_dir = self.update_signals(state, store)

        velvet_q = quote_from(state.order_depths.get(VELVET))
        S = velvet_q.mid
        implied_tilt = self.implied_spot_tilt(state, store, S) if S is not None else 0.0

        self.trade_hydro(state, store, result)
        self.trade_velvet(state, store, result, v_active, implied_tilt, v4_dir)

        # The flash-arb / smile-surface strategies were tuned to raw mid;
        # only the VELVET MM benefits from wall_mid (already used inside
        # trade_velvet). Use raw mid for everything downstream.
        if S is None:
            result = {p: o for p, o in result.items() if o}
            return result, 0, jsonpickle.encode(store)

        spot_edge = self.velvet_spot_edge(state, store, S, v_active, implied_tilt, v4_dir)

        # Surface shift across deep-ITM strikes (flash-bug detector).
        shifts = []
        for p, k in (("VEV_4000", 4000), ("VEV_4500", 4500)):
            opt_mid = quote_from(state.order_depths.get(p)).mid
            if opt_mid is not None:
                shifts.append(opt_mid - max(S - k, 0.0))
        surface_shift = sum(shifts) / len(shifts) if shifts else None

        wing_signal = basket_active or any(
            state.market_trades.get(p)
            for p in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
        )

        self.trade_itm_flash(state, result, "VEV_4000", 4000, S, surface_shift, wing_signal)
        self.trade_itm_flash(state, result, "VEV_4500", 4500, S, surface_shift, wing_signal)

        smile_lv = self.smile_level(state, store, S)
        self.trade_5000(state, store, result, S, spot_edge)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5100", 5100, S, v_active, spot_edge)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5200", 5200, S, v_active, spot_edge)

        self.trade_5300(state, result, S, vev_dir)
        self.trade_5400(state, store, result, S)
        self.trade_5500(state, store, result, S, basket_active)
        self.trade_zero_tail(state, result, "VEV_6000", basket_active)
        self.trade_zero_tail(state, result, "VEV_6500", basket_active)

        store["prev_S"] = S
        prev_mids = store.setdefault("prev_mids", {})
        for product in (
            "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
            "VEV_5300", "VEV_5400", "VEV_5500",
        ):
            mid = quote_from(state.order_depths.get(product)).mid
            if mid is not None:
                prev_mids[product] = mid

        result = {p: o for p, o in result.items() if o}
        return result, 0, jsonpickle.encode(store)
