# FINAL ROUND 4 TOURNAMENT AGENT
#
# Proven base: 519646 champion candidate.
# Architecture:
#   1) 510544 research-enhanced champion for VELVETFRUIT_EXTRACT + all VEV vouchers.
#   2) Thomas Hydrogel module isolated to HYDROGEL_PACK only.
#   3) Short-TTL counterparty overlays are retained only where they were already
#      part of the champion lineage: Mark67/Mark49 VELVET, Mark22 defensive wing
#      skew, Mark01/Mark14 toxicity controls.
#
# Deliberately NOT included as active production changes:
#   - broad Mark55 directional trading, because Mark55 is a regime marker rather
#     than a standalone direction signal.
#   - Mark38 VEV_4000 farming, because probes did not show it transfers.
#   - Mark22 VEV_6000/VEV_6500 direct farming, because direct fill rates were weak.
#   - mixed/two-sided Thomas Hydrogel overlays, because 519040 showed that mixing
#     them with other Hydrogel logic overtraded and underperformed.
#
# This file is intended as the conservative final candidate: keep the modules
# that transferred in backtests, and do not add untested overlays unless they are
# ablated separately.

# round4_champion_with_thomas_hydro_strategy_COMMENTED.py
#
# PURPOSE
# -------
# This is the clean "champion + Thomas Hydrogel" hybrid.
#
# The ONLY Thomas module intentionally used in final execution is the last
# Trader subclass at the bottom of this file, which overrides HYDROGEL_PACK.
# Everything else is inherited from the 510544 champion stack.
#
# WHAT IS NOT IN THOMAS'S VERSION
# -------------------------------
# The following modules come from the champion lineage rather than Thomas:
#
# 1. Wall-mid fair values
#    Uses the highest-volume bid/ask levels as a steadier "wall_mid" estimate,
#    because designated liquidity bots tend to park size there.
#
# 2. VELVETFRUIT_EXTRACT champion module
#    Uses structural mean reversion around the 5255 anchor, saturated quoting,
#    implied spot tilt from the voucher surface, and short-TTL named-flow
#    overlays for Mark67/Mark49/Mark55.
#
# 3. Voucher swing stack
#    Treats VEV_4000..VEV_5500 as a coordinated directional surface position
#    using the reusable VELVET swing edge:
#        edge = 0.7 * EMA15(VELVET_mid) + 0.3 * 5255 - VELVET_mid
#    This was the large source of champion PnL and is not Thomas Hydrogel logic.
#
# 4. Research counterparty overlay
#    Uses names only as short-lived execution/fair-value modifiers:
#      - Mark67 buy / Mark49 sell in VELVET = bullish
#      - Mark55 is a regime marker, not a standalone signal
#      - Mark22 high-strike selling = defensive wing skew
#      - Mark01/Mark14 fills = toxicity/quote-quality controls
#
# 5. OTM long-flip guards
#    Prevents HybridBeast-style overtrading by making long flips in
#    VEV_5300/5400/5500 require stronger evidence and by capping them.
#
# WHAT IS THOMAS'S VERSION
# ------------------------
# The final Trader class overrides only HYDROGEL_PACK with Thomas's hydro module:
#        edge = 0.7 * EMA10(HYDRO_mid) + 0.3 * 9980 - HYDRO_mid
#               + 12 * short-TTL hydro bot signal
#        target = +200 if edge > 14, -200 if edge < -14
#
# This isolation matters: earlier hybrids failed when Thomas Hydrogel was mixed
# with extra VELVET/counterparty modifications. This file keeps the champion
# VELVET/voucher stack intact and swaps HYDRO only.
#
# OVERFITTING CONTROL
# -------------------
# No timestamp rules or final-price assumptions are added here. Comments explain
# strategy provenance; they do not change execution logic.

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
    def bid(self):
        # Compatibility stub; ignored in normal algorithmic rounds.
        return 0

    # ---- state ----
    def load_state(self, td: str) -> Dict:
        default = {
            "emas": {},
            "v_signal_til": -1,
            "vev_dir": 0,
            "vev_dir_til": -1,
            "basket_til": -1,
            "v4_dir": 0,
            "v4_dir_til": -1,
            # Named-counterparty signals.  These are deliberately small TTL
            # fair-value tilts rather than hard-coded counterparty bans.
            "m67_vev_til": -1,        # gated Mark67 VELVET follow
            "m55_fade_dir": 0,         # +1 means fade a Mark55 sell, -1 fade a Mark55 buy
            "m55_fade_til": -1,
            "m49_vev_dir": 0,          # Mark49 VELVET seller/buyer directional cue
            "m49_vev_til": -1,
            "m14_vev_dir": 0,          # Mark14 VELVET confirmation
            "m14_vev_til": -1,
            "m14_opt_dir": 0,          # Mark14 VEV_5100/5300 confirmation
            "m14_opt_til": -1,
            "m38_hydro_dir": 0,        # Mark38 HYDRO informed-flow guard/lean
            "m38_hydro_til": -1,
            # Anti-snowball guards. These are deliberately broad risk controls:
            # after our own trades show repeated adverse fills in low-conviction
            # wings, stop adding for a short TTL instead of averaging down.
            "bad_5000_til": -1,
            "bad_5000_pressure": 0,
            "bad_itm_til": -1,
            "bad_itm_pressure": 0,
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

    # ---- Counterparty behavior helpers ----
    def gate2_active(self, state: TradingState) -> bool:
        """Tight OTM surface gate used to filter named flow.

        Logs showed Mark67's VELVET buys are much cleaner when VEV_5300 is
        tight and VEV_5400 is one tick wide.  Keep this as a binary regime
        filter rather than overfitting timestamps.
        """
        q5300 = quote_from(state.order_depths.get("VEV_5300"))
        q5400 = quote_from(state.order_depths.get("VEV_5400"))
        return (
            q5300.spread is not None and q5300.spread <= 2
            and q5400.spread is not None and q5400.spread <= 1
        )

    @staticmethod
    def active_dir(store: Dict, dir_key: str, til_key: str, ts: int) -> int:
        return int(store.get(dir_key, 0)) if ts <= int(store.get(til_key, -1)) else 0

    def velvet_counterparty_tilt(self, state: TradingState, store: Dict) -> float:
        """Small fair-value tilt from named VELVET behavior.

        Positive means raise VELVET fair; negative means lower it.  The weights
        are intentionally small so this complements the anchor/EMA/surface fair
        instead of turning into a fragile chase model.
        """
        ts = state.timestamp
        tilt = 0.0
        if ts <= int(store.get("m67_vev_til", -1)):
            tilt += 2.25
        tilt += 1.15 * self.active_dir(store, "m55_fade_dir", "m55_fade_til", ts)
        tilt += 0.85 * self.active_dir(store, "m49_vev_dir", "m49_vev_til", ts)
        tilt += 0.55 * self.active_dir(store, "m14_vev_dir", "m14_vev_til", ts)
        return max(-3.0, min(3.0, tilt))

    def update_named_counterparty_signals(self, state: TradingState, store: Dict) -> None:
        """Update TTL signals for the named-bot behaviours found in logs.

        Rules encoded here:
        - Mark67 VELVET buy is followed only under gate2.
        - Mark55 VELVET is faded.
        - Mark49 VELVET sells are bearish.
        - Mark14 VELVET / 5100 / 5300 is a weak confirmation signal.
        - Mark38 HYDRO flow is treated as informed/adverse selection, so we
          lean with it and avoid fighting it.
        """
        ts = state.timestamp
        gate2 = self.gate2_active(state)

        def note_velvet_party(buyer, seller):
            if buyer == "Mark 67" and gate2:
                store["m67_vev_til"] = ts + 1600
            # Mark55 is a noisy crosser: fade his side.
            if buyer == "Mark 55":
                store["m55_fade_dir"] = -1
                store["m55_fade_til"] = ts + 1000
            elif seller == "Mark 55":
                store["m55_fade_dir"] = 1
                store["m55_fade_til"] = ts + 1000
            # Mark49 is a persistent VELVET seller; seller => bearish.
            if seller == "Mark 49":
                store["m49_vev_dir"] = -1
                store["m49_vev_til"] = ts + 1200
            elif buyer == "Mark 49":
                store["m49_vev_dir"] = 1
                store["m49_vev_til"] = ts + 1200
            # Mark14 is confirmation, stronger under tight surface but still small.
            if buyer == "Mark 14":
                store["m14_vev_dir"] = 1
                store["m14_vev_til"] = ts + (1400 if gate2 else 900)
            elif seller == "Mark 14":
                store["m14_vev_dir"] = -1
                store["m14_vev_til"] = ts + (1400 if gate2 else 900)

        def note_hydro_party(buyer, seller):
            if buyer == "Mark 38":
                store["m38_hydro_dir"] = 1
                store["m38_hydro_til"] = ts + 1400
            elif seller == "Mark 38":
                store["m38_hydro_dir"] = -1
                store["m38_hydro_til"] = ts + 1400

        def note_mark14_option(buyer, seller, symbol):
            if symbol not in ("VEV_5100", "VEV_5300"):
                return
            if buyer == "Mark 14":
                store["m14_opt_dir"] = 1
                store["m14_opt_til"] = ts + (1400 if gate2 else 800)
            elif seller == "Mark 14":
                store["m14_opt_dir"] = -1
                store["m14_opt_til"] = ts + (1400 if gate2 else 800)

        # Public bot-vs-bot market trades.
        for tr in state.market_trades.get(VELVET, []):
            note_velvet_party(getattr(tr, "buyer", None), getattr(tr, "seller", None))
        for tr in state.market_trades.get(HYDRO, []):
            note_hydro_party(getattr(tr, "buyer", None), getattr(tr, "seller", None))
        for prod in ("VEV_5100", "VEV_5300"):
            for tr in state.market_trades.get(prod, []):
                note_mark14_option(getattr(tr, "buyer", None), getattr(tr, "seller", None), prod)

        # Our own fills also reveal counterparties and should update the same TTLs.
        for tr in state.own_trades.get(VELVET, []):
            note_velvet_party(getattr(tr, "buyer", None), getattr(tr, "seller", None))
        for tr in state.own_trades.get(HYDRO, []):
            note_hydro_party(getattr(tr, "buyer", None), getattr(tr, "seller", None))
        for prod in ("VEV_5100", "VEV_5300"):
            for tr in state.own_trades.get(prod, []):
                note_mark14_option(getattr(tr, "buyer", None), getattr(tr, "seller", None), prod)

    # ---- Signals ----
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        """Detect:
          (a) VELVET Accumulator: any market trade with quantity >= 9.
          (b) vev_dir: directional score on VEV_5200/5300 trades, but only
              when BOTH spreads are <= 2 (the alpha-tip filter).
        Both signals decay over time via a TTL stored in `store`.
        """
        # 0) Named counterparty TTLs from public trades and our fills.
        self.update_named_counterparty_signals(state, store)

        # 1) Own-fill adverse selection guard.
        # These names are not used as a brittle "never trade with X" rule.
        # Instead, repeated fills from the same informed-side sellers trigger a
        # short cooldown in the weakest modules. This targets inventory
        # snowballing while preserving occasional true-dislocation fills.
        bad_5000_qty = 0
        for tr in state.own_trades.get("VEV_5000", []):
            if getattr(tr, "buyer", None) == "SUBMISSION" and getattr(tr, "seller", None) in {"Mark 14", "Mark 01", "Mark 22"}:
                bad_5000_qty += abs(int(tr.quantity))
        if bad_5000_qty:
            store["bad_5000_pressure"] = min(40, int(store.get("bad_5000_pressure", 0)) + bad_5000_qty)
            if int(store.get("bad_5000_pressure", 0)) >= 12:
                store["bad_5000_til"] = state.timestamp + 3500
        else:
            store["bad_5000_pressure"] = max(0, int(store.get("bad_5000_pressure", 0)) - 2)

        bad_itm_qty = 0
        for prod in ("VEV_4000", "VEV_4500"):
            for tr in state.own_trades.get(prod, []):
                if getattr(tr, "buyer", None) == "SUBMISSION" and getattr(tr, "seller", None) in {"Mark 22", "Mark 38"}:
                    bad_itm_qty += abs(int(tr.quantity))
        if bad_itm_qty:
            store["bad_itm_pressure"] = min(30, int(store.get("bad_itm_pressure", 0)) + bad_itm_qty)
            if int(store.get("bad_itm_pressure", 0)) >= 10:
                store["bad_itm_til"] = state.timestamp + 2500
        else:
            store["bad_itm_pressure"] = max(0, int(store.get("bad_itm_pressure", 0)) - 1)

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

    # ---- HYDROGEL_PACK ----
    # Anchor = 9991. Justified by the data/ROUND_3 historical mean of
    # ~9990 across 3 days (NOT by any single real-submission result).
    # Tested adaptive (slow-EMA) anchor — bled $30K because the anchor
    # is structural mean-reversion, not just a regime drift correction:
    # without it, fair tracks the current mid and our MM stops betting
    # on reversion. Keep the constant; it's data-derived from the 30K
    # tick training corpus.
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

        # ANCHOR = 9980 wins on real deploy ($7,868 ff_OLD/optimum vs
        # $6,434 ff/final_3 with 9991). Backtest grid liked 9991 but real
        # bots trade around 9979-9980; anchor weight 0.15 means a 10-tick
        # error => 1.5 ticks of fair drift, enough to flip our quote
        # competitiveness. Real evidence wins.
        ANCHOR = 9980.0
        fast = update_ema(store["emas"], f"{HYDRO}_fast", wall, 30)
        slow = update_ema(store["emas"], f"{HYDRO}_slow", wall, 120)
        fair = 0.20 * wall + 0.25 * fast + 0.40 * slow + 0.15 * ANCHOR
        fair -= 0.05 * position
        # Mark38 is the clearest HYDRO informed/aggressive flow.  Lean with
        # him for a short TTL; keep the shift modest so structural mean
        # reversion still dominates.
        m38_dir = self.active_dir(store, "m38_hydro_dir", "m38_hydro_til", state.timestamp)
        fair += 4.0 * m38_dir

        TAKE_EDGE = 7.0
        QUOTE_EDGE = 4.0
        QUOTE_SIZE = 18
        # MAX_TAKE: data/ROUND_3 sweep shows the take-edge book never
        # has more than ~15 lots at HYDRO, so any cap >= 15 hits the
        # exact same fills. Keep at 35 as harmless headroom in case
        # live deploy occasionally has deeper fills we want to scoop.
        MAX_TAKE = 35

        if q.ask is not None and q.ask <= fair - TAKE_EDGE:
            self.buy(state, HYDRO, orders, q.ask, min(-q.ask_vol, MAX_TAKE))
        if q.bid is not None and q.bid >= fair + TAKE_EDGE:
            self.sell(state, HYDRO, orders, q.bid, min(q.bid_vol, MAX_TAKE))

        if q.spread is not None and q.spread >= 10:
            target_bid = int(min(q.bid + 1, math.floor(fair - QUOTE_EDGE)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + QUOTE_EDGE)))
            if target_bid < target_ask:
                self.buy(state, HYDRO, orders, target_bid, QUOTE_SIZE)
                self.sell(state, HYDRO, orders, target_ask, QUOTE_SIZE)

    # ---- CHAMPION VELVET MODULE, NOT THOMAS ----
    # Uses wall_mid (more stable than raw mid) as the EMA source, a 5255 anchor,
    # implied-spot tilt, and named-flow overlays. This is separate from Thomas's
    # Hydrogel idea and should remain intact because it drove the champion's
    # VELVET improvement.
    #
    # Note: vev_dir is intentionally NOT used here. It hurts VELVET while helping
    # VEV_5300, so the option-specific version is consumed only in trade_5300.
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
        # 5255 is the historical 3-day mean of VELVET (per data/ROUND_3).
        # This is a structural mean-reversion anchor, not a fit to any
        # single submission. Tested with adaptive slow-EMA replacement
        # which bled $23K — confirms the constant is what makes our
        # saturated MM mean-revert.
        fair = 0.7 * fast + 0.3 * 5255.0
        # Ultra-soft skew. MADSCIENTIST.log proved soft VELVET skew lifts
        # real PnL ($5831 vs $4969 baseline). Bot flow is uninformed so
        # skew was leaking edge.
        fair -= 0.005 * position
        if v_active:
            fair += 1.5
        fair += implied_tilt
        fair += 1.0 * v4_dir
        fair += self.velvet_counterparty_tilt(state, store)

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
        fair = 0.7 * fast + 0.3 * 5255.0  # match trade_velvet's anchor
        fair -= 0.005 * self.position(state, VELVET)  # match trade_velvet
        if v_active:
            fair += 1.5
        fair += implied_tilt
        fair += 1.0 * v4_dir
        fair += self.velvet_counterparty_tilt(state, store)
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
        store: Dict,
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
        # Deep-ITM vouchers are mostly synthetic underlying plus wide spread.
        # They were small repeated losers in two runs, so keep them as flash-arb
        # instruments only. The cooldown prevents adding during repeated
        # adverse-selection fills.
        bad_itm = state.timestamp <= int(store.get("bad_itm_til", -1))
        actual_cheap = q.ask <= fair - (3.25 if bad_itm else 2.75)
        spike_bid = flash_up and q.bid >= fair + 1.0

        MAX_LONG_ITM = 8
        add_room = max(0, MAX_LONG_ITM - position)

        if (not bad_itm or position <= 5) and add_room > 0 and flash_down and q.ask <= fair - (1.0 if bad_itm else 0.0):
            size = 12 if strike == 4000 else 8
            if actual_cheap:
                size += 4
            if wing_signal or spread <= 10:
                size += 3
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size, add_room))

        if (not bad_itm or position <= 5) and add_room > 0 and actual_cheap:
            size = 10 if strike == 4000 else 7
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, size, add_room))

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

        if position < 6 and spread >= 8 and not flash_up and not bad_itm:
            target_bid = int(min(q.bid + 1,
                                 math.floor(fair - (2.5 if strike == 4000 else 3.5))))
            if 0 < target_bid < q.ask:
                self.buy(state, product, orders, target_bid, min(2, max(0, MAX_LONG_ITM - position)))

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
            return update_ema(store["emas"], "smile_level_default", 0.018, 60)
        level = update_ema(store["emas"], "smile_level", weighted / total_w, 60)
        # Real intraday IV (per data_analysis_round_3.ipynb) drifts from
        # ~0.0158 at day-0 start to ~0.020 at day-2 end. Old clamp
        # [0.026, 0.037] was pinning fair to a 30-50% over-priced floor —
        # we sold cheap and bought rich. Open the floor to 0.010 and
        # raise the ceiling to 0.045 so the EMA can actually track.
        return max(0.010, min(0.045, level))

    def trade_5000(self, state, store, result, S, spot_edge: float):
        """Conservative VEV_5000 module.

        Log 497781 showed VEV_5000 was the largest drag:
        + repeated buys around timestamps 25.7k-40.8k at 258-274,
        + final long 196,
        + final PnL around -1.07k.

        So this version keeps the relative-value logic but treats 5000 as a
        low-conviction strike: only add longs when the underlying edge is
        supportive and the option is materially cheap, and use a soft long cap.
        """
        product, strike, anchor = "VEV_5000", 5000, 2.75
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 80)
        # 5000 is deep ITM with only ~1-3 ticks of observed time value in the
        # live logs. The old 13-tick anchor systematically overvalued it and
        # created long synthetic-underlying inventory. Anchor the sleeve to
        # observed extrinsic and only buy true dislocations.
        ext_blend = max(0.0, min(6.0, 0.65 * ext_ema + 0.35 * anchor))
        fair = intrinsic + ext_blend + 0.55 * spot_edge - 0.04 * position
        fair += self.option_micro_reversion(store, product, q, S, 0.9, 0.80)

        # Soft cap based on repeated evidence: 5000 was the largest loser
        # before the first patch and remained the only material option loser
        # after it. Keep a small allocation for genuine dislocations, but
        # avoid high-delta averaging down. If our own fills indicate informed
        # sellers are repeatedly feeding us, require a much stronger spot edge.
        MAX_LONG_5000 = 30
        bad_5000 = state.timestamp <= int(store.get("bad_5000_til", -1))
        add_room = max(0, MAX_LONG_5000 - position)
        min_spot_edge = 2.25 if bad_5000 else 1.35
        min_buy_edge = 4.50 if bad_5000 else 3.25
        can_add_long = add_room > 0 and spot_edge >= min_spot_edge

        if can_add_long and q.ask <= fair - min_buy_edge:
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 6, add_room))

        # Make exits easier than entries. If we accidentally exceed the soft
        # cap, prioritize reducing inventory.
        if q.bid >= fair + 2.0 or position > MAX_LONG_5000:
            reduce_qty = 12 if position <= MAX_LONG_5000 else min(20, position - MAX_LONG_5000 + 8)
            self.sell(state, product, orders, q.bid, min(q.bid_vol, reduce_qty))

        if q.spread >= 3:
            target_bid = int(min(q.bid + 1, math.floor(fair - 2.75)))
            target_ask = int(max(q.ask - 1, math.ceil(fair + 0.75)))
            if target_bid < target_ask:
                if can_add_long and not bad_5000 and target_bid > 0:
                    self.buy(state, product, orders, target_bid, min(3, add_room))
                # Always show an exit/short-side quote unless spot edge is
                # strongly positive.
                if spot_edge <= 2.0 or position > 0:
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
        # Mark14 option flow is a confirmation signal, not a standalone chase.
        # Apply only a small tilt to 5100/5200 ATM fair.
        m14_opt_dir = self.active_dir(store, "m14_opt_dir", "m14_opt_til", state.timestamp)
        if m14_opt_dir:
            fair += (0.65 if strike == 5100 else 0.35) * m14_opt_dir
        fair += self.option_micro_reversion(store, product, q, S, delta, 0.75)
        # trader_ff: softer skew. ATM bots are uninformed enough that
        # 0.05-0.06 skew was leaking edge per MADSCIENTIST.log evidence.
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
    # ---- CHAMPION WING MODULE, NOT THOMAS ----
    # Handles VEV_5300. It combines the swing-stack target with defensive
    # Mark22-bundle skew and guards against fragile long flips.
    def trade_5300(self, state, store, result, S, vev_dir):
        product, strike = "VEV_5300", 5300
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        fair = max(S - strike, 0.0) + 47.0
        # trader_ff: ultra-soft skew. MADSCIENTIST.log proved 5300 with
        # very soft skew + 290 cap returned $1,380 real (vs OPTIMUM $695,
        # CRAZY/EXPERIMENTS $834). The $1.38K is the single largest
        # confirmed-real-tradable option win we have.
        fair -= 0.015 * position
        if vev_dir != 0:
            fair += 2.0 * vev_dir
        m14_opt_dir = self.active_dir(store, "m14_opt_dir", "m14_opt_til", state.timestamp)
        if m14_opt_dir:
            fair += 0.9 * m14_opt_dir

        # Profit-protect: at near-cap long inventory, if the live bid is rich
        # to our own inventory-skewed fair, cross out some risk instead of
        # only waiting passively. This avoids high-gamma inventory giveback
        # without using a timestamp-specific exit.
        if position >= 240 and q.bid >= fair + 1.0:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 12, max(0, position - 220)))

        # Bigger take size to use the bigger position cap.
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
        # trader_ff: softer skew per MADSCIENTIST.log ($201 vs $123 baseline).
        fair -= 0.02 * position

        # Profit-protect for accumulated cheap-vol inventory. If the option is
        # rich to the same fair used for entries, take the displayed bid on a
        # small slice rather than carrying the whole position through reversals.
        if position >= 120 and q.bid >= fair + 0.8:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 10, max(0, position - 100)))

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
        """Conservative VEV_5500 basket absorber.

        In log 497781, every filled VEV_5500 trade was a buy from Mark 01 at
        price 5, the strategy reached +300, and the final mark was 3.5. That
        made this module a pure ~-450 drag. The fix is to stop paying 5 for
        the basket tail: buy only at 4 or cheaper, cap the long, and keep
        liquidation quotes for any inventory we do acquire.
        """
        product, strike = "VEV_5500", 5500
        q = quote_from(state.order_depths.get(product))
        if q.mid is None:
            return
        orders = result.setdefault(product, [])
        position = self.position(state, product)

        intrinsic = max(S - strike, 0.0)
        anchor = 5.2  # lower than previous 6.5 after observed 497781 leakage
        observed_ext = q.mid - intrinsic
        ext_ema = update_ema(store["emas"], f"{product}_ext", observed_ext, 120)
        ext_blend = max(anchor - 2.5, min(anchor + 2.5, 0.35 * ext_ema + 0.65 * anchor))
        fair = intrinsic + ext_blend
        fair += self.option_micro_reversion(store, product, q, S, 0.08, 0.50)
        fair -= 0.02 * position

        MAX_LONG_5500 = 80
        add_room = max(0, MAX_LONG_5500 - position)
        can_add = add_room > 0

        # Do not absorb Mark 01 at 5 anymore. Only take true distressed tails.
        if can_add and basket_active and q.ask <= min(fair - 1.25, 4):
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 4, add_room))
        elif can_add and q.ask <= min(fair - 2.5, 4):
            self.buy(state, product, orders, q.ask, min(-q.ask_vol, 3, add_room))

        if q.bid >= fair + 1.5:
            self.sell(state, product, orders, q.bid, min(q.bid_vol, 6))

        # Passive bid only at genuinely cheap levels.
        if can_add and q.spread is not None and q.spread <= 2 and q.bid <= min(fair - 1.0, 3):
            target_bid = q.bid
            if q.spread >= 2 and q.bid + 1 < q.ask and q.bid + 1 <= 4:
                target_bid = q.bid + 1
            self.buy(state, product, orders, target_bid, min(3 if basket_active else 2, add_room))

        # Keep trying to unwind; previous version accumulated and held to expiry.
        if position > 0:
            target_ask = int(max(q.ask, math.ceil(fair + 0.5)))
            if target_ask > q.bid:
                self.sell(state, product, orders, target_ask, min(position, 8))
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


    # ---- selective opening sell shock ----
    def initial_sell_everything(self, state: TradingState, result: Dict[str, List[Order]]) -> None:
        """At the first timestamp only, sweep selected opening bids.

        The full sell-everything experiment improved PnL, but the product-level
        attribution showed that the robust gains came from deep/near-ITM call
        vouchers, while HYDROGEL and VEV_5200 were hurt and zero-tail vouchers
        can only be sold at 0. This production-oriented variant keeps the
        opening shock only where it has a market-structure rationale:

        - VEV_4000 / VEV_4500 are mostly synthetic-underlying inventory with
          very wide spreads; opening bids are attractive to sell into.
        - VEV_5000 still behaved like an overpriced ITM call in prior logs.
        - VEV_5500 has low absolute value and visible bids can be harvested,
          while risk is small.

        It intentionally does NOT force-sell HYDROGEL, VEV_5200, VEV_6000 or
        VEV_6500 at the open.
        """
        OPENING_SELL_PRODUCTS = {"VEV_4000", "VEV_4500", "VEV_5000", "VEV_5500"}
        for product in OPENING_SELL_PRODUCTS:
            depth = state.order_depths.get(product)
            if depth is None or not depth.buy_orders:
                continue
            orders = result.setdefault(product, [])
            # Lowest visible bid makes the sell order marketable against all visible bids.
            sweep_price = min(depth.buy_orders.keys())
            qty = self.sell_room(state, product, orders)
            if qty > 0:
                self.sell(state, product, orders, sweep_price, qty)

    # ---- main ----
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        store = self.load_state(state.traderData)

        # Selective opening sell from prior controlled experiment, then normal
        # counterparty-aware strategy.  traderData makes the one-shot behavior
        # robust even if class/global variables do not persist.
        if not store.get("initial_sell_done", False):
            self.initial_sell_everything(state, result)
            store["initial_sell_done"] = True
            result = {p: o for p, o in result.items() if o}
            return result, 0, jsonpickle.encode(store)

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

        self.trade_itm_flash(state, store, result, "VEV_4000", 4000, S, surface_shift, wing_signal)
        self.trade_itm_flash(state, store, result, "VEV_4500", 4500, S, surface_shift, wing_signal)

        smile_lv = self.smile_level(state, store, S)
        self.trade_5000(state, store, result, S, spot_edge)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5100", 5100, S, v_active, spot_edge)
        self.trade_smile_atm(state, store, result, smile_lv, "VEV_5200", 5200, S, v_active, spot_edge)

        self.trade_5300(state, store, result, S, vev_dir)
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
# --- begin appended COMBO_HYBRID_SWING_COUNTERPARTY.py ---
# This final Trader combines:
#   * Counterparty/robust base: HYDRO + VELVET market-making and named-flow tilts.
#   * SwingBeast voucher stack: use VELVET mean-reversion edge as a directional
#     target for all liquid vouchers instead of repeatedly flipping long/short
#     on local option micro-fair values.
# It intentionally avoids timestamp rules.  The only global directional driver is
# the reusable swing edge:
#     edge = 0.7 * EMA15(VELVET_mid) + 0.3 * 5255 - VELVET_mid
# This is the same structural mean-reversion signal that explained the high-PnL
# swing run, while the underlying and HYDRO execution remain from the safer
# counterparty-aware strategy.

