"""Compile ROUND_N traders with local imports into one pasted submission file.

The generated file is intentionally plain: each local dependency is pasted
into a small builder function, local import statements are rewritten from the
AST, and the final module's ``Trader`` class is exposed at top level.
"""

from __future__ import annotations

import argparse
import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

ROUND_RE = re.compile(r"^ROUND_(\d+)$")

# Canonical handle for a local module: (round_num, module_name).
# Cross-round imports (e.g. ROUND_4 → ROUND_3 base trader) need the
# round tag because two rounds may legally have files with the same
# stem.
Module = tuple[int, str]


@dataclass(frozen=True)
class CompileResult:
    src: Path
    dst: Path
    modules: tuple[Module, ...]


def default_round() -> int | None:
    rounds = []
    for path in ROOT.glob("ROUND_*"):
        if path.is_dir() and (m := ROUND_RE.match(path.name)):
            rounds.append(int(m.group(1)))
    return max(rounds, default=None)


def round_dir(round_num: int) -> Path:
    return ROOT / f"ROUND_{round_num}"


def module_name(path: Path) -> str:
    return path.stem


def module_path(round_num: int, name: str) -> Path:
    return round_dir(round_num) / f"{name}.py"


def _round_from_pkg(name: str) -> int | None:
    """Return N for `ROUND_N`, else None."""
    m = ROUND_RE.match(name)
    return int(m.group(1)) if m else None


