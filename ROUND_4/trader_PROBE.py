"""ROUND_4: trader_PROBE — diagnostics, NOT for PnL.

Purpose: probe the LIVE IMC website to verify behavior the local backtester
can't directly reproduce.  Specifically:

  1. Counterparty names on fills.  market_trades and own_trades are
     DISJOINT in the open-source backtester (matched volume is removed
     from market_trades — see prosperity4bt.runner.match_orders), so this
     trader writes a structured ledger to ``state.traderData`` of every
     (timestamp, product, buyer, seller, price, qty) it sees on EITHER
     channel.  After a live run, decode traderData with jsonpickle and
     compare with the local backtest log.

  2. Queue priority on the zero-tail wings (VEV_6000 / VEV_6500).  Mark 22
     dumps these at price 0, Mark 01 absorbs at 0.  If we sit at a 0-bid
     in size, do any of those fills come our way on the live site?  We
     post a 1-lot 0-bid each tick: cost is at most 0 per contract.

  3. Whether we can sell back at price 1 on the wings.  We never bid 1
     (would cross the 1-ask) but we DO offer 1 if we hold a wing
     position from the 0-bid.  Mark 22 / Mark 14 / Mark 38 might lift it.

  4. A tiny one-tick passive quote on VELVET (1 lot at best bid - 1).
     Probability of fill is small, but a fill identifies the live
     counterparty in own_trades so we can see who's hitting us.

Safety:
  * Position cap is hard-capped to PROBE_MAX_POS (3 contracts) per product.
  * No taking — every order is at-or-below the best opposite quote.
  * No flatten-on-day-end logic (the round end will mark to mid; for
    wings that's price 0.5, so a 1-lot wing position at end of day costs
    at most 0.5 * |pos| ≈ $1.50 in mark-to-market mark.  Acceptable.).

To run:
  uv run rank --round 4 --trader trader_PROBE.py --show-per-product
  # then look at the per-product 'b' column and the recorded traderData
  # in runs/.../submission.log -> tradeHistory and the last logs entry.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import jsonpickle

from datamodel import Order, TradingState


# Hard caps so we don't accumulate meaningful exposure even on a long live run.
PROBE_MAX_POS = 3
WING_PRODUCTS = ("VEV_6000", "VEV_6500")
VELVET = "VELVETFRUIT_EXTRACT"

# Cap traderData growth.  jsonpickle of TradingState should stay <10KB.
LEDGER_MAX = 600


def _new_state() -> Dict:
    return {
        "schema": 1,
        "ledger": [],            # list of [ts, prod, buyer, seller, price, qty, src]
        "ticks": 0,
        "first_ts": None,
        "last_ts": None,
    }


def _load(td: str) -> Dict:
    if not td:
        return _new_state()
    try:
        d = jsonpickle.decode(td)
    except Exception:
        return _new_state()
    base = _new_state()
    base.update(d if isinstance(d, dict) else {})
    base.setdefault("ledger", [])
    return base


def _record(store: Dict, ts: int, prod: str, tr, src: str) -> None:
    """Append one normalised tape print to the ledger."""
    store["ledger"].append([
        int(ts),
        str(prod),
        getattr(tr, "buyer", None) or "",
        getattr(tr, "seller", None) or "",
        float(getattr(tr, "price", 0) or 0),
        int(getattr(tr, "quantity", 0) or 0),
        src,
    ])
    if len(store["ledger"]) > LEDGER_MAX:
        # Keep recent entries; the oldest are usually pre-arming opening book.
        store["ledger"] = store["ledger"][-LEDGER_MAX:]


def _position(state: TradingState, product: str) -> int:
    return int((state.position or {}).get(product, 0))


class Trader:
    def run(self, state: TradingState):
        store = _load(state.traderData)
        store["ticks"] = int(store.get("ticks", 0)) + 1
        if store.get("first_ts") is None:
            store["first_ts"] = int(state.timestamp)
        store["last_ts"] = int(state.timestamp)

        # Record both tapes verbatim.  These channels are DISJOINT in the
        # open-source backtester, but on the live website they BOTH carry
        # bot identities — that's what we're verifying.
        for prod, trs in (state.market_trades or {}).items():
            for tr in trs or []:
                _record(store, state.timestamp, prod, tr, "market")
        for prod, trs in (state.own_trades or {}).items():
            for tr in trs or []:
                _record(store, state.timestamp, prod, tr, "own")

        result: Dict[str, List[Order]] = {}

        # ── Probe 1: 0-bid on the wings (queue priority test). ─────────
        for wing in WING_PRODUCTS:
            depth = (state.order_depths or {}).get(wing)
            if depth is None:
                continue
            bid_levels = getattr(depth, "buy_orders", None) or {}
            ask_levels = getattr(depth, "sell_orders", None) or {}
            pos = _position(state, wing)
            orders = result.setdefault(wing, [])

            # Only bid 0 if a 0-bid level already exists in the book (the
            # ask is at 1 and the bid is at 0); never cross.
            if 0 in bid_levels and ask_levels and min(ask_levels) >= 1:
                room = max(0, PROBE_MAX_POS - pos)
                if room > 0:
                    orders.append(Order(wing, 0, 1))

            # Offer back at 1 if we accumulated wings.
            if pos > 0 and 1 in ask_levels and 0 in bid_levels:
                orders.append(Order(wing, 1, -min(pos, 1)))

        # ── Probe 2: 1-lot passive bid one tick under the best on VELVET.
        depth_v = (state.order_depths or {}).get(VELVET)
        if depth_v is not None:
            bids = getattr(depth_v, "buy_orders", None) or {}
            asks = getattr(depth_v, "sell_orders", None) or {}
            if bids and asks:
                best_bid = max(bids)
                best_ask = min(asks)
                pos_v = _position(state, VELVET)
                orders = result.setdefault(VELVET, [])
                if best_ask - best_bid >= 2 and (best_bid - 1) > 0:
                    if pos_v < PROBE_MAX_POS:
                        orders.append(Order(VELVET, best_bid - 1, 1))
                # Symmetric passive offer above best_ask if we got long.
                if pos_v > 0:
                    orders.append(Order(VELVET, best_ask + 1, -1))
                # Symmetric ask if short.
                if pos_v > -PROBE_MAX_POS and best_ask - best_bid >= 2:
                    orders.append(Order(VELVET, best_ask + 1, -1))

        # Strip empty product lists before returning.
        result = {p: o for p, o in result.items() if o}
        return result, 0, jsonpickle.encode(store)
