# From data_analysis.ipynb

## Product-wise Data Overview and Statistics

## Visualization 1: Mid Price Over Time (Separate Chart Per Product and Day, with Rolling Average)

## Visualization 2: Bid-Ask Spread Over Time (Separate Chart Per Product and Day, with Rolling Average)

## Visualization 3: Trading Volume and Price Distribution

## Visualization 4: Bid and Ask Prices with Mid Price (Separate Chart Per Product and Day, with Rolling Average)

## Visualization 5: Trading Activity Analysis

## Summary Insights

## Visualization: Bid/Ask Price with Spread (Per Product)

---

# From log_visualizer.ipynb

# Log Visualizer

Interactive notebook dashboard for Prosperity `.log` files.

This notebook:
- reads one `.log` file
- exposes the same split sections as `write_csvs.py`
- shows interactive market/PnL/trade plots with Plotly
- lets you inspect logs in a selected time window

---

# From prices_linear_model_analysis.ipynb

# Trading Data Analysis and Linear Price Model

This notebook loads round-1 price CSV files, visualizes market behavior, and trains a linear regression model in scikit-learn to predict `mid_price`.

---

# From round1_combo_analysis.ipynb

# Round 1 Combo Strategy Analysis

This notebook documents the simple observations that led to `ROUND_1/pepper_osmium_combo.py`:

- `INTARIAN_PEPPER_ROOT` trends upward almost linearly within each day, so an early long position is extremely valuable.
- `ASH_COATED_OSMIUM` is much closer to a stationary market-making product, and quoting one tick inside only pays when the spread is still comfortably wide.


---

# From round1_ml_strategy.ipynb

# Round 1 ML Strategy
This notebook trains and evaluates lightweight machine-learning signals on the official `data/ROUND_1` order books, then compares the final deployed trader in `ROUND_1/ml_round1_trader.py` against the strongest pre-ML baselines already present in the repo.

## Strongest Existing Baseline
Before adding any ML, the repo already contained a spread-based Pepper+Osmium family. We can recover the best complete 3-day non-ML trader directly from `runs/*/metrics.json`.

## Model Benchmark
We compare a few real ML models out-of-sample with leave-one-day-out validation. For Osmium, the useful target is short-horizon mid-price change; for Pepper, the useful target is longer-horizon drift because the dominant edge is carrying inventory into the close.

## Final Submission-Safe Osmium Model
The deployed trader uses a small linear model so the runtime stays within the Prosperity library constraints. The coefficients below are fitted offline, then embedded directly into `ROUND_1/ml_round1_trader.py`.

## Final Backtest
These are the checked-in results for `ROUND_1/ml_round1_trader.py`, using the Rust backtester on all three official Round 1 days.

## Result
The final strategy keeps Pepper simple and inventory-heavy because the learned end-value slope is consistently positive, then uses a lightweight ML fair-value model to shift Osmium quoting and inventory unwind. That combination materially improves Round 1 PnL over the earlier heuristic baselines while staying submission-safe.

---

# From round1_outside_box_visuals.ipynb

# Round 1 Outside-Box Visuals

This notebook renders the core plots behind `ROUND_1/pepper_osmium_outside_box.py`:

- Pepper root's near-deterministic drift and the passive sell band that gets lifted.
- One-step mean reversion in both products.
- Final score comparison and a representative PnL-path comparison.


---

# From round_0_tutorial.ipynb

## Strategy: simple market-making

1. **EMERALDS**: fair = 10000 (fixed). Post buy at 9998, sell at 10002.
2. **TOMATOES**: fair = (best_bid + best_ask) / 2 (dynamic). Post buy at fair-1, sell at fair+1.