_BaseCounterpartyTrader = Trader

# ---------------------------------------------------------------------------
# CHAMPION MODULE NOT IN THOMAS: coordinated VELVET/voucher swing stack
# ---------------------------------------------------------------------------
# This wrapper is the main non-Thomas alpha engine.  Instead of valuing each
# option independently and flipping on tiny local fair-value errors, it projects
# one structural VELVET mean-reversion edge across all liquid vouchers.
#
# Why it exists:
#   * SwingBeast/Combo runs showed the biggest PnL came from coordinated short
#     exposure across VEV_4000..VEV_5500 when VELVET was rich to its anchor.
#   * This signal was far larger than name-only counterparty alpha.
#
# How it avoids overfit:
#   * It uses one reusable edge formula, not per-timestamp rules.
#   * Product-specific thresholds only make weak/problematic strikes more
#     conservative; they do not invent separate fitted predictors.
class Trader(_BaseCounterpartyTrader):
    # Entry levels copied from the swing-stack behavior that worked across the
    # high-PnL run.  Higher thresholds on deep/near-ITM and 5500 reduce churn.
    SWING_ENTRY = 6.0
    SWING_ENTRY_BY_PRODUCT = {
        "VEV_4000": 7.0,
        "VEV_4500": 7.0,
        "VEV_5000": 7.0,
        "VEV_5500": 7.0,
    }
    SWING_LIMITS = {
        VELVET: 200,
        "VEV_4000": 300,
        "VEV_4500": 300,
        "VEV_5000": 300,
        "VEV_5100": 300,
        "VEV_5200": 300,
        "VEV_5300": 300,
        "VEV_5400": 300,
        "VEV_5500": 300,
    }

    def load_state(self, td: str) -> Dict:
        # Disable the old one-shot selective opening-sell early return.  The
        # swing target will sell at the open naturally when the structural edge
        # is negative, and it will also sell 5100/5200/5300/5400 which the old
        # selective opening rule skipped.
        store = super().load_state(td)
        store["initial_sell_done"] = True
        return store

    def _update_swing_edge(self, state: TradingState, store: Dict) -> Optional[float]:
        q = quote_from(state.order_depths.get(VELVET))
        if q.mid is None:
            return None
        fast = update_ema(store["emas"], "velvet_swing_fast", q.mid, 15)
        edge = 0.7 * fast + 0.3 * 5255.0 - q.mid
        edge = max(-25.0, min(25.0, edge))
        self._swing_edge = edge
        return edge

    def _swing_target(self, product: str, edge: float, current: int) -> int:
        entry = self.SWING_ENTRY_BY_PRODUCT.get(product, self.SWING_ENTRY)
        if edge > entry:
            return self.SWING_LIMITS[product]
        if edge < -entry:
            return -self.SWING_LIMITS[product]
        return current

    def _trade_to_target(self, state: TradingState, result: Dict[str, List[Order]], product: str, target: int) -> None:
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        delta = int(target) - self.position(state, product)
        if delta > 0:
            self.buy(state, product, orders, q.ask, min(delta, max(1, -q.ask_vol), 40))
        elif delta < 0:
            self.sell(state, product, orders, q.bid, min(-delta, max(1, q.bid_vol), 40))

    # Keep counterparty-aware VELVET execution, but always refresh the swing edge
    # first so the voucher stack can use it.
    def trade_velvet(self, state, store, result, v_active, implied_tilt, v4_dir):
        self._update_swing_edge(state, store)
        return super().trade_velvet(state, store, result, v_active, implied_tilt, v4_dir)

    # Downstream option modules should see the structural swing edge, not the
    # local option-surface fair.  This keeps the voucher stack coordinated.
    def velvet_spot_edge(self, state, store, S, v_active, implied_tilt, v4_dir):
        edge = getattr(self, "_swing_edge", None)
        if edge is None:
            edge = self._update_swing_edge(state, store)
        return 0.0 if edge is None else float(edge)

    # Keep robust/counterparty HYDRO from the base unchanged.

    def trade_itm_flash(self, state, store, result, product, strike, S, surface_shift, wing_signal):
        # For VEV_4000 keep the known flash layer before applying the swing target.
        # For VEV_4500 the cleaner high-PnL behavior was to trust VELVET-source
        # direction rather than local flash/mid anomalies.
        if strike == 4000:
            super().trade_itm_flash(state, store, result, product, strike, S, surface_shift, wing_signal)
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target(product, edge, self.position(state, product))
        self._trade_to_target(state, result, product, target)

    def trade_5000(self, state, store, result, S, spot_edge):
        edge = float(getattr(self, "_swing_edge", spot_edge))
        target = self._swing_target("VEV_5000", edge, self.position(state, "VEV_5000"))
        self._trade_to_target(state, result, "VEV_5000", target)

    def trade_smile_atm(self, state, store, result, smile_lv, product, strike, S, v_active, spot_edge):
        edge = float(getattr(self, "_swing_edge", spot_edge))
        target = self._swing_target(product, edge, self.position(state, product))
        self._trade_to_target(state, result, product, target)

    # ---- CHAMPION WING MODULE, NOT THOMAS ----
    # Handles VEV_5300. It combines the swing-stack target with defensive
    # Mark22-bundle skew and guards against fragile long flips.
    def trade_5300(self, state, store, result, S, vev_dir):
        # Counterparty signal is useful as confirmation, but the dominant alpha
        # in the logs was the global VELVET swing.  Do not allow local 5300 fair
        # to flip us long against a still-negative swing edge.
        edge = float(getattr(self, "_swing_edge", 0.0))
        # Small confirmation: if the gated 5200/5300 flow points with the edge,
        # let the entry threshold be marginally easier; if against, require more.
        if vev_dir and edge * vev_dir > 0:
            edge *= 1.08
        elif vev_dir and edge * vev_dir < 0:
            edge *= 0.85
        target = self._swing_target("VEV_5300", edge, self.position(state, "VEV_5300"))
        self._trade_to_target(state, result, "VEV_5300", target)

    def trade_5400(self, state, store, result, S):
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target("VEV_5400", edge, self.position(state, "VEV_5400"))
        self._trade_to_target(state, result, "VEV_5400", target)

    def trade_5500(self, state, store, result, S, basket_active):
        edge = float(getattr(self, "_swing_edge", 0.0))
        target = self._swing_target("VEV_5500", edge, self.position(state, "VEV_5500"))
        self._trade_to_target(state, result, "VEV_5500", target)

