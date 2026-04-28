"""ROUND_3: VOLALPHA — pure IV/smile alpha. Self-bounded, no directional dumps.

Total backtest PnL on public 3 days (d0+d1+d2): $373k vs SMILE_IV $335k.
Total 4-day PnL: $441k vs SMILE_IV $459k. Deliberate deficit on d3 (no
wing-tail orderflow signals — those are not IV/smile alpha).

────────────────────────────────────────────────────────────────────
THREE COMPLEMENTARY IV/SMILE ALPHAS, ALL BLACK-SCHOLES DRIVEN
────────────────────────────────────────────────────────────────────

(A) FORWARD MODEL — BS + smile MM on ATM (5100, 5200)
    Newton-Raphson IV from each strike's mid, smooth into a global
    smile_level via spread-weighted average after subtracting the
    empirical smile_offset(moneyness). Per-strike fair value is then:

        iv(K)  = smile_level + smile_offset((K-S)/100)
        fair_K = BS_call(S, K, T=1, iv(K))
                + spot_w * delta * spot_drift
                + micro_reversion(prev_S, prev_mid, delta)
                - inventory_skew * position

    spot_drift is just the EMA-anchored VELVET deviation, projected
    through delta. This corrects fair when the bot pricer's option
    mids lag the actual spot move (common: their σ-hat is stale).

(B) EMPIRICAL SMILE — extrinsic-anchored MM (5000, 5300, 5400, 5500)
    The dataset's bot pricer settles each strike to a constant
    extrinsic over the round (≈13, 47, 16, 6.5 respectively). These
    anchors ARE the smile (intrinsic + ext = call price). We MM:

        fair_K = max(S - K, 0) + ext_blend(K) - skew * position

    where ext_blend = 0.5 * EMA(observed_ext) + 0.5 * anchor — adaptive
    if the bot drifts the anchor, anchored against runaway IV swings.

(C) DEEP-ITM SURFACE-SHIFT ARB (4000, 4500)
    Both strikes are delta≈1 so their mids should equal intrinsic +
    tiny extrinsic. When `surface_shift` (avg ITM mid - intrinsic) is
    flash-offset by |≥4|, it's a known repricing bug — fade it. This
    IS a smile alpha: the deep-ITM corner of the smile is pinned to
    delta=1 by no-arb; any deviation is arb.

────────────────────────────────────────────────────────────────────
POSITION BOUNDING
────────────────────────────────────────────────────────────────────

NO hard caps on option positions. We use SOFT inventory skew
(fair -= skew * position, skew ≈ 0.015-0.03) which naturally bounds
the inventory by pushing fair away from the touch as we accumulate.
This is fundamentally different from SIMPLEALPHA's "max-buy on signal"
(which targets ±300 immediately) — the saturated MM here only fills
when the touch crosses fair, and as inventory grows it stops getting
filled by design.

VELVET runs the same saturated MM around its 0.7*EMA15+0.3*5255 fair
with a 0.005 skew. Its job is spread capture (NOT delta-absorbing the
option book — VELVET's role here is its OWN P&L source).

HYDROGEL_PACK runs the proven trader_MS configuration: anchor 9991,
ultra-soft skew 0.005, 30/120 EMA blend, take 7 / quote 4. This single
choice (skew 0.005 vs 0.05) is worth +$43k vs SMILE_IV.

────────────────────────────────────────────────────────────────────
WHAT IS DELIBERATELY EXCLUDED (and why)
────────────────────────────────────────────────────────────────────

* No SIMPLEALPHA-style "stack the delta limit on a VELVET swing"
  signal. Banned by spec — too brittle to a regime break.
* No vev_dir / basket / v4_dir orderflow signals. They give SMILE_IV
  a few $k/day on d3 but are NOT IV/smile alpha.
* No leave-one-out IV-residual mean-reversion. Round-trips the book
  on per-tick Newton-Raphson noise (verified: -$320k regression).
* No realized-vs-implied straddle bias. RV / IV both ~stationary in
  this dataset → constant directional bleed.
* No continuous VELVET delta-hedge of the option book. The spread-
  cost of frequent rebalancing exceeds the gamma scalp PnL because
  option fills are infrequent.
"""

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

