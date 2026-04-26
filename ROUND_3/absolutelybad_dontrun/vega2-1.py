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

        # ── 2. Time to expiry (Fixed to user 5 -> 4 specification) ─────────────
        # Note: Ensure TOTAL_TIMESTAMPS matches the actual round length (e.g., 1,000,000)
        frac  = state.timestamp / 1_000_000 
        TTE_days = 5.0 - frac * (5.0 - 4.0) 
        T     = TTE_days / 365.0 

        # ── 3. Underlying mid ─────────────────────────────────────────────────
        S = self._mid_price(state, "VELVETFRUIT_EXTRACT")
        if S is None:
            return result, conversions, json.dumps(saved)

        # ── 4. Extract implied vols for Surface Fitting ───────────────────────
        # We only use liquid options to build the "fair" volatility surface
        moneyness_list = []
        impl_vol_list  = []
        valid_syms     = []
        iv_cache       = {} 

        for sym in OPTION_SYMS:
            if sym not in state.order_depths: continue
            K = STRIKES[sym]
            od = state.order_depths[sym]
            bid, ask = self._best_bid(od), self._best_ask(od)

            if bid and ask and bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                iv_mid = implied_vol_newton(mid, S, K, T, 0.0, 'call')
                iv_bid = implied_vol_newton(bid, S, K, T, 0.0, 'call')
                iv_ask = implied_vol_newton(ask, S, K, T, 0.0, 'call')

                if iv_mid and iv_bid and iv_ask:
                    iv_cache[sym] = {'mid': mid, 'bid': bid, 'ask': ask, 'iv_bid': iv_bid, 'iv_ask': iv_ask}
                    moneyness_list.append(np.log(K / S))
                    impl_vol_list.append(iv_mid)
                    valid_syms.append(sym)

        if len(valid_syms) < 3: # Need at least 3 points for a quadratic fit
            return result, conversions, json.dumps(saved)

        # ── 5. Weighted Surface Fit ───────────────────────────────────────────
        # We fit the surface once per tick to determine "Fair IV" for all strikes
        vol_spreads = [iv_cache[s]['iv_ask'] - iv_cache[s]['iv_bid'] for s in valid_syms]
        avg_vol_spread = float(np.mean(vol_spreads))
        
        # INCREASED THRESHOLD: 1.2x spread ensures we only trade high-conviction edges
        Z_THRESHOLD = 1.2 * avg_vol_spread 
        
        weights = np.array([1.0 / max(s, 1e-4) for s in vol_spreads])
        coeffs = np.polyfit(moneyness_list, impl_vol_list, deg=2, w=weights)

        # ── 6. Portfolio Delta Calculation (FIXED) ────────────────────────────
        # We calculate delta for EVERY position we hold, using the fitted surface
        net_delta = 0.0
        for sym in OPTION_SYMS:
            pos = state.position.get(sym, 0)
            if pos == 0: continue
            
            # Use the surface to get the "Fair IV" for this strike even if it's not trading
            fair_iv = np.polyval(coeffs, np.log(STRIKES[sym] / S))
            net_delta += bs_delta(S, STRIKES[sym], T, 0.0, fair_iv, 'call') * pos

        # ── 7. Option Signals (Entry/Exit) ────────────────────────────────────
        for sym in valid_syms:
            K = STRIKES[sym]
            fair_iv = np.polyval(coeffs, np.log(K / S))
            cache = iv_cache[sym]
            pos = state.position.get(sym, 0)
            
            # Buy if the Market Ask is significantly below our Fair IV
            if cache['iv_ask'] < fair_iv - Z_THRESHOLD:
                qty = min(5, 50 - pos) # ORDER_SIZE=5, LIMIT=50
                if qty > 0:
                    result[sym] = [Order(sym, int(cache['ask']), qty)]
                    net_delta += bs_delta(S, K, T, 0.0, fair_iv, 'call') * qty

            # Sell if the Market Bid is significantly above our Fair IV
            elif cache['iv_bid'] > fair_iv + Z_THRESHOLD:
                qty = min(5, 50 + pos)
                if qty > 0:
                    result[sym] = [Order(sym, int(cache['bid']), -qty)]
                    net_delta -= bs_delta(S, K, T, 0.0, fair_iv, 'call') * qty

        # ── 8. Delta Hedge (Aggressive Execution) ─────────────────────────────
        underlying_pos = state.position.get("VELVETFRUIT_EXTRACT", 0)
        total_delta = net_delta + underlying_pos

        if abs(total_delta) > 5: # HEDGE_BAND
            hedge_qty = -int(round(total_delta))
            # Clip to position limits (200)
            hedge_qty = int(np.clip(hedge_qty, -200 - underlying_pos, 200 - underlying_pos))

            if hedge_qty != 0:
                und_od = state.order_depths.get("VELVETFRUIT_EXTRACT")
                price = self._best_ask(und_od) if hedge_qty > 0 else self._best_bid(und_od)
                if price:
                    # We initialize the list or add to it if we already have orders
                    result.setdefault("VELVETFRUIT_EXTRACT", []).append(Order("VELVETFRUIT_EXTRACT", price, hedge_qty))

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