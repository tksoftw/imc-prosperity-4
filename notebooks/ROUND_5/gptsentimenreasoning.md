# Complete reasoning table used to set the return priors
Sources / calibration notes:
  * Current task: Prosperity 4 Ashflow Alpha / Ignith news page.
  * Main historical calibration: Prosperity 3 Round 5 news trading.
    Public writeups report actual one-day moves including approximately:
      - Quantum Coffee:  -66.79%
      - Cacti Needle:    -41.20%
      - Solar Panels:     -8.90%
      - Red Flags:       +50.90%
      - VR Monocle:      +22.40%
      - Moonshine:        +3.00%
      - Striped Shirts:   +0.21%
      - Haystacks:        -0.48%
      - Ranch Sauce:      -0.72%
  * Prosperity 2 Round 5 provides broader move-scale priors, especially that
    severe negative consumer/health stories can be very large, while weak hype
    stories often deserve little or no allocation.
  * Prosperity 1 evidence is thinner publicly, but the key lesson from later
    comparisons is that tax/regulatory headline severity can be overstated;
    this is one reason Pyroflex is sized more mildly than the scary headline
    might imply.

Current product reasoning:

| Current product | Current Ashflow quote anchors | Signal interpretation | Closest prior-round analogue | Historical move anchor | Prior range used in BASE_PRIORS | Direction |
|---|---|---|---|---:|---:|---|
| Lava Cakes | "Traces of actual lava found"; "formal review"; "immediate halt in sales"; "civil lawsuits are already piling up"; vendors returning stock with lawyer letters | Confirmed health/safety issue plus halted sales, lawsuits, and vendor returns. This is the cleanest severe negative. | P3 Quantum Coffee: doctors assessed long-term effects and authorities debated an immediate ban. P2 Serum is another severe-negative anchor. | P3 Quantum Coffee about -66.79%; P2 Serum was in the extreme negative bucket. | -80% / -68% / -55% | SELL |
| Sulfur Reactor / Sulfur Ltd. | "Elemental Index 118 will add Sulfur Reactor"; "Funds tracking the index are expected to adjust their holdings" | Confirmed index inclusion creates a forced-flow / index-tracker demand catalyst. This is cleaner than promotional demand. | No exact P3 analogue. Use strong positive catalysts such as P3 Red Flags, P3 VR Monocle, and P2 PS6 only as broad move-scale anchors. | P3 Red Flags about +50.90%; P3 VR Monocle about +22.40%; P2 PS6 about +31% in public analyses. | +18% / +30% / +45% | BUY |
| Thermalite Smart Devices | "active projected users rising from 1.42 million...to 3.89 million"; "16 hours and 42 minutes per day"; "very strong next quarter" | Clean demand and usage-growth story. Both user count and engagement increase, making this a high-quality positive. | P3 VR Monocle: monthly active players surged and average time spent was very high. | P3 VR Monocle about +22.40%. | +18% / +26% / +34% | BUY |
| Obsidian Cutlery | "Manufacturing halted"; "sliced through portions of the chemical assembly line"; "contamination protocols"; temporary evacuation | Product-safety and production failure. Large negative, but less severe than a consumer health ban; supply reduction adds some ambiguity. | P3 Cacti Needle: rail-spike defect derailed the Economic Express and created broad safety/infrastructure concerns. | P3 Cacti Needle about -41.20%. | -42% / -32% / -20% | SELL |
| Ashes of the Phoenix | "resurfaced video shows the sourcing method"; "public concern escalated"; "public outcry"; defensive company statement | Viral reputation / ethical sourcing scandal. Negative, but less direct than sales halt or ban. | Product-scandal bucket; partly comparable to P3 Cacti Needle or P2 negative consumer-product stories, but with reputation rather than physical health as the main channel. | Use medium-large negative bucket, not as extreme as Quantum Coffee. | -42% / -30% / -15% | SELL |
| Pyroflex Cell | "discontinue the Pyroflex Cell Tax Cut"; "effectively doubles the current levy"; "slow new purchases" | Clear negative tax shock, but historical tax stories moved less than headline language suggested. | P3 Solar Panels: tax increase article said the new law "triples the cost" and casts a "dark shadow". | P3 Solar Panels moved only about -8.90%, despite a harsher headline than Pyroflex. | -18% / -10% / -5% | SELL |
| Scoria Paste | "stock up on Scoria Paste before it becomes unaffordable"; "the paste that keeps Ignith together"; used in repairs and infrastructure upkeep | Positive stockpiling narrative tied to an essential good. Better than pure hype, but source is a self-proclaimed market medium and there is no true scarcity event. | P3 Red Flags had real scarcity after a sandstorm destroyed supply. Scoria is weaker because it has demand-pull without confirmed supply destruction. | P3 Red Flags about +50.90%, but should be heavily discounted as an anchor. | +6% / +15% / +25% | BUY |
| Magma Ink / Lava Fountain Pen | "large crowd gathered"; "waiting in line for more than six hours"; "hot drop"; limited-edition release | Consumer launch hype and visible demand. Positive, but likely promotional and partly already priced by the time of the article. | P3 Ranch Sauce acquisition/hype and P3 Haystacks revival/community story both barely moved; P2 Earrings was a modest positive. | P3 Ranch Sauce about -0.72%; P3 Haystacks about -0.48%; P2 Earrings modest positive. | 0% / +7% / +15% | BUY small |
| Volcanic Incense | "extended its rally"; "accelerated buying"; public call to "follow his lead and buy" | Crowded influencer pump that has already rallied. Low informational edge and reversal/crowding risk. | P3 Striped Shirts and P3 Moonshine were promotional/optimistic stories with little realized movement. | P3 Striped Shirts about +0.21%; P3 Moonshine about +3.00%. | -8% / -3% / +8% | AVOID / tiny SELL |

