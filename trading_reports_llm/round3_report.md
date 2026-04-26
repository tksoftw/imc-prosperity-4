# From conclusions.ipynb

# Round 3 — Products, Relationships & Conclusions

This notebook explains each instrument, quantifies the relationships between them, and draws trading conclusions from the data.

---
## Part 1 — What are the products?

Round 3 contains **3 instrument types**:

| Instrument | Type | Typical mid | Notes |
|---|---|---|---|
| `VELVETFRUIT_EXTRACT` | **Underlying spot** | ~5 250 | Mean-reverting, σ ≈ 14 ticks/day |
| `HYDROGEL_PACK` | **Independent spot** | ~10 000 | No mechanical link to VELVET |
| `VEV_K` (10 strikes) | **European call options** on VELVET | 0.5 – 1 250 | Strike K; expire end of round |

The **VEV options** follow the payoff:  `C = max(S − K, 0)`  at expiry, so the mid-price today reflects *intrinsic value + time value (extrinsic)*.

### Strike ladder & moneyness

With VELVET ≈ 5 250, the strikes split into three zones:

```
Deep ITM ←─────────────────────────────────→ Deep OTM
 VEV_4000  VEV_4500  VEV_5000  VEV_5100  VEV_5200  VEV_5300  VEV_5400  VEV_5500  VEV_6000  VEV_6500
  Δ≈1        Δ≈1      Δ≈0.9     Δ≈0.8     Δ≈0.7     Δ≈0.5     Δ≈0.3     Δ≈0.2     Δ≈0        Δ≈0
```

---
## Part 2 — VELVETFRUIT_EXTRACT (the underlying)

**Behaviour:** mean-reverting around ~5 250 with tight bid-ask of exactly **5 ticks** every day. Short-term autocorrelation suggests market makers quote a stable range. No structural drift across the 3 days.

---
## Part 3 — HYDROGEL_PACK (the independent instrument)

**Return correlation with VELVET: ≈ 0.01 (effectively zero).** These two instruments have no direct price link — they move from independent information flows.

The ratio `HYDROGEL / VELVET ≈ 1.90` is stable (std ≈ 0.008) but that's just a numerical coincidence from their absolute price levels — not a synthetic link. There is **no exploitable pair-trade** between them from the data alone.

Bid-ask spread is 16 ticks (vs 5 for VELVET) — a wider, thinner market.

---
## Part 4 — The VEV Call Options

### What they are

`VEV_K` is a **European-style call option** on VELVETFRUIT_EXTRACT with strike `K`. Their theoretical price is:

$$C = \underbrace{\max(S - K,\; 0)}_{\text{intrinsic}} + \underbrace{\text{TV}}_{\text{extrinsic / time value}}$$

Time value decays to zero at expiry, so options bought at a premium over intrinsic lose that premium as the round progresses.

### Key zones in the ladder

| Zone | Strikes | Extrinsic | Spread | Behaviour |
|---|---|---|---|---|
| Deep ITM | 4000, 4500 | ≈ 0 | 16–21 ticks | Tracks underlying 1-for-1; wide spread |
| Near/ATM | 5000–5300 | 3–51 ticks | 2–6 ticks | Max time-value; tightest spreads relative to premium |
| OTM | 5400, 5500 | 6–18 ticks | 1 tick | Small extrinsic, cheapest to trade |
| Dead OTM | 6000, 6500 | 0.5 (floor) | 1 tick | Zero real value, minimum-quoted |

### Time decay across days

Extrinsic value falls monotonically Day 0 → Day 2 — exactly what theta-decay looks like.

### Option–underlying co-movement (delta)

The effective **delta** of each VEV option (sensitivity to moves in the underlying) decreases as the strike moves OTM. Deep-ITM options move almost tick-for-tick with VELVET; OTM options barely budge.

---
## Part 5 — Are VEVs fairly priced? (No-arbitrage check)

