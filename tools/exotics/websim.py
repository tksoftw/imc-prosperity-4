import dash
from dash import dcc, html, Input, Output, State, ctx
import plotly.graph_objects as go
import numpy as np
from scipy.optimize import minimize

# --- Market Data & Constants ---
SPOT = 50.0
SIGMA = 2.51
TRADING_DAYS_PER_YEAR = 252
STEPS_PER_DAY = 4
DT = 1 / (TRADING_DAYS_PER_YEAR * STEPS_PER_DAY)
MULTIPLIER = 3000
BINARY_PAYOUT = 10
KO_BARRIER = 35

MARKET = [
    {'id': 'AC', 'name': 'AETHER_CRYSTAL', 'bid': 49.975, 'ask': 50.025, 'size': 200, 'type': 'Stock'},
    {'id': 'AC_50_P_2', 'name': 'AC_50_P_2', 'strike': 50, 'bid': 9.7, 'ask': 9.75, 'size': 50, 'type': 'Put 2W'},
    {'id': 'AC_50_C_2', 'name': 'AC_50_C_2', 'strike': 50, 'bid': 9.7, 'ask': 9.75, 'size': 50, 'type': 'Call 2W'},
    {'id': 'AC_50_P', 'name': 'AC_50_P', 'strike': 50, 'bid': 12, 'ask': 12.05, 'size': 50, 'type': 'Put 3W'},
    {'id': 'AC_50_C', 'name': 'AC_50_C', 'strike': 50, 'bid': 12, 'ask': 12.05, 'size': 50, 'type': 'Call 3W'},
    {'id': 'AC_35_P', 'name': 'AC_35_P', 'strike': 35, 'bid': 4.33, 'ask': 4.35, 'size': 50, 'type': 'Put 3W'},
    {'id': 'AC_40_P', 'name': 'AC_40_P', 'strike': 40, 'bid': 6.5, 'ask': 6.55, 'size': 50, 'type': 'Put 3W'},
    {'id': 'AC_45_P', 'name': 'AC_45_P', 'strike': 45, 'bid': 9.05, 'ask': 9.1, 'size': 50, 'type': 'Put 3W'},
    {'id': 'AC_60_C', 'name': 'AC_60_C', 'strike': 60, 'bid': 8.8, 'ask': 8.85, 'size': 50, 'type': 'Call 3W'},
    {'id': 'AC_50_CO', 'name': 'AC 50 Chooser', 'strike': 50, 'bid': 22.2, 'ask': 22.3, 'size': 50, 'type': 'Exotic'},
    {'id': 'AC_40_BP', 'name': 'AC 40 Binary Put', 'strike': 40, 'bid': 5.0, 'ask': 5.1, 'size': 50, 'type': 'Exotic'},
    {'id': 'AC_45_KO', 'name': 'AC 45 Knock-Out Put', 'strike': 45, 'bid': 0.15, 'ask': 0.175, 'size': 500, 'type': 'Exotic'}
]

# --- PRE-COMPUTE 100K PATHS FOR JUDGE SIMULATOR ---
print("Pre-computing 100k paths for Judge Simulator...")
SIM_PATHS = 100_000
Z_pre = np.random.standard_normal((SIM_PATHS, 60))
log_returns_pre = (-0.5 * SIGMA**2 * DT) + (SIGMA * np.sqrt(DT) * Z_pre)
log_paths_pre = np.cumsum(log_returns_pre, axis=1)
multipliers_pre = np.exp(log_paths_pre)

paths_pre = np.hstack([np.ones((SIM_PATHS, 1)), multipliers_pre]) * SPOT
S_2w_pre = paths_pre[:, 40]
S_3w_pre = paths_pre[:, 60]
min_S_pre = np.min(paths_pre[:, 1:], axis=1)

