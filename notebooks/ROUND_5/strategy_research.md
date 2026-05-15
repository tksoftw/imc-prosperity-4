# Round 5 Strategy Research

## Market Analysis (from blank_trader baseline)

### Product Family Characteristics

1. **SNACKPACK** - *High Spread, High Volume*
   - Avg spread: $16.79 (0.167% of price)
   - L1 volume: 29.5 units
   - **Strategy**: Tight market maker - bid/ask around mid with tight spreads (1-4 ticks)
   - Opportunity: Large spread + high volume = good for capturing spread premium

2. **GALAXY_SOUNDS** - *High-Medium Spread, Good Volume*
   - Avg spread: $13.73 (0.127%)
   - L1 volume: 18.3 units
   - **Strategy**: Market maker with momentum overlay
   - Opportunity: Wide spread allows for profitable bid/ask placements

3. **UV_VISOR** - *High-Medium Spread, Good Volume*
   - Avg spread: $13.13 (0.128%)
   - L1 volume: 18.3 units
   - **Strategy**: Spread-based market making
   - Opportunity: Similar to GALAXY_SOUNDS

4. **OXYGEN_SHAKE** - *High-Medium Spread, Good Volume*
   - Avg spread: $12.90 (0.127%)
   - L1 volume: 18.3 units
   - **Strategy**: Liquid market maker
   - Opportunity: Consistent spreads suggest predictable market behavior

5. **PEBBLES** - *High-Medium Spread, Medium Volume*
   - Avg spread: $12.81 (0.128%)
   - L1 volume: 12.6 units
   - **Strategy**: Conservative market maker
   - Opportunity: Similar to above but with slightly lower volume

6. **PANEL** - *Medium Spread, Medium Volume*
   - Avg spread: $9.40 (0.096%)
   - L1 volume: 12.9 units
   - **Strategy**: Market maker + pair trading if related
   - Opportunity: Tighter spread requires efficient execution

7. **SLEEP_POD** - *Medium Spread, Low-Medium Volume*
   - Avg spread: $9.65 (0.088%)
   - L1 volume: 11.0 units
   - **Strategy**: Selective market maker
   - Opportunity: Lower volume means be more cautious with position limits

8. **MICROCHIP** - *Lower Spread, Low Volume*
   - Avg spread: $8.79 (0.089%)
   - L1 volume: 6.8 units
   - **Strategy**: Momentum + selective market making
   - Opportunity: Tight spread + low volume = focus on trend following

9. **TRANSLATOR** - *Lower Spread, Medium Volume*
   - Avg spread: $8.78 (0.089%)
   - L1 volume: 11.5 units
   - **Strategy**: Market maker for liquid moments
   - Opportunity: Most correlated within TRANSLATOR family?

10. **ROBOT** - *Lowest Spread, Lowest Volume*
    - Avg spread: $7.13 (0.073%)
    - L1 volume: 7.9 units
    - **Strategy**: Momentum/trend following
    - Opportunity: Tightest spread suggests efficient pricing; focus on directional alpha

## Strategy Templates

### Template A: Simple Market Maker
- Place bids at mid - offset (e.g., -3)
- Place asks at mid + offset (e.g., +3)
- Position limits based on volume

### Template B: Momentum Rider
- Track mid price changes
- Bid aggressively when trend is up, scale back when down
- Ask conservatively when trend is up

### Template C: Spread Harvester
- For very wide spreads, place orders closer to mid
- Capture spread premium systematically

### Template D: Volume Weighted
- Scale position size based on market volume
- High volume = larger positions, lower vol = smaller

---

## Implementation Plan
1. Start with simple market maker strategies (Templates A, B, C)
2. Test each group independently
3. Combine best approaches
4. Iterate on spread widths and volumes
