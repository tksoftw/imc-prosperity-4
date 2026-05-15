"""Combine per-day .log files in runs/ into one merged log per run.

Each run shows up in runs/ as several folders sharing a prefix and ending in
`-round-{R}-day+{D}`. This walks those groups, merges the per-day JSON .log
files (activitiesLog, logs, tradeHistory) and writes the result into the
folder for the highest day number in the group.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
SUFFIX_RE = re.compile(r"^(?P<prefix>.+)-round-(?P<round>\d+)-day\+(?P<day>\d+)$")


@dataclass
class DayFolder:
    path: Path
    day: int
    log_path: Path


def find_log(folder: Path) -> Path | None:
    candidates = [
        p for p in folder.glob("*.log")
        if "_submission_d" in p.name and "_combined" not in p.name
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        candidates.sort()
        return candidates[0]
    return None


def group_runs(runs_dir: Path) -> dict[str, list[DayFolder]]:
    groups: dict[str, list[DayFolder]] = {}
    for entry in runs_dir.iterdir():
        if not entry.is_dir():
            continue
        m = SUFFIX_RE.match(entry.name)
        if not m:
            continue
        log_path = find_log(entry)
        if log_path is None:
            continue
        day = int(m.group("day"))
        prefix = m.group("prefix")
        groups.setdefault(prefix, []).append(
            DayFolder(path=entry, day=day, log_path=log_path)
        )
    for days in groups.values():
        days.sort(key=lambda d: d.day)
    return groups


DAY_TICKS = 1_000_000  # match carry-mode: each day occupies a 1M timestamp slot


def _shift_activities_rows(body: str, offset: int) -> str:
    """Add `offset` to the timestamp column (index 1) of each non-empty row.

    Returns the rows joined by `\\n`, no trailing newline, header stripped.
    """
    if not body:
        return ""
    out: list[str] = []
    for line in body.split("\n"):
        if not line:
            continue
        # Fast path: split only twice — day;timestamp;<rest>
        first = line.find(";")
        if first == -1:
            out.append(line)
            continue
        second = line.find(";", first + 1)
        if second == -1:
            out.append(line)
            continue
        try:
            ts = int(line[first + 1 : second])
        except ValueError:
            out.append(line)
            continue
        out.append(f"{line[:first + 1]}{ts + offset}{line[second:]}")
    return "\n".join(out)


def combine(days: list[DayFolder]) -> dict:
    activities_parts: list[str] = []
    header: str | None = None
    logs: list = []
    trades: list = []
    submission_id_parts: list[str] = []

    for i, d in enumerate(days):
        offset = i * DAY_TICKS
        data = json.loads(d.log_path.read_text())
        submission_id_parts.append(str(data.get("submissionId", d.path.name)))

        body = data.get("activitiesLog") or ""
        if body:
            first_nl = body.find("\n")
            if first_nl == -1:
                this_header, rest = body, ""
            else:
                this_header, rest = body[:first_nl], body[first_nl + 1 :]
            if i == 0 and header is None:
                header = this_header
            activities_parts.append(_shift_activities_rows(rest, offset))

        for entry in data.get("logs") or []:
            if "timestamp" in entry:
                entry = {**entry, "timestamp": entry["timestamp"] + offset}
            logs.append(entry)

        for trade in data.get("tradeHistory") or []:
            if "timestamp" in trade:
                trade = {**trade, "timestamp": trade["timestamp"] + offset}
            trades.append(trade)

    pieces = []
    if header is not None:
        pieces.append(header)
    pieces.extend(p for p in activities_parts if p)
    activities_log = "\n".join(pieces)

    return {
        "submissionId": "+".join(submission_id_parts),
        "activitiesLog": activities_log,
        "logs": logs,
        "tradeHistory": trades,
    }


def combined_name(final: DayFolder) -> str:
    stem = final.log_path.name
    stem = re.sub(r"_submission_d\d+\.log$", "_submission_combined.log", stem)
    if stem == final.log_path.name:
        stem = final.log_path.stem + "_combined.log"
    return stem


def process(prefix: str, days: list[DayFolder], force: bool) -> tuple[Path, str]:
    if len(days) < 2:
        return days[-1].path, "skip (single day)"
    final = days[-1]
    out_path = final.path / combined_name(final)
    if out_path.exists() and not force:
        return out_path, "exists (use --force to overwrite)"
    merged = combine(days)
    out_path.write_text(json.dumps(merged))
    return out_path, f"wrote {len(days)} days"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing combined log")
    parser.add_argument("--prefix", help="only process runs whose prefix contains this")
    args = parser.parse_args()

    if not args.runs_dir.is_dir():
        print(f"runs dir not found: {args.runs_dir}", file=sys.stderr)
        return 1

    groups = group_runs(args.runs_dir)
    if not groups:
        print("no run groups found")
        return 0

    for prefix in sorted(groups):
        if args.prefix and args.prefix not in prefix:
            continue
        days = groups[prefix]
        out_path, status = process(prefix, days, args.force)
        rel = out_path.relative_to(args.runs_dir.parent) if out_path.is_relative_to(args.runs_dir.parent) else out_path
        print(f"{prefix}: {status} -> {rel}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
