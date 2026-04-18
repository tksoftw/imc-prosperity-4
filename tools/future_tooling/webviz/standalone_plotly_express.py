from __future__ import annotations

import argparse
import json
import os
from html import escape
from io import StringIO
from typing import Any

import pandas as pd
import plotly.express as px


def load_log_bundle(log_path: str) -> dict[str, Any]:
    with open(log_path, encoding="utf-8") as infile:
        payload = json.load(infile)

    activities_log = payload.get("activitiesLog", "")
    logs = payload.get("logs", payload.get("graphLog", []))
    trade_history = payload.get("tradeHistory", [])

    activities_df = pd.read_csv(StringIO(activities_log), sep=";")
    if "timestamp" in activities_df.columns:
        activities_df["timestamp"] = pd.to_numeric(activities_df["timestamp"], errors="coerce")
    if "profit_and_loss" in activities_df.columns:
        activities_df["profit_and_loss"] = pd.to_numeric(activities_df["profit_and_loss"], errors="coerce")

    for col in activities_df.columns:
        if col.startswith(("bid_price_", "ask_price_", "bid_volume_", "ask_volume_", "mid_price")):
            activities_df[col] = pd.to_numeric(activities_df[col], errors="coerce")

    logs_df = pd.DataFrame(logs)
    if not logs_df.empty and "timestamp" in logs_df.columns:
        logs_df["timestamp"] = pd.to_numeric(logs_df["timestamp"], errors="coerce")

    trades_df = pd.DataFrame(trade_history)
    if not trades_df.empty:
        trades_df["timestamp"] = pd.to_numeric(trades_df.get("timestamp"), errors="coerce")
        trades_df["price"] = pd.to_numeric(trades_df.get("price"), errors="coerce")
        trades_df["quantity"] = pd.to_numeric(trades_df.get("quantity"), errors="coerce")
        trades_df["side"] = "external"
        if "buyer" in trades_df.columns:
            trades_df.loc[trades_df["buyer"] == "SUBMISSION", "side"] = "our_buy"
        if "seller" in trades_df.columns:
            trades_df.loc[trades_df["seller"] == "SUBMISSION", "side"] = "our_sell"

    return {
        "raw": payload,
        "activities": activities_df,
        "logs": logs_df,
        "trade_history": trades_df,
    }


def make_market_figure(
    activities: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    product: str,
    t_min: int,
    t_max: int,
    show_depth: bool,
    show_spread: bool,
    show_trades: bool,
) -> Any:
    market = activities[
        (activities["product"] == product)
        & (activities["timestamp"] >= t_min)
        & (activities["timestamp"] <= t_max)
    ].copy()
    market = market.sort_values("timestamp")

    fig = px.line(title=f"{product} Market")
    if market.empty:
        return fig

    if "mid_price" in market.columns:
        mid_df = market[["timestamp", "mid_price"]].rename(columns={"mid_price": "value"})
        mid_df["series"] = "mid_price"
        mid_fig = px.line(mid_df, x="timestamp", y="value", color="series")
        for trace in mid_fig.data:
            trace.line.width = 2
            fig.add_trace(trace)

    if show_depth:
        for col, name, opacity in [
            ("bid_price_1", "bid_1", 0.45),
            ("ask_price_1", "ask_1", 0.45),
            ("bid_price_2", "bid_2", 0.28),
            ("ask_price_2", "ask_2", 0.28),
            ("bid_price_3", "bid_3", 0.22),
            ("ask_price_3", "ask_3", 0.22),
        ]:
            if col in market.columns:
                depth_df = market[["timestamp", col]].rename(columns={col: "value"})
                depth_df["series"] = name
                depth_fig = px.line(depth_df, x="timestamp", y="value", color="series")
                for trace in depth_fig.data:
                    trace.opacity = opacity
                    trace.line.width = 1
                    fig.add_trace(trace)

    if show_spread and "ask_price_1" in market.columns and "bid_price_1" in market.columns:
        spread_df = market[["timestamp"]].copy()
        spread_df["spread_1"] = market["ask_price_1"] - market["bid_price_1"]
        spread_fig = px.line(spread_df, x="timestamp", y="spread_1")
        for trace in spread_fig.data:
            trace.name = "spread_1"
            trace.line.width = 2
            trace.line.color = "#9467bd"
            fig.add_trace(trace)

    if show_trades and not trades.empty:
        local_trades = trades[
            (trades["symbol"] == product)
            & (trades["timestamp"] >= t_min)
            & (trades["timestamp"] <= t_max)
        ].copy()
        if not local_trades.empty:
            local_trades["side"] = local_trades["side"].fillna("external").astype(str)
            local_trades["size"] = local_trades["quantity"].fillna(0).astype(float).clip(lower=0).map(
                lambda q: max(6.0, min(18.0, 6.0 + q))
            )
            trade_fig = px.scatter(
                local_trades,
                x="timestamp",
                y="price",
                color="side",
                symbol="side",
                size="size",
                size_max=18,
                category_orders={"side": ["our_buy", "our_sell", "external"]},
                color_discrete_map={"our_buy": "#1f77b4", "our_sell": "#d62728", "external": "#7f7f7f"},
                symbol_map={"our_buy": "triangle-up", "our_sell": "triangle-down", "external": "circle"},
            )
            for trace in trade_fig.data:
                trace.name = f"trades:{trace.name}"
                trace.marker.opacity = 0.85
                fig.add_trace(trace)

    fig.update_layout(
        height=440,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
        legend={"orientation": "h"},
        xaxis_title="timestamp",
        yaxis_title="price/spread",
    )
    return fig