def local_dependency_names(path: Path, round_num: int) -> set[Module]:
    """Return (round_num, module_name) pairs for local imports in ``path``.

    Recognises imports from the entry's own round AND from any other
    ROUND_N package — this is required because ROUND_4 traders often
    inherit from ROUND_3 base classes (trader_FLIPVOL etc.). Bare
    relative or unqualified imports default to ``round_num``.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    deps: set[Module] = set()

    def _add(target_round: int, candidate: str) -> bool:
        if module_path(target_round, candidate).exists():
            deps.add((target_round, candidate))
            return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Absolute, e.g. `from X import …` (level 0).
            if node.level == 0 and node.module:
                # `from ROUND_N import trader_X as alias`
                if (target := _round_from_pkg(node.module)) is not None:
                    for alias in node.names:
                        _add(target, alias.name.split(".", 1)[0])
                    continue
                # `from ROUND_N.trader_X import Foo`
                if "." in node.module:
                    head, tail = node.module.split(".", 1)
                    if (target := _round_from_pkg(head)) is not None:
                        if _add(target, tail.split(".", 1)[0]):
                            continue
                # `from trader_X import Foo` — same round (rare/legacy).
                _add(round_num, node.module.split(".", 1)[0])
                continue

            # `from . import trader_X`
            if node.level == 1 and node.module is None:
                for alias in node.names:
                    _add(round_num, alias.name.split(".", 1)[0])
                continue

            # `from .trader_X import Foo`
            if node.level == 1 and node.module:
                _add(round_num, node.module.split(".", 1)[0])
                continue

        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import ROUND_N.trader_X as x`
                parts = alias.name.split(".")
                if len(parts) >= 2 and (target := _round_from_pkg(parts[0])) is not None:
                    if _add(target, parts[1]):
                        continue
                # `import trader_X as x` — same round (rare/legacy).
                _add(round_num, parts[0])

    deps.discard((round_num, module_name(path)))
    return deps


def local_module_expr(name: str) -> str:
    return f"globals()[{name!r}]"


def alias_syntax(alias: ast.alias) -> str:
    return alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"


def import_from_syntax(node: ast.ImportFrom, aliases: list[ast.alias]) -> str:
    dots = "." * node.level
    module = "" if node.module is None else node.module
    names = ", ".join(alias_syntax(a) for a in aliases)
    return f"from {dots}{module} import {names}"


def import_syntax(aliases: list[ast.alias]) -> str:
    return "import " + ", ".join(alias_syntax(a) for a in aliases)


def local_import_replacement(node: ast.AST, round_num: int) -> tuple[list[str] | None, set[str]]:
    """Return replacement lines for local imports, plus future imports seen.

    ``None`` means leave the source text untouched. An empty list means remove
    the statement. Recognises ROUND_N prefixes for ANY N — not just the
    entry trader's round — so cross-round inheritance works.
    """
    futures: set[str] = set()

    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
        futures.update(alias.name for alias in node.names)
        return [], futures

    if isinstance(node, ast.ImportFrom):
        replacements: list[str] = []
        remaining: list[ast.alias] = []

        def add_module_alias(alias: ast.alias, candidate: str) -> None:
            target = alias.asname or candidate
            replacements.append(f"{target} = {local_module_expr(candidate)}")

        def add_attr_alias(alias: ast.alias, candidate: str) -> None:
            if alias.name == "*":
                raise RuntimeError("cannot compile wildcard local import")
            target = alias.asname or alias.name
            replacements.append(f"{target} = {local_module_expr(candidate)}.{alias.name}")

        # Absolute imports.
        if node.level == 0 and node.module:
            target_round = _round_from_pkg(node.module)

            # `from ROUND_N import trader_X as alias`
            if target_round is not None:
                for alias in node.names:
                    candidate = alias.name.split(".", 1)[0]
                    if module_path(target_round, candidate).exists():
                        add_module_alias(alias, candidate)
                    else:
                        remaining.append(alias)

            # `from ROUND_N.trader_X import Foo`
            elif "." in node.module:
                head, tail = node.module.split(".", 1)
                target_round = _round_from_pkg(head)
                if target_round is not None:
                    candidate = tail.split(".", 1)[0]
                    if module_path(target_round, candidate).exists():
                        for alias in node.names:
                            add_attr_alias(alias, candidate)
                        return replacements, futures

            else:
                # `from trader_X import Foo` — legacy same-round form.
                candidate = node.module.split(".", 1)[0]
                if module_path(round_num, candidate).exists():
                    for alias in node.names:
                        add_attr_alias(alias, candidate)
                    return replacements, futures

        elif node.level == 1 and node.module is None:
            for alias in node.names:
                candidate = alias.name.split(".", 1)[0]
                if module_path(round_num, candidate).exists():
                    add_module_alias(alias, candidate)
                else:
                    remaining.append(alias)

        elif node.level == 1 and node.module:
            candidate = node.module.split(".", 1)[0]
            if module_path(round_num, candidate).exists():
                for alias in node.names:
                    add_attr_alias(alias, candidate)
                return replacements, futures

        if replacements:
            if remaining:
                replacements.append(import_from_syntax(node, remaining))
            return replacements, futures

    if isinstance(node, ast.Import):
        replacements = []
        remaining = []
        for alias in node.names:
            parts = alias.name.split(".")

            # `import ROUND_N.trader_X as x`
            if len(parts) >= 2 and (tgt := _round_from_pkg(parts[0])) is not None:
                candidate = parts[1]
                if module_path(tgt, candidate).exists():
                    target = alias.asname or parts[0]
                    value = (
                        local_module_expr(candidate) if alias.asname
                        else local_module_expr(parts[0])
                    )
                    replacements.append(f"{target} = {value}")
                    continue

            # `import trader_X as x` — legacy same-round form.
            root = parts[0]
            if module_path(round_num, root).exists():
                target = alias.asname or root
                replacements.append(f"{target} = {local_module_expr(root)}")
                continue

            remaining.append(alias)

        if replacements:
            if remaining:
                replacements.append(import_syntax(remaining))
            return replacements, futures

    return None, futures


def pasted_source(path: Path, round_num: int) -> tuple[str, set[str]]:
    """Return source with only local/future import lines rewritten."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    replacements: list[tuple[int, int, list[str]]] = []
    futures: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        repl, fut = local_import_replacement(node, round_num)
        futures.update(fut)
        if repl is None:
            continue
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        replacements.append((start, end, repl))

    for start, end, repl in sorted(replacements, reverse=True):
        lines[start:end] = repl

    text = "\n".join(lines).rstrip()
    return (text + "\n" if text else ""), futures


