import numpy as np
import json
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple

# ── 1. Constants & Configuration ──────────────────────────────────────────────
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
    p = 0.327591100
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    poly = t * (a1 + t * (a2 + t * (a3 + t * (a4 + t * a5))))
    return 0.5 * (1.0 + sign * (1.0 - poly * np.exp(-x * x / 2.0)))

def bs_price(S, K, T, r, sigma, type='call'):
    if T <= 0: return max(0, S - K) if type == 'call' else max(0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if type == 'call':
        return S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2)
    return K * np.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def bs_delta(S, K, T, r, sigma, type='call'):
    if T <= 0: return 1.0 if S > K else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return norm_cdf(d1) if type == 'call' else norm_cdf(d1) - 1.0

def implied_vol_newton(price, S, K, T, r, type='call'):
    sigma = 0.5
    for _ in range(10):
        p = bs_price(S, K, T, r, sigma, type)
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        vega = S * np.sqrt(T) * norm_pdf(d1)
        if vega < 1e-4: break
        diff = price - p
        if abs(diff) < 1e-4: return sigma
        sigma += diff / vega
    return sigma if sigma > 0 else 0.1

# ── 3. Trader Class ───────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        conversions: int = 0
        
        # Load diagnostics
        try:
            saved = json.loads(state.traderData) if state.traderData else {}
        except:
            saved = {}

        # Time to expiry logic (5 -> 4 days)
        # Assuming round length is 1,000,000 timestamps
        T_start, T_end = 5.0, 4.0
        frac = state.timestamp / 1_000_000
        TTE_days = T_start - frac * (T_start - T_end)
        T = TTE_days / 365.0 

        S = self._mid_price(state, UNDERLYING)
        if S is None:
            return result, conversions, json.dumps(saved)

        # ── 4. Surface Fitting ───────────────────────────────────────────────
        iv_data = []
        moneyness = []
        valid_syms = []
        
        for sym in OPTION_SYMS:
            od = state.order_depths.get(sym)
            if not od: continue
            b, a = self._best_bid(od), self._best_ask(od)
            if b and a:
                mid_iv = implied_vol_newton((b + a) / 2.0, S, STRIKES[sym], T, 0.0, 'call')
                iv_data.append(mid_iv)
                moneyness.append(np.log(STRIKES[sym] / S))
                valid_syms.append(sym)

        if len(valid_syms) < 3:
            return result, conversions, json.dumps(saved)

        coeffs = np.polyfit(moneyness, iv_data, deg=2)
        
        # Determine current portfolio delta
        net_delta = 0.0
        for sym in OPTION_SYMS:
            pos = state.position.get(sym, 0)
            if pos != 0:
                fair_iv = np.polyval(coeffs, np.log(STRIKES[sym] / S))
                net_delta += bs_delta(S, STRIKES[sym], T, 0.0, fair_iv, 'call') * pos

        # ── 5. Trading Logic (Making + Taking) ───────────────────────────────
        for sym in OPTION_SYMS:
            K = STRIKES[sym]
            fair_iv = np.polyval(coeffs, np.log(K / S))
            fair_p = bs_price(S, K, T, 0.0, fair_iv, 'call')
            pos = state.position.get(sym, 0)
            orders = []

            # A. Taking (Hitting the book if mispriced > 1.5 tick)
            od = state.order_depths.get(sym)
            b, a = self._best_bid(od), self._best_ask(od)
            
            if a and a < fair_p - 1.5:
                qty = min(20, 50 - pos)
                if qty > 0:
                    orders.append(Order(sym, int(a), qty))
                    pos += qty
                    net_delta += bs_delta(S, K, T, 0.0, fair_iv, 'call') * qty

            if b and b > fair_p + 1.5:
                qty = min(20, 50 + pos)
                if qty > 0:
                    orders.append(Order(sym, int(b), -qty))
                    pos -= qty
                    net_delta -= bs_delta(S, K, T, 0.0, fair_iv, 'call') * qty

            # B. Quoting (Placing limit orders to capture spread)
            bid_p = int(np.floor(fair_p - 0.6))
            ask_p = int(np.ceil(fair_p + 0.6))
            
            if pos < 50:
                orders.append(Order(sym, bid_p, min(10, 50 - pos)))
            if pos > -50:
                orders.append(Order(sym, ask_p, -min(10, 50 + pos)))
            
            if orders: result[sym] = orders

        # ── 6. Delta Hedge ───────────────────────────────────────────────────
        und_pos = state.position.get(UNDERLYING, 0)
        total_delta = net_delta + und_pos
        
        if abs(total_delta) > 2: # Tighter hedge band
            hedge_qty = int(np.clip(-round(total_delta), -200 - und_pos, 200 - und_pos))
            if hedge_qty != 0:
                und_od = state.order_depths.get(UNDERLYING)
                px = self._best_ask(und_od) if hedge_qty > 0 else self._best_bid(und_od)
                if px:
                    result.setdefault(UNDERLYING, []).append(Order(UNDERLYING, px, hedge_qty))

        # Save state for visibility
        saved.update({'tte': TTE_days, 'd': net_delta, 's': S})
        return result, conversions, json.dumps(saved)

    def _mid_price(self, state, sym):
        od = state.order_depths.get(sym)
        if not od: return None
        b, a = self._best_bid(od), self._best_ask(od)
        return (b + a) / 2.0 if b and a else None

    def _best_bid(self, od): return max(od.buy_orders.keys()) if od.buy_orders else None
    def _best_ask(self, od): return min(od.sell_orders.keys()) if od.sell_orders else None