PAYOFFS_PRE = {}
for c in MARKET:
    cid = c['id']
    K = c.get('strike', 0)
    
    if cid == 'AC': po = S_3w_pre
    elif cid == 'AC_50_P_2': po = np.maximum(K - S_2w_pre, 0)
    elif cid == 'AC_50_C_2': po = np.maximum(S_2w_pre - K, 0)
    elif cid == 'AC_50_P': po = np.maximum(K - S_3w_pre, 0)
    elif cid == 'AC_50_C': po = np.maximum(S_3w_pre - K, 0)
    elif cid == 'AC_35_P': po = np.maximum(K - S_3w_pre, 0)
    elif cid == 'AC_40_P': po = np.maximum(K - S_3w_pre, 0)
    elif cid == 'AC_45_P': po = np.maximum(K - S_3w_pre, 0)
    elif cid == 'AC_60_C': po = np.maximum(S_3w_pre - K, 0)
    elif cid == 'AC_50_CO': po = np.where(S_2w_pre > K, np.maximum(S_3w_pre - K, 0), np.maximum(K - S_3w_pre, 0))
    elif cid == 'AC_40_BP': po = np.where(S_3w_pre < K, BINARY_PAYOUT, 0)
    elif cid == 'AC_45_KO': po = np.where(min_S_pre >= KO_BARRIER, np.maximum(K - S_3w_pre, 0), 0)
    
    PAYOFFS_PRE[cid] = po

# Initialize Dash with Tailwind CSS via CDN for identical styling
app = dash.Dash(__name__, external_scripts=['https://cdn.tailwindcss.com'])
app.title = "Aether Casino"

# --- Initial Empty Graphs ---
init_fig = go.Figure()
init_fig.add_hline(y=KO_BARRIER, line_dash="dash", line_color="#ef4444")
init_fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(visible=False), yaxis=dict(visible=False, range=[0, 100]))

init_judge_fig = go.Figure()
init_judge_fig.update_layout(template="plotly_dark", paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(visible=False), yaxis=dict(visible=False))

# Helper for stat rows
def stat_row(label, val_id):
    return html.Div(className="flex justify-between items-center bg-slate-800/80 p-4 rounded-xl border border-slate-700/50 shadow-sm", children=[
        html.Span(label, className="text-slate-400 font-bold text-sm tracking-widest"),
        html.Div(id=val_id, className="font-mono font-bold text-lg text-slate-500", children="-")
    ])