def dependency_order(entry: Path, round_num: int) -> list[Module]:
    """Depth-first dependency order, excluding the entry module itself."""
    visiting: set[Module] = set()
    visited: set[Module] = set()
    ordered: list[Module] = []
    entry_mod: Module = (round_num, module_name(entry))

    def visit(mod: Module, stack: tuple[Module, ...]) -> None:
        if mod in visited:
            return
        if mod in visiting:
            cycle = " -> ".join(f"ROUND_{r}.{n}" for r, n in (*stack, mod))
            raise RuntimeError(f"local import cycle detected: {cycle}")
        round_n, name = mod
        path = module_path(round_n, name)
        if not path.exists():
            raise FileNotFoundError(f"local import not found: {path}")

        visiting.add(mod)
        for dep in sorted(local_dependency_names(path, round_n)):
            visit(dep, (*stack, mod))
        visiting.remove(mod)
        visited.add(mod)
        if mod != entry_mod:
            ordered.append(mod)

    visit(entry_mod, ())
    return ordered


def render_compiled(entry: Path, round_num: int) -> tuple[str, tuple[Module, ...]]:
    deps = dependency_order(entry, round_num)
    entry_mod: Module = (round_num, module_name(entry))
    modules: list[Module] = [*deps, entry_mod]

    # Detect flat-namespace collisions: two modules with the same stem
    # would clobber each other in `globals()`. Cross-round trees that
    # legitimately have the same name (e.g. ROUND_3/trader_X.py AND
    # ROUND_4/trader_X.py both pulled in) need a more sophisticated
    # rename pass — fail loudly until that's needed.
    by_name: dict[str, list[Module]] = {}
    for mod in modules:
        by_name.setdefault(mod[1], []).append(mod)
    duplicates = {n: ms for n, ms in by_name.items() if len(ms) > 1}
    if duplicates:
        details = "; ".join(
            f"{n} in " + ", ".join(f"ROUND_{r}" for r, _ in ms)
            for n, ms in duplicates.items()
        )
        raise RuntimeError(f"local module name collision across rounds: {details}")

    pasted: dict[Module, str] = {}
    futures: set[str] = set()
    for mod in modules:
        round_n, name = mod
        source, module_futures = pasted_source(module_path(round_n, name), round_n)
        pasted[mod] = source
        futures.update(module_futures)

    chunks = [
        "# Auto-generated by `uv run compile`.",
        "# Do not edit by hand; edit the source trader and recompile.",
    ]
    if futures:
        chunks.append(f"from __future__ import {', '.join(sorted(futures))}")

    rounds_used = sorted({r for r, _ in modules})
    chunks.extend(
        [
            "",
            "class __CompiledModule:",
            "    pass",
            "",
        ]
    )
    for r in rounds_used:
        chunks.append(f"ROUND_{r} = __CompiledModule()")
    chunks.append("")

    for mod in modules:
        round_n, name = mod
        path = module_path(round_n, name)
        rel = path.relative_to(ROOT).as_posix()
        source = pasted[mod].rstrip()
        builder = f"__build_{name}"
        chunks.extend(
            [
                f"# --- begin pasted {rel} ---",
                f"def {builder}():",
                textwrap.indent(source, "    ") if source else "    pass",
                "",
                "    __m = __CompiledModule()",
                "    for __k, __v in list(locals().items()):",
                "        if not __k.startswith('__'):",
                "            setattr(__m, __k, __v)",
                "    return __m",
                "",
                f"{name} = {builder}()",
                f"ROUND_{round_n}.{name} = {name}",
                f"# --- end pasted {rel} ---",
                "",
            ]
        )

    chunks.extend(
        [
            f"Trader = {entry_mod[1]}.Trader",
            "",
            "__all__ = ['Trader']",
            "",
        ]
    )
    return "\n".join(chunks), tuple(modules)


