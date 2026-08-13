#!/usr/bin/env python3
"""Correlate each detector's per-trial event count against the trial's GRS score.

A fault detector that works should fire more often on low-scoring trials, giving a
negative Spearman rho against grs_total. A good-execution detector should do the
opposite. Anything near zero is noise dressed up as a finding.

This exists because the original validation was done ad hoc and its numbers were
carried around in prose, so nothing could be re-tested after the detectors changed.
It matters here specifically: the good-execution detectors were found to correlate
BACKWARDS -- they fired on weak trials -- and floors were then added to
localize_events.py to stop short, fragmented spans reading as efficient. Whether
that worked is a measurement, not an opinion, and --compare makes it.

    python3 scripts/validate_detectors.py --task Suturing
    python3 scripts/validate_detectors.py --all
    python3 scripts/validate_detectors.py --all --compare

--compare runs twice, once with the floors disabled, so the change is attributable.

One deliberate simplification: baselines are built once per task over all its
trials, rather than per trial excluding the trial under analysis. For generation
the exclusion matters -- a trial should not be compared against itself. For a
correlation study a single fixed baseline is better, because it removes a confound
where each trial is scored against a slightly different yardstick.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import localize_events as le

TASKS = ["Suturing", "Knot_Tying", "Needle_Passing"]
FAULT = {"slow", "wandering", "jerky", "idle", "regrasp"}
GOOD = {"efficient", "economical"}


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation, with ties averaged. No scipy on every machine this runs on."""
    n = len(xs)
    if n < 3:
        return float("nan")

    def ranks(vals: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: vals[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def collect(root: Path, tasks: list[str]) -> dict[str, list[tuple[int, Counter]]]:
    """Per task: [(grs_total, Counter of event kinds)] for every usable trial."""
    per_task: dict[str, list[tuple[int, Counter]]] = {}
    for task in tasks:
        task_root = le.resolve_task_root(root, task)
        meta = le.read_meta(task_root, task)
        baselines = le.build_baselines(task_root, task, meta, exclude=None)
        rows = []
        for trial, info in meta.items():
            if not le.read_spans(task_root, trial):
                continue
            events = le.events_for_trial(task_root, task, trial, meta, baselines)
            rows.append((info["grs_total"], Counter(e["kind"] for e in events)))
        per_task[task] = rows
    return per_task


def report(per_task: dict[str, list[tuple[int, Counter]]], label: str) -> dict[str, float]:
    print(f"\n{'=' * 74}")
    print(f"{label}")
    print(f"{'=' * 74}")
    kinds = sorted({k for rows in per_task.values() for _, c in rows for k in c})
    all_rows = [r for rows in per_task.values() for r in rows]
    out: dict[str, float] = {}
    print(f"{'detector':12s} {'expect':>7s} {'rho':>7s} {'trials':>7s} {'events':>7s}  verdict")
    for kind in kinds:
        grs = [g for g, _ in all_rows]
        counts = [float(c.get(kind, 0)) for _, c in all_rows]
        total = int(sum(counts))
        rho = spearman(counts, grs)
        want = "negative" if kind in FAULT else "positive" if kind in GOOD else "?"
        ok = (rho < -0.2) if kind in FAULT else (rho > 0.2) if kind in GOOD else False
        weak = abs(rho) < 0.2
        verdict = ("noise -- near zero" if weak
                   else "works" if ok
                   else "BACKWARDS -- fires the wrong way")
        out[kind] = rho
        print(f"{kind:12s} {want:>7s} {rho:>7.2f} {len(all_rows):>7d} {total:>7d}  {verdict}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default="outputs/datasets/JIGSAWS")
    ap.add_argument("--task", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--compare", action="store_true",
                    help="also run with the good-execution floors disabled")
    args = ap.parse_args()

    tasks = TASKS if args.all or not args.task else [args.task]
    root = Path(args.jigsaws_root)

    after = report(collect(root, tasks), "WITH the good-execution floors (current code)")

    if args.compare:
        # Disable the floors to reproduce the earlier behaviour. Patching the module
        # constants is what makes the two runs differ by exactly one thing.
        saved = (le.MIN_GOOD_BASELINE_S, le.MIN_GOOD_SAVING_S, le.MIN_GOOD_RATIO)
        le.MIN_GOOD_BASELINE_S, le.MIN_GOOD_SAVING_S, le.MIN_GOOD_RATIO = 0.0, 0.0, 0.0
        before = report(collect(root, tasks), "WITHOUT the floors (before the fix)")
        le.MIN_GOOD_BASELINE_S, le.MIN_GOOD_SAVING_S, le.MIN_GOOD_RATIO = saved

        print(f"\n{'=' * 74}")
        print("CHANGE IN THE GOOD-EXECUTION DETECTORS")
        print(f"{'=' * 74}")
        for kind in sorted(GOOD):
            b, a = before.get(kind), after.get(kind)
            if b is None or a is None:
                continue
            print(f"  {kind:12s} rho {b:+.2f} -> {a:+.2f}"
                  f"   ({'improved' if a > b else 'no better'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