# --- Layout ---
app.layout = html.Div(className="min-h-screen bg-slate-950 text-slate-200 p-6 font-sans", children=[
    dcc.Store(id='store-bankroll', data=0),
    
    html.Div(className="max-w-6xl mx-auto", children=[
        
        # Header & Bankroll
        html.Div(className="flex flex-col md:flex-row justify-between items-center bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-2xl mb-8", children=[
            html.Div([
                html.H1("Aether Casino", className="text-3xl font-black text-transparent bg-clip-text bg-gradient-to-r from-purple-400 to-pink-600 uppercase tracking-widest"),
                html.P("100 Paths. Infinite Variance. No Refunds.", className="text-slate-400 text-sm mt-1")
            ]),
            html.Div(className="text-right mt-4 md:mt-0", children=[
                html.Div("Total Winnings", className="text-sm text-slate-500 uppercase tracking-wider font-bold"),
                html.Div(id='display-bankroll', className="text-4xl font-mono font-black text-emerald-400 drop-shadow-[0_0_10px_rgba(52,211,153,0.3)]", children="+0")
            ])
        ]),

        html.Div(className="grid grid-cols-1 lg:grid-cols-3 gap-8", children=[
            
            # Left Column: Betting Table
            html.Div(className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl", children=[
                html.Div(className="flex justify-between items-center border-b border-slate-800 pb-4 mb-4", children=[
                    html.H2("Place Your Bets", className="text-xl font-bold text-white"),
                    html.Div(className="flex flex-wrap justify-end gap-2", children=[
                        html.Button("DAY 1 DELTA HEDGE", id="btn-delta", n_clicks=0, className="px-3 py-1 bg-yellow-900/50 text-yellow-400 text-xs font-bold rounded hover:bg-yellow-800/50 border border-yellow-800/50 transition"),
                        html.Button("STRUCTURAL ARB", id="btn-struct", n_clicks=0, className="px-3 py-1 bg-blue-900/50 text-blue-400 text-xs font-bold rounded hover:bg-blue-800/50 border border-blue-800/50 transition"),
                        html.Button("🤖 AI: MAX SHARPE", id="btn-ai-sharpe", n_clicks=0, className="px-3 py-1 bg-fuchsia-900/50 text-fuchsia-400 text-xs font-bold rounded hover:bg-fuchsia-800/50 border border-fuchsia-800/50 transition"),
                        html.Button("🛡️ AI: MIN RISK", id="btn-ai-safe", n_clicks=0, className="px-3 py-1 bg-emerald-900/50 text-emerald-400 text-xs font-bold rounded hover:bg-emerald-800/50 border border-emerald-800/50 transition")
                    ])
                ]),

                html.Div(className="overflow-x-auto", children=[
                    html.Table(className="w-full text-left text-sm", children=[
                        html.Thead(
                            html.Tr(className="text-slate-500 uppercase text-xs border-b border-slate-800", children=[
                                html.Th("Instrument", className="py-3 font-medium"),
                                html.Th("Bid", className="py-3 font-medium text-right"),
                                html.Th("Ask", className="py-3 font-medium text-right"),
                                html.Th("Your Position", className="py-3 font-medium text-right")
                            ])
                        ),
                        html.Tbody([
                            html.Tr(className="border-b border-slate-800/50 hover:bg-slate-800/20 transition-colors", children=[
                                html.Td(className="py-3", children=[
                                    html.Div(c['name'], className="font-bold text-slate-300"),
                                    html.Div(c['type'], className="text-xs text-slate-600")
                                ]),
                                html.Td(f"{c['bid']:.3f}", className="py-3 text-right text-red-400/80 font-mono"),
                                html.Td(f"{c['ask']:.3f}", className="py-3 text-right text-emerald-400/80 font-mono"),
                                html.Td(className="py-3 text-right", children=[
                                    dcc.Input(
                                        id=f"qty-{c['id']}", type="number", value=0, min=-c['size'], max=c['size'], step=1,
                                        className="w-24 bg-slate-950 border border-slate-700 text-slate-300 rounded p-2 text-right font-mono focus:outline-none focus:border-blue-500"
                                    )
                                ])
                            ]) for c in MARKET
                        ])
                    ])
                ])
            ]),

            # Right Column: Game Output
            html.Div(className="flex flex-col gap-6", children=[
                
                # Action Button
                html.Button(
                    "Pull Lever", id="btn-pull", n_clicks=0,
                    className="w-full py-8 rounded-xl font-black text-2xl uppercase tracking-widest transition-all bg-gradient-to-b from-purple-500 to-pink-600 text-white hover:from-purple-400 hover:to-pink-500 shadow-[0_0_30px_rgba(217,70,239,0.4)] hover:shadow-[0_0_50px_rgba(217,70,239,0.6)] transform hover:-translate-y-1"
                ),

                # Last Result Box
                html.Div(className="bg-slate-900 border border-slate-800 rounded-xl p-6 text-center flex flex-col justify-center items-center h-48", children=[
                    html.Div("Result (100 Paths)", className="text-sm text-slate-500 uppercase tracking-wider font-bold mb-2"),
                    html.Div(id='display-last-pnl', className="text-slate-600 italic", children="Awaiting your roll...")
                ]),

                # Canvas View
                html.Div(className="bg-slate-900 border border-slate-800 rounded-xl p-4", children=[
                    html.Div(className="text-xs text-slate-500 uppercase font-bold mb-2 flex justify-between", children=[
                        html.Span("Path Visualizer"),
                        html.Span("--- KO Barrier", className="text-red-500")
                    ]),
                    html.Div(className="bg-slate-950 rounded-lg border border-slate-800 overflow-hidden relative", style={'height': '200px'}, children=[
                        dcc.Graph(
                            id='path-graph',
                            figure=init_fig,
                            config={'displayModeBar': False},
                            style={'width': '100%', 'height': '100%'}
                        )
                    ])
                ])
            ])
        ]),

        # --- FULL JUDGE SIMULATOR SECTION ---
        html.Div(className="mt-8 bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl", children=[
            html.Div(className="flex flex-col md:flex-row justify-between items-center border-b border-slate-800 pb-4 mb-6", children=[
                html.Div(className="mb-4 md:mb-0", children=[
                    html.H2("100,000 Judge Seed Distribution", className="text-xl font-bold text-white"),
                    html.P("See the true statistical edge and risk of your portfolio across 100,000 alternate realities.", className="text-slate-400 text-sm")
                ]),
                html.Button("RUN FULL SIMULATION", id="btn-judge", n_clicks=0, className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 text-white font-black uppercase tracking-wider rounded-lg transition shadow-[0_0_15px_rgba(79,70,229,0.4)]")
            ]),
            
            dcc.Loading(
                type="dot",
                color="#4f46e5",
                children=[
                    html.Div(className="grid grid-cols-1 lg:grid-cols-3 gap-8", children=[
                        # Graph
                        html.Div(className="lg:col-span-2 bg-slate-950 rounded-xl border border-slate-800", children=[
                            dcc.Graph(id='judge-graph', figure=init_judge_fig, style={'height': '500px'})
                        ]),
                        # Stats column
                        html.Div(className="flex flex-col gap-2 justify-center", children=[
                            stat_row("MEAN", "stat-mean"),
                            stat_row("STD", "stat-std"),
                            stat_row("SHARPE", "stat-sharpe"),
                            stat_row("P(LOSS)", "stat-ploss"),
                            stat_row("VAR 5%", "stat-var5"),
                            stat_row("CVAR 5%", "stat-cvar5"),
                            stat_row("MEDIAN", "stat-median"),
                            stat_row("P95", "stat-p95"),
                            stat_row("P99", "stat-p99"),
                        ])
                    ])
                ]
            )
        ])

    ])
])

