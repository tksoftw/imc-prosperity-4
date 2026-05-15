"""Inline trader imports into a standalone submission file."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

from tools.paths import ROOT, default_round, traders_dir

ROUND_RE = re.compile(r"^ROUND_(\d+)$")


def _is_docstring_stmt(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _body_without_leading_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and _is_docstring_stmt(body[0]):
        return body[1:]
    return body


class _StripDocstrings(ast.NodeTransformer):
    """Remove leading docstrings from Module / Class / def (only standard doc slots)."""

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        node.body = _body_without_leading_docstring(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        node.body = _body_without_leading_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        self.generic_visit(node)
        node.body = _body_without_leading_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self.generic_visit(node)
        node.body = _body_without_leading_docstring(node.body)
        return node


def squeeze_pasted_python(source: str, *, filename: str) -> str:
    """Drop docstrings and comments (via ``ast.unparse``) to shrink pasted modules."""
    stripped = source.strip()
    if not stripped:
        return ""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return source.rstrip() + "\n"
    tree = _StripDocstrings().visit(tree)
    ast.fix_missing_locations(tree)
    try:
        text = ast.unparse(tree).rstrip()
    except Exception:
        return source.rstrip() + "\n"
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text + "\n"


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


DEFAULT_ROUND = default_round()


# Use traders_dir() from tools.paths instead
def round_dir(round_num: int) -> Path:
    return traders_dir(round_num)


def module_name(path: Path) -> str:
    return path.stem


def module_path(round_num: int, name: str) -> Path:
    return round_dir(round_num) / f"{name}.py"


def _parse_traders_pkg(dotted: str) -> tuple[int, str | None] | None:
    """Parse a dotted import head rooted at the `traders.` package.

    Returns ``(round_num, submodule | None)`` for ``traders.ROUND_N`` and
    ``traders.ROUND_N.submodule[.…]``; returns ``None`` for everything
    else. ``submodule`` is the file we'll inline (the piece immediately
    after ``ROUND_N``); deeper dotted segments are caller-resolved
    attribute access.

    `traders.` is the ONLY supported root for local imports — bare
    ``ROUND_N``, bare module names, and relative imports are not
    recognised.
    """
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != "traders":
        return None
    m = ROUND_RE.match(parts[1])
    if not m:
        return None
    sub = parts[2] if len(parts) >= 3 else None
    return int(m.group(1)), sub


def local_dependency_names(path: Path, round_num: int) -> set[Module]:
    """Return (round_num, module_name) pairs for local imports in ``path``.

    Only ``traders.ROUND_N``-rooted imports are recognised. Cross-round
    imports (e.g. a ROUND_4 trader inheriting from ``traders.ROUND_3``)
    work because the round tag is encoded in the dotted path itself.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    deps: set[Module] = set()

    def _add(target_round: int, candidate: str) -> None:
        if module_path(target_round, candidate).exists():
            deps.add((target_round, candidate))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue
            parsed = _parse_traders_pkg(node.module)
            if parsed is None:
                continue
            target_round, sub = parsed
            if sub is None:
                # `from traders.ROUND_N import trader_X[, trader_Y]`
                for alias in node.names:
                    _add(target_round, alias.name.split(".", 1)[0])
            else:
                # `from traders.ROUND_N.trader_X import Foo`
                _add(target_round, sub)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # `import traders.ROUND_N.trader_X[.…] [as x]`
                parsed = _parse_traders_pkg(alias.name)
                if parsed is None or parsed[1] is None:
                    continue
                _add(parsed[0], parsed[1])

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
    the statement. Only ``traders.ROUND_N…`` imports are rewritten.
    """
    futures: set[str] = set()

    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
        futures.update(alias.name for alias in node.names)
        return [], futures

    if isinstance(node, ast.ImportFrom):
        if node.level != 0 or not node.module:
            return None, futures
        parsed = _parse_traders_pkg(node.module)
        if parsed is None:
            return None, futures
        target_round, sub = parsed
        replacements: list[str] = []
        remaining: list[ast.alias] = []

        if sub is None:
            # `from traders.ROUND_N import trader_X[, trader_Y][ as alias]`
            for alias in node.names:
                candidate = alias.name.split(".", 1)[0]
                if module_path(target_round, candidate).exists():
                    target = alias.asname or candidate
                    replacements.append(f"{target} = {local_module_expr(candidate)}")
                else:
                    remaining.append(alias)
        else:
            # `from traders.ROUND_N.trader_X import Foo[, Bar][ as Baz]`
            if not module_path(target_round, sub).exists():
                return None, futures
            for alias in node.names:
                if alias.name == "*":
                    raise RuntimeError("cannot compile wildcard local import")
                target = alias.asname or alias.name
                replacements.append(f"{target} = {local_module_expr(sub)}.{alias.name}")

        if replacements:
            if remaining:
                replacements.append(import_from_syntax(node, remaining))
            return replacements, futures
        return None, futures

    if isinstance(node, ast.Import):
        replacements = []
        remaining = []
        consumed_any = False
        for alias in node.names:
            parsed = _parse_traders_pkg(alias.name)
            if parsed is None:
                remaining.append(alias)
                continue
            target_round, sub = parsed
            if sub is None or not module_path(target_round, sub).exists():
                remaining.append(alias)
                continue
            consumed_any = True
            if alias.asname:
                # `import traders.ROUND_N.trader_X as x` -> `x = trader_X`
                replacements.append(f"{alias.asname} = {local_module_expr(sub)}")
            # else: `import traders.ROUND_N.trader_X` — already exposed at
            # the top of the compiled file as `traders.ROUND_N.trader_X`,
            # so the line can be dropped without binding anything new.

        if consumed_any:
            if remaining:
                replacements.append(import_syntax(remaining))
            return replacements, futures
        return None, futures

    return None, futures


def pasted_source(path: Path, round_num: int) -> tuple[str, set[str]]:
    """Return source with only local/future import lines rewritten."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines()
    # (start inclusive, end exclusive) — overlap on same slice should not happen,
    # but last write wins after sort by descending start line.
    span_replacements: dict[tuple[int, int], list[str]] = {}
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
        span_replacements[(start, end)] = repl

    for (start, end), repl in sorted(span_replacements.items(), reverse=True):
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


