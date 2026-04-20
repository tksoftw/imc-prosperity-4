# Round Data Lab

Visual analyzer and synthetic data generator for `data/ROUND_N`.

Architecture:
- `models.py`: shared model layer for snapshots, boundary bands, and product profiles
- `analyzer.py`: analysis / classification logic
- `generator.py`: synthetic order-book series generator
- `server.py`: FastAPI controller layer
- `frontend/index.html`: browser view

Run:

```bash
uvicorn tools.round_data_lab.server:app --reload --port 8002
```

Open [http://127.0.0.1:8002](http://127.0.0.1:8002).
