# Intro Strategies by Product Group

**Baseline**: strategy_v4_momentum_selective.py generates $2,964,227.5 P&L with carry mode.

## 1. SNACKPACK (Widest Spread: $16.79)

### Characteristics
- Ultra-wide spreads ($16.79)
- High volume (29.5 units at L1)
- Mix of winners (RASPBERRY, STRAWBERRY, VANILLA) and losers (CHOCOLATE, PISTACHIO)

### Strategy Intro
**Simple Spread Harvester**: Place tight bids/asks in middle of wide spread
- Bid at mid-2, Ask at mid+2
- Scale position limits by volume: 100-120 units max
- Apply momentum overlay to shift spreads slightly
- **Key insight**: The spread is wide enough that even passive placement captures premium

### Next Iterations
1. Detect which flavors are winners vs losers (flavor-specific momentum)
2. Reduce exposure to losing products (CHOCOLATE, PISTACHIO)
3. Increase position limits on winning products (RASPBERRY, STRAWBERRY, VANILLA)
4. Track intra-day flavor preferences

---

## 2. GALAXY_SOUNDS (Wide Spread: $13.73)

### Characteristics
- Wide spread ($13.73)
- Good volume (18.3 units)
- Consistent winners across all variants

### Strategy Intro
**Spread + Momentum Maker**:
- Base bid -4, ask +4 (capture half the spread)
- Track 3-bar momentum to scale spreads (tighter on trends)
- Max position 100 units
- Scale volumes to 1.0x market volume

### Next Iterations
1. Test if products correlate within group (arbitrage opportunities?)
2. Add multi-timeframe momentum (5-bar, 10-bar acceleration)
3. Experiment with pair trading (if some products lead others)
4. Test trend reversal detection to fade big moves

---

## 3. OXYGEN_SHAKE (Wide Spread: $12.90)

### Characteristics
- Wide spread ($12.90)
- Good volume (18.3 units)
- Consistent profitability

### Strategy Intro
**Liquid Market Maker**:
- Similar to GALAXY_SOUNDS but slightly more aggressive
- Bid -4, ask +4
- Higher position limits (95 units)
- Monitor product-specific performance (all seem similar so far)

### Next Iterations
1. Test if specific flavors (GARLIC, MINT) correlate with external factors
2. Look for flavor-switching patterns (customers rotating)
3. Test wider spreads temporarily during low-volume periods
4. Experiment with multiple scales of orders (L1, L2, L3)

---

## 4. UV_VISOR (Wide-Medium Spread: $13.13)

### Characteristics
- Wide spread ($13.13)
- Good volume (18.3 units)
- Major variance: AMBER loses, MAGENTA/RED/YELLOW win, ORANGE mixed

### Strategy Intro
**Color-Differentiated Market Maker**:
- Winners (MAGENTA, RED, YELLOW): Standard bid-4/ask+4, max 95 pos
- Loser (AMBER): Very conservative - bid-6/ask+6, max 30 pos
- ORANGE: Moderate - bid-5/ask+5, max 50 pos

### Next Iterations
1. Detect why AMBER loses - is there a structural demand issue?
2. Test if loser colors can be hedged via other products
3. Monitor if color preferences change day-to-day
4. Test wider spreads on consistently losing colors

---

## 5. PEBBLES (Wide-Medium Spread: $12.81)

### Characteristics
- Wide spread ($12.81)
- Medium volume (12.6 units)
- All sizes profitable (L, M, S, XL, XS)

### Strategy Intro
**Simple Size-Based Maker**:
- Base bid -4, ask +4 for all sizes
- Max position 85 units
- Scale volumes to 0.95x market
- No special treatment needed - even distribution

### Next Iterations
1. Test if size preferences vary (do traders prefer specific sizes?)
2. Add volume-weighted liquidity concentration strategy
3. Test "size flow following" - if L is hot, be more aggressive on L
4. Experiment with size-pair spreads (L vs M arbitrage)

---

## 6. PANEL (Medium Spread: $9.40)

### Characteristics
- Medium spread ($9.40)
- Medium volume (12.9 units)
- All sizes profitable

