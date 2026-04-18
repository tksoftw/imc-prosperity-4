from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd


def load_log_bundle(log_path: str | Path) -> dict[str, Any]:
    """Read a simulator .log file and normalize sections."""
    path = Path(log_path)
    with path.open() as infile:
        payload = json.load(infile)

    activities_log = payload.get("activitiesLog", "")
    logs = payload.get("logs", payload.get("graphLog", []))
    trade_history = payload.get("tradeHistory", [])

    activities_df = pd.read_csv(StringIO(activities_log), sep=";")
    if "timestamp" in activities_df.columns:
        activities_df["timestamp"] = pd.to_numeric(activities_df["timestamp"], errors="coerce")
    if "profit_and_loss" in activities_df.columns:
        activities_df["profit_and_loss"] = pd.to_numeric(
            activities_df["profit_and_loss"], errors="coerce"
        )

    for col in activities_df.columns:
        if col.startswith(("bid_price_", "ask_price_", "bid_volume_", "ask_volume_", "mid_price")):
            activities_df[col] = pd.to_numeric(activities_df[col], errors="coerce")

    logs_df = pd.DataFrame(logs)
    if not logs_df.empty and "timestamp" in logs_df.columns:
        logs_df["timestamp"] = pd.to_numeric(logs_df["timestamp"], errors="coerce")

    trades_df = pd.DataFrame(trade_history)
    if not trades_df.empty:
        trades_df["timestamp"] = pd.to_numeric(trades_df["timestamp"], errors="coerce")
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


def write_split_files(log_path: str | Path, out_dir: str | Path | None = None) -> dict[str, Path]:
    """Write split artifacts similar to write_csvs.py."""
    path = Path(log_path)
    out_base = Path(out_dir) if out_dir else path.parent
    out_base.mkdir(parents=True, exist_ok=True)

    data = load_log_bundle(path)
    activities_path = out_base / f"{path.stem}_activities.csv"
    logs_path = out_base / f"{path.stem}_logs.json"
    trades_path = out_base / f"{path.stem}_trade_history.json"

    data["activities"].to_csv(activities_path, sep=";", index=False)
    data["logs"].to_json(logs_path, orient="records", indent=4)
    data["trade_history"].to_json(trades_path, orient="records", indent=4)

    return {
        "activities": activities_path,
        "logs": logs_path,
        "trade_history": trades_path,
    }


