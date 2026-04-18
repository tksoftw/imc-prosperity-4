# Allocation Optimizer (no Streamlit reruns)

A tiny FastAPI + static-HTML replacement for `tools/allocation_cluster_plotly.py`.

Charts update in place via `Plotly.react()` instead of re-mounting the whole
page like Streamlit does. The heavy lifting stays in Python — the server just
wraps `build_profit_grid_fast` from `tools/allocation.py` and returns raw JSON
arrays.

## Run (from top-level directory)

```bash
source .venv/bin/activate
uvicorn tools.allocation_webviz.server:app --reload --port 8001
```

Then open http://localhost:8001.

## Controls

- **Base floor**: constant bid added to every integer in `[0, 100]`.
- **Clusters**: each has `center` (mode), `size` (total headcount), `width`
  (triangular spread). Set `width=0` for a spike at `center`.
- **Probe z**: highlights one `z` on the speed curve and shows the rank/speed.

## How the no-rerender works

1. The browser keeps all DOM/Plotly nodes mounted.
2. On every control change, it debounces (120 ms) then `POST /api/compute`
   with the current config (cheap — just a list of clusters).
3. The server calls `build_profit_grid_fast` and returns arrays.
4. `Plotly.react(div, traces, layout, config)` diffs the new data against the
   previously rendered traces and patches only what changed — camera position
   on the 3D surface, zoom state on the heatmap, etc. are preserved.

## API

`POST /api/compute`

```json
{
  "clusters": [{ "id": 1, "center": 99, "size": 80, "width": 1 }],
  "base_floor": 0,
  "probe_z": 36
}
```

Returns:

```jsonc
{
  "bids": [0, 0, ..., 5],              // length 101
  "speed_curve": [0.9, 0.89, ...],     // length 101
  "above_curve": [300, 295, ...],      // length 101
  "profit_grid": [[null, 1200, ...], ...], // [y][x], nulls where x+y > 100
  "best": { "x": 8, "y": 44, "z": 36, "profit": 42973.12 },
  "total_bids": 303,
  "probe": { "z": 36, "speed": 0.5123, "rank": 180, "total": 304 }
}
```
