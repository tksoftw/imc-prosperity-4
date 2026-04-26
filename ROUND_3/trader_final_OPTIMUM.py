import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

import jsonpickle

from datamodel import Order, TradingState


# ── Black-Scholes (statistics.NormalDist, no scipy needed) ──────────────────

NORMAL = NormalDist()
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


# ── Smile shape (raw mid calibrated, kept identical to trader_final) ────────

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

# Actual game limits: HYDRO/VELVET = 200, every VEV voucher = 300.
# We use soft caps on individual options to avoid sitting on too much
# directional gamma; the deep-ITM and ATM strikes are bumped up to
# match where most of the working PnL came from in trader_final.
POS_LIMITS = {
    VELVET: 200,
    HYDRO: 200,
    "VEV_4000": 200,
    "VEV_4500": 150,
    "VEV_5000": 100,
    "VEV_5100": 80,
    "VEV_5200": 80,
    "VEV_5300": 100,
    "VEV_5400": 80,
    "VEV_5500": 60,
    "VEV_6000": 50,
    "VEV_6500": 50,
}

# Empirical extrinsics from notebooks/round3/conclusions.ipynb (avg D0).
# Used for ATM and wing strategies. These differ materially from the
# placeholder anchors in trader_combined (which had 5100=8 → -$3K bleed).
EXT_ANCHOR = {
    5000: 7.0,
    5100: 18.0,
    5200: 50.0,
    5300: 47.0,
    5400: 16.0,
    5500: 8.0,
    6000: 0.5,
    6500: 0.5,
}


# ── Trader ──────────────────────────────────────────────────────────────────


