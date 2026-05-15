from datamodel import Order, Product, TradingState
from typing import Callable, Dict, List
import math
import json
from functools import wraps

def is_buy_order(order: Order) -> bool:
    return order.quantity > 0
def is_sell_order(order: Order) -> bool:
    return order.quantity < 0

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_call(S: float, K: float, TTE: float, sigma: float, r: float = 0.0) -> float:
    if TTE <= 0:
        return max(S - K, 0)
    d1 = (math.log(S/K) + (r + 0.5 * sigma**2) * TTE) / (sigma * math.sqrt(TTE))
    d2 = d1 - sigma * math.sqrt(TTE)
    return (S * norm_cdf(d1)) - (K * math.exp(-r * TTE) * norm_cdf(d2))

def bs_vega(S: float, K: float, TTE: float, sigma: float, r: float = 0.0) -> float:
    if TTE <= 0: 
        return 0.0
    d1 = (math.log(S/K) + (r + 0.5*sigma**2) * TTE) / (sigma * math.sqrt(TTE))
    return S * (math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)) * math.sqrt(TTE)

def implied_volatility(S: float, K: float, TTE: float, market_price: float, r: float = 0.0, tol: float = 1e-4, max_iter: int = 100) -> float:
    sigma = 0.15 # initial guess
    if TTE <= 0 or market_price <= max(S - K, 0): return sigma
    for _ in range(max_iter):
        price = bs_call(S, K, TTE, sigma, r)
        diff = price - market_price
        if abs(diff) < tol:
            break
        vega = bs_vega(S, K, TTE, sigma, r)
        if vega == 0:
            break
        sigma -= diff / vega
    return max(1e-4, sigma)

class ProductAnalysis:
    def __init__(self, state: TradingState, product: Product):
        self.state = state
        self.product = product
        self.order_depth = state.order_depths.get(product, None)
        self.position = state.position.get(product, 0)

    @property
    def bid_wall(self) -> int|None:
        if not self.order_depth or not self.order_depth.buy_orders: return None
        return max(self.order_depth.buy_orders.keys())

    @property
    def ask_wall(self) -> int|None:
        if not self.order_depth or not self.order_depth.sell_orders: return None
        return min(self.order_depth.sell_orders.keys())

    @property
    def wall_mid(self) -> float|None:
        bw, aw = self.bid_wall, self.ask_wall
        if bw is not None and aw is not None: return (bw + aw) / 2.0
        return None

MAX_POS_VELVET = 200
MAX_POS_HYDROGEL = 200
MAX_POS_VEV = 200

STRIKES = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500]
VEV_PRODUCTS = [f"VEV_{k}" for k in STRIKES]
PRODUCTS = ["VELVETFRUIT_EXTRACT", "HYDROGEL_PACK"] + VEV_PRODUCTS

