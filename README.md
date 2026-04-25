# IMC Prosperity 4

## Quick Links
[Official Discord Server](https://discord.gg/SABeB8uKxd) |
[Official Wiki](https://imc-prosperity.notion.site/prosperity-4-wiki) |
[Online Visualizer/Leaderboard](https://prosperity.equirag.com/) |
[Hedgehogs writeup](examples/hedgehogs.md)

---

## Contents

- [Quickstart](#quickstart)
- [Tools we use](#tools-we-use)
- [Tools we built](#tools-we-built)
- [Optional additions](#optional-additions)
    - [Create a `.venv` alias](#create-a-venv-alias)
    - [Use WSL in VS Code/Cursor](#use-wsl-in-vs-codecursor)
    - [Editor extensions](#editor-extensions)
- [Writeups & reference repos](#writeups--reference-repos)

---

## Quickstart

### Windows

Admin PowerShell:
```powershell
# 1. From an ADMIN PowerShell. Reboot afterwards, then launch "WSL" from Start.
wsl --install
```
Ubuntu:
```bash
# 2. Inside the Ubuntu (WSL) shell from here on:
sudo apt update && sudo apt install -y build-essential git curl
curl -LsSf https://astral.sh/uv/install.sh | sh && exec bash
git clone https://github.com/tksoftw/imc-prosperity-4 && cd imc-prosperity-4
uv sync --extra dev && source .venv/bin/activate
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && . "$HOME/.cargo/env"
cargo install rust_backtester --locked
```

<details>
<summary><b>Trouble connecting to the Internet on WSL? (Cisco AnyConnect / other VPN)</b></summary>

>WSL2's default NAT networking breaks while AnyConnect is connected (DNS + `curl` fail inside WSL). Switch WSL to mirrored networking by creating `C:\Users\<you>\.wslconfig` with:
>
>```ini
>[wsl2]
>networkingMode=mirrored
>```
>
>Then from PowerShell run `wsl --shutdown` and reopen Ubuntu. Verify with `curl -I https://astral.sh` from inside WSL.

</details>


### macOS

```bash
xcode-select --install   # skip if already installed
curl -LsSf https://astral.sh/uv/install.sh | sh && exec zsh
git clone https://github.com/tksoftw/imc-prosperity-4 && cd imc-prosperity-4
uv sync --extra dev && source .venv/bin/activate
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && . "$HOME/.cargo/env"
cargo install rust_backtester --locked
```

---

## Tools we use

### [uv](https://docs.astral.sh/uv/)

Dependency manager and venv, run from the repo root. Basically a pip replacement. Main uses:

1. `source .venv/bin/activate` enter the venv (**important**, *cannot use repo without*)
2. `deactivate` exit the venv
3. `uv sync --extra dev` sync packages
4. `uv add <pkg>` add a new package
5. `uv remove <pkg>` remove a package


### [Rust backtester](https://github.com/tksoftw/prosperity_rust_backtester)

Note: We mostly use [rank_traders](#rank-traders) for quick backtesting. But otherwise, run `rust_backtester --help` for more information.

### [Online Visualizer](https://prosperity.equirag.com/)

Drop a submission `.log` to render fills, PnL, and the leaderboard. Logs from the [rust backtester](#rust-backtester) are located in the [runs](runs/) folder (generated after at least one backtest is run).

### Submission Libraries

The competition only allows the following libraries inside trader files
[pandas](https://pandas.pydata.org/),
[NumPy](https://numpy.org/),
[statistics](https://docs.python.org/3.9/library/statistics.html),
[math](https://docs.python.org/3.9/library/math.html),
[typing](https://docs.python.org/3.9/library/typing.html), and
[jsonpickle](https://jsonpickle.github.io/)

---

## Tools we built

> Note: Run all programs in the `.venv`

### General tools

### [rank traders](tools/rank_traders.py)

`python3 tools/rank_traders.py --round <N>`
> also: show individual products with `--show-per-product`


### [round data lab](tools/round_data_lab/README.md)
`uvicorn tools.round_data_lab.server:app --reload --port 8002`

### By round

<details>
<summary>Round 1</summary>

### [orderbook optimizer](tools/orderbook.py)
`python3 tools/orderbook.py`

</details>

<details>
<summary>Round 2</summary>

### [allocation optimizer](tools/allocation_webviz/)
`uvicorn tools.allocation_webviz.server:app --reload --port 8001`

</details>

<details>
<summary>Round 3</summary>

### [round 3 playground](tools/round_3_playground.py)
`python3 tools/round_3_playground.py`
> headless mode: `python3 tools/round_3_playground.py --no-gui --b1 755 --b2 840 --avg-b2 840`

</details>

## Optional additions

### Create a venv alias

Add to the following to your `~/.bashrc` file (or `~/.zshrc` on macOS):

```bash
alias vv="source .venv/bin/activate"
```

Then do `source ~/.bashrc`. Now you can type `vv` to activate the venv.

### Use WSL in VS Code/Cursor

1. Open VS Code on Windows
2. Press `CTRL`+`SHIFT`+`P`
3. Type in `WSL: Connect to WSL`

You'll see a green "WSL" indicator in the bottom-left once connected.

### Editor extensions

[Edit CSV](https://marketplace.visualstudio.com/items?itemName=janisdd.vscode-edit-csv)

[Data Wrangler](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.datawrangler)

---

## Writeups & reference repos

### [Hedgehogs writeup](examples/hedgehogs.md)
2nd overall, very detailed.

### [CarterT27/imc-prosperity-3](https://github.com/CarterT27/imc-prosperity-3)
9th overall, 2nd US. Companion [Prosperity 3 Sauce doc](https://docs.google.com/document/d/1oYBRozQtJ6HgfmLOf4HesRJFKZLATWrYdkxZDLT47cU/edit?tab=t.0).

### [Rust Backtester (fork)](https://github.com/tksoftw/prosperity_rust_backtester)
Source for the `rust_backtester` binary used by `rank_traders.py`.
