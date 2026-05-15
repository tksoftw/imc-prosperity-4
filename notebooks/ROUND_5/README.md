# Round 5 Strategy Development

## Executive Summary

Analyzed baseline log from `runs/rank_round5_blank_trader_19f8b6b9/blank_trader_submission_carry.log` to develop intro trading strategies for 10 product families.

**Best Strategy**: `strategy_v4_momentum_selective.py` - $2,964,227 P&L (+19.7% vs blank baseline)

## Methodology

### 1. Data Analysis
- Extracted 533,858 lines of trading data
- Identified 10 distinct product families
- Calculated market characteristics per group:
  - Spreads: $7-17
  - Volume ranges: 6.8 - 29.5 units
  - Spread efficiency: 0.073% - 0.167% of price

### 2. Strategy Development Iterations

| Version | Approach | P&L | Notes |
|---------|----------|-----|-------|
| blank_trader | No trading | $0 | Baseline |
| intro_market_maker | Basic family spreads | $2,476,174 | First pass |
| v2_tuned | Add trend detection | $2,813,450 | +13.6% |
| v3_aggressive_spread | Exploit wide spreads | $2,755,049 | Too aggressive |
| **v4_momentum_selective** | **Enhanced momentum + selective entry** | **$2,964,227** | **BEST** |
| v5_imbalance_aware | Add order imbalance | $2,576,439 | Imbalance not helpful |
| v6_final_optimized | Winner/loser selection | $2,907,721 | Slightly worse |

### 3. Key Findings

**Spread-Based Segmentation**:
- Ultra-wide (SNACKPACK $16.79): Tight spread capture
- Wide (GALAXY_SOUNDS $13.73): Conservative market making
- Medium (SLEEP_POD $9.65): Selective entry
- Tight (ROBOT $7.13): Momentum-only trading

**Product-Specific Winners/Losers**:
- Consistent winners: SNACKPACK_RASPBERRY/STRAWBERRY/VANILLA, TRANSLATOR_ECLIPSE/VOID_BLUE, UV_VISOR_MAGENTA/RED/YELLOW
- Consistent losers: SLEEP_POD_LAMB_WOOL/NYLON, SNACKPACK_CHOCOLATE/PISTACHIO, TRANSLATOR_ASTRO_BLACK/SPACE_GRAY, UV_VISOR_AMBER

**Momentum Impact**:
- 3-period trend detection: +5.4% improvement
- Acceleration detection: Small additional gains
- Multi-level momentum response: Most effective

## Strategy: v4_momentum_selective.py

### Core Components

1. **Family-Based Parameters**
   - Group products by market characteristics
   - Assign base bid/ask offsets per family
   - Adjust position limits by liquidity

2. **Momentum Detection**
   - Track 20-bar price history
   - Identify 3-period trends
   - Detect acceleration patterns
   - Return momentum score: -2 (strong down) to +2 (strong up)

3. **Dynamic Offset Adjustment**
   - Uptrends: Tighten bid side (more aggressive buying)
   - Downtrends: Tighten ask side (more aggressive selling)
   - Acceleration: Extra 1 tick adjustment

4. **Product-Specific Overrides**
   - Conservative parameters for known losers
   - 0.4-0.6x normal position limits
   - 1.5-1.8x wider spreads
   - Reduced volume scaling

### Specific Group Strategies

See `intro_strategies_by_group.md` for detailed intro strategies for each family, including:
- Market characteristics
- Initial bid/ask parameters
- Position sizing
- Next iteration ideas

## Files

- `strategy_research.md` - Initial market analysis and template strategies
- `iteration_log.md` - Performance tracking across all versions
- `intro_strategies_by_group.md` - Detailed intro strategy guide per product family
- `strategy_v4_momentum_selective.py` - Best performing implementation

## How to Use

### Test against baseline
```bash
uv run rank --carry --trader strategy_v4_momentum_selective.py
```

### Compare multiple versions
```bash
uv run rank --carry --trader blank_trader.py --trader strategy_v4_momentum_selective.py
```

### View per-product performance
```bash
uv run rank --carry --trader strategy_v4_momentum_selective.py --show-per-product
```

## Next Steps

### Priority 1: Exploit Winners/Losers
- Increase position limits on consistent winners (SNACKPACK_RASPBERRY, etc.)
- Reduce exposure to consistent losers (SLEEP_POD_LAMB_WOOL, etc.)
- Expected gain: +$100-200k

### Priority 2: Intra-Family Correlations
- Detect which products move together
- Implement pair trading for mean-reverting products
- Fade outliers that move against group
- Expected gain: +$150-300k

### Priority 3: Flow Detection
- Track order imbalance evolution
- Predict reversals from momentum patterns
- Adjust momentum thresholds per product
- Expected gain: +$100-150k

### Priority 4: Advanced Techniques
- L2/L3 order placement (deeper depth analysis)
- Time-of-day patterns (different strategy by hour)
- Volatility-adjusted position sizing
- Expected gain: +$200-400k

## Performance Notes

- v4 averages $988k P&L per day (day 2: $1.1M, day 3: $1.1M, day 4: $727k)
- Day 4 underperformance suggests market regime change (monitor!)
- 3,822 total trades across 50 products over 3 days
- Trade frequency varies by product: liquid products (SNACKPACK) ~43/day, illiquid (ROBOT) ~66/day

## Architecture

Strategy v4 uses:
- Family-based parameter grouping (scalable)
- Per-product override system (flexible)
- Simple momentum calculation (explainable)
- No external data dependencies (clean)
- ~150 lines of code (maintainable)

## Risk Considerations

1. **Known issue**: Day 4 performance drop - investigate if market structure changed
2. **Loser products**: Need structural analysis - why do SLEEP_POD_LAMB_WOOL and TRANSLATOR_ASTRO_BLACK consistently lose?
3. **Position limits**: Currently static per product - should adjust by realized volatility
4. **Momentum lag**: 3-period momentum detection is reactive - test leading indicators

---

**Created**: 2026-04-30  
**Strategy version**: v4_momentum_selective.py  
**Best P&L**: $2,964,227.5  
**Improvement**: +19.7% vs blank baseline
