#!/usr/bin/env python3
"""Recompute every gesture/kinematic step in a run's rationales from the raw JIGSAWS files.

Independent of the code that produced the steps (build_jigsaws_enriched_pilot.py,
localize_events.py): it parses the meta file, transcription and kinematics itself
and recomputes, for each step in each record's evidence,

  - that the transcription has that gesture at those frames, as that occurrence
  - the reference group: other trials of the task rated at or above the recorded
    time-and-motion floor, and that a floor of 3 was used only where fewer than
    three trials are rated 4 or above
  - the step's duration and PSM1 (right) and PSM2 (left) tool-tip path lengths
  - the medians of those over every span of the same gesture in the reference
    group, the number of those spans, and the three ratios

and compares each with the recorded value. Exit status 1 on any mismatch.

Definitions, from the JIGSAWS paper (Gao et al., 2014): kinematics at 30 Hz;
columns 39-41 are the PSM1 tool tip ("the right ... PSM"), 58-60 the PSM2 tool tip.
Duration is (end - start) / 30; a path is the summed straight-line distance
between consecutive tool-tip positions over rows start..end. Spans shorter than
15 frames are not measured, as in the producing code.

Usage:
  python3 scripts/verify_jigsaws_step_evidence.py <qa_records.jsonl> \\
      --jigsaws-root /mnt/sun/shared/datasets/surgical_skill/JIGSAWS [--report out.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import numpy as np

FPS = 30
MIN_SPAN_FRAMES = 15
PSM1_XYZ = slice(38, 41)
PSM2_XYZ = slice(57, 60)
TIME_AND_MOTION = 5  # meta columns: trial, skill, total, then six elements in readme order
STRONG_FLOOR, FALLBACK_FLOOR, MIN_REFERENCE_TRIALS = 4, 3, 3
RATIO_TOLERANCE = 0.0015  # recorded ratios are rounded to 3 decimals
VALUE_TOLERANCE = 1e-4    # recorded values are rounded to 5 decimals


def read_meta(task_dir: Path, task: str) -> dict[str, list[str]]:
    rows = {}
    for line in (task_dir / f"meta_file_{task}.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 9:
            rows[parts[0]] = parts
    return rows


def read_transcription(task_dir: Path, trial: str) -> list[tuple[int, int, str]]:
    path = task_dir / "transcriptions" / f"{trial}.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            out.append((int(parts[0]), int(parts[1]), parts[2]))
    return out


def read_kinematics(task_dir: Path, trial: str) -> np.ndarray | None:
    for sub in ("AllGestures", ""):
        path = task_dir / "kinematics" / sub / f"{trial}.txt"
        if path.exists():
            return np.loadtxt(path)
    return None


def path_length(rows: np.ndarray) -> float:
    return float(np.sqrt(((rows[1:] - rows[:-1]) ** 2).sum(axis=1)).sum())


def measure(kin: np.ndarray, start: int, end: int) -> dict[str, float] | None:
    if end - start < MIN_SPAN_FRAMES:
        return None
    rows = kin[max(0, start - 1):end]
    if len(rows) < MIN_SPAN_FRAMES:
        return None
    return {
        "duration_s": (end - start) / FPS,
        "right_hand_path": path_length(rows[:, PSM1_XYZ]),
        "left_hand_path": path_length(rows[:, PSM2_XYZ]),
    }


def cohort(meta: dict[str, list[str]], target: str, floor: int) -> list[str]:
    return sorted(t for t, p in meta.items() if t != target and int(p[TIME_AND_MOTION]) >= floor)


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def verify_time(record: dict, root: Path) -> list[dict]:
    """Recompute the timing evidence an answer used: trial time and time per gesture."""
    evidence = record.get("evidence") or {}
    recorded = evidence.get("time_evidence")
    if not (evidence.get("timing_used") and recorded):
        return []
    task, trial = record["procedure_or_task"], record["trial_id"]
    task_dir = next(root.rglob(f"meta_file_{task}.txt")).parent
    meta = read_meta(task_dir, task)
    group = cohort(meta, trial, evidence.get("reference_min_time_and_motion"))

    def per_gesture(spans):
        out = {}
        for s, e, g in spans:
            entry = out.setdefault(g, [0.0, 0])
            entry[0] += (e - s) / FPS
            entry[1] += 1
        return out

    def seconds(spans):
        return (max(e for _, e, _ in spans) - min(s for s, _, _ in spans)) / FPS

    reference = [(seconds(sp), per_gesture(sp)) for sp in (read_transcription(task_dir, t) for t in group) if sp]
    target = read_transcription(task_dir, trial)
    mine = per_gesture(target)
    problems = []

    def compare(name, value, recorded_value):
        if not close(value, recorded_value, RATIO_TOLERANCE):
            problems.append(f"{name} {value:.3f} recomputed vs {recorded_value} recorded")

    if len(reference) != recorded["reference_trial_count"]:
        problems.append(f"reference trials {len(reference)} recomputed vs {recorded['reference_trial_count']} recorded")
    trial_s = seconds(target)
    ref_median = statistics.median(s for s, _ in reference)
    compare("trial seconds", trial_s, recorded["trial_seconds"])
    compare("reference median trial seconds", ref_median, recorded["reference_median_trial_seconds"])
    compare("ratio", trial_s / ref_median, recorded["ratio"])
    for g in recorded["by_gesture"]:
        gid = g["gesture_id"]
        total, count = mine.get(gid, [0.0, 0])
        ref_total = statistics.median(t.get(gid, [0.0, 0])[0] for _, t in reference)
        ref_count = statistics.median(t.get(gid, [0.0, 0])[1] for _, t in reference)
        compare(f"{gid} total seconds", total, g["total_seconds"])
        compare(f"{gid} reference median seconds", ref_total, g["reference_median_total_seconds"])
        compare(f"{gid} extra seconds", total - ref_total, g["extra_seconds"])
        if count != g["count"] or ref_count != g["reference_median_count"]:
            problems.append(f"{gid} counts {count}/{ref_count} recomputed vs {g['count']}/{g['reference_median_count']} recorded")
    used = ", ".join(f"{s['gesture_id']} {s['total_seconds']:.1f}s vs {s['reference_median_total_seconds']:.1f}s"
                     for s in evidence.get("timing_steps") or [])
    return [{
        "check": "timing",
        "ok": not problems,
        "detail": "; ".join(problems) if problems else (
            f"trial {trial_s:.1f}s vs median {ref_median:.1f}s over {len(reference)} reference trials "
            f"({trial_s / ref_median:.2f}x); all {len(recorded['by_gesture'])} gesture totals and counts match"
            + (f"; steps used: {used}" if used else "")
        ),
    }]


def verify_record(record: dict, root: Path) -> list[dict]:
    evidence = record.get("evidence") or {}
    details = evidence.get("finding_details") or []
    if not details:
        return verify_time(record, root)
    task, trial = record["procedure_or_task"], record["trial_id"]
    task_dir = next(root.rglob(f"meta_file_{task}.txt")).parent
    meta = read_meta(task_dir, task)
    results = []

    floor = evidence.get("reference_min_time_and_motion")
    group = cohort(meta, trial, floor)
    strong = cohort(meta, trial, STRONG_FLOOR)
    floor_ok = (floor == STRONG_FLOOR and len(strong) >= MIN_REFERENCE_TRIALS) or (
        floor == FALLBACK_FLOOR and len(strong) < MIN_REFERENCE_TRIALS
    )
    results.append({
        "check": "reference group",
        "ok": floor_ok and group == sorted(evidence.get("reference_trial_ids") or []),
        "detail": f"floor {floor}: {len(group)} trials recomputed vs "
                  f"{len(evidence.get('reference_trial_ids') or [])} recorded",
    })

    spans = read_transcription(task_dir, trial)
    kin = read_kinematics(task_dir, trial)
    for d in details:
        g, start, end, occ = d["gesture_id"], d["start_frame"], d["end_frame"], d["occurrence"]
        same = [(s, e) for s, e, gid in spans if gid == g]
        label = f"{g} occurrence {occ} frames {start}-{end}"
        span_ok = 0 < occ <= len(same) and same[occ - 1] == (start, end) and len(same) == d["occurrences_in_trial"]
        target = measure(kin, start, end) if kin is not None else None

        pooled: dict[str, list[float]] = {"duration_s": [], "right_hand_path": [], "left_hand_path": []}
        for ref_trial in group:
            ref_kin = read_kinematics(task_dir, ref_trial)
            ref_spans = read_transcription(task_dir, ref_trial)
            if ref_kin is None or not ref_spans:
                continue
            for s, e, gid in ref_spans:
                if gid != g:
                    continue
                m = measure(ref_kin, s, e)
                if m:
                    for key in pooled:
                        pooled[key].append(m[key])
        medians = {key: statistics.median(vals) for key, vals in pooled.items() if vals}
        n = len(pooled["duration_s"])

        problems = []
        if not span_ok:
            problems.append("transcription does not have this gesture at these frames as this occurrence")
        if target is None:
            problems.append("span could not be measured")
        if n != d["reference_span_count"]:
            problems.append(f"reference spans {n} recomputed vs {d['reference_span_count']} recorded")
        recomputed = {}
        if target and medians:
            for key, ratio_key in (
                ("duration_s", "duration_ratio"),
                ("right_hand_path", "right_hand_path_ratio"),
                ("left_hand_path", "left_hand_path_ratio"),
            ):
                ratio = target[key] / medians[key]
                recomputed[ratio_key] = round(ratio, 3)
                if not close(ratio, d[ratio_key], RATIO_TOLERANCE):
                    problems.append(f"{ratio_key} {ratio:.3f} recomputed vs {d[ratio_key]} recorded")
                measured = d.get("measured") or {}
                for value_key, value in ((key, target[key]), (f"reference_median_{key}", medians[key])):
                    if value_key in measured and not close(value, measured[value_key], VALUE_TOLERANCE):
                        problems.append(f"{value_key} {value:.5f} recomputed vs {measured[value_key]} recorded")
        results.append({
            "check": label,
            "ok": not problems,
            "detail": "; ".join(problems) if problems else (
                f"duration {target['duration_s']:.2f}s vs median {medians['duration_s']:.2f}s; "
                f"right-hand path {target['right_hand_path']:.4f} vs {medians['right_hand_path']:.4f}; "
                f"left-hand path {target['left_hand_path']:.4f} vs {medians['left_hand_path']:.4f}; "
                f"{n} reference spans; ratios {recomputed}"
            ),
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("records")
    parser.add_argument("--jigsaws-root", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    lines = ["# Step evidence verification", "",
             "Every gesture/kinematic step in the rationales, recomputed from the raw JIGSAWS "
             "files by scripts/verify_jigsaws_step_evidence.py.", ""]
    failures = checked = 0
    for line in Path(args.records).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        results = verify_record(record, args.jigsaws_root)
        if not results:
            continue
        lines += [f"## {record['trial_id']} {record['template_id']}", ""]
        for r in results:
            checked += 1
            failures += not r["ok"]
            lines.append(f"- {'PASS' if r['ok'] else 'FAIL'} {r['check']}: {r['detail']}")
        lines.append("")
    lines[3:3] = [f"{checked} checks, {failures} failed.", ""]
    report = "\n".join(lines) + "\n"
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