For deep-ITM options the **lower bound** is `max(S−K, 0)`. Any time the mid drops *below* intrinsic we have a textbook arbitrage: buy the call, exercise immediately.

Below we plot `C − intrinsic` (must be ≥ 0 at all times). Negative values signal mispricings that can be traded.

---
## Part 6 — Spread as a fraction of option value

Absolute spread means little on its own — what matters is **spread / mid** (the round-trip cost as a percentage of the option's value). OTM options look cheap in absolute spread, but may be expensive per-dollar-of-option.

---
## Part 7 — Market structure summary diagram

One visual that shows all relationships at a glance.

---
## Part 8 — Key Conclusions

### 1. Product taxonomy
- **VELVETFRUIT_EXTRACT** — mean-reverting spot with tight 5-tick spread. The foundational instrument to trade.
- **HYDROGEL_PACK** — fully independent. No signal from VELVET. Trade on its own mean-reversion.
- **VEV options** — 10 European calls expiring at the end of Round 3.

### 2. Option market structure
- Deep-ITM calls (4000, 4500) are **delta-1 proxies** for VELVET — pure directional exposure, no extrinsic risk, but expensive to trade (16–21 tick spread).
- ATM zone (5000–5300) carries **the most time value** and tightest *relative* spread. This is where options are most interesting.
- OTM calls (5400, 5500) are cheap in spread (1 tick) but carry small and decaying extrinsic.
- Deep-OTM (6000, 6500) are floor-valued at 0.5 — treat as worthless.

### 3. Time decay is real and measurable
Extrinsic value declines Day 0 → Day 1 → Day 2 across every strike. Options sellers collect theta; buyers pay for gamma.

### 4. Potential mispricings
Deep-ITM calls occasionally print **below intrinsic** (extrinsic < 0). This is a risk-free arb: buy the call at the ask (< intrinsic), and it must converge upward or can be exercised for immediate profit.

### 5. Spread cost reality check
- OTM options look cheap in ticks but have 10–50%+ relative spread → hard to profit from directional trades.
- ATM options (5200, 5300) have 2–3 tick spread on a 50–100 tick option → more tractable.
- VELVET at 5 ticks on a 5250 price = 0.1% round-trip — the tightest market in the round.

---

# From data_analysis_round_3.ipynb

# ROUND 3 — Data Visualization

Dataset: `data/ROUND_3/` (3 days, prices + trades).

Instruments:
- **VELVETFRUIT_EXTRACT** — underlying, ~5250
- **HYDROGEL_PACK** — separate instrument, ~10000
- **VEV_{4000..6500}** — call options on VELVETFRUIT_EXTRACT at 10 strikes

A consistent **day palette** is used across every plot so you can trace each day as you scroll.

## 1. Underlying & HYDROGEL — mid-price tracks

Three days laid side-by-side. Same y-axis across days so drifts are visible.

## 2. The full VEV option chain over time

Each strike gets its own track. Log scale on y makes the deep-OTM wings visible next to the deep-ITM legs.

## 3. The smile — option price vs strike

For each day, sample evenly across the session and plot price vs strike. A bundle of curves per day reveals how the smile shape moves.

## 4. Bid–ask spread heatmap

Median spread (ticks) per product × day. Warmer = wider market.

## 5. Return distributions, by day

Log-returns of mid-price for the underlying and HYDROGEL. KDE per day with rug ticks — stacks the day-to-day character.

## 6. Volatility profile — realized σ vs strike

Rolling 100-tick std of log-returns, averaged over each day. Gives a quick sense of how twitchy each strike was.

## 7. Cross-product correlation heatmap

Tick-level log-return correlation. VEV options should cluster around their underlying; HYDROGEL should be an island.

## 8. Option-vs-underlying scatter hexbin

For each near-the-money strike, the joint distribution of (S, option mid). Bone-dry call options would sit on a hockey stick; the thickness of the cloud is the time-value.

## 9. Trade activity — who's trading what, when

Quantity traded per product stacked by day (left); time-of-day trade intensity per day (right).

## 10. Order-book depth over time — underlying

Stack the top-3 bid and ask volumes around the mid. Thickness of the cloud = liquidity; the gap at the center is the spread.

## 11. Moneyness ribbon

For every tick, express each VEV price as a fraction of its strike-relative moneyness `ln(S/K)` and plot the mid / K ratio. Lines colored by day and shaded by strike get a clean waterfall.

## 12. Summary table

---

# From manual_analysis.ipynb

# Round 3 — Manual Trading: Priors & Monte Carlo

The Round 3 manual challenge ("Celestial Gardeners' Guild") is a sealed-bid game against 51 counterparties whose reserve prices live on the grid `{670, 675, …, 920}`. Each player submits **two bids** `(b1, b2)`; `b1` has priority. The catch is the cubic penalty on `b2`: if your `b2` is below the population mean of all `b2`s, your payoff is multiplied by

$$\Big(\frac{920 - \overline{b_2}}{920 - b_2}\Big)^{3}$$

so winning against the average matters a lot.

This notebook models the **population's `b2` choice** as a five-component mixture of player archetypes and shows, for each component:

1. **10 %** — *Perfect Nash*: bid the equilibrium `b2*` exactly.
2. **30 %** — *Tight Nash cluster*: uniform on `[b2* − 5, b2* + 5]`.
3. **58 %** — *Slightly above GTO*: distribution starting at `b2* + 5`, decaying upward.
4. **1 %** — *Random*: uniform on the entire bid grid.
5. **1 %** — *Griefers*: bid `920` to drag the average up.

We then aggregate the components into the implied `avg_b2` distribution, run a Monte Carlo over the population, and find the best response `(b1, b2)` under this prior.

---
## 1 · Solve the GTO baseline

We need a numerical anchor to define "Nash" before we can sample the prior. Compute the joint best response `(b1*, b2*)` over the integer bid grid `[670, 920]` for any assumed `avg_b2`, then iterate to a fixed point.

---
## 2 · Population priors

We model `b2` choices on the integer grid `[670, 920]`. Each component is encoded as a discrete probability mass function (PMF). The five archetypes match the brief exactly.

### 2a · Each archetype on its own

One panel per category, on a shared x-axis.

### 2b · The combined mixture

The full population PMF is the weighted sum of the components:

$$P(b_2) = \sum_{c} w_c \cdot P_c(b_2)$$

The plot below stacks the contribution of each component so you can read off both the marginal and where each archetype lives.

---
## 3 · Monte Carlo over the population

The PnL formula depends on the *realised* `avg_b2` of the round, not on its expectation. Sample `N` players from the mixture, compute their `avg_b2`, and repeat `K` times to get a distribution.

---
## 4 · Best response under this prior

For each candidate `(b1, b2)` we compute the *expected* PnL across the `avg_b2` distribution sampled above. Because only the cubic `((920 - avg_b2) / (920 - b2))^3` term depends on `avg_b2`, we can pre-compute the expectation per `b2` and reuse it across all `b1`s.

---
## 5 · PnL distribution at the chosen bid

Pick a candidate `(b1, b2)` and inspect the *full* distribution of round-by-round PnL across the same Monte Carlo. This shows variance, not just mean.

---
## 6 · Combining the priors → a robust bid

The baseline prior gives `(b1*, b2*) = (756, 846)` — but those weights are guesses. To get a **single consolidated recommendation** we:

1. Define a small family of plausible alternative priors (more griefers, fewer "above GTO" players, a wider above-GTO tail, etc.).
2. For each scenario, recompute the implied `avg_b2` distribution and the `E[PnL]` surface.
3. Find the **max-min bid**: the `(b1, b2)` that gives the highest *worst-case* expected PnL across all scenarios. This is the bid you'd pick if you weren't sure which scenario is right.
4. Compare it to the per-scenario optima and to the GTO baseline.

---
## 7 · Final recommendation

Combining everything above into a single answer:

| candidate | b1 | b2 | mean E[PnL] | sd | 5 % | 95 % | P(beat GTO) |
|---|---:|---:|---:|---:|---:|---:|---:|
| GTO baseline | 751 | 836 | 3,922 | 98 | 3,777 | 4,087 | 50 % |
| Baseline-prior best | **756** | **846** | 4,268 | 34 | 4,187 | 4,284 | 100 % |
| **Robust (max-min)** | **761** | **851** | **4,263** | **0** | **4,263** | **4,263** | **100 %** |
| Mean-of-scenarios | 756 | 846 | 4,268 | 35 | 4,186 | 4,284 | 100 % |

**Recommended bid: `(b1, b2) = (761, 851)`.**

### Why this and not the baseline-prior `(756, 846)`?

- The two candidates have **essentially identical expected PnL** (`4,263` vs `4,268`, a 0.1 % gap).
- But `(761, 851)` has **zero variance** — `b2 = 851` sits above the realised `avg_b2` in every one of the eight stress scenarios, so the cubic penalty never fires and the round PnL is deterministic at `4,263`.
- `(756, 846)` is great in the baseline world but in scenarios with a wider above-GTO tail (`E[avg_b2] ≈ 847`) it dips into the penalty region — its 5 %-ile drops to `4,187`, a `~100`-unit downside.
- For a single-shot manual round, paying `5` units of expected value to lock in deterministic PnL is the right trade.

### Intuition for each component
- **`b1 = 761`**: `b1` is unconstrained by `avg_b2`. Sweep it (cell 16, right pane) and the PnL surface peaks around `756-761` — the robust scenarios push it up by 5 because more aggressive `b2` levels mean the `b2` capture-region is smaller, so `b1` has to do more work.
- **`b2 = 851`**: enough above the mixture mean (`E[avg_b2] ≈ 843-847` across scenarios) that the cubic penalty never triggers, and still well below the resale price `920` so you keep `69` per `b2` fill.
- **Beats GTO 100 % of the time** in every scenario considered — never tied, never worse.

---

# From trader_identification.ipynb

# Round 3 — Trader Identification

The public trade tape has `buyer` and `seller` columns that are **100% NaN** — we don't get counterparty IDs. But distinct traders leave distinct fingerprints. By clustering trades across:

- **Product** (what they trade)
- **Direction** (always buy / always sell / balanced)
- **Size signature** (what order sizes they use)
- **Timing** (regular, bursty, clock-driven)
- **Co-occurrence** (which products they trade *together*)

...we can reconstruct 5 distinct participants.

## Fingerprint matrix — the first clue

For every symbol: how one-sided is the flow? This gives us the first trader split. Products where the flow is **~50/50** are market-maker-taken (two-way traders). Products where the flow is **100% one-sided** have a single directional participant.

**What we can already see:**
- `VEV_5300` through `VEV_6500` are ~100% SELL — a single selling participant
- `VELVET`, `HYDROGEL`, `VEV_4000` are ~50/50 — two-way flow
- `VEV_4500`, `VEV_5000`, `VEV_5100`, `VEV_5200` barely trade (1–20 trades)

---
## Trader 1 — The Wing Seller

**Products:** `VEV_5300`, `VEV_5400`, `VEV_5500`, `VEV_6000`, `VEV_6500`

**Strategy:** Short OTM call wing (theta farming). Sells baskets of OTM calls at bid simultaneously.

**Smoking gun:** every single OTM call trade is a basket event where multiple strikes hit bid at the *same timestamp*. The table below lists the unique basket compositions.

---
## Trader 2 — The VELVET Accumulator (Big Buyer)

**Discovered by:** size distribution split. VELVET sizes 3–8 are *balanced* buy/sell. Sizes **9–15 are 99% BUY** — a separate directional participant with a distinct size signature.

This trader never takes the sell side, never coincides with other products, and accumulates ~1100 VELVET units of net long exposure across the 3 days.

---
## Trader 3 — The VELVET Two-Way (small size market-maker taker)

**Products:** VELVETFRUIT_EXTRACT only
**Size signature:** 3–8 (never 9+)
**Direction:** near-balanced — 682 BUY / 589 SELL (54% buy-skewed)

This is likely an algorithmic two-way aggressor — taking both the bid and the ask with roughly equal frequency, maybe with a slight mean-reversion bias given the small net long it ends up carrying.

---
## Trader 4 — The HYDROGEL Trader

**Products:** HYDROGEL_PACK only. No other product correlation.
**Size signature:** 2–6, very flat distribution (roughly 100 trades per size).
**Direction:** balanced — 524 BUY / 486 SELL

HYDROGEL has **near-zero return correlation** with VELVET (0.01) and **3 coincident trades with the Big Buyer out of 1010** — statistically independent. A single stand-alone trading engine runs on HYDROGEL in isolation.

---
## Trader 5 — The VEV_4000 Trader

**Products:** `VEV_4000` only. Deep ITM call (~delta 1).
**Size signature:** 1–3 (flat distribution).
**Direction:** balanced — 226 BUY / 238 SELL.
**Timing:** slow — mean inter-trade gap ~6 k ticks (vs ~2 k for VELVET).

**Curious angle:** VEV_4000 is essentially a forward on VELVET (delta ≈ 1, extrinsic ≈ 0). You'd expect this trader to delta-hedge in VELVET — but the data shows only **7 co-occurring trades out of 464** with VELVET. They trade in isolation: probably a statarb-style trader exploiting ITM option mispricings against a theoretical fair value, holding positions briefly.

---
## Cross-trader fingerprint summary

One chart per trader — their signatures side-by-side. Each row is a different dimension: products, direction bias, size distribution, timing density.

---
## Co-occurrence matrix

How often do different traders trade at the same timestamp? A high number means they're plausibly the same trader doing multiple legs; zero means independent.

---
## Classification: Market Maker, Market Taker, or Insider?

Three functional roles to distinguish:

| Role | What they do | How to spot |
|---|---|---|
| **Market Maker** | Posts resting bids + asks, earns the spread, tries to stay flat | *Invisible in the trade tape* — they're the counterparty to every trade. Inferred from persistent, tight two-sided quotes in `prices` |
| **Market Taker** | Crosses the spread to hit a resting quote | Every trade with direction=BUY (hit ask) or SELL (hit bid) has a taker on the aggressive side |
| **Insider (informed taker)** | A taker whose direction *predicts* future price moves | Buys followed by price rises; sells followed by price drops — significantly above 50% hit rate |

**Critical point:** all 5 traders we identified are **takers** — by definition, because they show up in the trade tape crossing the spread. The market makers (posting the resting quotes they hit) are *invisible* in this dataset; we can only infer their presence from the tight, stable bid-ask spreads in the prices file (VELVET: 5 ticks, VEV_5400/5500: 1 tick, always quoted).

The question then becomes: **which takers are *informed* (insiders)?**

We can test this directly — take every trade a participant does, and look at VELVET's mid price 500 / 2,000 / 10,000 ticks later. If their direction predicts the move, they have an edge.

### Reading the hit-rate chart

- **Accumulator (BUY)**: 83% hit rate at 500 ticks, 75% at 2,000 ticks. Mean move **+2.0 ticks** per event. This is not statistical chance — this is a trader with an information edge. **Insider.**
- **Wing Seller (SELL)**: mild negative drift after they sell (~47% hit rate on VELVET falling is weak but consistent). They're short-vol / short-delta; they have a view but it's more of a theta harvest than a directional bet.
- **VEV_4000 BUY/SELL**: small informed edge — ~54% / 56% hit rate. Since VEV_4000 is delta-1, buying it is effectively buying VELVET, so this is a modest directional signal.
- **Two-Way BUY / SELL**: right at 48–51% — **pure noise**. No edge. They aren't insiders or informed traders.
- **HYDROGEL Trader** can't be evaluated against VELVET (independent product), but internal flow is balanced — no directional edge visible.

### Final classification

| Trader | Role | Informed? | Evidence |
|---|---|---|---|
| **VELVET Accumulator** | Taker | 🚨 **Insider** | 83% hit rate @ 500 ticks, persistent edge |
| **Wing Seller** | Taker | Mildly informed (short vol) | −0.5 tick drift after sells; basket structure |
| **VEV_4000 Trader** | Taker | Mildly informed (direction) | Small but real edge (54–56%) |
| **VELVET Two-Way** | Taker | Uninformed | Hit rate ≈ 50%, noise |
| **HYDROGEL Trader** | Taker | Uninformed | Balanced, isolated product |
| *(invisible)* | **Market Makers** | N/A | Not in trade tape — post the resting quotes all takers hit |

### Strategic implication

The Accumulator is a gift: every time you see a BUY print of size ≥9 on VELVET, you have an 83% edge that mid goes up in the next 500 ticks. **Copy the Accumulator.** Conversely, trading against them (selling when you see their print) is betting against someone with a clear information advantage — don't do it.

---
## Trader summary table

---
## Conclusions — who's in the market, and what it means

We identified **5 distinct taker profiles** plus an **inferred market-maker layer** that never appears in the trade tape.

### The ecosystem

| # | Participant | Role | Informed? | Direction | Size |
|---|---|---|---|---|---|
| — | **Market Makers** (NPC + others) | Maker | N/A | Two-sided quotes | Inferred from tight spreads |
| 1 | **Wing Seller** | Taker | Mildly (short-vol) | 100% SELL | 2–5 |
| 2 | **VELVET Accumulator** | Taker | **🚨 Insider (83% hit rate)** | 100% BUY | 9–15 |
| 3 | **VELVET Two-Way** | Taker | Uninformed (noise) | 54/46 | 3–8 |
| 4 | **HYDROGEL Trader** | Taker | Uninformed | 52/48 | 2–6 |
| 5 | **VEV_4000 Trader** | Taker | Mildly (slight edge) | 49/51 | 1–3 |

### The key insights

1. **Every trade you see is a taker hitting a maker.** The 5 participants we identified are all takers; the makers are invisible counterparties (the game's NPC market maker and other participants' quoting bots) providing the resting liquidity.

2. **The Accumulator is an insider.** Their buy prints (size ≥9) predict a +2 tick VELVET mid rise with 83% accuracy over the next 500 ticks. This is a copy-tradeable edge.

3. **The Wing Seller is a theta harvester with a mild short-vol view** — not a directional insider, but their selling correlates weakly with low-vol periods.

4. **Two-Way and HYDROGEL flow is pure noise** — hit rates right at 50%. These are the market's "uninformed order flow" — pleasant to market-make against.

5. **Compartmentalization.** The co-occurrence matrix is essentially zero off-diagonal (except Wing Seller's basket). No cross-asset hedging happens — an arbitrage gap in the ecosystem.

### Trading playbook, ranked by edge

1. **🟢 Copy the Accumulator** — when you see a VELVET print with quantity ≥9 and direction=BUY, immediately lift the ask yourself. 83% hit rate on +2 tick moves is a phenomenal signal.
2. **🟢 Absorb the Wing Seller** — post bids on VEV_5400 / VEV_5500 slightly above the market. The Wing Seller will reliably fill you. Immediately re-offer at ask for 1-tick spread capture. Long gamma, delta-hedge with VELVET.
3. **🟡 Market-make Two-Way / HYDROGEL** — quote both sides, collect the spread from noise takers. Balanced flow means low inventory risk.
4. **🔴 Don't fight the Accumulator** — selling VELVET when the Accumulator is buying is betting against someone with information. Losing trade on average.
5. **🟡 VEV_4000 arb** — wide 21-tick spread makes it hard, but the trader there doesn't hedge; occasional dislocations vs VELVET mid may be harvestable.