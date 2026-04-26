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

"""
This does what you think it does.
"""
def bs_price(S, K, T, r, sigma, option_type='call'):
    d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    d2 = d1 - sigma*np.sqrt(T)
    if option_type == 'call':
        return S*norm.cdf(d1) - K*np.exp(-r*T)*norm.cdf(d2)
    else:
        return K*np.exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)


"""
Given the objective function f, with bounds a and b, what number gives f(x)=0
"""
def brentq(f, a, b, tol=1e-6, max_iter=100):
    fa, fb = f(a), f(b)
    if fa * fb > 0:
        raise ValueError("Root not bracketed")
    
    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa
    
    c, fc = a, fa
    mflag = True
    s = 0
    
    for _ in range(max_iter):
        if abs(b - a) < tol:
            return b
            
        if fa != fc and fb != fc:  # inverse quadratic interpolation
            s = (a*fb*fc/((fa-fb)*(fa-fc)) + 
                 b*fa*fc/((fb-fa)*(fb-fc)) + 
                 c*fa*fb/((fc-fa)*(fc-fb)))
        else:  # secant method
            s = b - fb*(b-a)/(fb-fa)
        
        # conditions to fall back to bisection
        cond1 = not ((3*a+b)/4 < s < b or b < s < (3*a+b)/4)
        cond2 = mflag and abs(s-b) >= abs(b-c)/2
        cond3 = not mflag and abs(s-b) >= abs(c-d)/2
        
        if cond1 or cond2 or cond3:
            s = (a+b)/2  # bisection
            mflag = True
        else:
            mflag = False
        
        fs = f(s)
        d, c, fc = c, b, fb
        
        if fa*fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        
        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa
    
    return b


def implied_vol(market_price, S, K, T, r, option_type='call'):
    objective = lambda sigma: bs_price(S, K, T, r, sigma, option_type) - market_price
    return brentq(objective, 1e-6, 10.0)  # search between 0% and 1000% vol


class Trader:
    def __init__(self):
        self.round_number = 0

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
         
        # fit a quadratic to implied vols across strikes (simplest smile model)
        # moneyness = log(K/S)

        
        moneyness = np.log(strikes / S)
        implied_vols = [implied_vol(price, S, K, T, r) for K, price in zip(strikes, prices)]

        coeffs = np.polyfit(moneyness, implied_vols, deg=2)
        fitted_vol = np.polyval(coeffs, moneyness)

        # residual = where each option sits vs the smooth surface
        residuals = implied_vols - fitted_vol  # in vol space
        z_score = residuals  # already vol-normalized, just set threshold in vol units

        