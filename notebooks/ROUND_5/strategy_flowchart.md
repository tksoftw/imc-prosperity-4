# Strategy v4 Flow Chart

```
For each product in market:

1. IDENTIFY FAMILY
   └─ Map product to one of 10 families
      (SNACKPACK, GALAXY_SOUNDS, UV_VISOR, etc.)

2. EXTRACT MARKET DATA
   ├─ Best bid price & volume
   ├─ Best ask price & volume
   └─ Calculate mid price = (bid + ask) / 2

3. CALCULATE MOMENTUM (last 20 bars)
   ├─ Compare recent 3-bar avg to older 3-bar avg
   ├─ If trend exists, check for acceleration
   │  (6-bar momentum difference)
   └─ Return: -2, -1, 0, 1, 2

4. GET BASE PARAMETERS from family
   ├─ base_bid_offset (e.g., -3 for GALAXY_SOUNDS)
   ├─ base_ask_offset (e.g., +3)
   ├─ max_position (e.g., 80 units)
   └─ vol_scale (e.g., 0.9x market volume)

5. CHECK FOR PROBLEM PRODUCTS
   ├─ If known loser product (SLEEP_POD_LAMB_WOOL, etc.)
   │  ├─ Reduce max_pos to 40% of normal
   │  ├─ Reduce vol_scale to 40%
   │  └─ Widen spreads by 1.6x (bid-5, ask+5)
   └─ Otherwise use family defaults

6. APPLY MOMENTUM ADJUSTMENT
   ├─ If momentum >= 1 (uptrend)
   │  └─ Tighten bid (more aggressive buying): bid_offset -= 1
   │
   ├─ If momentum <= -1 (downtrend)
   │  └─ Tighten ask (more aggressive selling): ask_offset += 1
   │
   └─ Otherwise: keep base offsets

7. CALCULATE ACTUAL PRICES
   ├─ bid_price = floor(mid) + bid_offset
   └─ ask_price = floor(mid) + ask_offset

8. CALCULATE VOLUMES
   ├─ bid_vol = market_bid_volume × vol_scale
   └─ ask_vol = market_ask_volume × vol_scale

9. CHECK POSITION LIMITS
   ├─ If current_pos < max_pos
   │  └─ Place BID order (buy) at bid_price for bid_vol
   │
   └─ If current_pos > -max_pos
      └─ Place ASK order (sell) at ask_price for ask_vol

10. RETURN
    └─ All orders for all products
```

## Decision Examples

### Example 1: SNACKPACK_RASPBERRY (Winner, Wide Spread)
```
Family: SNACKPACK
Base params: bid=-2, ask=+2, max_pos=100, vol=1.1x
Is loser product? NO
Momentum detected: +1 (uptrend)

Adjust: bid = -2 - 1 = -3 (more aggressive)
        ask = +2 (unchanged)

Mid = 10000
bid_price = 9997 (10000-3)
ask_price = 10002 (10000+2)

Place:
  - BUY 35 units @ $9997
  - SELL 35 units @ $10002
```

### Example 2: SLEEP_POD_LAMB_WOOL (Loser, Medium Spread)
```
Family: SLEEP_POD
Base params: bid=-3, ask=+3, max_pos=70, vol=0.9x
Is loser product? YES
Problem override: max_pos_mult=0.6, vol_mult=0.5, offset_mult=1.5

Adjust params:
  max_pos = 70 × 0.6 = 42
  vol = 0.9 × 0.5 = 0.45x
  bid = -3 × 1.5 = -5 (rounded)
  ask = +3 × 1.5 = +5 (rounded)

Momentum detected: 0 (flat)
No momentum adjustment needed

Mid = 10000
bid_price = 9995 (10000-5)
ask_price = 10005 (10000+5)

Place:
  - BUY 20 units @ $9995 (much smaller, wider spread)
  - SELL 20 units @ $10005 (conservative approach)
```

### Example 3: ROBOT_VACUUMING (Tight Spread, Low Volume)
```
Family: ROBOT
Base params: bid=-2, ask=+2, max_pos=50, vol=0.65x
Is loser product? NO
Momentum detected: +2 (strong uptrend!)

Adjust: bid = -2 - 1 = -3 (aggressive on bid)
        ask = +2 (unchanged - already on bid side)

Mid = 10000
bid_price = 9997
ask_price = 10002

Place:
  - BUY 30 units @ $9997 (momentum play, tighter spread)
  - SELL 30 units @ $10002
```

## Key Insights Encoded

1. **Wide spread products (SNACKPACK)** → Use tight placement to harvest spread
2. **Problem products (LAMB_WOOL)** → Be defensive: wider spreads, smaller positions
3. **Tight spread products (ROBOT)** → Use momentum as trigger, not spread
4. **Uptrend momentum** → Aggress on bid (buy side)
5. **Downtrend momentum** → Aggress on ask (sell side)
6. **Flat markets** → Use base family parameters unchanged

## Scalability

This strategy scales to new products by:
1. Computing market characteristics (spread, volume)
2. Grouping by similarity
3. Assigning to appropriate family parameters
4. Adding product overrides if it's a known outlier
5. Monitoring performance to update overrides

No retraining required - it's rules-based.