### Strategy Intro
**Conservative Medium-Spread Maker**:
- Tighter spreads: bid -3, ask +3 (market is tighter)
- Max position 75 units
- Scale volumes to 0.90x market
- Monitor for overspreading (wider spreads don't work here)

### Next Iterations
1. Detect if panel types (1x2, 1x4, 2x2, etc.) have different demand patterns
2. Test if larger/smaller panels have different spread characteristics
3. Add "best effort" orders at tighter spreads (L2, L3)
4. Monitor if panel demand relates to size/cost

---

## 7. SLEEP_POD (Medium Spread: $9.65)

### Characteristics
- Medium spread ($9.65)
- Low-medium volume (11 units)
- **Major variance**: LAMB_WOOL, NYLON lose; COTTON/POLYESTER/SUEDE win

### Strategy Intro
**Material-Selective Maker**:
- Winners (COTTON, POLYESTER, SUEDE): bid-3/ask+3, max 70 pos, vol 0.9x
- Losers (LAMB_WOOL, NYLON): bid-5/ask+5, max 28 pos, vol 0.3x
- Be defensive on losing materials

### Next Iterations
1. Investigate why LAMB_WOOL and NYLON consistently lose
2. Test if there's a material quality issue or seasonal preference
3. Try hedging losing materials with winning materials
4. Test if losers perform better at different spread levels
5. Monitor if material preferences change over time

---

## 8. MICROCHIP (Lower Spread: $8.79)

### Characteristics
- Lower spread ($8.79)
- Low volume (6.8 units)
- Consistent profitability across shapes

### Strategy Intro
**Selective Low-Spread Maker**:
- Bid -2, ask +2 (can't widen - market is efficient)
- Max position 55 units (conservative on low volume)
- Scale volumes to 0.75x market
- Focus on selective entry using momentum

### Next Iterations
1. Shape-specific trading: do circles vs squares have different flows?
2. Add momentum filter - only trade on strong trends
3. Test "limit order" placement (hit bid/ask vs inside quotes)
4. Monitor if shapes correlate (rotate together or independently)

---

## 9. TRANSLATOR (Lower Spread: $8.78)

### Characteristics
- Lower spread ($8.78)
- Medium volume (11.5 units)
- Major variance: ECLIPSE/VOID_BLUE win; ASTRO_BLACK/SPACE_GRAY lose; GRAPHITE mixed

### Strategy Intro
**Color-Selective Maker**:
- Winners (ECLIPSE, VOID_BLUE): bid-2/ask+2, max 65 pos, vol 0.8x
- Losers (ASTRO_BLACK, SPACE_GRAY): bid-3/ask+3, max 24 pos, vol 0.4x
- GRAPHITE: Moderate bid-2/ask+2, max 45 pos
- Very selective on losers

### Next Iterations
1. Investigate loser colors - structural demand issue?
2. Test if losers can be traded as shorts instead
3. Add color-specific momentum (do colors trend independently?)
4. Monitor if color preferences change seasonally

---

## 10. ROBOT (Lowest Spread: $7.13)

### Characteristics
- Lowest spread ($7.13)
- Lowest volume (7.9 units)
- Consistent profitability

### Strategy Intro
**Ultra-Selective Trend Follower**:
- Bid -2, ask +2 (minimal room for spread capture)
- Max position 50 units
- Scale volumes to 0.65x market
- Strong momentum filter - only trade on clear trends

### Next Iterations
1. Task-specific flow: do robots for different tasks correlate?
2. Add momentum acceleration requirement (only scalp strong trends)
3. Test reversal detection (mean reversion play for overshoots)
4. Monitor if task demand varies (LAUNDRY vs MOPPING preference shifts)

---

## Summary: Next Priorities

### High Priority (Easy Wins)
1. **SNACKPACK flavor selection**: Separate losers from winners
2. **UV_VISOR/TRANSLATOR/SLEEP_POD color/material selection**: Reduce exposure to consistent losers
3. **Multi-timeframe momentum**: Add 5-bar and 10-bar windows for better trend detection

### Medium Priority (Structural Improvements)
1. **Correlation detection**: Pair-trade products within families
2. **Shape/color/material specific flows**: Do preferences vary?
3. **Volume-weighted scaling**: Adjust position limits by actual market volume

### Lower Priority (Optimization)
1. **Order book depth**: Use L2 and L3 for better spread capture
2. **Time-of-day patterns**: Different strategies by trading hour
3. **Reversal detection**: Fade overshoots on tight-spread products