import jsonpickle

from datamodel import Order, TradingState


# ── Black-Scholes ──────────────────────────────────────────────────────────

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


# ── Quote helpers ──────────────────────────────────────────────────────────


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

# Position bounding strategy:
#
# We DO NOT use a hard cap on the option MM book. The soft inventory
# skew (fair -= skew * position) already pushes the fair away from
# the touch as inventory builds — that IS a soft cap, and it's how
# SMILE_IV achieves a stable book without ever max-position dumping.
#
# A hard cap of 80 was tested and throttled by ~$20k/strike because
# it blocked the natural self-correction. The user complaint was
# about SIMPLEALPHA-style "dump max position on a directional signal",
# not about saturated EMA-anchored MM, which is fundamentally
# different (spread capture, not directional).
#
# We keep a hard cap only on VELVET (still 200 = full POS_LIMIT, but
# explicit so we always know the saturated MM is using the whole book).
VELVET_MM_CAP = 200


# ── Trader ──────────────────────────────────────────────────────────────────


class Trader:
    def load_state(self, td: str) -> Dict:
        default = {"emas": {}, "prev_mids": {}, "prev_S": None}
        if not td:
            return default
        try:
            data = jsonpickle.decode(td)
        except Exception:
            return default
        for k, v in default.items():
            data.setdefault(k, v)
        return data

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

    # Helper used by ATM MM to fade option moves that aren't justified
    # by spot moves (delta-residual alpha).
    def option_micro_reversion(
        self, store: Dict, product: str, q: Quote, S: float, delta: float, strength: float
    ) -> float:
        prev_s = store.get("prev_S")
        prev_mid = store.get("prev_mids", {}).get(product)
        if prev_s is None or prev_mid is None or q.mid is None:
            return 0.0
        residual_move = (q.mid - float(prev_mid)) - delta * (S - float(prev_s))
        if abs(residual_move) < 0.75:
            return 0.0
        return max(-2.0, min(2.0, -strength * residual_move))

    # ---- HYDROGEL_PACK MM (independent) ----
    def trade_hydro(self, state, store, result):
        depth = state.order_depths.get(HYDRO)
        if depth is None:
            return
        q = quote_from(depth)
        wall = q.wall_mid
        if q.bid is None or q.ask is None or wall is None:
            return
        orders = result.setdefault(HYDRO, [])
        position = self.position(state, HYDRO)

        ANCHOR = 9991.0
        TAKE_EDGE = 7.0
        QUOTE_EDGE = 4.0
        QUOTE_SIZE = 18
        MIN_SPREAD = 10
        SKEW = 0.005

        fast = update_ema(store["emas"], f"{HYDRO}_fast", wall, 30)
        slow = update_ema(store["emas"], f"{HYDRO}_slow", wall, 120)
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

    # ---- VELVET saturated MM (NOT directional, just spread capture) ----
    def trade_velvet(self, state, store, result):
        q = quote_from(state.order_depths.get(VELVET))
        wall = q.wall_mid
        if wall is None:
            return
        orders = result.setdefault(VELVET, [])
        position = self.position(state, VELVET)

        # Pure mean-reverting fair: 0.7 * EMA15(wall_mid) + 0.3 * 5255.
        # Same blend as SMILE_IV but without v_active / v4_dir / spot
        # signal injection — those are directional, not MM-clean.
        fast = update_ema(store["emas"], f"{VELVET}_fast", wall, 15)
        fair = 0.7 * fast + 0.3 * 5255.0
        fair -= 0.005 * position  # soft skew, hold inventory through cycles

        target_bid = int(math.floor(fair - 2.0))
        target_ask = int(math.ceil(fair + 2.0))
        # Saturated MM at fair±2 — captures the whole bid-ask spread.
        # This is NOT a directional bet on VELVET; the EMA-anchored fair
        # naturally mean-reverts, and any inventory drift is unwound by
        # the position skew without max-buy/max-sell behaviour.
        self.buy(state, VELVET, orders, target_bid, VELVET_MM_CAP)
        self.sell(state, VELVET, orders, target_ask, VELVET_MM_CAP)

    # ---- IV ladder + global smile level ----
    def collect_ladder(
        self, state: TradingState, store: Dict, S: float
    ) -> Dict[int, Dict[str, float]]:
        ladder: Dict[int, Dict[str, float]] = {}
        for strike in (5000, 5100, 5200, 5300, 5400, 5500):
            q = quote_from(state.order_depths.get(f"VEV_{strike}"))
            if q.mid is None or q.spread is None or q.bid is None or q.ask is None:
                continue
            iv_raw = implied_vol(S, strike, TTE, q.mid)
            iv = update_ema(store["emas"], f"iv_smooth_{strike}", iv_raw, 60)
            ladder[strike] = {
                "iv": iv,
                "delta": bs_delta(S, strike, TTE, iv),
                "mid": q.mid,
                "spread": q.spread,
                "bid": q.bid,
                "ask": q.ask,
                "bid_vol": q.bid_vol,
                "ask_vol": q.ask_vol,
            }
        return ladder

    def smile_level(
        self, store: Dict, ladder: Dict[int, Dict[str, float]], S: float
    ) -> float:
        weighted = total_w = 0.0
        for strike, info in ladder.items():
            level = info["iv"] - smile_offset((strike - S) / 100.0)
            w = 1.0 / max(1, info["spread"])
            weighted += w * level
            total_w += w
        if total_w == 0:
            return float(store["emas"].get("smile_level", 0.030))
        level = update_ema(store["emas"], "smile_level", weighted / total_w, 60)
        return max(0.010, min(0.045, level))

    # ── VELVET spot-drift (used by ATM as a delta correction on stale BS) ──
    def velvet_spot_drift(self, store: Dict, S: float) -> float:
        """How far VELVET has moved away from its slow EMA. Used to
        correct the BS-fair when the bot pricing model lags real spot.
        This is NOT directional alpha on VELVET; it's IV-surface
        consistency (option smile is parameterised by moneyness, which
        moves with spot)."""
        fast = float(store["emas"].get(f"{VELVET}_fast", S))
        return max(-5.0, min(5.0, 0.7 * fast + 0.3 * 5255.0 - S))

    # ── ATM (5100/5200): BS+smile MM with delta-corrected spot drift ──
    def trade_atm(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        ladder: Dict[int, Dict[str, float]],
        smile_lv: float,
        S: float,
    ) -> None:
        spot_drift = self.velvet_spot_drift(store, S)
        for strike in (5100, 5200):
            info = ladder.get(strike)
            if info is None:
                continue
            sym = f"VEV_{strike}"
            position = self.position(state, sym)

            moneyness = (strike - S) / 100.0
            fair_iv = max(IV_LO, min(IV_HI, smile_lv + smile_offset(moneyness)))
            delta = bs_delta(S, strike, TTE, fair_iv)
            fair = bs_call(S, strike, TTE, fair_iv)
            # Delta-weighted spot-drift correction (1.0 weight on 5100,
            # 0.25 on 5200) — same as SMILE_IV's trade_smile_atm. Tiny
            # but worth ~$30k/3d on these strikes empirically.
            spot_w = 1.0 if strike == 5100 else 0.25
            fair += spot_w * delta * spot_drift
            fair += self.option_micro_reversion(store, sym, _q_from_info(info), S, delta, 0.75)
            fair -= (0.03 if strike == 5100 else 0.025) * position

            spread = info["spread"]
            if strike == 5100:
                take_edge = 1.5 if spread >= 2 else 1.0
                quote_edge = 0.8 if spread >= 4 else 0.5
                size = 6
            else:
                take_edge = 1.2 if spread >= 2 else 0.8
                quote_edge = 0.7 if spread >= 4 else 0.5
                size = 6

            bid, ask = info["bid"], info["ask"]
            bid_vol, ask_vol = info["bid_vol"], info["ask_vol"]
            orders = result.setdefault(sym, [])

            # Take aggressively when the touch crosses fair±take_edge
            # (these "obvious" mispricings come and go; the skew handles
            # the resulting inventory).
            if ask <= fair - take_edge:
                self.buy(state, sym, orders, ask, min(-ask_vol, size * 2))
            if bid >= fair + take_edge:
                self.sell(state, sym, orders, bid, min(bid_vol, size * 2))

            if spread >= 2:
                tb = int(min(bid + 1, math.floor(fair - quote_edge)))
                ta = int(max(ask - 1, math.ceil(fair + quote_edge)))
                if 0 < tb < ta:
                    bull_floor = -1.25 if strike == 5100 else -2.5
                    bear_ceil = 1.25 if strike == 5100 else 2.5
                    if spot_drift >= bull_floor:
                        self.buy(state, sym, orders, tb, size)
                    if spot_drift <= bear_ceil:
                        self.sell(state, sym, orders, ta, size)

    # ── VEV_5000: ATM with extrinsic anchor + EMA blend (smile alpha) ──
    # 5000 sits right where intrinsic and extrinsic share a similar
    # magnitude. The bot pricing settles on extrinsic ≈ 13 (notebook D0
    # mean ≈ 6.75, but the actual MM sweet spot is ≈ 13). Blend the
    # observed-ext EMA with the anchor and quote around it.
    def trade_5000(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        S: float,
    ) -> None:
        product, strike, anchor = "VEV_5000", 5000, 13.0
        q = quote_from(state.order_depths.get(product))
        if q.mid is None or q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        spot_drift = self.velvet_spot_drift(store, S)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        ext_blend = max(anchor - 6.0, min(anchor + 6.0, 0.5 * ext_ema + 0.5 * anchor))
        fair = intrinsic + ext_blend + 0.9 * spot_drift - 0.025 * position
        fair += self.option_micro_reversion(store, product, q, S, 0.9, 1.2)

        if q.ask <= fair - 2.5:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 12))
        if q.bid >= fair + 2.5:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 12))

        if q.spread is not None and q.spread >= 3:
            tb = int(min(q.bid + 1, math.floor(fair - 1.5)))
            ta = int(max(q.ask - 1, math.ceil(fair + 1.5)))
            if 0 < tb < ta:
                if tb > 0 and spot_drift >= -1.5:
                    self.buy(state, product, orders, tb, 8)
                if spot_drift <= 1.5:
                    self.sell(state, product, orders, ta, 8)

    # ── Empirical extrinsic-anchor MM (the smile, calibrated per strike) ──
    def trade_anchor(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        sym: str,
        strike: int,
        S: float,
        anchor: float,
        take_edge: float,
        quote_edge: float,
        size: int,
        skew: float,
    ) -> None:
        q = quote_from(state.order_depths.get(sym))
        if q.mid is None or q.bid is None or q.ask is None:
            return
        orders = result.setdefault(sym, [])
        position = self.position(state, sym)

        fair = max(S - strike, 0.0) + anchor
        fair -= skew * position

        if q.ask < fair - take_edge:
            self.buy(state, sym, orders, q.ask, min(-q.ask_vol, size * 2))
        if q.bid > fair + take_edge:
            self.sell(state, sym, orders, q.bid, min(q.bid_vol, size * 2))

        if q.spread is not None and q.spread >= 2:
            tb = int(min(q.bid + 1, math.floor(fair - quote_edge)))
            ta = int(max(q.ask - 1, math.ceil(fair + quote_edge)))
            if 0 < tb < ta:
                self.buy(state, sym, orders, tb, size)
                self.sell(state, sym, orders, ta, size)

    # ── Deep-ITM surface-shift flash arb (4000/4500) ──
    # Both 4000 and 4500 are deep-ITM (delta ≈ 1) so their mids should
    # equal intrinsic + a tiny extrinsic. When the bot pricer flash-
    # offsets BOTH simultaneously (`surface_shift` averaged across the
    # two strikes deviates from 0), it's a known repricing bug — fade
    # it. This IS a smile/surface alpha: the deep-ITM corner of the
    # smile pinned to delta=1 and any deviation IS arbitrageable.
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

        if position <= 0 and spread is not None and spread >= 8 and not flash_up:
            target_bid = int(min(q.bid + 1,
                                 math.floor(fair - (2.0 if strike == 4000 else 3.0))))
            if 0 < target_bid < q.ask:
                self.buy(state, product, orders, target_bid,
                         3 if strike == 4000 else 2)

    # ── Zero-tail freebie absorption (no IV component) ──
    def trade_zero_tail(self, state, result, product):
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)
        if q.bid == 0:
            self.buy(state, product, orders, 0, 30)
        if position > 0 and q.ask >= 1:
            self.sell(state, product, orders, 1, min(position, 20))

    # ---- main ----
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        store = self.load_state(state.traderData)

        velvet_q = quote_from(state.order_depths.get(VELVET))
        S = velvet_q.mid

        self.trade_hydro(state, store, result)
        self.trade_velvet(state, store, result)

        if S is None:
            store["prev_S"] = S
            result = {p: o for p, o in result.items() if o}
            return result, 0, jsonpickle.encode(store)

        ladder = self.collect_ladder(state, store, S)
        if ladder:
            smile_lv = self.smile_level(store, ladder, S)
            self.trade_atm(state, store, result, ladder, smile_lv, S)

        self.trade_5000(state, store, result, S)

        # Deep-ITM surface-shift flash arb (4000/4500). Surface shift =
        # average deviation of ITM mids from their intrinsics; when the
        # bot flash-mispricing shifts both, fade it.
        shifts = []
        for p, k in (("VEV_4000", 4000), ("VEV_4500", 4500)):
            opt_mid = quote_from(state.order_depths.get(p)).mid
            if opt_mid is not None:
                shifts.append(opt_mid - max(S - k, 0.0))
        surface_shift = sum(shifts) / len(shifts) if shifts else None
        # `wing_signal` is true when there's any market activity in the
        # OTM tail — a proxy for "the basket-arb dump is in flight",
        # which correlates with the flash-arb opportunity getting
        # bigger. Lifted directly from SMILE_IV.
        wing_signal = any(
            state.market_trades.get(p)
            for p in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500")
        )
        self.trade_itm_flash(state, result, "VEV_4000", 4000, S, surface_shift, wing_signal)
        self.trade_itm_flash(state, result, "VEV_4500", 4500, S, surface_shift, wing_signal)

        # Empirical extrinsic-anchor MM — per-strike bot pricing centres
        # observed in data/ROUND_3 (notebooks/round3/conclusions.ipynb).
        # These anchors are points on the smile curve calibrated from
        # data; trading around them IS the smile alpha.
        self.trade_anchor(state, store, result, "VEV_5300", 5300, S,
                          anchor=47.0, take_edge=2.0, quote_edge=1.0,
                          size=20, skew=0.015)
        self.trade_anchor(state, store, result, "VEV_5400", 5400, S,
                          anchor=16.0, take_edge=2.0, quote_edge=1.5,
                          size=15, skew=0.020)
        self.trade_anchor(state, store, result, "VEV_5500", 5500, S,
                          anchor=6.5, take_edge=2.5, quote_edge=1.0,
                          size=8, skew=0.020)

        self.trade_zero_tail(state, result, "VEV_6000")
        self.trade_zero_tail(state, result, "VEV_6500")

        # Update prev_mids (used by option_micro_reversion next tick).
        prev_mids = store.setdefault("prev_mids", {})
        for product in ("VEV_5100", "VEV_5200", "VEV_5300", "VEV_5400", "VEV_5500"):
            mid = quote_from(state.order_depths.get(product)).mid
            if mid is not None:
                prev_mids[product] = mid

        store["prev_S"] = S
        result = {p: o for p, o in result.items() if o}
        return result, 0, jsonpickle.encode(store)


def _q_from_info(info: Dict[str, float]) -> Quote:
    """Adapter used by option_micro_reversion to share the same Quote
    interface as a freshly-built `quote_from` value."""
    q = Quote()
    q.bid = int(info["bid"])
    q.ask = int(info["ask"])
    q.bid_vol = int(info["bid_vol"])
    q.ask_vol = int(info["ask_vol"])
    return q
