# Strategy Iteration Log

## Baseline Comparison
| Strategy | P&L | Improvement |
|----------|-----|------------|
| blank_trader.py | $0 | - |
| intro_market_maker.py | $2,476,174 | Baseline |
| strategy_v2_tuned.py | $2,813,450 | +$337k (+13.6%) |
| strategy_v3_aggressive_spread.py | $2,755,049 | -$58k vs v2 |
| strategy_v4_momentum_selective.py | $2,964,227.5 | +$150k (+5.4% vs v2, +19.7% vs baseline) |
| strategy_v5_imbalance_aware.py | $2,576,439.5 | -$387k vs v4 (imbalance not helpful) |

## Strategy Analysis

### intro_market_maker.py
- Simple market maker with family-based offsets
- Fixed bid/ask spreads per family
- Good baseline performance

### strategy_v2_tuned.py (BEST SO FAR)
- Adds trend detection (momentum overlay)
- Per-product overrides for losing products
- Dynamic offset scaling based on momentum
- **Result: +13.6% improvement**

### strategy_v3_aggressive_spread.py
- Focus on exploiting wide spreads with tighter placements
- More aggressive position limits
- Slightly underperforms v2
- **Issue: May be too aggressive on tight-spread products**

## Winners & Losers Analysis

### Strong Performers (from v4)
- GALAXY_SOUNDS products (consistent profits)
- MICROCHIP products (tight spreads but profitable)
- OXYGEN_SHAKE products (wide spreads, good volume)
- PANEL, PEBBLES, ROBOT products (moderate profitability)
- SNACKPACK_RASPBERRY, STRAWBERRY, VANILLA (winners)
- TRANSLATOR_ECLIPSE_CHARCOAL, VOID_BLUE (winners)
- UV_VISOR_MAGENTA, RED, YELLOW (winners)

### Problem Products (negative P&L)
- SLEEP_POD_LAMB_WOOL (consistent loss)
- SLEEP_POD_NYLON (consistent loss)
- SNACKPACK_CHOCOLATE, PISTACHIO (losers)
- TRANSLATOR_ASTRO_BLACK, SPACE_GRAY (losers)
- UV_VISOR_AMBER, ORANGE (losers)

## Final Strategy Recommendation

**strategy_v4_momentum_selective.py** is the best iteration:
- **Total P&L: $2,964,227.5**
- +19.7% improvement over baseline blank_trader
- Combines momentum detection with selective market making
- Per-product conservative adjustments for problem products
- Dynamic spread adjustment based on trend strength

## Key Insights
1. **Momentum detection is crucial** (+13.6% from v2)
2. **Product-specific tuning is essential** - can't use one-size-fits-all
3. **Conservative entry on low-spread products** improves results
4. **Order book imbalance did not add value** (v5 underperformed)
5. **Position scaling by momentum** is more effective than static positions