P3 article calibration table from full news images:

| P3 product/article | P3 quote anchors | Actual move | Lesson applied here |
|---|---|---:|---|
| Quantum Coffee | "doctors have now assessed the long-term health effects"; "debating an immediate ban" | -66.79% | Severe health/regulatory stories belong in the largest negative bucket; this drives Lava Cakes. |
| Cacti Needle | "cause of the derailment"; "small flaw with massive consequences" | -41.20% | Product-safety failures with operational/infrastructure consequences are large negatives; this drives Obsidian. |
| Solar Panels | "8.4% tax increase"; "triples the cost" | -8.90% | Tax headlines can underperform; this keeps Pyroflex from being oversized. |
| Red Flags | "only three red flags survived"; "reprinted during the coming months" | +50.90% | Actual scarcity can produce extreme upside; Scoria is positive but weaker because it lacks true scarcity. |
| VR Monocle | users rose from 800k to 4.6M; average time spent 18h32 | +22.40% | Hard usage metrics are reliable positives; this drives Thermalite. |
| Ranch Sauce | "hottest sauce"; acquisition story | -0.72% | Corporate/consumer hype may not move much; this tempers Magma Ink. |
| Haystacks | community revival / hidden needles narrative | -0.48% | Vague revival narratives are weak; avoid over-sizing hype. |
| Striped Shirts | trend prediction and discount-code promotion | +0.21% | Promotional fashion/trend calls are mostly noise; this tempers Volcanic Incense. |
| Moonshine | successful expedition and optimistic next mission | +3.00% | Optimistic announcements without hard demand/supply shocks move little. |

Practical interpretation:
  Highest-conviction trades: SELL Lava Cakes, BUY Sulfur, BUY Thermalite,
  SELL Obsidian, SELL Ashes.
  Moderate: BUY Scoria, SELL Pyroflex.
  Low edge / optional: BUY small Magma Ink, avoid or tiny SELL Volcanic Incense.
  The optimizer should be allowed to cap individual products for risk control,
  but cap sweeps are important because a 25% cap can bind on Lava Cakes.
---------------------------------------------------------------------------
---------------------------------------------------------------------------