# --- end appended COMBO_HYBRID_SWING_COUNTERPARTY.py ---
# --- begin appended RESEARCH_ENHANCED_STRATEGY.py ---
# This final wrapper incorporates the counterparty research report and the
# overfitting-risk review:
#   * keep the simple 504261 swing core as the dominant signal;
#   * treat counterparty names as short-TTL execution/fair-value overlays;
#   * correct Mark49 / Mark55 pair interpretation from the research report;
#   * use Mark38 in HYDRO from own-fill quality, not public tape chasing;
#   * reduce fragile OTM long flips and add small defensive wing skew.

_Combo504261Trader = Trader

# ---------------------------------------------------------------------------
# CHAMPION MODULE NOT IN THOMAS: research-enhanced counterparty overlay
# ---------------------------------------------------------------------------
# This wrapper keeps the swing stack as the primary predictor, then adds the
# behavioral conclusions as small, short-lived modifiers.
#
# Important distinction:
#   * Mark67/Mark49 in VELVET can be directional.
#   * Mark22 in wing vouchers is defensive skew only.
#   * Mark55 is a regime/liquidity marker, not a simple buy/sell signal.
#   * Mark01/Mark14 are mostly toxicity/quote-quality labels.
#
# This is deliberately weaker than a "name = trade" strategy, because the
# research showed counterparty alpha is mostly execution context, not the main
# source of PnL.
class Trader(_Combo504261Trader):
    # Long OTM flips were a known HybridBeast leak.  Keep shorts fully enabled,
    # but require more evidence before going long in the wings.
    OTM_LONG_ENTRY_BY_PRODUCT = {
        "VEV_5300": 9.5,
        "VEV_5400": 10.5,
        "VEV_5500": 12.0,
    }
    OTM_LONG_CAP_BY_PRODUCT = {
        "VEV_5300": 220,
        "VEV_5400": 170,
        "VEV_5500": 90,
    }

    def load_state(self, td: str) -> Dict:
        store = super().load_state(td)
        store.setdefault("m55_pair_dir", 0)
        store.setdefault("m55_pair_til", -1)
        store.setdefault("wing_mark22_pressure", 0)
        store.setdefault("wing_mark22_til", -1)
        store.setdefault("toxic_velvet_til", -1)
        store.setdefault("toxic_velvet_pressure", 0)
        return store

    def velvet_counterparty_tilt(self, state: TradingState, store: Dict) -> float:
        """Research-report counterparty overlay.

        The report found Mark67 buys and Mark49 sells are the only extract
        signals worth crossing; Mark55 must be interpreted through the passive
        maker, not as a flat Mark55 fade.  Mark01/Mark14 are treated as quote
        quality controls rather than direct alpha.
        """
        ts = state.timestamp
        tilt = 0.0
        if ts <= int(store.get("m67_vev_til", -1)):
            tilt += 2.35
        # IMPORTANT: positive m49_vev_dir means fade Mark49's stale selling.
        tilt += 1.25 * self.active_dir(store, "m49_vev_dir", "m49_vev_til", ts)
        tilt += 0.85 * self.active_dir(store, "m55_pair_dir", "m55_pair_til", ts)
        # Mark14 is demoted to a weak contextual signal only.
        tilt += 0.20 * self.active_dir(store, "m14_vev_dir", "m14_vev_til", ts)
        # After toxic own VELVET fills from Mark01/Mark14, reduce the next tilt
        # toward neutral instead of aggressively chasing the same side.
        if ts <= int(store.get("toxic_velvet_til", -1)):
            tilt *= 0.65
        return max(-3.0, min(3.0, tilt))

    def update_named_counterparty_signals(self, state: TradingState, store: Dict) -> None:
        """Update short-TTL counterparty signals from the report.

        Public tape:
          - Mark67 buy: bullish, especially under tight surface or size >= 10.
          - Mark49 sell: bullish because he is the stale seller.
          - Mark55 must be read through passive maker identity.
          - Mark22 wing sells are defensive skew, not a direct chase.
        Own fills:
          - Mark38 HYDRO fills are worth farming; update HYDRO lean from own
            fills only, not public chase signals.
          - Mark01/Mark14 VELVET fills are treated as toxic quote-quality flags.
        """
        ts = state.timestamp
        gate2 = self.gate2_active(state)

        def note_velvet_public(buyer, seller, qty):
            qty = abs(int(qty))
            # Mark67 buy is crossable; allow either tight surface or meaningful size.
            if buyer == "Mark 67" and (gate2 or qty >= 10):
                store["m67_vev_til"] = ts + (1800 if qty >= 10 else 1200)
            # Mark49 seller is stale: fade his sell by leaning bullish.
            if seller == "Mark 49":
                store["m49_vev_dir"] = 1
                store["m49_vev_til"] = ts + (1600 if qty >= 10 else 1000)
            elif buyer == "Mark 49":
                store["m49_vev_dir"] = -1
                store["m49_vev_til"] = ts + 1000

            # Mark55 is not intrinsically informative.  Read passive maker.
            # Mark55 buys from Mark01: side with Mark01 seller => bearish.
            if buyer == "Mark 55" and seller == "Mark 01":
                store["m55_pair_dir"] = -1
                store["m55_pair_til"] = ts + 900
            # Mark55 buys from Mark14: Mark14 is weak passive side => mild bullish.
            elif buyer == "Mark 55" and seller == "Mark 14":
                store["m55_pair_dir"] = 1
                store["m55_pair_til"] = ts + 700
            # Mark55 sells to Mark01: side with Mark01 buyer => bullish.
            elif seller == "Mark 55" and buyer == "Mark 01":
                store["m55_pair_dir"] = 1
                store["m55_pair_til"] = ts + 900
            # Mark55 sells to Mark14: Mark14 weak buyer => mild bearish.
            elif seller == "Mark 55" and buyer == "Mark 14":
                store["m55_pair_dir"] = -1
                store["m55_pair_til"] = ts + 700

            # Mark14 alone is weak maker-quality context, not direct momentum.
            if buyer == "Mark 14":
                store["m14_vev_dir"] = 1
                store["m14_vev_til"] = ts + 600
            elif seller == "Mark 14":
                store["m14_vev_dir"] = -1
                store["m14_vev_til"] = ts + 600

        def note_hydro_own_fill(buyer, seller):
            # Report: being hit by Mark38 in HYDRO was good both ways.
            # If Mark38 buys from us, keep the offer side attractive by leaning fair down.
            # If Mark38 sells to us, keep the bid side attractive by leaning fair up.
            if buyer == "Mark 38" and seller == "SUBMISSION":
                store["m38_hydro_dir"] = -1
                store["m38_hydro_til"] = ts + 1200
            elif seller == "Mark 38" and buyer == "SUBMISSION":
                store["m38_hydro_dir"] = 1
                store["m38_hydro_til"] = ts + 1200
            # Mark14 HYDRO own fills were poor: cool down by neutralizing the Mark38 lean.
            if (buyer == "Mark 14" and seller == "SUBMISSION") or (seller == "Mark 14" and buyer == "SUBMISSION"):
                store["m38_hydro_til"] = min(int(store.get("m38_hydro_til", -1)), ts - 1)

        def note_mark14_option(buyer, seller, symbol):
            # Keep the old Mark14 option confirmation, but only as weak context.
            if symbol not in ("VEV_5100", "VEV_5300"):
                return
            if buyer == "Mark 14":
                store["m14_opt_dir"] = 1
                store["m14_opt_til"] = ts + (1000 if gate2 else 500)
            elif seller == "Mark 14":
                store["m14_opt_dir"] = -1
                store["m14_opt_til"] = ts + (1000 if gate2 else 500)

        def note_wing_public(buyer, seller, symbol, qty):
            if symbol not in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"):
                return
            # Mark22 is usually the right-side wing seller, but not enough to chase.
            # Use this only as a defensive skew against buying wings.
            if seller == "Mark 22":
                store["wing_mark22_pressure"] = min(30, int(store.get("wing_mark22_pressure", 0)) + abs(int(qty)))
                store["wing_mark22_til"] = ts + 1400

        # Public tape updates.
        for tr in state.market_trades.get(VELVET, []):
            note_velvet_public(getattr(tr, "buyer", None), getattr(tr, "seller", None), getattr(tr, "quantity", 0))
        for prod in ("VEV_5100", "VEV_5300"):
            for tr in state.market_trades.get(prod, []):
                note_mark14_option(getattr(tr, "buyer", None), getattr(tr, "seller", None), prod)
        for prod in ("VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500"):
            for tr in state.market_trades.get(prod, []):
                note_wing_public(getattr(tr, "buyer", None), getattr(tr, "seller", None), prod, getattr(tr, "quantity", 0))

        # Own-fill quality updates.
        for tr in state.own_trades.get(HYDRO, []):
            note_hydro_own_fill(getattr(tr, "buyer", None), getattr(tr, "seller", None))
        toxic_qty = 0
        for tr in state.own_trades.get(VELVET, []):
            b = getattr(tr, "buyer", None)
            s = getattr(tr, "seller", None)
            if (b == "SUBMISSION" and s in {"Mark 01", "Mark 14"}) or (s == "SUBMISSION" and b in {"Mark 01", "Mark 14"}):
                toxic_qty += abs(int(tr.quantity))
            # Own fills can also confirm Mark49/Mark55 rules.
            note_velvet_public(b, s, getattr(tr, "quantity", 0))
        if toxic_qty:
            store["toxic_velvet_pressure"] = min(60, int(store.get("toxic_velvet_pressure", 0)) + toxic_qty)
            store["toxic_velvet_til"] = ts + 1200
        else:
            store["toxic_velvet_pressure"] = max(0, int(store.get("toxic_velvet_pressure", 0)) - 2)

        for prod in ("VEV_5100", "VEV_5300"):
            for tr in state.own_trades.get(prod, []):
                note_mark14_option(getattr(tr, "buyer", None), getattr(tr, "seller", None), prod)

        # Cache current-iteration defensive flags for order-routing overrides.
        self._wing_mark22_til = int(store.get("wing_mark22_til", -1))

    def _swing_target(self, product: str, edge: float, current: int) -> int:
        """Swing target with overfit guardrails.

        Short voucher exposure remains the core alpha.  Long flips in the OTM
        wings require a stronger edge and are size-capped because the research
        report and prior HybridBeast logs showed this was a fragile leak.
        """
        short_entry = self.SWING_ENTRY_BY_PRODUCT.get(product, self.SWING_ENTRY)
        long_entry = self.OTM_LONG_ENTRY_BY_PRODUCT.get(product, short_entry)
        if edge < -short_entry:
            return -self.SWING_LIMITS[product]
        if edge > long_entry:
            return self.OTM_LONG_CAP_BY_PRODUCT.get(product, self.SWING_LIMITS[product])
        return current

    def _trade_to_target(self, state: TradingState, result: Dict[str, List[Order]], product: str, target: int) -> None:
        """Move toward target, with lower churn and defensive wing-skew.

        Mark22 wing selling only lowers our willingness to add long wing risk;
        it does not force an aggressive short chase.
        """
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        # Defensive wing skew: if Mark22 is actively selling wings, do not add
        # long 5300+ exposure unless the swing edge is very strong.
        if product in ("VEV_5300", "VEV_5400", "VEV_5500") and state.timestamp <= int(getattr(self, "_wing_mark22_til", -1)):
            if target > self.position(state, product):
                target = self.position(state, product)
        orders = result.setdefault(product, [])
        delta = int(target) - self.position(state, product)
        # Avoid noisy reversals around the target; this was one of the HybridBeast churn risks.
        if abs(delta) < 8:
            return
        max_slice = 35 if product in ("VEV_5300", "VEV_5400", "VEV_5500") else 40
        if delta > 0:
            self.buy(state, product, orders, q.ask, min(delta, max(1, -q.ask_vol), max_slice))
        elif delta < 0:
            self.sell(state, product, orders, q.bid, min(-delta, max(1, q.bid_vol), max_slice))