def make_market_figure(
    activities: pd.DataFrame,
    trades: pd.DataFrame,
    product: str,
    t_min: int | float,
    t_max: int | float,
    show_depth: bool,
    show_trades: bool,
) -> Any:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    market = activities[
        (activities["product"] == product)
        & (activities["timestamp"] >= t_min)
        & (activities["timestamp"] <= t_max)
    ].copy()
    market = market.sort_values("timestamp")

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=[f"{product} Market", "PnL"],
    )

    fig.add_trace(
        go.Scatter(
            x=market["timestamp"],
            y=market["mid_price"],
            mode="lines",
            name="mid_price",
            line={"width": 2},
        ),
        row=1,
        col=1,
    )

    if show_depth:
        for col, name in [
            ("bid_price_1", "bid_1"),
            ("ask_price_1", "ask_1"),
            ("bid_price_2", "bid_2"),
            ("ask_price_2", "ask_2"),
            ("bid_price_3", "bid_3"),
            ("ask_price_3", "ask_3"),
        ]:
            if col in market.columns:
                fig.add_trace(
                    go.Scatter(
                        x=market["timestamp"],
                        y=market[col],
                        mode="lines",
                        name=name,
                        opacity=0.35,
                    ),
                    row=1,
                    col=1,
                )

    if show_trades and not trades.empty:
        local_trades = trades[
            (trades["symbol"] == product)
            & (trades["timestamp"] >= t_min)
            & (trades["timestamp"] <= t_max)
        ].copy()
        if not local_trades.empty:
            side_style = {
                "our_buy": ("triangle-up", "#1f77b4"),
                "our_sell": ("triangle-down", "#d62728"),
                "external": ("circle", "#7f7f7f"),
            }
            for side, chunk in local_trades.groupby("side"):
                marker_symbol, color = side_style.get(side, ("circle", "#7f7f7f"))
                fig.add_trace(
                    go.Scatter(
                        x=chunk["timestamp"],
                        y=chunk["price"],
                        mode="markers",
                        name=f"trades:{side}",
                        marker={"symbol": marker_symbol, "size": 9, "color": color},
                        customdata=chunk[["quantity"]],
                        hovertemplate=(
                            "t=%{x}<br>price=%{y}<br>qty=%{customdata[0]}<extra></extra>"
                        ),
                    ),
                    row=1,
                    col=1,
                )

    pnl = activities[
        (activities["timestamp"] >= t_min) & (activities["timestamp"] <= t_max)
    ].drop_duplicates("timestamp")
    if not pnl.empty and "profit_and_loss" in pnl.columns:
        fig.add_trace(
            go.Scatter(
                x=pnl["timestamp"],
                y=pnl["profit_and_loss"],
                mode="lines",
                name="profit_and_loss",
                line={"color": "#2ca02c"},
            ),
            row=2,
            col=1,
        )

    fig.update_layout(height=720, template="plotly_white", legend={"orientation": "h"})
    fig.update_xaxes(title_text="timestamp", row=2, col=1)
    fig.update_yaxes(title_text="price", row=1, col=1)
    fig.update_yaxes(title_text="PnL", row=2, col=1)
    return fig


def create_dashboard(log_path: str | Path) -> dict[str, Any]:
    """Render an interactive notebook dashboard for one log file."""
    import ipywidgets as widgets
    from IPython.display import display

    bundle = load_log_bundle(log_path)
    activities = bundle["activities"]
    trades = bundle["trade_history"]
    logs_df = bundle["logs"]

    products = sorted(activities["product"].dropna().unique().tolist())
    min_ts = int(activities["timestamp"].min())
    max_ts = int(activities["timestamp"].max())

    product_widget = widgets.Dropdown(options=products, description="Product:")
    range_widget = widgets.IntRangeSlider(
        value=[min_ts, max_ts],
        min=min_ts,
        max=max_ts,
        step=100,
        description="Window:",
        layout=widgets.Layout(width="95%"),
    )
    depth_widget = widgets.Checkbox(value=True, description="Show depth lines")
    trades_widget = widgets.Checkbox(value=True, description="Show trades")

    out_plot = widgets.Output()
    out_logs = widgets.Output()

    def _render(*_: Any) -> None:
        out_plot.clear_output(wait=True)
        out_logs.clear_output(wait=True)

        t_min, t_max = range_widget.value
        fig = make_market_figure(
            activities=activities,
            trades=trades,
            product=product_widget.value,
            t_min=t_min,
            t_max=t_max,
            show_depth=depth_widget.value,
            show_trades=trades_widget.value,
        )

        with out_plot:
            fig.show()

        if not logs_df.empty and "timestamp" in logs_df.columns:
            local_logs = logs_df[
                (logs_df["timestamp"] >= t_min) & (logs_df["timestamp"] <= t_max)
            ].sort_values("timestamp")
            with out_logs:
                if local_logs.empty:
                    print("No logs in selected range.")
                else:
                    display(local_logs.tail(30))
        else:
            with out_logs:
                print("No structured logs available in this file.")

    for w in [product_widget, range_widget, depth_widget, trades_widget]:
        w.observe(_render, names="value")

    controls = widgets.VBox(
        [
            widgets.HBox([product_widget, depth_widget, trades_widget]),
            range_widget,
        ]
    )

    display(controls, out_plot, widgets.HTML("<h4>Recent logs in window</h4>"), out_logs)
    _render()

    return bundle