# --- Callbacks ---

# Presets & AI Optimizer Logic
@app.callback(
    [Output(f"qty-{c['id']}", 'value') for c in MARKET],
    [Input('btn-delta', 'n_clicks'), Input('btn-struct', 'n_clicks'), Input('btn-ai-sharpe', 'n_clicks'), Input('btn-ai-safe', 'n_clicks')],
    prevent_initial_call=True
)
def apply_presets(b_delta, b_struct, b_ai_sharpe, b_ai_safe):
    if not ctx.triggered:
        return dash.no_update
        
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    outputs = []
    
    # --- UPGRADED LIVE SCIPY OPTIMIZER ---
    if button_id in ['btn-ai-sharpe', 'btn-ai-safe']:
        # Grab a robust subset of 10,000 paths so the solver is instant but highly accurate
        PAYOFFS_SUBSET = np.array([PAYOFFS_PRE[c['id']][:10000] for c in MARKET])
        mid_prices = np.array([(c['ask'] + c['bid'])/2 for c in MARKET])
        half_spreads = np.array([(c['ask'] - c['bid'])/2 for c in MARKET])
        
        def get_stats(q):
            cost = np.sum(q * mid_prices + np.abs(q) * half_spreads)
            pnls = np.tensordot(q, PAYOFFS_SUBSET, axes=1)
            net = (pnls - cost) * MULTIPLIER
            mean = np.mean(net)
            # Crucial fix: Evaluate STD of the *100-path average*, not single paths
            std = np.std(net) / 10.0 + 1e-6 
            return mean, std
            
        def obj_eval(q):
            mean, std = get_stats(q)
            if std == 0: return 1e9
            
            if button_id == 'btn-ai-sharpe':
                # MAX SHARPE: Maximize Mean with a small variance penalty.
                # Because it evaluates in raw DOLLARS, it naturally pushes to volume limits!
                return -(mean - 0.5 * std)
            else:
                # MIN RISK: Maximize the 95% worst-case lower bound (Mean - 2*Std).
                # Forces the optimizer to squeeze the variance to prevent negative PnL.
                return -(mean - 2.0 * std)
            
        bounds = [(-c['size'], c['size']) for c in MARKET]
        x0 = [101, 50, 50, 50, -50, 50, 0, 50, -50, -50, -50, 0] 
        
        res = minimize(obj_eval, x0, bounds=bounds, method='SLSQP')
        
        # 1. Round options securely
        q_opt = np.round(res.x)
        for i in range(1, 12):
            q_opt[i] = np.clip(q_opt[i], -MARKET[i]['size'], MARKET[i]['size'])
            
        # 2. Mathematical 1D Grid Search over Underlying to PERFECTLY neutralize delta mismatch from integer rounding
        best_obj = float('inf')
        best_ac = 0
        for ac in range(-MARKET[0]['size'], MARKET[0]['size'] + 1):
            q_test = q_opt.copy()
            q_test[0] = ac
            val = obj_eval(q_test)
            if val < best_obj:
                best_obj = val
                best_ac = ac
                
        q_opt[0] = best_ac
        return [int(x) for x in q_opt]

    # Manual Presets
    for c in MARKET:
        cid = c['id']
        if button_id == 'btn-delta':
            presets = {'AC': 101, 'AC_50_P_2': 50, 'AC_50_C_2': 50, 'AC_50_P': 50, 'AC_50_C': -50, 'AC_35_P': 50, 'AC_40_P': 0, 'AC_45_P': 50, 'AC_60_C': -50, 'AC_50_CO': -50, 'AC_40_BP': -50, 'AC_45_KO': 0}
            outputs.append(presets.get(cid, 0))
        elif button_id == 'btn-struct':
            presets = {'AC': 0, 'AC_50_P_2': 50, 'AC_50_C_2': 50, 'AC_50_P': 50, 'AC_50_C': 50, 'AC_50_CO': -50, 'AC_35_P': 0, 'AC_40_P': 0, 'AC_45_P': 0, 'AC_60_C': 0, 'AC_40_BP': 0, 'AC_45_KO': 0}
            outputs.append(presets.get(cid, 0))
            
    return outputs

