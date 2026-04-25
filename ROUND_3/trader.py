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

            # Only trade VELVETFRUIT_EXTRACT exactly on edges of the spread
            if deviation > 0.5 and remaining_sell > 0:
                result[velvet].append(Order(velvet, int(velvet_analysis.ask_wall), -remaining_sell))
            elif deviation < -0.5 and remaining_buy > 0:
                result[velvet].append(Order(velvet, int(velvet_analysis.bid_wall), remaining_buy))
        
        # 2. HYDROGEL_PACK Static Valuation Strategy
        if hydro_analysis.order_depth:
            hydro_true_value = 10000 
            current_pos = hydro_analysis.position
            remaining_buy = max(0, MAX_POS_HYDROGEL - current_pos)
            remaining_sell = max(0, MAX_POS_HYDROGEL + current_pos)

            for price, volume in sorted(hydro_analysis.order_depth.sell_orders.items()):
                if price < hydro_true_value - 3 and remaining_buy > 0:
                    buy_vol = min(max(0, -volume), remaining_buy)
                    if buy_vol > 0:
                        result[hydrogel].append(Order(hydrogel, price, buy_vol))
                        remaining_buy -= buy_vol
            
            for price, volume in sorted(hydro_analysis.order_depth.buy_orders.items(), reverse=True):
                if price > hydro_true_value + 3 and remaining_sell > 0:
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
                
                if vev_analysis.wall_mid is not None and vev_analysis.bid_wall is not None and vev_analysis.ask_wall is not None:
                    # Delta neutral intrinsic edge mean reversion 
                    # Deep ITM behaves entirely like the underlying. Wait for the market maker
                    # to offer stupid spreads.
                    extrinsic = vev_analysis.wall_mid - intrinsic_value
                    mean_extrinsic = get_ema(f"{vev_prod}_extrinsic", extrinsic, 50)
                    
                    diff = extrinsic - mean_extrinsic
                    
                    if diff >= 0.75 and remaining_sell > 0:
                        # Price is higher than mean extrinsic + intrinsic
                        volume = min(15, remaining_sell)
                        if vev_analysis.bid_wall > intrinsic_value:
                            result[vev_prod].append(Order(vev_prod, int(vev_analysis.bid_wall), -volume))
                            
                    elif diff <= -0.75 and remaining_buy > 0:
                        volume = min(15, remaining_buy)
                        result[vev_prod].append(Order(vev_prod, int(vev_analysis.ask_wall), volume))

        # Clean up orders dict
        result = {p: orders for p, orders in result.items() if orders}
        
        # Save state 
        trader_state['emas'] = emas
        traderData = json.dumps(trader_state)

        return result, conversions, traderData
