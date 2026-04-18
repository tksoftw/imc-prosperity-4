# IMC Prosperity 4

[Official Discord Server](https://discord.gg/SABeB8uKxd) | [Official Wiki](https://imc-prosperity.notion.site/prosperity-4-wiki)

---

## Setup (WSL + uv)

This project runs on Linux/WSL using [uv](https://docs.astral.sh/uv/) for
dependency management. On Windows, do **all** of this inside WSL, not
PowerShell — Streamlit/Uvicorn/matplotlib tooling is much happier on Linux.

### 1. Install WSL (Windows only — **macOS users skip to step 2**)

From an **admin** PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot, launch `Ubuntu` from the Start menu, create your user, then:

```bash
sudo apt update && sudo apt install -y build-essential git curl
```

> **Cisco AnyConnect / corporate VPN users:** WSL2's default NAT networking
> breaks while AnyConnect is connected (DNS + `curl` fail inside WSL). Fix it
> by switching WSL to mirrored networking. Create `C:\Users\<you>\.wslconfig`
> (e.g. `C:\Users\tk\.wslconfig`) containing:
>
> ```ini
> [wsl2]
> networkingMode=mirrored
> ```
>
> Then from PowerShell run `wsl --shutdown` and reopen Ubuntu. Verify with
> `curl -I https://astral.sh` from inside WSL.

### 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec bash        # reload shell so `uv` is on PATH
uv --version
```

### 3. Clone + sync

```bash
git clone <this-repo> imc-prosperity-4
cd imc-prosperity-4

# Creates .venv/, installs everything from pyproject.toml + uv.lock.
# Add --extra dev for the ML notebook / test deps.
uv sync --extra dev
```

`uv sync` resolves the lockfile, creates `.venv/`, and installs exactly the
pinned versions. Rerun it whenever `pyproject.toml` or `uv.lock` changes.

### 4. Activate the venv

From the repo root:

```bash
source .venv/bin/activate
python3 --version
deactivate      # exit
```

> **Optional:** add this alias to your `~/.bashrc` so you can just type `vv`
> from the repo root to activate:
>
> ```bash
> alias vv="source .venv/bin/activate"
> ```
>
> Then `source ~/.bashrc` to pick it up in the current shell.

### 5. Editor extensions (optional, VS Code / Cursor)

- [Edit CSV](https://marketplace.visualstudio.com/items?itemName=janisdd.vscode-edit-csv)
- [Data Wrangler](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.datawrangler)

---

## Running things

```bash
vv # activate venv

# Allocation optimizer (interactive, no reruns). See tools/allocation_webviz/README.md
uvicorn tools.allocation_webviz.server:app --reload --port 8001

# CLI heatmap
python tools/allocation.py --heatmap

# Notebooks
jupyter lab
```

---

## External tools

- Backtester: <https://github.com/jmerle/imc-prosperity-3-backtester>
- Visualizer: <https://github.com/jmerle/imc-prosperity-3-visualizer>

## Reference repos & writeups

- [CarterT27/imc-prosperity-3](https://github.com/CarterT27/imc-prosperity-3) (9th overall, 2nd US)
- [Prosperity 3 Sauce doc](https://docs.google.com/document/d/1oYBRozQtJ6HgfmLOf4HesRJFKZLATWrYdkxZDLT47cU/edit?tab=t.0) (9th overall, 2nd US)

## Libraries allowed in submissions

[pandas](https://pandas.pydata.org/) ·
[NumPy](https://numpy.org/) ·
[statistics](https://docs.python.org/3.9/library/statistics.html) ·
[math](https://docs.python.org/3.9/library/math.html) ·
[typing](https://docs.python.org/3.9/library/typing.html) ·
[jsonpickle](https://jsonpickle.github.io/)