# Input color styling dynamically
for c in MARKET:
    @app.callback(
        Output(f"qty-{c['id']}", 'className'),
        Input(f"qty-{c['id']}", 'value')
    )
    def style_input(val):
        base = "w-24 bg-slate-950 border rounded p-2 text-right font-mono focus:outline-none focus:border-blue-500 "
        try:
            val = int(val)
        except:
            val = 0
        if val > 0: return base + "border-emerald-500/50 text-emerald-400"
        if val < 0: return base + "border-red-500/50 text-red-400"
        return base + "border-slate-700 text-slate-300"


# Pull Lever / Simulate ONE Seed
@app.callback(
    [Output('display-bankroll', 'children'),
     Output('display-bankroll', 'className'),
     Output('display-last-pnl', 'children'),
     Output('display-last-pnl', 'className'),
     Output('path-graph', 'figure'),
     Output('store-bankroll', 'data')],
    [Input('btn-pull', 'n_clicks')],
    [State(f"qty-{c['id']}", 'value') for c in MARKET] + [State('store-bankroll', 'data')],
    prevent_initial_call=True
)
def run_casino(n_clicks, *args):
    current_bankroll = args[-1]
    raw_qtys = args[:-1]
    qtys = [int(q) if q else 0 for q in raw_qtys]
    
    total_cost = 0
    for q, c in zip(qtys, MARKET):
        if q > 0: total_cost += q * c['ask']
        elif q < 0: total_cost += q * c['bid']

    Z = np.random.standard_normal((100, 60))
    log_returns = (-0.5 * SIGMA**2 * DT) + (SIGMA * np.sqrt(DT) * Z)
    log_paths = np.cumsum(log_returns, axis=1)
    multipliers = np.exp(log_paths)
    
    paths = np.hstack([np.ones((100, 1)), multipliers]) * SPOT
    
    S_2w = paths[:, 40]
    S_3w = paths[:, 60]
    min_S = np.min(paths[:, 1:], axis=1)

    total_payoffs = np.zeros(100)
    for q, c in zip(qtys, MARKET):
        if q == 0: continue
        
        cid = c['id']
        K = c.get('strike', 0)
        
        # Exact string match
        if cid == 'AC': po = S_3w
        elif cid == 'AC_50_P_2': po = np.maximum(K - S_2w, 0)
        elif cid == 'AC_50_C_2': po = np.maximum(S_2w - K, 0)
        elif cid == 'AC_50_P': po = np.maximum(K - S_3w, 0)
        elif cid == 'AC_50_C': po = np.maximum(S_3w - K, 0)
        elif cid == 'AC_35_P': po = np.maximum(K - S_3w, 0)
        elif cid == 'AC_40_P': po = np.maximum(K - S_3w, 0)
        elif cid == 'AC_45_P': po = np.maximum(K - S_3w, 0)
        elif cid == 'AC_60_C': po = np.maximum(S_3w - K, 0)
        elif cid == 'AC_50_CO': po = np.where(S_2w > K, np.maximum(S_3w - K, 0), np.maximum(K - S_3w, 0))
        elif cid == 'AC_40_BP': po = np.where(S_3w < K, BINARY_PAYOUT, 0)
        elif cid == 'AC_45_KO': po = np.where(min_S >= KO_BARRIER, np.maximum(K - S_3w, 0), 0)
        
        total_payoffs += q * po
        
    avg_payoff = np.mean(total_payoffs)
    final_pnl = (avg_payoff - total_cost) * MULTIPLIER
    new_bankroll = current_bankroll + final_pnl

    fmt_pnl = f"+{round(final_pnl):,}" if final_pnl >= 0 else f"{round(final_pnl):,}"
    fmt_bankroll = f"+{round(new_bankroll):,}" if new_bankroll >= 0 else f"{round(new_bankroll):,}"
    
    pnl_class = "text-4xl md:text-5xl font-mono font-black animate-pulse " + ("text-emerald-400" if final_pnl >= 0 else "text-red-500")
    bankroll_class = "text-4xl font-mono font-black drop-shadow-[0_0_10px_rgba(52,211,153,0.3)] " + ("text-emerald-400" if new_bankroll >= 0 else "text-red-500")

    fig = go.Figure()
    x_axis = np.arange(61)
    for p in range(100):
        fig.add_trace(go.Scatter(x=x_axis, y=paths[p, :], mode='lines', line=dict(width=1, color='rgba(56, 189, 248, 0.15)'), hoverinfo='skip'))
    
    fig.add_hline(y=KO_BARRIER, line_dash="dash", line_color="#ef4444")
    fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(visible=False, fixedrange=True), yaxis=dict(visible=False, fixedrange=True, range=[0, min(np.max(paths), 200)]))

    return fmt_bankroll, bankroll_class, fmt_pnl, pnl_class, fig, new_bankroll