# --- end appended RESEARCH_ENHANCED_STRATEGY.py ---

# ---------------------------------------------------------------------------
# Champion + Thomas HYDRO variant
# ---------------------------------------------------------------------------
# Keep the 510544 champion logic for every product except HYDROGEL_PACK.
# HYDROGEL_PACK is replaced by Thomas's Agent1-style Hydrogel module:
#   edge = 0.7 * EMA10(HYDRO_mid) + 0.3 * 9980 - HYDRO_mid
#          + 12 * short-TTL named-bot signal
# with targets +/-200 when |edge| > 14.
# This is intentionally isolated to HYDRO so the champion VELVET/voucher stack
# remains unchanged.

_ChampionTrader = Trader

# ---------------------------------------------------------------------------
# THOMAS MODULE: HYDROGEL override only
# ---------------------------------------------------------------------------
# This final subclass is the only part imported from Thomas's strategy.
# It replaces inherited HYDROGEL_PACK behavior with Thomas's broader hydro
# swing/bot-score logic, while leaving every VELVET and voucher method inherited
# from the champion stack unchanged.
class Trader(_ChampionTrader):
    THOMAS_HYDRO_BOT_ALPHA = {
        "Mark 14": +1.0,
        "Mark 38": -1.0,
        "Mark 22": -1.5,
    }
    THOMAS_HYDRO_MIN_WEIGHT = 8.0
    THOMAS_HYDRO_TTL = 2500
    THOMAS_HYDRO_TILT = 12.0

    @staticmethod
    def _thomas_score_bots(trades, alpha) -> float:
        score = 0.0
        for tr in trades or []:
            qty = abs(int(getattr(tr, "quantity", 0) or 0))
            buyer = getattr(tr, "buyer", None)
            seller = getattr(tr, "seller", None)
            # Same orientation as Thomas's Agent1 best:
            # non-submission buyer contributes +alpha, non-submission seller contributes -alpha.
            if buyer != "SUBMISSION":
                score += alpha.get(buyer, 0.0) * qty
            if seller != "SUBMISSION":
                score -= alpha.get(seller, 0.0) * qty
        return score

    @staticmethod
    def _thomas_all_trades(state: TradingState, product: str):
        out = list(state.market_trades.get(product, []) or [])
        out.extend(state.own_trades.get(product, []) or [])
        return out

    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        # Preserve all champion named-flow, swing, voucher, and adverse-selection state.
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        # Add only Thomas's HYDRO-specific bot signal.
        hydro_score = self._thomas_score_bots(
            self._thomas_all_trades(state, HYDRO),
            self.THOMAS_HYDRO_BOT_ALPHA,
        )
        if abs(hydro_score) >= self.THOMAS_HYDRO_MIN_WEIGHT:
            store["thomas_hydro_dir"] = 1 if hydro_score > 0 else -1
            store["thomas_hydro_til"] = state.timestamp + self.THOMAS_HYDRO_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _thomas_hydro_signal(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("thomas_hydro_til", -1)):
            return 0
        return int(store.get("thomas_hydro_dir", 0))

    def _thomas_trade_to_target(
        self,
        state: TradingState,
        result: Dict[str, List[Order]],
        product: str,
        target: int,
    ) -> None:
        q = quote_from(state.order_depths.get(product))
        if q.bid is None or q.ask is None:
            return
        orders = result.setdefault(product, [])
        delta = int(target) - self.position(state, product)
        if delta > 0:
            self.buy(state, product, orders, q.ask, min(delta, max(0, -q.ask_vol)))
        elif delta < 0:
            self.sell(state, product, orders, q.bid, min(-delta, max(0, q.bid_vol)))

    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        """Thomas's Hydrogel strategy, isolated inside the 510544 champion.

        This intentionally does not reuse the champion Hydrogel market-maker.
        It targets full long/short exposure only when the Thomas structural
        mean-reversion edge, plus his bot-identity Hydrogel tilt, exceeds a wide
        14-tick activation band.
        """
        q = quote_from(state.order_depths.get(HYDRO))
        if q.bid is None or q.ask is None or q.mid is None:
            return

        fast = update_ema(store["emas"], "hydro_swing_fast", q.mid, 10)
        edge = 0.7 * fast + 0.3 * 9980.0 - q.mid
        edge += self.THOMAS_HYDRO_TILT * self._thomas_hydro_signal(state, store)

        current = self.position(state, HYDRO)
        if edge > 14.0:
            target = POS_LIMITS[HYDRO]
        elif edge < -14.0:
            target = -POS_LIMITS[HYDRO]
        else:
            target = current

        self._thomas_trade_to_target(state, result, HYDRO, target)
