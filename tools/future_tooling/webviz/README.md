# Web Visualizer (no Streamlit reruns)

This is a lightweight **FastAPI + Plotly Express** visualizer with a small API and a fast browser client.

## Install

```bash
source .venv/bin/activate
python -m pip install -e .
```

## Run

From repo root:

```bash
source .venv/bin/activate
uvicorn webviz.server:app --reload --port 8000
```

Open `http://localhost:8000` and set the log path (default `logs/94304.log`).

## Standalone Plotly Express file

If you want a single standalone script (no FastAPI dependency, no `Path()` usage), use:

```bash
python3 webviz/standalone_plotly_express.py --log logs/94304.log --out webviz_dashboard.html
```

Then open `webviz_dashboard.html` in your browser.

## API

- `GET /api/options?path=...`
- `GET /api/dashboard?path=...&product=...&t_min=...&t_max=...&show_depth=true&show_trades=true&show_pnl=true`