def output_path_for(src: Path, round_num: int, out_dir: Path | None) -> Path:
    target_dir = out_dir if out_dir is not None else round_dir(round_num) / "compiled"
    return target_dir / f"{src.stem}_compiled.py"


def compile_one(src: Path, round_num: int, out_dir: Path | None) -> CompileResult:
    text, modules = render_compiled(src, round_num)
    dst = output_path_for(src, round_num, out_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return CompileResult(src=src, dst=dst, modules=modules)


def discover_traders(round_num: int) -> list[Path]:
    """All top-level traders in ROUND_N/. Never recurses; never returns
    files inside ROUND_N/compiled/ or any leading-underscore subdir."""
    rdir = round_dir(round_num)
    return sorted(
        p
        for p in rdir.glob("trader*.py")
        if p.is_file()
        and not p.name.startswith("_")
    )


def resolve_trader(arg: str, round_num: int) -> Path:
    """Resolve --trader against ROUND_N/. No fuzzy matching, no `.py`
    autocomplete, no path-traversal escape — must exist exactly where
    pointed."""
    rdir = round_dir(round_num)
    candidate = (rdir / arg).resolve()
    if not candidate.is_file():
        raise SystemExit(f"trader not found: {arg!r} (looked at {candidate})")
    try:
        candidate.relative_to(rdir.resolve())
    except ValueError as exc:
        raise SystemExit(f"trader {arg!r} resolved outside {rdir}: {candidate}") from exc
    return candidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    dflt = default_round()
    parser = argparse.ArgumentParser(
        prog="compile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Compile ROUND_N traders with local ROUND_N imports into one file.

            Examples:
              uv run compile --round 3 --trader trader_BUGALPHA.py
              uv run compile --round 3 --trader sub/trader_X.py
              uv run compile --round 3 --all
            """
        ),
    )
    parser.add_argument("-r", "--round", type=int, default=dflt,
                        required=dflt is None, metavar="N",
                        help=f"round number (default: {dflt if dflt is not None else 'required'})")
    parser.add_argument("--trader", action="append", dest="traders", metavar="PATH",
                        help="trader file relative to ROUND_N/. Repeatable. "
                             "No fuzzy matching: must exist exactly.")
    parser.add_argument("--all", action="store_true",
                        help="compile every ROUND_N/trader*.py that has at least one local import. "
                             "Traders with no local deps are skipped (their source is already self-contained).")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="output directory (default: ROUND_N/compiled)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.all and args.traders:
        raise SystemExit("use either --all or --trader, not both")

    round_num = int(args.round)
    rdir = round_dir(round_num)
    if not rdir.is_dir():
        raise SystemExit(f"round directory not found: {rdir}")

    if args.all:
        candidates = discover_traders(round_num)
        # Skip self-contained traders: --all is for compiling things
        # that NEED inlining, not for re-emitting copies of leaf traders.
        targets = [
            p for p in candidates
            if local_dependency_names(p, round_num)
        ]
        skipped = len(candidates) - len(targets)
    else:
        if not args.traders:
            raise SystemExit("provide --trader PATH, or use --all")
        targets = [resolve_trader(arg, round_num) for arg in args.traders]
        skipped = 0

    if not targets:
        if skipped:
            print(f"no traders with local imports found in ROUND_{round_num} "
                  f"({skipped} self-contained trader(s) skipped)")
            return 0
        raise SystemExit(f"no traders found in ROUND_{round_num}")

    results: list[CompileResult] = []
    for src in targets:
        result = compile_one(src.resolve(), round_num, args.out_dir)
        results.append(result)
        rel_dst = result.dst.relative_to(ROOT) if result.dst.is_relative_to(ROOT) else result.dst
        dep_count = max(0, len(result.modules) - 1)
        print(f"compiled {src.name} -> {rel_dst} ({dep_count} local import(s))")

    out_dir = args.out_dir or round_dir(round_num) / "compiled"
    msg = f"wrote {len(results)} file(s) to {out_dir}"
    if skipped:
        msg += f" (skipped {skipped} self-contained trader(s))"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
