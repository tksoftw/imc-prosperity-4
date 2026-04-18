# IMC Prosperity 4

## Quick Links
[Official Discord Server](https://discord.gg/SABeB8uKxd) |
[Official Wiki](https://imc-prosperity.notion.site/prosperity-4-wiki) |
[Online Visualizer/Leaderboard](https://prosperity.equirag.com/) | 
[Hedgehogs writeup](examples/hedgehogs.md)


---

## Contents

- [Setup (WSL + uv)](#setup-wsl--uv)
  - [1. Install WSL (Windows only. macOS users skip to step 2)](#1-install-wsl-windows-only--macos-users-skip-to-step-2)
  - [2. Install uv](#2-install-uv)
  - [3. Clone + sync](#3-clone--sync)
  - [4. Activate the venv](#4-activate-the-venv)
  - [5. Connect VS Code to WSL](#5-connect-vs-code-to-wsl)
  - [6. Editor extensions (optional, VS Code / Cursor)](#6-editor-extensions-optional-vs-code--cursor)
- [Running things](#running-things)
- [External tools](#external-tools)
- [Reference repos & writeups](#reference-repos--writeups)
- [Libraries allowed in submissions](#libraries-allowed-in-submissions)

---

## Setup (WSL + uv)

This project runs on Linux/WSL using [uv](https://docs.astral.sh/uv/) for
dependency management. On Windows, you should do **all** of this inside WSL, not
PowerShell. We primarally need WSL for the [backtester](https://github.com/tksoftw/prosperity_rust_backtester), but Streamlit/Uvicorn/matplotlib tooling is much easier on Linux.

### 1. Install WSL (Windows only. **macOS users skip to step 2**)

From an **admin** PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot, launch `Ubuntu` from the Start menu, create your user, then:

```bash
sudo apt update && sudo apt install -y build-essential git curl
```

> **Cisco AnyConnect users:** WSL2's default NAT networking
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

### 5. Connect VS Code to WSL

Open VS Code on Windows and connect it to your WSL environment:

1. Press **Ctrl+Shift+P** to open the command palette
2. Type **"connect to wsl"**
3. Select **WSL: Connect to WSL**

This allows you to edit code on Windows while running everything (Python, git, etc.) inside WSL. You'll see a green "WSL" indicator in the bottom-left corner of VS Code once connected.


### 6. Editor extensions (optional, VS Code / Cursor)

- [Edit CSV](https://marketplace.visualstudio.com/items?itemName=janisdd.vscode-edit-csv)
- [Data Wrangler](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.datawrangler)

---

## Running things

```bash
vv # activate venv

# rank trader backtests
python3 tools/rank_traders.py --round 1

# Allocation optimizer (interactive, no reruns). See tools/allocation_webviz/README.md
uvicorn tools.allocation_webviz.server:app --reload --port 8001

# CLI heatmap
python tools/allocation.py --heatmap

# add a package to uv
uv add jsontreeview

# sync packages
uv sync
```

---

## External tools

- [Rust Backtester (fork)](https://github.com/tksoftw/prosperity_rust_backtester)
- [Online Visualizer/Leaderboard](https://prosperity.equirag.com/)

## Reference repos & writeups

- [CarterT27/imc-prosperity-3](https://github.com/CarterT27/imc-prosperity-3) (9th overall, 2nd US)
- [Prosperity 3 Sauce doc](https://docs.google.com/document/d/1oYBRozQtJ6HgfmLOf4HesRJFKZLATWrYdkxZDLT47cU/edit?tab=t.0) (9th overall, 2nd US)
- [Hedgehogs writeup](examples/hedgehogs.md) (2nd overall, very good, detailed writeup)

## Libraries allowed in submissions

[pandas](https://pandas.pydata.org/) ·
[NumPy](https://numpy.org/) ·
[statistics](https://docs.python.org/3.9/library/statistics.html) ·
[math](https://docs.python.org/3.9/library/math.html) ·
[typing](https://docs.python.org/3.9/library/typing.html) ·
[jsonpickle](https://jsonpickle.github.io/)
