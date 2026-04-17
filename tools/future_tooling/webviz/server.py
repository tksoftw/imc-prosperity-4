import json
import os
from io import StringIO
from typing import Any

import pandas as pd
import plotly.express as px
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(default_response_class=ORJSONResponse)


def load_bundle(log_path: str) -> dict[str, Any]:
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail=f"File not found: {log_path}")
    if os.path.getsize(log_path) == 0:
        raise HTTPException(status_code=400, detail=f"Empty file: {log_path}")

    with open(log_path, encoding="utf-8") as infile:
        payload = json.load(infile)

    activities_df = pd.read_csv(StringIO(payload.get("activitiesLog", "")), sep=";")
    trades_df = pd.DataFrame(payload.get("tradeHistory", []))
    logs_df = pd.DataFrame(payload.get("logs", payload.get("graphLog", [])))

    if "timestamp" in activities_df.columns:
        activities_df["timestamp"] = pd.to_numeric(activities_df["timestamp"], errors="coerce")
    if "product" in activities_df.columns:
        activities_df["product"] = activities_df["product"].astype(str)
    if "profit_and_loss" in activities_df.columns:
        activities_df["profit_and_loss"] = pd.to_numeric(activities_df["profit_and_loss"], errors="coerce")
    for col in activities_df.columns:
        if col.startswith(("bid_price_", "ask_price_", "mid_price")):
            activities_df[col] = pd.to_numeric(activities_df[col], errors="coerce")

    if not trades_df.empty:
        trades_df["timestamp"] = pd.to_numeric(trades_df.get("timestamp"), errors="coerce")
        trades_df["price"] = pd.to_numeric(trades_df.get("price"), errors="coerce")
        trades_df["quantity"] = pd.to_numeric(trades_df.get("quantity"), errors="coerce")
        trades_df["side"] = "external"
        if "buyer" in trades_df.columns:
            trades_df.loc[trades_df["buyer"] == "SUBMISSION", "side"] = "our_buy"
        if "seller" in trades_df.columns:
            trades_df.loc[trades_df["seller"] == "SUBMISSION", "side"] = "our_sell"

    if not logs_df.empty and "timestamp" in logs_df.columns:
        logs_df["timestamp"] = pd.to_numeric(logs_df["timestamp"], errors="coerce")

    return {"activities": activities_df, "trades": trades_df, "logs": logs_df}


def build_market_figure(
    activities: pd.DataFrame,
    trades: pd.DataFrame,
    product: str,
    t_min: int,
    t_max: int,
    show_depth: bool,
    show_trades: bool,
) -> dict[str, Any]:
    market = activities[
        (activities["product"] == product)
        & (activities["timestamp"] >= t_min)
        & (activities["timestamp"] <= t_max)
    ].sort_values("timestamp")

    fig = px.line(title=f"{product} Market")
    if market.empty:
        return fig.to_dict()

    if "mid_price" in market.columns:
        mid = market[["timestamp", "mid_price"]].rename(columns={"mid_price": "value"})
        mid["series"] = "mid_price"
        for trace in px.line(mid, x="timestamp", y="value", color="series").data:
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
                depth = market[["timestamp", col]].rename(columns={col: "value"})
                depth["series"] = name
                for trace in px.line(depth, x="timestamp", y="value", color="series").data:
                    trace.opacity = opacity
                    trace.line.width = 1
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
            scatter = px.scatter(
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
            for trace in scatter.data:
                trace.name = f"trades:{trace.name}"
                trace.marker.opacity = 0.85
                fig.add_trace(trace)

    fig.update_layout(
        height=480,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
        legend={"orientation": "h"},
        xaxis_title="timestamp",
        yaxis_title="price",
    )
    return fig.to_dict()


def build_pnl_figure(activities: pd.DataFrame, t_min: int, t_max: int) -> dict[str, Any]:
    pnl = activities[(activities["timestamp"] >= t_min) & (activities["timestamp"] <= t_max)].drop_duplicates("timestamp")
    if pnl.empty or "profit_and_loss" not in pnl.columns:
        return px.line(title="PnL").to_dict()
    fig = px.line(pnl, x="timestamp", y="profit_and_loss", title="PnL")
    fig.update_traces(line={"color": "#2ca02c", "width": 2})
    fig.update_layout(
        height=320,
        margin={"l": 40, "r": 20, "t": 40, "b": 40},
        showlegend=False,
        xaxis_title="timestamp",
        yaxis_title="PnL",
    )
    return fig.to_dict()


@app.get("/api/options")
def api_options(path: str = Query(..., description="Path to simulator .log JSON file")) -> dict[str, Any]:
    bundle = load_bundle(path)
    activities = bundle["activities"]
    if activities.empty or "timestamp" not in activities.columns or "product" not in activities.columns:
        raise HTTPException(status_code=400, detail="Missing activities/product/timestamp data.")
    products = sorted(activities["product"].dropna().unique().tolist())
    if not products:
        raise HTTPException(status_code=400, detail="No products found.")
    return {"products": products, "min_ts": int(activities["timestamp"].min()), "max_ts": int(activities["timestamp"].max())}


@app.get("/api/dashboard")
def api_dashboard(
    path: str,
    product: str,
    t_min: int,
    t_max: int,
    show_depth: bool = True,
    show_trades: bool = True,
    show_pnl: bool = True,
) -> dict[str, Any]:
    bundle = load_bundle(path)
    activities = bundle["activities"]
    trades = bundle["trades"]
    logs = bundle["logs"]
    return {
        "market_figure": build_market_figure(activities, trades, product, t_min, t_max, show_depth, show_trades),
        "pnl_figure": build_pnl_figure(activities, t_min, t_max) if show_pnl else None,
        "logs": (
            logs[(logs["timestamp"] >= t_min) & (logs["timestamp"] <= t_max)].tail(80).to_dict(orient="records")
            if not logs.empty and "timestamp" in logs.columns
            else []
        ),
    }


frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