def make_pnl_figure(activities: pd.DataFrame, *, t_min: int, t_max: int) -> Any:
    pnl = activities[
        (activities["timestamp"] >= t_min) & (activities["timestamp"] <= t_max)
    ].drop_duplicates("timestamp")
    if pnl.empty or "profit_and_loss" not in pnl.columns:
        return px.line(title="PnL")

    fig = px.line(pnl, x="timestamp", y="profit_and_loss", title="PnL")
    fig.update_traces(line={"color": "#2ca02c", "width": 2})
    fig.update_layout(
        height=340,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
        xaxis_title="timestamp",
        yaxis_title="PnL",
        showlegend=False,
    )
    return fig


def render_html(
    *,
    log_path: str,
    product: str,
    t_min: int,
    t_max: int,
    market_fig: Any,
    pnl_fig: Any,
    logs_df: pd.DataFrame,
    show_pnl: bool,
) -> str:
    local_logs = logs_df[(logs_df["timestamp"] >= t_min) & (logs_df["timestamp"] <= t_max)] if not logs_df.empty else logs_df
    logs_payload = local_logs.tail(80).to_dict(orient="records") if local_logs is not None and not local_logs.empty else []
    logs_json = json.dumps(logs_payload, indent=2, ensure_ascii=True)

    market_html = market_fig.to_html(full_html=False, include_plotlyjs="cdn", config={"responsive": True})
    pnl_html = pnl_fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True}) if show_pnl else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Standalone Plotly Express Dashboard</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; }}
    .wrap {{ padding: 12px 14px; display: grid; gap: 12px; }}
    pre {{ background: #0b1020; color: #dbe7ff; padding: 10px; border-radius: 10px; overflow: auto; }}
    .meta {{ color: #444; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h2>Standalone Plotly Express Dashboard</h2>
    <div class="meta">
      log={escape(log_path)} | product={escape(product)} | window=[{t_min}, {t_max}]
    </div>
    {market_html}
    {pnl_html}
    <pre>{escape(logs_json) if logs_payload else "No logs in window."}</pre>
  </div>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate standalone Plotly Express dashboard HTML from one .log file.")
    parser.add_argument("--log", required=True, help="Path to simulator .log JSON file")
    parser.add_argument("--out", default="webviz_dashboard.html", help="Output HTML file path")
    parser.add_argument("--product", default="", help="Product symbol to plot; default is first product in file")
    parser.add_argument("--t-min", type=int, default=None, help="Start timestamp (default min)")
    parser.add_argument("--t-max", type=int, default=None, help="End timestamp (default max)")
    parser.add_argument("--show-depth", action="store_true", default=True)
    parser.add_argument("--hide-depth", action="store_true", help="Disable depth lines")
    parser.add_argument("--show-spread", action="store_true", default=False)
    parser.add_argument("--show-trades", action="store_true", default=True)
    parser.add_argument("--hide-trades", action="store_true", help="Disable trade markers")
    parser.add_argument("--show-pnl", action="store_true", default=True)
    parser.add_argument("--hide-pnl", action="store_true", help="Disable PnL chart")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not os.path.isfile(args.log):
        raise FileNotFoundError(f"File not found: {args.log}")
    if os.path.getsize(args.log) == 0:
        raise ValueError(f"Empty file: {args.log}")

    bundle = load_log_bundle(args.log)
    activities = bundle["activities"]
    trades = bundle["trade_history"]
    logs_df = bundle["logs"]

    if activities.empty or "timestamp" not in activities.columns:
        raise ValueError("No activities/timestamp data found in log.")
    products = sorted(activities["product"].dropna().astype(str).unique().tolist())
    if not products:
        raise ValueError("No products found in activities data.")

    selected_product = args.product if args.product in products else products[0]
    t_min = args.t_min if args.t_min is not None else int(activities["timestamp"].min())
    t_max = args.t_max if args.t_max is not None else int(activities["timestamp"].max())
    show_depth = args.show_depth and not args.hide_depth
    show_trades = args.show_trades and not args.hide_trades
    show_pnl = args.show_pnl and not args.hide_pnl

    market_fig = make_market_figure(
        activities,
        trades,
        product=selected_product,
        t_min=t_min,
        t_max=t_max,
        show_depth=show_depth,
        show_spread=args.show_spread,
        show_trades=show_trades,
    )
    pnl_fig = make_pnl_figure(activities, t_min=t_min, t_max=t_max)
    html = render_html(
        log_path=args.log,
        product=selected_product,
        t_min=t_min,
        t_max=t_max,
        market_fig=market_fig,
        pnl_fig=pnl_fig,
        logs_df=logs_df,
        show_pnl=show_pnl,
    )

    with open(args.out, "w", encoding="utf-8") as outfile:
        outfile.write(html)
    print(f"Wrote dashboard to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