class Trader:
    # ---- state ----
    def load_state(self, td: str) -> Dict:
        default = {
            "emas": {},
            "v_signal_til": -1,
            "vev_dir": 0,
            "vev_dir_til": -1,
            "wing_dump_til": -1,
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

    # ---- order helpers ----
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
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool]:
        # Accumulator (insider) signal — ≥9 lot VELVET prints.
        for tr in state.market_trades.get(VELVET, []):
            if abs(int(tr.quantity)) >= 9:
                store["v_signal_til"] = state.timestamp + 1500
                break

        # Wing-seller basket detector: any market trade across the OTM
        # wings within the same tick. Used by trade_wing_dump and the
        # 4000/4500 flash sizing.
        wing_trades_now = any(
            state.market_trades.get(p)
            for p in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
        )
        if wing_trades_now:
            store["wing_dump_til"] = state.timestamp + 200

        # vev_dir: tightness-gated directional score on 5200/5300 trades.
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
        wing_dump = state.timestamp <= int(store.get("wing_dump_til", -1))
        return v_active, vev_dir, wing_dump

    # ---- VELVET-vs-ATM implied-spot tilt ----
    def implied_spot_tilt(
        self,
        state: TradingState,
        store: Dict,
        velvet_mid: float,
    ) -> float:
        """Return a tilt added to VELVET fair when option-side mids
        disagree with VELVET. Uses BS to invert option mids into an
        implied spot, then debiases against an EMA of the implied-spot
        deviation (so persistent biases — e.g. a stale extrinsic
        anchor — wash out).

        Uses VEV_5100 / VEV_5200 only: 5000 has too wide a spread to
        be informative, 5300+ is too low-delta. Both strikes have the
        steepest delta among quoted options so a 1-tick option move =
        ~2 ticks of implied spot move, which leads VELVET by a tick
        on micro-flow events.

        Tilt is clamped to ±0.5 — VELVET MM size means even a
        small bias produces meaningful exposure, and over-trusting
        a noisy signal would override the saturated MM.
        """
        # Pick the tighter of the two; weight by 1/spread.
        weighted = total_w = 0.0
        for strike in (5100, 5200):
            opt_q = quote_from(state.order_depths.get(f"VEV_{strike}"))
            if opt_q.mid is None or opt_q.spread is None or opt_q.spread > 3:
                continue
            iv_obs = implied_vol(velvet_mid, strike, TTE, opt_q.mid)
            iv_smooth = update_ema(
                store["emas"], f"iv_for_implied_{strike}", iv_obs, 60
            )
            # Implied spot from inverting BS on a smoothed IV — solve
            # bs_call(S_imp, K, T, iv) = opt_mid via a tight bisection.
            lo, hi = velvet_mid - 50, velvet_mid + 50
            for _ in range(20):
                mid = 0.5 * (lo + hi)
                if bs_call(mid, strike, TTE, iv_smooth) > opt_q.mid:
                    hi = mid
                else:
                    lo = mid
            implied_s = 0.5 * (lo + hi)
            w = 1.0 / max(1, opt_q.spread)
            weighted += w * (implied_s - velvet_mid)
            total_w += w

        if total_w == 0:
            return 0.0
        raw = weighted / total_w
        smooth = update_ema(store["emas"], "implied_spot_dev", raw, 60)
        # Subtract the EMA-baseline so a *change* in implied disagreement
        # is what we trade, not a persistent miscalibration.
        signal = raw - smooth
        return max(-0.5, min(0.5, 0.4 * signal))

    # ---- HYDROGEL_PACK (claude_carry_2 fair, trader_final execution) ----
    # claude_carry_2 corrects anchor from 9991 → 9980 (closer to the
    # observed regime mean) and bumps position skew to 0.06. We keep
    # those changes but use trader_final's wall_mid input + tighter
    # quote_edge=4 + larger take_size=35 because those execution knobs
    # outperform claude_carry_2's mid/edge=5/take=28 on this dataset.
    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        depth = state.order_depths.get(HYDRO)
        if depth is None:
            return
        q = quote_from(depth)
        wall = q.wall_mid
        if q.bid is None or q.ask is None or wall is None:
            return
        orders = result.setdefault(HYDRO, [])
        position = self.position(state, HYDRO)

        ANCHOR = 9980.0
        FAST_W, SLOW_W = 30, 120
        TAKE_EDGE = 7.0
        QUOTE_EDGE = 4.0
        QUOTE_SIZE = 18
        MIN_SPREAD = 10
        SKEW = 0.05  # softer than claude_carry_2's 0.06; matches trader_final

        fast = update_ema(store["emas"], f"{HYDRO}_fast", wall, FAST_W)
        slow = update_ema(store["emas"], f"{HYDRO}_slow", wall, SLOW_W)
        fair = 0.20 * wall + 0.25 * fast + 0.40 * slow + 0.15 * ANCHOR
        fair -= SKEW * position

        if q.ask <= fair - TAKE_EDGE:
            self.buy(state, HYDRO, orders, q.ask, min(-q.ask_vol, 35))
        if q.bid >= fair + TAKE_EDGE:
            self.sell(state, HYDRO, orders, q.bid, min(q.bid_vol, 35))

        if q.spread >= MIN_SPREAD:
            tb = int(min(q.bid + 1, math.floor(fair - QUOTE_EDGE)))
            ta = int(max(q.ask - 1, math.ceil(fair + QUOTE_EDGE)))
            if tb < ta:
                self.buy(state, HYDRO, orders, tb, QUOTE_SIZE)
                self.sell(state, HYDRO, orders, ta, QUOTE_SIZE)

    # ---- VELVETFRUIT_EXTRACT (trader_final + implied-spot tilt) ----
    def trade_velvet(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        v_active: bool,
        implied_tilt: float,
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
        # Bias by the option-implied spot drift.
        fair += implied_tilt

        if v_active and q.ask is not None and q.ask <= fair:
            self.buy(state, VELVET, orders, q.ask, min(-q.ask_vol, 25))

        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))
        self.buy(state, VELVET, orders, target_bid, POS_LIMITS[VELVET])
        self.sell(state, VELVET, orders, target_ask, POS_LIMITS[VELVET])

    # ---- VEV_4000 / VEV_4500: flash-arb deep ITM (unchanged) ----
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

    # ---- VEV_5000: trader_combined ATM extrinsic-anchor MM ----
    # trader_final uses anchor=13 here and made $1718. The notebook says
    # avg D0 extrinsic is 6.75; the trader_final value of 13 is what
    # actually scored — the EMA blend is letting it adapt either way.
    # Keep trader_final's params verbatim — they're the highest-PnL
    # config across the rank table for this strike.
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
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend - 0.05 * position

        if q.ask <= fair - 3.0:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 8))
        if q.bid >= fair + 3.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 8))

        if q.spread >= 3:
            tb = int(min(q.bid + 1, math.floor(fair - 1.5)))
            ta = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if tb < ta:
                if tb > 0:
                    self.buy(state, product, orders, tb, 5)
                self.sell(state, product, orders, ta, 5)

    # ---- VEV_5100 / 5200: BS-smile MM (looser than trader_final) ----
    # trader_final's smile MM only fired ~17/32 trades because take_edge
    # was 1.5 on a 3-tick book. The fix isn't a different model — BS+smile
    # already correctly tracks moneyness. We just need to actually quote.
    # Lower take_edge to 0.8 / quote_edge to 0.5 and double size. Keep
    # the spread-weighted smile_lv from trader_final (works as designed).
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
        # Inventory skew — softer than ext-anchor strategy. Empirically
        # 0.06 / 0.05 work the best (mirrors trader_final with a tiny
        # damping; bigger values cause over-rotation with bigger size).
        fair -= (0.06 if strike <= 5100 else 0.05) * position

        # 5100 sits 1 strike ITM with a thin ~3-tick book — too aggressive
        # an edge gets us bagholding in regime shifts. 5200 has a wider
        # book and benefits from looser take. Per-strike tuning beats
        # uniform: trader_final used 1.5 / 0.8 uniformly and barely
        # traded 5100. We hold 5100 at trader_final's edges and ease 5200.
        if strike == 5100:
            take_edge = 1.5 if spread >= 2 else 1.0
            quote_edge = 0.8 if spread >= 4 else 0.5
            size = 6
        else:
            take_edge = 1.0 if spread >= 2 else 0.7
            quote_edge = 0.5 if spread >= 4 else 0.4
            size = 8

        if q.ask <= fair - take_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size * 2))
        if q.bid >= fair + take_edge:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, size * 2))

        if spread >= 2:
            tb = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            ta = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if tb < ta:
                self.buy(state, product, orders, tb, size)
                self.sell(state, product, orders, ta, size)

    # ---- Smile level computation (unchanged) ----
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

    # ---- VEV_5300 (unchanged) ----
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
            tb = int(min(q.bid + 1, math.floor(fair - 1.0)))
            ta = int(max(q.ask - 1, math.ceil(fair + 1.0)))
            if tb < ta:
                self.buy(state, product, orders, tb, 5)
                self.sell(state, product, orders, ta, 5)

    # ---- VEV_5400 (unchanged) ----
    def trade_5400(self, state, result, S):
        product, strike = "VEV_5400", 5400
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0) + 16.0
        fair -= 0.05 * position

        if q.ask < fair - 2.0:
            self.buy(state, product, orders, q.ask, -q.ask_vol)
        if q.bid > fair + 2.0:
            self.sell(state, product, orders, q.bid, q.bid_vol)

        if q.spread >= 2:
            tb = int(min(q.bid + 1, math.floor(fair - 1.5)))
            ta = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if tb < ta:
                self.buy(state, product, orders, tb, 5)
                self.sell(state, product, orders, ta, 5)

    # ---- VEV_5500: corrected wing scalp ----
    def trade_5500(self, state, store, result, S):
        product, strike = "VEV_5500", 5500
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        anchor = EXT_ANCHOR[strike]  # 8.0
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 100)
        ext_blend = max(anchor - 3.0, min(anchor + 3.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend - 0.05 * position

        TAKE_EDGE = 2.0
        SIZE = 6

        if q.ask <= fair - TAKE_EDGE:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, SIZE))
        if q.bid >= fair + TAKE_EDGE:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, SIZE))

        if q.spread is not None and q.spread >= 2:
            tb = int(min(q.bid + 1, math.floor(fair - 1.0)))
            ta = int(max(q.ask - 1, math.ceil(fair + 1.0)))
            if tb < ta and tb > 0:
                self.buy(state, product, orders, tb, SIZE)
                self.sell(state, product, orders, ta, SIZE)

        # Inventory unwind (kept from trader_final for safety).
        if position > 0:
            ta = int(max(q.ask, math.ceil(fair + 1.0)))
            if ta > q.bid:
                self.sell(state, product, orders, ta, min(position, SIZE))
        elif position < 0:
            tb = int(min(q.bid, math.floor(fair - 1.0)))
            if 0 < tb < q.ask:
                self.buy(state, product, orders, tb, min(-position, SIZE))

    # ---- VEV_6000 / VEV_6500: free-options scoop only ----
    # These strikes are pinned at bid=0 / ask=1 / mid=0.5 forever. The
    # Wing Seller dumps the basket here, hitting the existing 0-bid.
    # We sit on the 0-bid in small size; any fill is a guaranteed
    # ≥0 EV trade because the option is floored at 0. We then offer
    # inventory back at 1 and harvest if anything lifts. We DO NOT
    # lift ask=1 — the rust backtester marks at mid=0.5 so paying 1
    # books an immediate -0.5/lot, and the wing seller almost never
    # buys back which means we'd hold the inventory all day.
    def trade_far_wing(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
    ) -> None:
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        # Offer existing inventory at the ask wall (1). Free EV: cost
        # basis is 0 from the bid scoops below.
        if position > 0:
            self.sell(state, product, orders, max(1, q.ask), min(position, 10))

        # Stack the bid wall at 0 with modest size; FIFO-ish fills come
        # from wing-seller basket dumps that hit bid=0 simultaneously.
        if q.bid == 0:
            self.buy(state, product, orders, 0, 8)

    # ---- main ----
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        store = self.load_state(state.traderData)
        v_active, vev_dir, wing_dump = self.update_signals(state, store)

        # We need VELVET mid before we can compute the ATM tilt.
        velvet_q = quote_from(state.order_depths.get(VELVET))
        S = velvet_q.mid

        if S is not None:
            implied_tilt = self.implied_spot_tilt(state, store, S)
        else:
            implied_tilt = 0.0

        self.trade_hydro(state, store, result)
        self.trade_velvet(state, store, result, v_active, implied_tilt)

        if S is None:
            result = {p: o for p, o in result.items() if o}
            return result, 0, jsonpickle.encode(store)

        # Surface shift (flash-bug detector for deep ITM).
        shifts = []
        for p, k in (("VEV_4000", 4000), ("VEV_4500", 4500)):
            opt_mid = quote_from(state.order_depths.get(p)).mid
            if opt_mid is not None:
                shifts.append(opt_mid - max(S - k, 0.0))
        surface_shift = sum(shifts) / len(shifts) if shifts else None

        # Wing signal also folds in the persistent wing_dump TTL —
        # gives us a longer window after a basket print to size up.
        wing_signal = wing_dump or any(
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

        self.trade_far_wing(state, result, "VEV_6000")
        self.trade_far_wing(state, result, "VEV_6500")

        result = {p: o for p, o in result.items() if o}
        return result, 0, jsonpickle.encode(store)
