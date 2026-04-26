import numpy as np
import json
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple


# ── Math utilities ────────────────────────────────────────────────────────────

def norm_pdf(x: float) -> float:
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def norm_cdf(x: float) -> float:
    """Abramowitz & Stegun — max error 1.5e-7."""
    a1 =  0.254829592
    a2 = -0.284496736
    a3 =  1.421413741
    a4 = -1.453152027
    a5 =  1.061405429
    p  =  0.327591100
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    poly = t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    return 0.5 * (1.0 + sign * (1.0 - poly * np.exp(-x * x / 2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = 'call') -> float:
    if T <= 0 or sigma <= 0:
        if option_type == 'call':
            return max(S - K, 0.0)
        else:
            return max(K - S, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == 'call':
        return S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2)
    else:
        return K * np.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 1e-8
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    return S * norm_pdf(d1) * np.sqrt(T)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float,
             option_type: str = 'call') -> float:
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type == 'call':
        return norm_cdf(d1)
    else:
        return norm_cdf(d1) - 1.0


def implied_vol_newton(market_price: float, S: float, K: float, T: float, r: float,
                       option_type: str = 'call',
                       tol: float = 1e-6, max_iter: int = 50) -> float:
    """
    Newton-Raphson implied vol solver.
    Returns None if vega too small or no convergence (deep OTM near expiry).
    Initial guess of 1.0 (100%) suits short-dated options where annualized
    IV is typically large.
    """
    sigma = 1.0  # start at 100% — better for short-dated annualized vol

    for _ in range(max_iter):
        price = bs_price(S, K, T, r, sigma, option_type)
        vega  = bs_vega(S, K, T, r, sigma)

        if vega < 1e-8:
            return None

        diff  = price - market_price
        if abs(diff) < tol:
            return sigma

        sigma -= diff / vega
        sigma  = max(1e-7, min(sigma, 20.0))  # clamp: up to 2000% annualized

    return None


# ── Config ────────────────────────────────────────────────────────────────────

UNDERLYING = "VELVETFRUIT_EXTRACT"

OPTION_SYMS = [
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200",
    "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000", "VEV_6500",
]

STRIKES: Dict[str, float] = {
    "VEV_4000": 4000.0,
    "VEV_4500": 4500.0,
    "VEV_5000": 5000.0,
    "VEV_5100": 5100.0,
    "VEV_5200": 5200.0,
    "VEV_5300": 5300.0,
    "VEV_5400": 5400.0,
    "VEV_5500": 5500.0,
    "VEV_6000": 6000.0,
    "VEV_6500": 6500.0,
}

TOTAL_TIMESTAMPS = 1_000_000  # ticks in one round — verify against round spec
R                = 0.0        # risk-free rate (0 in Prosperity)
POSITION_LIMIT   = 200        # underlying position limit
OPTION_LIMIT     = 50         # per-option position limit

# TTE: day 3 round, starts at 5 days, ends at 4 days
TTE_START = 5.0               # days at timestamp=0
TTE_END   = 4.0               # days at timestamp=TOTAL_TIMESTAMPS

MIN_VEGA     = 0.01           # in annualized, dollar-vega terms — tune after first run
HEDGE_BAND   = 5              # rehedge only if |net delta| exceeds this
ORDER_SIZE   = 5              # base lots per signal


# ── Trader ────────────────────────────────────────────────────────────────────

class Trader:

    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result:      Dict[str, List[Order]] = {}
        conversions: int                    = 0

        # ── 1. Load persisted state ───────────────────────────────────────────
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            saved = {}

        # ── 2. Time to expiry ─────────────────────────────────────────────────
        # Interpolate TTE linearly from TTE_START to TTE_END across the round
        frac  = state.timestamp / TOTAL_TIMESTAMPS          # 0.0 → 1.0
        TTE_days = TTE_START - frac * (TTE_START - TTE_END) # 5.0 → 4.0
        T     = TTE_days / 365.0                            # annualized

        # ── 3. Underlying mid ─────────────────────────────────────────────────
        S = self._mid_price(state, UNDERLYING)
        if S is None:
            return result, conversions, json.dumps(saved)

        # ── 4. Extract implied vols from market ───────────────────────────────
        moneyness_list = []
        impl_vol_list  = []
        valid_syms     = []
        iv_cache: Dict[str, Dict[str, float]] = {}  # sym -> {mid, bid, ask, iv_mid, iv_bid, iv_ask}

        for sym in OPTION_SYMS:
            if sym not in state.order_depths:
                continue

            K    = STRIKES[sym]
            od   = state.order_depths[sym]
            bid  = self._best_bid(od)
            ask  = self._best_ask(od)

            if bid is None or ask is None or bid <= 0 or ask <= 0:
                continue

            mid    = (bid + ask) / 2.0
            iv_mid = implied_vol_newton(mid, S, K, T, R, 'call')
            iv_bid = implied_vol_newton(bid, S, K, T, R, 'call')
            iv_ask = implied_vol_newton(ask, S, K, T, R, 'call')

            if iv_mid is None or iv_bid is None or iv_ask is None:
                continue

            iv_cache[sym] = {
                'mid': mid, 'bid': bid, 'ask': ask,
                'iv_mid': iv_mid, 'iv_bid': iv_bid, 'iv_ask': iv_ask,
            }

            m = np.log(K / S)  # log-moneyness: 0 = ATM, + = OTM call
            moneyness_list.append(m)
            impl_vol_list.append(iv_mid)
            valid_syms.append(sym)

        if len(valid_syms) < 3:
            return result, conversions, json.dumps(saved)

        moneyness_arr = np.array(moneyness_list)
        impl_vol_arr  = np.array(impl_vol_list)

        # ── 5. Dynamic Z threshold from vol spreads ───────────────────────────
        vol_spreads = [
            iv_cache[sym]['iv_ask'] - iv_cache[sym]['iv_bid']
            for sym in valid_syms
        ]
        avg_vol_spread = float(np.mean(vol_spreads))
        Z_THRESHOLD    = 0.6 * avg_vol_spread

        # ── 6. Weighted quadratic surface fit ─────────────────────────────────
        # Weight = 1 / vol_spread so liquid ATM options anchor the fit
        weights     = np.array([1.0 / max(s, 1e-4) for s in vol_spreads])
        coeffs      = np.polyfit(moneyness_arr, impl_vol_arr, deg=2, w=weights)
        surface_vol = np.polyval(coeffs, moneyness_arr)

        # Residuals: positive = rich (sell), negative = cheap (buy)
        residuals = impl_vol_arr - surface_vol

        # ── 7. Option signals ─────────────────────────────────────────────────
        net_delta = 0.0

        for i, sym in enumerate(valid_syms):
            K        = STRIKES[sym]
            residual = residuals[i]
            sv       = surface_vol[i]
            cache    = iv_cache[sym]

            vega  = bs_vega(S, K, T, R, sv)
            delta = bs_delta(S, K, T, R, sv, 'call')

            if vega < MIN_VEGA:
                continue

            pos    = state.position.get(sym, 0)
            orders: List[Order] = []

            if residual < -Z_THRESHOLD:
                # Cheap vs surface — buy at ask
                # Only buy if even at the ask price the option is still cheap
                # i.e. iv_ask is still below surface vol
                if cache['iv_ask'] < sv:
                    qty = min(ORDER_SIZE, OPTION_LIMIT - pos)
                    if qty > 0:
                        orders.append(Order(sym, int(cache['ask']), qty))
                        net_delta += delta * qty

            elif residual > Z_THRESHOLD:
                # Rich vs surface — sell at bid
                # Only sell if even at the bid price the option is still rich
                if cache['iv_bid'] > sv:
                    qty = min(ORDER_SIZE, OPTION_LIMIT + pos)
                    if qty > 0:
                        orders.append(Order(sym, int(cache['bid']), -qty))
                        net_delta -= delta * qty

            if orders:
                result[sym] = orders

        # ── 8. Add delta from existing option positions ───────────────────────
        for sym in OPTION_SYMS:
            existing_pos = state.position.get(sym, 0)
            if existing_pos == 0 or sym not in iv_cache:
                continue
            sv = np.polyval(coeffs, np.log(STRIKES[sym] / S))
            net_delta += bs_delta(S, STRIKES[sym], T, R, sv, 'call') * existing_pos

        # ── 9. Delta hedge underlying ─────────────────────────────────────────
        underlying_pos = state.position.get(UNDERLYING, 0)
        total_delta    = net_delta + underlying_pos

        if abs(total_delta) > HEDGE_BAND:
            hedge_qty = -int(round(total_delta))
            hedge_qty = int(np.clip(
                hedge_qty,
                -POSITION_LIMIT - underlying_pos,
                 POSITION_LIMIT - underlying_pos
            ))

            if hedge_qty != 0:
                und_od = state.order_depths.get(UNDERLYING)
                if und_od:
                    if hedge_qty > 0:
                        ask = self._best_ask(und_od)
                        if ask:
                            result[UNDERLYING] = [Order(UNDERLYING, ask, hedge_qty)]
                    else:
                        bid = self._best_bid(und_od)
                        if bid:
                            result[UNDERLYING] = [Order(UNDERLYING, bid, hedge_qty)]
        # ── 10. Persist diagnostics ───────────────────────────────────────────
        saved['tte_days']       = TTE_days
        saved['avg_vol_spread'] = avg_vol_spread
        saved['z_threshold']    = Z_THRESHOLD
        saved['n_valid_syms']   = len(valid_syms)
        saved['net_delta']      = net_delta
        return result, conversions, json.dumps(saved)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mid_price(self, state: TradingState, sym: str):
        od = state.order_depths.get(sym)
        if od is None:
            return None
        bid = self._best_bid(od)
        ask = self._best_ask(od)
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    def _best_bid(self, od: OrderDepth):
        if not od.buy_orders:
            return None
        return max(od.buy_orders.keys())

    def _best_ask(self, od: OrderDepth):
        if not od.sell_orders:
            return None
        return min(od.sell_orders.keys())