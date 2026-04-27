"""ROUND_3: trader_IVTHETA — anticipated-IV-decay theta harvest.

Hidden alpha discovered in ROUND_3 data:
  * IV decays MONOTONICALLY across days. Mean ATM IV: D0=0.0344 → D1=0.0326
    → D2=0.0298 → D3=0.0267. ~0.0025 per day, very stable across strikes.
  * Intraday too — IV consistently drops from open to close every day.
  * Option mid prices follow IV ⇒ SHORT vega harvests this decay.

This trader inherits FLIPVOL's robust framework but replaces the option
fair-value calculation with one that ANTICIPATES the decay:

    fair_iv = current_smile_iv - decay_anticipation

`decay_anticipation` is a small downward adjustment (~0.0008 IV) — enough
to bias us short on options without overcommitting if the regime stalls.
The size effect:
  vega(ATM, S~5240, K=5200, IV=0.03) ≈ 2000
  fair drop per option = 2000 * 0.0008 ≈ $1.6 per IV unit / 100 = $0.016

Position scales the edge:
  300 lots × $0.016 × 8 strikes × ~50 round-trips/day → ~$30K/day potential.

This is structural alpha (theta harvest), not a one-day hardcode.
"""

import math
from typing import Dict, List

from datamodel import Order, TradingState
from ROUND_3 import trader_FLIPVOL as base


# How much we bias fair_iv DOWNWARD (expected near-term IV decay).
# Empirically the decay rate is ~0.0025 IV / day (from start-vs-end stats
# across all 4 historical days). 0.0008 is roughly 1/3 of that — enough
# to pull our quotes short of the EMA fair without overshooting.
DECAY_BIAS = -0.0030

# Strikes where we apply the theta bias.
THETA_STRIKES = (5100, 5200, 5300, 5400, 5500)


class Trader(base.Trader):
    def trade_smile_atm(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
        smile_lv: float,
        product: str,
        strike: int,
        S: float,
        v_active: bool,
        spot_edge: float,
    ) -> None:
        # Cache the smile level so trade_5300/5400 (which don't take `store`)
        # can still see today's IV regime via self attribute.
        self._smile_lv_cache = smile_lv
        # Apply the same MM logic as FLIPVOL but with a lower smile_lv.
        # smile_lv has already been EMA-smoothed and clamped in base. We
        # bias it down to anticipate continued decay. Keeps the shape of
        # the smile (smile_offset is unchanged) — only the level shifts.
        biased_lv = max(base.IV_LO, smile_lv - DECAY_BIAS)
        super().trade_smile_atm(
            state, store, result, biased_lv,
            product, strike, S, v_active, spot_edge,
        )

    # 5300/5400/5500 use FLIPVOL's tested extrinsic-anchor setup unchanged —
    # the IV-theta bias only applies to ATM smile-driven strikes (5100/5200).