class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {product: [] for product in PRODUCTS if product in state.listings}
        conversions = 0
        traderData = ""
        
        # Load previous traderData
        trader_state = {}
        if state.traderData:
            try:
                trader_state = json.loads(state.traderData)
            except:
                pass
        
        emas = trader_state.get('emas', {})
        
        def get_ema(key: str, value: float, window: int) -> float:
            if key not in emas or emas[key] is None:
                emas[key] = value
                return value
            
            alpha = 2.0 / (window + 1)
            emas[key] = alpha * value + (1 - alpha) * emas[key]
            return emas[key]

        velvet = "VELVETFRUIT_EXTRACT"
        hydrogel = "HYDROGEL_PACK"
        
        velvet_analysis = ProductAnalysis(state, velvet)
        hydro_analysis = ProductAnalysis(state, hydrogel)
        
        # 1. VELVETFRUIT_EXTRACT Mean Reversion Strategy
        if velvet_analysis.wall_mid is not None and velvet_analysis.bid_wall is not None and velvet_analysis.ask_wall is not None:
            # We track a short-term EMA to trade deviations against the fast-running mean
            fast_ema = get_ema(f"{velvet}_fast", velvet_analysis.wall_mid, 15)
            deviation = velvet_analysis.wall_mid - fast_ema
            
            current_pos = velvet_analysis.position
            remaining_buy = max(0, MAX_POS_VELVET - current_pos)
            remaining_sell = max(0, MAX_POS_VELVET + current_pos)

            # Market making around the EMA with a 5.0 median spread
            target_bid = math.floor(fast_ema - 2.0)
            target_ask = math.ceil(fast_ema + 2.0)

            if remaining_buy > 0:
                result[velvet].append(Order(velvet, int(target_bid), remaining_buy))
            if remaining_sell > 0:
                result[velvet].append(Order(velvet, int(target_ask), -remaining_sell))
        
        # 2. HYDROGEL_PACK Static Valuation Strategy
        if hydro_analysis.order_depth:
            hydro_true_value = 10000 
            current_pos = hydro_analysis.position
            remaining_buy = max(0, MAX_POS_HYDROGEL - current_pos)
            remaining_sell = max(0, MAX_POS_HYDROGEL + current_pos)

            # 16.0 median spread -> true edge is around +/- 8
            for price, volume in sorted(hydro_analysis.order_depth.sell_orders.items()):
                if price <= hydro_true_value - 7.0 and remaining_buy > 0:
                    buy_vol = min(max(0, -volume), remaining_buy)
                    if buy_vol > 0:
                        result[hydrogel].append(Order(hydrogel, price, buy_vol))
                        remaining_buy -= buy_vol
            
            for price, volume in sorted(hydro_analysis.order_depth.buy_orders.items(), reverse=True):
                if price >= hydro_true_value + 7.0 and remaining_sell > 0:
                    sell_vol = min(volume, remaining_sell)
                    if sell_vol > 0:
                        result[hydrogel].append(Order(hydrogel, price, -sell_vol))
                        remaining_sell -= sell_vol

        # 3. IV Scalping Strategy for VEV Options
        if velvet_analysis.wall_mid is not None:
            for k in STRIKES:
                vev_prod = f"VEV_{k}"
                if vev_prod not in state.listings:
                    continue
                
                vev_analysis = ProductAnalysis(state, vev_prod)
                current_pos = vev_analysis.position
                remaining_buy = max(0, MAX_POS_VEV - current_pos)
                remaining_sell = max(0, MAX_POS_VEV + current_pos)
                
                intrinsic_value = max(velvet_analysis.wall_mid - k, 0)
                
                # Explicit risk-free arbitrage on negative extrinsic value
                if vev_analysis.order_depth is not None:
                    # Buy opportunities
                    for price, volume in sorted(vev_analysis.order_depth.sell_orders.items()):
                        # If option price < intrinsic value, that is risk free money. 
                        # Account for transaction friction, say 1 seashell
                        if price <= intrinsic_value - 1 and remaining_buy > 0:
                            buy_vol = min(max(0, -volume), remaining_buy)
                            if buy_vol > 0:
                                result[vev_prod].append(Order(vev_prod, price, buy_vol))
                                remaining_buy -= buy_vol
                
                # Black-Scholes Pricing and Market Making
                median_spreads = {
                    4000: 21.0, 4500: 16.0, 5000: 6.0, 5100: 4.0, 5200: 3.0,
                    5300: 2.0, 5400: 1.0, 5500: 1.0, 6000: 1.0, 6500: 1.0
                }
                vev_spread = median_spreads.get(k, 2.0)
                
                if vev_analysis.wall_mid is not None:
                    # Calculate implied vol from current market price
                    TTE = 1.0  # Approx 1 year logic assumed to stabilize the model
                    current_iv = implied_volatility(velvet_analysis.wall_mid, k, TTE, vev_analysis.wall_mid)
                    
                    # Smooth the IV to avoid reacting to noise
                    smoothed_iv = get_ema(f"iv_{vev_prod}", current_iv, 50)
                    
                    # Calculate fair price with smoothed IV
                    fair_price = bs_call(velvet_analysis.wall_mid, k, TTE, smoothed_iv)
                    
                    target_bid = math.floor(fair_price - (vev_spread / 2.0) + 0.1)
                    target_ask = math.ceil(fair_price + (vev_spread / 2.0) - 0.1)
                    
                    # Only quote if we aren't crossing the spread unless strongly mispriced
                    if remaining_buy > 0:
                        result[vev_prod].append(Order(vev_prod, int(target_bid), remaining_buy))
                    if remaining_sell > 0:
                        result[vev_prod].append(Order(vev_prod, int(target_ask), -remaining_sell))

        # Clean up orders dict
        result = {p: orders for p, orders in result.items() if orders}
        
        # Save state 
        trader_state['emas'] = emas
        traderData = json.dumps(trader_state)

        return result, conversions, traderData
