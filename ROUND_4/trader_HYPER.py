"""ROUND_4: trader_HYPER.

Named-bot tape signals are strong enough on the public R4 data to be a
first-class entry trigger, not just a tilt.  Lead-lag table on
``data/ROUND_4/`` (see ``notebooks/round4/bot_alpha_analysis.py``):

  product  bot      side  h=100 mean    h=100 hit
  VELVET   Mark 67  buy   +1.17         85.5%
  VELVET   Mark 49  sell  -1.23         90.5%
  VELVET   Mark 22  sell  -0.80         82.2%
  HYDRO    Mark 14  any   +8.0          100%
  HYDRO    Mark 38  any   -8.0          99.4%

Builds on ``WAITALPHA`` (warm-up gate + BUGALPHA option stack):

* HYDRO: swing edge += HYDRO_TILT (=12) * named-bot direction.  The base
  swing entry threshold is 14, so a 12-tick tilt flips the entry when
  the local EMA is borderline — without overriding the underlying anchor
  if there is no bot signal.
* VELVET: directional bot tilt that follows Mark 67 / Mark 55 and fades
  Mark 49 / Mark 22.  Tilt is applied only to VELVET sizing — not the
  shared option-stack swing edge — because BOTVELVET > BOTFLOW
  empirically (a global tilt bleeds across vouchers).
* WAITALPHA gate is preserved: nothing fires until the tape has moved or
  the 4000-tick warm-up cap is reached.

Result on ``uv run rank --round 4 --show-per-product``:

  rank trader        total      avg/day   d1       d2       d3
  1    HYPER         785,117.5  261,706   341,589  243,421  200,108
  2    BOTVELVET     779,833.5  259,944   341,891  237,935  200,008

Per-product diff (HYPER − BOTVELVET):

  HYDROGEL_PACK       +4,475   (the win — bigger HYDRO tilt)
  VELVETFRUIT_EXTRACT -1,127   (small noise)
  rest                identical
"""

from typing import Dict, List, Tuple

from datamodel import Order, TradingState

from ROUND_3 import trader_FLIPVOL as flip
from ROUND_4 import trader_WAITALPHA as base


# Lead-lag mean signed move (h=100) used as the per-bot tilt magnitude.
# A buy event credits the buyer's alpha; a sell event credits -seller.
# These are slightly damped (we want robustness, not point estimates).
VELVET_BOT_ALPHA = {
    "Mark 67": 3.0,    # measured +1.17 / 86% hit; BOTVELVET tuning kept
    "Mark 55": 1.0,
    "Mark 49": -2.0,   # measured -1.23 / 90% hit
    "Mark 22": -1.5,   # measured -0.80 / 82% hit (basket-dump tells)
}

HYDRO_BOT_ALPHA = {
    "Mark 14": +1.0,   # +8 mean, 100% hit @ h=100
    "Mark 38": -1.0,   # -8 mean, 99% hit @ h=100
    "Mark 22": -1.5,   # rare but informative HYDRO sells
}

# How long after the print the signal stays armed.
VELVET_BOT_TTL = 2_500
HYDRO_BOT_TTL = 2_500

# Minimum aggregated weight required to trigger.
VELVET_MIN_WEIGHT = 8.0
HYDRO_MIN_WEIGHT = 8.0

# VELVET: keep BOTVELVET's tilt shape (it wins on data).
VELVET_TILT_BASE = 2.0
VELVET_TILT_CAP = 3.0
VELVET_SCORE_NORM = 60.0

# HYDRO: bot signals deliver +/-8 mean / 100% hit at h=100.  A 12-tick
# additive tilt is enough to flip the swing decision near threshold 14
# without overriding the base anchor.  Sweep showed 8 -> +1.5K, 12 -> +5K,
# and 14+ saturates at the threshold (no further gain).
HYDRO_TILT = 12.0


