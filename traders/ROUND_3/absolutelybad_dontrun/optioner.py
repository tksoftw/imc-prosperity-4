import numpy as np
from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List

def norm_cdf(x: float) -> float:
    """
    Abramowitz & Stegun approximation — max error 1.5e-7, plenty good enough.
    """
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


def brentq(f, a: float, b: float, tol: float = 1e-6, max_iter: int = 50) -> float:
    """
    Brent's method root finder. Finds x in [a,b] such that f(x) == 0.
    Raises ValueError if f(a) and f(b) do not bracket a root.
    """
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        raise ValueError("f(a) and f(b) must have opposite signs")
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa

    c, fc = a, fa
    mflag = True
    s = b
    d = 0.0

    for _ in range(max_iter):
        if abs(b - a) < tol:
            break
        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa - fb) * (fa - fc))
               + b * fa * fc / ((fb - fa) * (fb - fc))
               + c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)

        cond1 = not ((3 * a + b) / 4 < s < b or b < s < (3 * a + b) / 4)
        cond2 = mflag and abs(s - b) >= abs(b - c) / 2
        cond3 = not mflag and abs(s - b) >= abs(c - d) / 2

        if cond1 or cond2 or cond3:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False

        fs = f(s)
        d, c, fc = c, b, fb

        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs

        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa

    return b


# ── black-scholes ─────────────────────────────────────────────────────────────

def bs_call(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm_cdf(d1) - K * norm_cdf(d2))


