from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .analyzer import analyze_round, analyze_snapshots, product_detail, round_summary
from .data_loader import discover_rounds
from .generator import (
    EVENT_MODE_OPTIONS,
    PARAMETER_SCHEMA,
    TYPE_OPTIONS,
    _valid_price_row,
    default_config,
    generate_rows,
    merge_config,
    rows_to_csv,
    rows_to_snapshots,
)


app = FastAPI(default_response_class=ORJSONResponse)


class GeneratorRequest(BaseModel):
    product_name: str | None = None
    type_key: str = "flat_random_walk"
    ticks: int | None = None
    seed: int | None = None
    start_price: float | None = None
    anchor_price: float | None = None
    drift_per_tick: float | None = None
    noise: float | None = None
    reversion_strength: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    lower_bound_offset: float | None = None
    upper_bound_offset: float | None = None
    spread_mean: float | None = None
    spread_jitter: float | None = None
    shock_probability: float | None = None
    shock_size: float | None = None
    shock_bias: float | None = None
    major_event_mode: str | None = None
    major_event_size: float | None = None
    major_event_persistence: float | None = None
    major_event_volatility: float | None = None
    volume_base: int | None = None


@app.get("/api/rounds")
def api_rounds() -> dict[str, Any]:
    rounds = []
    for round_num in discover_rounds():
        summary = round_summary(round_num)
        rounds.append(
            {
                "round": round_num,
                "product_count": len(summary["products"]),
                "products": summary["products"],
            }
        )
    return {"rounds": rounds}


@app.get("/api/round/{round_num}")
def api_round(round_num: int) -> dict[str, Any]:
    try:
        return round_summary(round_num)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/round/{round_num}/product/{product}")
def api_product(round_num: int, product: str) -> dict[str, Any]:
    try:
        return product_detail(round_num, product)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/generator/meta")
def api_generator_meta() -> dict[str, Any]:
    return {
        "types": TYPE_OPTIONS,
        "event_modes": EVENT_MODE_OPTIONS,
        "parameter_schema": PARAMETER_SCHEMA,
        "defaults": {type_key: default_config(type_key) for type_key in TYPE_OPTIONS},
    }


@app.post("/api/generator/preview")
def api_generator_preview(req: GeneratorRequest) -> dict[str, Any]:
    config = merge_config(req.model_dump())
    rows = [row for row in generate_rows(config) if _valid_price_row(row)]
    snapshots = rows_to_snapshots(rows)
    profile = analyze_snapshots(config["product_name"], snapshots, round_num=None)
    preview_n = 50
    return {
        "config": config,
        "profile": profile.to_dict(include_series=True),
        "rows": rows[:preview_n],
        "row_count": len(rows),
        "csv_preview": rows_to_csv(rows[:preview_n]),
        "csv_full": rows_to_csv(rows),
    }


@app.get("/api/healthz")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="info")