def render_compiled(entry: Path, round_num: int, *, squeeze: bool = True) -> tuple[str, tuple[Module, ...]]:
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
        path_mod = module_path(round_n, name)
        source, module_futures = pasted_source(path_mod, round_n)
        if squeeze:
            source = squeeze_pasted_python(source, filename=str(path_mod))
        pasted[mod] = source
        futures.update(module_futures)

    chunks = [
        "# `uv run compile`; edit source & recompile. Needs datamodel on sys.path.",
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
            "traders = __CompiledModule()",
        ]
    )
    for r in rounds_used:
        chunks.append(f"traders.ROUND_{r} = __CompiledModule()")
    chunks.append("")

    for mod in modules:
        round_n, name = mod
        path = module_path(round_n, name)
        rel = path.relative_to(ROOT).as_posix()
        source = pasted[mod].rstrip()
        builder = f"__build_{name}"
        chunks.extend(
            [
                f"#+{rel}",
                f"def {builder}():",
                textwrap.indent(source.rstrip("\n"), "    ") if source else "    pass",
                "    __m=__CompiledModule()",
                "    for __k,__v in list(locals().items()):",
                "        if not __k.startswith('__'):setattr(__m,__k,__v)",
                "    return __m",
                f"{name}={builder}()",
                f"setattr(traders.ROUND_{round_n},{name!r},{name})",
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


def compile_one(src: Path, round_num: int, out_dir: Path | None, *, verify: bool, squeeze: bool = True) -> CompileResult:
    if not local_dependency_names(src, round_num):
        raise SystemExit(
            f"refusing to compile {src.name!r}: it has no `traders.ROUND_{round_num}…` "
            f"imports to inline — submit that `.py` from traders/ROUND_{round_num}/ as-is."
        )
    text, modules = render_compiled(src, round_num, squeeze=squeeze)
    dst = output_path_for(src, round_num, out_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    if verify:
        verify_compiled_loads(dst, ROOT)
    return CompileResult(src=src, dst=dst, modules=modules)


def discover_round_py_modules(round_num: int) -> list[Path]:
    """Top-level ``traders/ROUND_N/*.py`` only (not subdirs, not ``compiled/``).

    ``ROUND_N`` here is ``traders/ROUND_{round_num}/`` under the repo root.
    """
    rdir = round_dir(round_num)
    skip = {"__init__.py"}
    return sorted(
        p
        for p in rdir.glob("*.py")
        if p.is_file()
        and not p.name.startswith("_")
        and p.name not in skip
    )


def verify_compiled_loads(dst: Path, root: Path) -> None:
    """Execute the compiled module with only ``root`` on ``sys.path``; require ``Trader``."""
    dst_abs = dst.resolve()
    snippet = f"""import pathlib, sys
ROOT = pathlib.Path({str(root.resolve())!r}).resolve()
sys.path.insert(0, str(ROOT))
p = pathlib.Path({str(dst_abs)!r}).resolve()
code = compile(p.read_text(encoding="utf-8"), str(p), "exec")
ns = {{"__name__": "compiled_trader_probe", "__file__": str(p), "__builtins__": __builtins__}}
exec(code, ns)
T = ns.get("Trader")
if T is None:
    raise RuntimeError("compiled file did not define top-level Trader")
"""
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"compiled trader failed import probe: {dst_abs}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
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
    parser = argparse.ArgumentParser(
        prog="compile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """\
            Compile a trader with its local `traders.ROUND_N…` imports into one file.

            Examples:
              uv run compile --round 3 --trader trader_BUGALPHA.py
              uv run compile --round 3 --trader sub/trader_X.py
              uv run compile --round 3 --all
            """
        ),
    )
    parser.add_argument("-r", "--round", type=int, default=DEFAULT_ROUND,
                        required=DEFAULT_ROUND is None, metavar="N",
                        help=f"round number (default: {DEFAULT_ROUND if DEFAULT_ROUND is not None else 'required'})")
    parser.add_argument("--trader", action="append", dest="traders", metavar="PATH",
                        help="trader file relative to ROUND_N/. Repeatable. "
                             "No fuzzy matching: must exist exactly.")
    parser.add_argument("--all", action="store_true",
                        help="compile every `traders/ROUND_N/*.py` that imports at "
                             "least one sibling via `traders.ROUND_N…` (skip zero-dep files).")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="output directory (default: ROUND_N/compiled)")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip subprocess import probe (default: verify each output loads with only workspace root on sys.path)")
    parser.add_argument("--no-squeeze", action="store_true",
                        help="paste original source formatting (much larger output; default squeezes pasted modules)")
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
        candidates = discover_round_py_modules(round_num)
        targets = [p for p in candidates if local_dependency_names(p, round_num)]
        skipped_self = len(candidates) - len(targets)
        if not targets:
            print(
                f"no traders with local imports in traders/ROUND_{round_num}/ "
                f"({skipped_self} file(s) skipped — zero `traders.ROUND_N…` deps)"
            )
            return 0
    else:
        if not args.traders:
            raise SystemExit("provide --trader PATH relative to ROUND_N/, or use --all")
        targets = [resolve_trader(arg, round_num) for arg in args.traders]

    verify = not args.no_verify
    results: list[CompileResult] = []
    for src in targets:
        result = compile_one(
            src.resolve(), round_num, args.out_dir, verify=verify, squeeze=not args.no_squeeze
        )
        results.append(result)
        rel_dst = result.dst.relative_to(ROOT) if result.dst.is_relative_to(ROOT) else result.dst
        dep_count = max(0, len(result.modules) - 1)
        print(f"compiled {src.name} -> {rel_dst} ({dep_count} local import(s))")

    out_dir = args.out_dir or round_dir(round_num) / "compiled"
    msg = f"wrote {len(results)} file(s) to {out_dir}"
    if args.all:
        skipped_self = len(discover_round_py_modules(round_num)) - len(results)
        if skipped_self:
            msg += f" ({skipped_self} zero-dep file(s) in traders/ROUND_{round_num}/ skipped)"
    if verify:
        msg += " (import probe OK)"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
