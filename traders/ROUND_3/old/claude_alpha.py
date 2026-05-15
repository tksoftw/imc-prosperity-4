import numpy as np
import json
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List, Tuple

# ── 1. Constants ─────────────────────────────────────────────────────────────
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

# ── Gate: both ATM strikes must be tight for surface to be reliable ──────────
GATE_SYMS = ("VEV_5200", "VEV_5300")
SPREAD_THRESHOLD = 2  # ticks

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

def bs_price(S, K, T, r, sigma, type='call'):
    if T <= 0: return max(0, S - K) if type == 'call' else max(0, K - S)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if type == 'call': return S * norm_cdf(d1) - K * np.exp(-r * T) * norm_cdf(d2)
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
    return max(sigma, 0.01)

def book_spread(od) -> float:
    if not od or not od.buy_orders or not od.sell_orders:
        return float('inf')
    return min(od.sell_orders.keys()) - max(od.buy_orders.keys())

# ── 3. Trader Class ───────────────────────────────────────────────────────────

class Trader:
    def run(self, state: TradingState) -> Tuple[Dict[str, List[Order]], int, str]:
        result: Dict[str, List[Order]] = {}
        conversions: int = 0

        try: saved = json.loads(state.traderData) if state.traderData else {}
        except: saved = {}

        TTE_days = 5.0 - (state.timestamp / 1_000_000)
        T = max(TTE_days / 365.0, 1e-5)

        S = self._mid_price(state, UNDERLYING)
        if S is None: return result, conversions, json.dumps(saved)

        # ── 4. Surface Gate (leaked signal) ──────────────────────────────────
        # Only quote when the informed bot CAN hedge — i.e. both ATM spreads tight
        gate_spreads = {sym: book_spread(state.order_depths.get(sym)) for sym in GATE_SYMS}
        surface_live = all(s <= SPREAD_THRESHOLD for s in gate_spreads.values())

        # ── 5. Surface Fit ───────────────────────────────────────────────────
        ivs, moneyness, valid_syms = [], [], []
        for sym in OPTION_SYMS:
            od = state.order_depths.get(sym)
            if not od: continue
            b, a = self._best_bid(od), self._best_ask(od)
            if b and a:
                mid_iv = implied_vol_newton((b + a) / 2.0, S, STRIKES[sym], T, 0.0, 'call')
                ivs.append(mid_iv); moneyness.append(np.log(STRIKES[sym] / S)); valid_syms.append(sym)

        if len(valid_syms) < 3: return result, conversions, json.dumps(saved)
        coeffs = np.polyfit(moneyness, ivs, deg=2)

        # ── 6. Delta Hedge (always runs — don't stop hedging just because gate is closed) ──
        net_delta = 0.0
        for sym in OPTION_SYMS:
            pos = state.position.get(sym, 0)
            if pos != 0:
                fair_iv = np.polyval(coeffs, np.log(STRIKES[sym] / S))
                net_delta += bs_delta(S, STRIKES[sym], T, 0.0, fair_iv, 'call') * pos

        und_pos = state.position.get(UNDERLYING, 0)
        total_delta = net_delta + und_pos

        if abs(total_delta) > 1.0:
            hedge_qty = int(np.clip(-round(total_delta), -200 - und_pos, 200 - und_pos))
            if hedge_qty != 0:
                und_od = state.order_depths.get(UNDERLYING)
                px = self._best_ask(und_od) if hedge_qty > 0 else self._best_bid(und_od)
                if px: result[UNDERLYING] = [Order(UNDERLYING, px, hedge_qty)]

        # ── 7. Quoting Logic (gated) ──────────────────────────────────────────
        if surface_live:
            for sym in OPTION_SYMS:
                K = STRIKES[sym]
                fair_iv = np.polyval(coeffs, np.log(K / S))
                fair_p = bs_price(S, K, T, 0.0, fair_iv, 'call')
                pos = state.position.get(sym, 0)

                bid_p, ask_p = int(round(fair_p - 1)), int(round(fair_p + 1))

                orders = []
                if pos < 50:  orders.append(Order(sym, bid_p, min(15, 50 - pos)))
                if pos > -50: orders.append(Order(sym, ask_p, -min(15, 50 + pos)))
                if orders: result[sym] = orders

        # ── 8. DEBUG DASHBOARD ───────────────────────────────────────────────
        if state.timestamp % 2000 == 0:
            print(f"\n--- [DASHBOARD @ {state.timestamp}] ---")
            print(f"MARKET: Underlyer Mid: {S:.2f} | TTE: {TTE_days:.4f}")
            print(f"GATE: 5200_spread={gate_spreads['VEV_5200']} | 5300_spread={gate_spreads['VEV_5300']} | LIVE={surface_live}")
            print(f"RISK: OptionDelta: {net_delta:.2f} | UndPos: {und_pos} | Total: {total_delta:.2f}")
            print(f"FITTING: PolyCoeffs: {[round(c, 5) for c in coeffs]} | DataPoints: {len(valid_syms)}")
            test_sym = "VEV_5000"
            if test_sym in state.order_depths:
                test_p = bs_price(S, STRIKES[test_sym], T, 0.0, np.polyval(coeffs, np.log(STRIKES[test_sym]/S)), 'call')
                print(f"SAMPLE {test_sym}: MktMid: {self._mid_price(state, test_sym)} | Fair: {test_p:.2f}")

        return result, conversions, json.dumps(saved)

    def _mid_price(self, state, sym):
        od = state.order_depths.get(sym); b, a = self._best_bid(od), self._best_ask(od)
        return (b + a) / 2.0 if b and a else None

    def _best_bid(self, od): return max(od.buy_orders.keys()) if od and od.buy_orders else None
    def _best_ask(self, od): return min(od.sell_orders.keys()) if od and od.sell_orders else None