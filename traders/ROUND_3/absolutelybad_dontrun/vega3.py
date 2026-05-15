import numpy as np
import json
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple

# ── 1. Configuration ─────────────────────────────────────────────────────────
UNDERLYING = "VELVETFRUIT_EXTRACT"
OPTION_SYMS = [
    "VEV_4000", "VEV_4500", "VEV_5000", "VEV_5100", "VEV_5200", 
    "VEV_5300", "VEV_5400", "VEV_5500", "VEV_6000"
]
STRIKES = {
    "VEV_4000": 4000, "VEV_4500": 4500, "VEV_5000": 5000,
    "VEV_5100": 5100, "VEV_5200": 5200, "VEV_5300": 5300,
    "VEV_5400": 5400, "VEV_5500": 5500, "VEV_6000": 6000
}

# ── 2. Math Utilities ─────────────────────────────────────────────────────────

def norm_pdf(x: float) -> float:
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)

def norm_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p, sign = 0.327591100, 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    poly = t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    return 0.5 * (1.0 + sign * (1.0 - poly * np.exp(-x * x / 2.0)))

def bs_price(S, K, T, r, sigma):
    if T <= 0: return max(0, S - K)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2)

def bs_delta(S, K, T, r, sigma):
    if T <= 0: return 1.0 if S > K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm_cdf(d1)

def get_implied_vol(price, S, K, T, r):
    sigma = 0.5
    for _ in range(10):
        p = bs_price(S, K, T, r, sigma)
        diff = price - p
        if abs(diff) < 0.01: return sigma
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        vega = S * np.sqrt(T) * norm_pdf(d1)
        sigma += diff / max(vega, 1e-4)
    return max(sigma, 0.01)

# ── 3. Trader Class ───────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        
        # 1. Market Context
        T = max((5.0 - (state.timestamp / 1_000_000)) / 365.0, 1e-5) 
        S = self._get_mid(state, UNDERLYING)
        if S is None: return {}, 0, ""

        # 2. Fit Volatility Surface
        iv_list, log_moneyness = [], []
        for sym in OPTION_SYMS:
            mid = self._get_mid(state, sym)
            if mid:
                iv_list.append(get_implied_vol(mid, S, STRIKES[sym], T, 0.0))
                log_moneyness.append(np.log(STRIKES[sym] / S))
        
        if len(iv_list) < 3: return {}, 0, ""
        coeffs = np.polyfit(log_moneyness, iv_list, 2)

        # 3. Market Making (Quoting)
        net_delta = 0.0
        for sym in OPTION_SYMS:
            K = STRIKES[sym]
            pos = state.position.get(sym, 0)
            
            # Calculate Fair Value
            fair_iv = np.polyval(coeffs, np.log(K / S))
            fair_p = bs_price(S, K, T, 0.0, fair_iv)
            
            # Accumulate current option delta
            net_delta += bs_delta(S, K, T, 0.0, fair_iv) * pos

            # DEFENSIVE QUOTING: Using a 3-tick edge to stop the PnL bleed
            bid_price = int(round(fair_p - 1.5))
            ask_price = int(round(fair_p + 1.5))
            
            orders = []
            if pos < 50:  orders.append(Order(sym, bid_price, 10))
            if pos > -50: orders.append(Order(sym, ask_price, -10))
            result[sym] = orders

        # 4. Delta Hedging (Smart Execution)
        und_pos = state.position.get(UNDERLYING, 0)
        total_delta = net_delta + und_pos
        
        # Only hedge if delta is > 10 to avoid excessive spread crossing
        if abs(total_delta) > 10.0:
            hedge_qty = int(np.clip(-round(total_delta), -200 - und_pos, 200 - und_pos))
            und_od = state.order_depths.get(UNDERLYING)
            if und_od:
                # Hedge at best available price
                px = self._best_ask(und_od) if hedge_qty > 0 else self._best_bid(und_od)
                if px: result[UNDERLYING] = [Order(UNDERLYING, px, hedge_qty)]

        # 5. LOGGING DASHBOARD
        if state.timestamp % 2000 == 0:
            print(f"--- TICK {state.timestamp} ---")
            print(f"S: {S:.2f} | TTE: {T*365:.4f} days | OptDelta: {net_delta:.2f} | TotalDelta: {total_delta:.2f}")
            # Sanity check on one strike
            test_iv = np.polyval(coeffs, np.log(5000/S))
            print(f"VEV_5000: MktMid {self._get_mid(state, 'VEV_5000')} | Fair {bs_price(S, 5000, T, 0, test_iv):.2f}")

        return result, 0, ""

    def _get_mid(self, state, sym):
        od = state.order_depths.get(sym)
        if not od: return None
        b, a = self._best_bid(od), self._best_ask(od)
        return (b + a) / 2.0 if b and a else None

    def _best_bid(self, od): return max(od.buy_orders.keys()) if od.buy_orders else None
    def _best_ask(self, od): return min(od.sell_orders.keys()) if od.sell_orders else None