def bs_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = (np.log(S / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    return float(norm_cdf(d1))


def implied_vol(market_price: float, S: float, K: float, T: float) -> float:
    intrinsic = max(S - K, 0.0)
    if market_price <= intrinsic + 1e-6 or market_price >= S:
        return float("nan")
    try:
        return brentq(lambda sigma: bs_call(S, K, T, sigma) - market_price, 1e-6, 10.0)
    except Exception:
        return float("nan")


# ── order book helpers ────────────────────────────────────────────────────────

def mid(od: OrderDepth):
    if not od.buy_orders or not od.sell_orders:
        return None
    return (max(od.buy_orders) + min(od.sell_orders)) / 2.0

def best_bid(od: OrderDepth):
    return max(od.buy_orders) if od.buy_orders else None

def best_ask(od: OrderDepth):
    return min(od.sell_orders) if od.sell_orders else None


# ── constants ─────────────────────────────────────────────────────────────────

VOUCHERS      = [5000, 5100, 5200, 5300, 5400, 5500]
SMILE_STRIKES = [5000, 5100, 5200, 5300, 5500]   # exclude 5400 from smile fit
TARGET_STRIKE = 5400

TOLERANCE       = 1.5
DELTA_THRESHOLD = 15
MAX_VOUCHER_POS = 280
MAX_VEV_POS     = 190
TRADE_SIZE      = 10
HYDRO_EDGE      = 4.0
HYDRO_SIZE      = 4

ROUND_TO_TTE = {1: 7, 2: 6, 3: 5, 4: 4, 5: 3, 6: 2, 7: 1}


# ── trader ────────────────────────────────────────────────────────────────────

class Trader:
    def __init__(self):
        self.round_number = 0

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        self.round_number += 1
        orders: Dict[str, List[Order]] = {}

        tte = ROUND_TO_TTE.get(self.round_number, 5)
        T   = tte / 365.0
        od  = state.order_depths
        pos = state.position

        # options
        vev_od = od.get("VELVETFRUIT_EXTRACT")
        S = mid(vev_od) if vev_od else None

        if S is not None and tte > 1:
            opt, hedge = self._options_strategy(S, T, tte, od, pos)
            for k, v in opt.items():
                orders.setdefault(k, []).extend(v)
            for k, v in hedge.items():
                orders.setdefault(k, []).extend(v)

        # hydrogel mm
        hydro_od = od.get("HYDROGEL_PACK")
        if hydro_od:
            orders["HYDROGEL_PACK"] = self._hydrogel_mm(
                hydro_od, pos.get("HYDROGEL_PACK", 0)
            )

        return orders, 0, ""

    def _options_strategy(self, S, T, tte, od, pos):
        # fit smile on all strikes except 5400
        ivs, moneyness = {}, {}
        for K in SMILE_STRIKES:
            v_od = od.get(f"VEV_{K}")
            if v_od is None:
                continue
            mp = mid(v_od)
            if mp is None:
                continue
            iv = implied_vol(mp, S, K, T)
            if iv != iv or iv > 1.0 or iv < 0.05:
                continue
            m = np.log(K / S) / np.sqrt(tte)
            ivs[K], moneyness[K] = iv, m

        if len(ivs) < 3:
            return {}, {}

        m_arr  = np.array([moneyness[k] for k in ivs])
        iv_arr = np.array([ivs[k] for k in ivs])
        coeffs = np.polyfit(m_arr, iv_arr, deg=2)

        m_target   = np.log(TARGET_STRIKE / S) / np.sqrt(tte)
        iv_fair    = float(np.polyval(coeffs, m_target))
        if iv_fair <= 0:
            return {}, {}

        fair_price = bs_call(S, TARGET_STRIKE, T, iv_fair)
        target_od  = od.get(f"VEV_{TARGET_STRIKE}")
        if target_od is None:
            return {}, {}

        current_pos   = pos.get(f"VEV_{TARGET_STRIKE}", 0)
        option_orders: Dict[str, List[Order]] = {}

        ask = best_ask(target_od)
        if ask is not None and (fair_price - ask) > TOLERANCE:
            size = min(MAX_VOUCHER_POS - current_pos, TRADE_SIZE)
            if size > 0:
                option_orders[f"VEV_{TARGET_STRIKE}"] = [
                    Order(f"VEV_{TARGET_STRIKE}", ask, size)
                ]

        bid = best_bid(target_od)
        if bid is not None and (bid - fair_price) > TOLERANCE:
            size = min(MAX_VOUCHER_POS + current_pos, TRADE_SIZE)
            if size > 0:
                option_orders[f"VEV_{TARGET_STRIKE}"] = [
                    Order(f"VEV_{TARGET_STRIKE}", bid, -size)
                ]

        # net delta across whole voucher book
        net_delta = 0.0
        for K in VOUCHERS:
            p = pos.get(f"VEV_{K}", 0)
            if p == 0:
                continue
            m_k  = np.log(K / S) / np.sqrt(tte)
            iv_k = max(float(np.polyval(coeffs, m_k)), 0.01)
            net_delta += p * bs_delta(S, K, T, iv_k)

        # hedge VEV if drift too large
        hedge_orders: Dict[str, List[Order]] = {}
        current_vev    = pos.get("VELVETFRUIT_EXTRACT", 0)
        total_exposure = net_delta + current_vev

        if abs(total_exposure) > DELTA_THRESHOLD:
            hedge_qty = int(np.clip(
                -round(total_exposure),
                -MAX_VEV_POS - current_vev,
                 MAX_VEV_POS - current_vev
            ))
            if hedge_qty != 0:
                vev_od = od.get("VELVETFRUIT_EXTRACT")
                if vev_od:
                    px = best_ask(vev_od) if hedge_qty > 0 else best_bid(vev_od)
                    if px is not None:
                        hedge_orders["VELVETFRUIT_EXTRACT"] = [
                            Order("VELVETFRUIT_EXTRACT", px, hedge_qty)
                        ]

        return option_orders, hedge_orders

    def _hydrogel_mm(self, od: OrderDepth, current_pos: int) -> List[Order]:
        fair = mid(od)
        if fair is None:
            return []

        our_bid   = round(fair - HYDRO_EDGE)
        our_ask   = round(fair + HYDRO_EDGE)
        buy_size  = min(HYDRO_SIZE, 200 - current_pos)
        sell_size = min(HYDRO_SIZE, 200 + current_pos)

        out = []
        # if buy_size  > 0: out.append(Order("HYDROGEL_PACK", our_bid,  buy_size))
        # if sell_size > 0: out.append(Order("HYDROGEL_PACK", our_ask, -sell_size))
        return out