def _score_bots(trades, alpha: Dict[str, float]) -> float:
    """Aggregate per-tick bot score: + when alpha bot is buying, - when alpha
    bot is selling.

    Important: own_trades and market_trades are DISJOINT in the open-source
    backtester (matched volume gets removed from market_trades), but BOTH
    carry counterparty bot names on the live website.  Caller passes the
    union list so we don't miss informed prints we filled against.
    """
    score = 0.0
    for tr in trades or []:
        b = getattr(tr, "buyer", None)
        s = getattr(tr, "seller", None)
        qty = abs(int(getattr(tr, "quantity", 0) or 0))
        # Skip the SUBMISSION counterparty entry (we are not informed),
        # but still credit the named bot on the OTHER side.
        if b != "SUBMISSION":
            score += alpha.get(b, 0.0) * qty
        if s != "SUBMISSION":
            score -= alpha.get(s, 0.0) * qty
    return score


def _all_trades(state: TradingState, product: str):
    out = list(state.market_trades.get(product, []) or [])
    out.extend(state.own_trades.get(product, []) or [])
    return out


class Trader(base.Trader):
    # ── signal capture ─────────────────────────────────────────────────
    def update_signals(self, state: TradingState, store: Dict) -> Tuple[bool, int, bool, int]:
        v_active, vev_dir, basket_active, v4_dir = super().update_signals(state, store)

        # VELVET: exclude own_trades.  The signal got noisier when we
        # included our own fills — the basket-dump tilt would fade an entry
        # we already chose to take.  Public-tape only, like BOTVELVET.
        v_score = _score_bots(state.market_trades.get(flip.VELVET, []), VELVET_BOT_ALPHA)
        if abs(v_score) >= VELVET_MIN_WEIGHT:
            store["velvet_bot_dir"] = 1 if v_score > 0 else -1
            store["velvet_bot_strength"] = min(
                VELVET_TILT_CAP,
                VELVET_TILT_BASE * min(1.5, abs(v_score) / VELVET_SCORE_NORM),
            )
            store["velvet_bot_til"] = state.timestamp + VELVET_BOT_TTL

        # HYDRO: union of public + own trades.  HYDRO bot signals deliver
        # +/-8 mean / 100% hit so even our own counterparty fills carry the
        # signal cleanly.
        h_score = _score_bots(_all_trades(state, flip.HYDRO), HYDRO_BOT_ALPHA)
        if abs(h_score) >= HYDRO_MIN_WEIGHT:
            store["hydro_bot_dir"] = 1 if h_score > 0 else -1
            store["hydro_bot_til"] = state.timestamp + HYDRO_BOT_TTL

        return v_active, vev_dir, basket_active, v4_dir

    def _velvet_tilt(self, state: TradingState, store: Dict) -> float:
        if state.timestamp > int(store.get("velvet_bot_til", -1)):
            return 0.0
        d = int(store.get("velvet_bot_dir", 0))
        s = float(store.get("velvet_bot_strength", 0.0))
        return d * s

    def _hydro_signal(self, state: TradingState, store: Dict) -> int:
        if state.timestamp > int(store.get("hydro_bot_til", -1)):
            return 0
        return int(store.get("hydro_bot_dir", 0))

    # ── VELVET: tilt the swing edge but do NOT propagate to options ────
    def trade_velvet(self, state, store, result, v_active, implied_tilt, v4_dir):
        edge = self._update_swing_edge(state, store)
        if edge is None:
            return
        tilt = self._velvet_tilt(state, store)
        bot_edge = max(-25.0, min(25.0, edge + tilt))
        target = self._swing_target(
            flip.VELVET, bot_edge, self.position(state, flip.VELVET)
        )
        self._trade_to_target(state, result, flip.VELVET, target)
        self._swing_edge = edge  # leave option stack on the unmodified edge

    # ── HYDRO: bot tilt added to swing edge (no hard override) ─────────
    def trade_hydro(
        self,
        state: TradingState,
        store: Dict,
        result: Dict[str, List[Order]],
    ) -> None:
        q = flip.quote_from(state.order_depths.get(flip.HYDRO))
        if q.bid is None or q.ask is None or q.mid is None:
            return
        fast = flip.update_ema(store["emas"], "hydro_swing_fast", q.mid, 10)
        edge = 0.7 * fast + 0.3 * 9980.0 - q.mid
        edge += HYDRO_TILT * self._hydro_signal(state, store)

        current = self.position(state, flip.HYDRO)
        if edge > 14.0:
            target = 200
        elif edge < -14.0:
            target = -200
        else:
            target = current

        self._trade_to_target(state, result, flip.HYDRO, target)