# --- FULL JUDGE SIMULATOR ---
@app.callback(
    [Output('judge-graph', 'figure'),
     Output('stat-mean', 'children'), Output('stat-std', 'children'),
     Output('stat-sharpe', 'children'), Output('stat-ploss', 'children'),
     Output('stat-var5', 'children'), Output('stat-cvar5', 'children'),
     Output('stat-median', 'children'), Output('stat-p95', 'children'),
     Output('stat-p99', 'children')],
    [Input('btn-judge', 'n_clicks')],
    [State(f"qty-{c['id']}", 'value') for c in MARKET],
    prevent_initial_call=True
)
def run_judge_simulator(n_clicks, *raw_qtys):
    qtys = [int(q) if q else 0 for q in raw_qtys]
    
    total_cost = 0
    for q, c in zip(qtys, MARKET):
        if q > 0: total_cost += q * c['ask']
        elif q < 0: total_cost += q * c['bid']
        
    port_payoffs = np.zeros(SIM_PATHS)
    for q, c in zip(qtys, MARKET):
        if q == 0: continue
        port_payoffs += q * PAYOFFS_PRE[c['id']]
        
    path_pnls = (port_payoffs - total_cost) * MULTIPLIER
    
    # Bootstrap 100,000 seeds (each seed is an average of 100 random paths)
    sampled_indices = np.random.randint(0, SIM_PATHS, size=(100000, 100))
    seed_pnls = np.mean(path_pnls[sampled_indices], axis=1)
    
    mean_pnl = np.mean(seed_pnls)
    std_pnl = np.std(seed_pnls)
    sharpe = mean_pnl / std_pnl if std_pnl > 0 else 0
    ploss = np.mean(seed_pnls < 0) * 100
    var5 = np.percentile(seed_pnls, 5)
    cvar5 = np.mean(seed_pnls[seed_pnls <= var5]) if len(seed_pnls[seed_pnls <= var5]) > 0 else 0
    median_pnl = np.median(seed_pnls)
    p95 = np.percentile(seed_pnls, 95)
    p99 = np.percentile(seed_pnls, 99)
    
    # Plotly Red/Green Histogram
    fig = go.Figure()
    counts, bin_edges = np.histogram(seed_pnls, bins=80)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    colors = ['#ef4444' if x < 0 else '#10b981' for x in bin_centers]
    
    fig.add_trace(go.Bar(
        x=bin_centers, y=counts, 
        marker_color=colors, 
        width=(bin_edges[1]-bin_edges[0]),
        showlegend=False
    ))
    
    fig.add_vline(x=mean_pnl, line_width=2, line_color="#38bdf8", annotation_text="E[PnL]", annotation_font_color="#38bdf8", annotation_position="top left")
    
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=40, r=40, t=40, b=40),
        xaxis_title="", yaxis_title=""
    )
    
    # Formatting helper
    def make_span(val, is_pct=False, is_sharpe=False):
        if np.isnan(val): return html.Span("-", className="text-slate-500")
        if is_pct:
            text = f"{val:.2f}%"
            color = "text-red-400" if val > 50 else ("text-emerald-400" if val < 25 else "text-yellow-400")
        elif is_sharpe:
            text = f"{val:.3f}"
            color = "text-emerald-400" if val > 0 else "text-red-400"
        else:
            text = f"${val:,.0f}" if val >= 0 else f"-${abs(val):,.0f}"
            color = "text-emerald-400" if val >= 0 else "text-red-400"
        return html.Span(text, className=color)

    return (fig, 
            make_span(mean_pnl), make_span(std_pnl), 
            make_span(sharpe, is_sharpe=True), make_span(ploss, is_pct=True), 
            make_span(var5), make_span(cvar5), 
            make_span(median_pnl), make_span(p95), make_span(p99))

if __name__ == '__main__':
    app.run(debug=True)
