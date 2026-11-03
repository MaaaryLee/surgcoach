#!/usr/bin/env python3
"""Which kinematic measures track which JIGSAWS rating, across every trial?

Before a measurement is used to show where a rated problem happened, it has to be
shown to move with that rating in the dataset itself. For every trial with a
transcription and kinematics, this computes a set of candidate measures and their
Spearman correlation with each GRS element, within each task and pooled across
tasks (each measure ranked within its task), with permutation p-values.

Candidate measures, chosen for what they physically are:
  duration_s          trial length, first to last annotated frame
  path_right/left     PSM1 (right) / PSM2 (left) tool-tip travel over the trial
  idle_fraction       share of frames with both tool tips below 0.005 speed
  velocity_change     mean frame-to-frame change in tool-tip velocity (jerk proxy)
  gripper_cycles_pm   gripper open/close cycles per minute, both instruments
  gesture_count       annotated gesture segments
  needle_redo_count   segments of G2 Positioning needle and G8 Orienting needle
  slow_step_share     share of gesture spans taking >= 1.25x the median for that
                      gesture in the task's efficient trials (leave-one-out)
  long_path_share     share of gesture spans where either tool tip travels >= 1.25x
                      that median (the localization rule the pipeline uses)

Usage:
  python3 scripts/validate_jigsaws_kinematic_measures.py --jigsaws-root <root> --out <dir>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

FPS = 30
TASKS = ("Suturing", "Knot_Tying", "Needle_Passing")
ELEMENTS = ("respect_for_tissue", "suture_needle_handling", "time_and_motion",
            "flow_of_operation", "overall_performance", "quality_of_final_product")
PSM1_XYZ, PSM2_XYZ = slice(38, 41), slice(57, 60)      # Gao et al. 2014, Table 1
PSM1_VEL, PSM2_VEL = slice(50, 53), slice(69, 72)
PSM1_GRIP, PSM2_GRIP = 56, 75
IDLE_SPEED = 0.005
MIN_SPAN_FRAMES = 15
RATIO = 1.25
PERMUTATIONS = 5000


def path(rows: np.ndarray) -> float:
    return float(np.sqrt(((rows[1:] - rows[:-1]) ** 2).sum(axis=1)).sum())


def grip_cycles(values: np.ndarray) -> int:
    delta = np.diff(values)
    signs = np.sign(delta[np.abs(delta) > 0.01])
    return int((np.diff(signs) != 0).sum() // 2) if len(signs) > 1 else 0


def load_task(root: Path, task: str) -> list[dict]:
    task_dir = next(root.rglob(f"meta_file_{task}.txt")).parent
    trials = []
    for line in (task_dir / f"meta_file_{task}.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        trial = parts[0]
        tpath = task_dir / "transcriptions" / f"{trial}.txt"
        kpath = task_dir / "kinematics" / "AllGestures" / f"{trial}.txt"
        if not tpath.exists() or not kpath.exists():
            continue
        spans = [(int(a), int(b), g) for a, b, g in (l.split()[:3] for l in tpath.read_text().splitlines() if len(l.split()) >= 3)]
        trials.append({
            "task": task, "trial": trial,
            "scores": dict(zip(ELEMENTS, map(int, parts[3:9]))),
            "spans": spans, "kin": np.loadtxt(kpath),
        })
    return trials


def span_measures(kin: np.ndarray, start: int, end: int) -> dict | None:
    if end - start < MIN_SPAN_FRAMES:
        return None
    rows = kin[max(0, start - 1):end]
    return {"duration": (end - start) / FPS, "right": path(rows[:, PSM1_XYZ]), "left": path(rows[:, PSM2_XYZ])}


def trial_measures(t: dict, task_trials: list[dict]) -> dict:
    kin, spans = t["kin"], t["spans"]
    start, end = spans[0][0], spans[-1][1]
    rows = kin[max(0, start - 1):end]
    speed_r = np.linalg.norm(rows[:, PSM1_VEL], axis=1)
    speed_l = np.linalg.norm(rows[:, PSM2_VEL], axis=1)
    dv = (np.linalg.norm(np.diff(rows[:, PSM1_VEL], axis=0), axis=1).mean()
          + np.linalg.norm(np.diff(rows[:, PSM2_VEL], axis=0), axis=1).mean()) / 2
    minutes = (end - start) / FPS / 60

    # Leave-one-out reference: other trials of the task rated 4-5 on time and motion,
    # or 3+ where fewer than three such trials exist -- the pipeline's own rule.
    others = [o for o in task_trials if o["trial"] != t["trial"]]
    ref = [o for o in others if o["scores"]["time_and_motion"] >= 4]
    if len(ref) < 3:
        ref = [o for o in others if o["scores"]["time_and_motion"] >= 3]
    pooled: dict[str, dict[str, list[float]]] = {}
    for o in ref:
        for s, e, g in o["spans"]:
            m = span_measures(o["kin"], s, e)
            if m:
                for k, v in m.items():
                    pooled.setdefault(g, {}).setdefault(k, []).append(v)
    medians = {g: {k: float(np.median(v)) for k, v in d.items()} for g, d in pooled.items() if len(d["duration"]) >= 8}
    slow = long_path = measured = 0
    for s, e, g in spans:
        m = span_measures(kin, s, e)
        if not m or g not in medians:
            continue
        measured += 1
        slow += m["duration"] / medians[g]["duration"] >= RATIO
        long_path += max(m["right"] / medians[g]["right"], m["left"] / medians[g]["left"]) >= RATIO
    return {
        "duration_s": (end - start) / FPS,
        "path_right": path(rows[:, PSM1_XYZ]),
        "path_left": path(rows[:, PSM2_XYZ]),
        "idle_fraction": float(((speed_r < IDLE_SPEED) & (speed_l < IDLE_SPEED)).mean()),
        "velocity_change": float(dv),
        "gripper_cycles_pm": (grip_cycles(rows[:, PSM1_GRIP]) + grip_cycles(rows[:, PSM2_GRIP])) / minutes,
        "gesture_count": len(spans),
        "needle_redo_count": sum(g in ("G2", "G8") for _, _, g in spans),
        "slow_step_share": slow / measured if measured else float("nan"),
        "long_path_share": long_path / measured if measured else float("nan"),
    }


def ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort(kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    # average ties
    for v in np.unique(x):
        idx = np.where(x == v)[0]
        if len(idx) > 1:
            r[idx] = r[idx].mean()
    return r


def spearman(x: np.ndarray, y: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    ok = ~(np.isnan(x) | np.isnan(y))
    rx, ry = ranks(x[ok]), ranks(y[ok])
    rho = float(np.corrcoef(rx, ry)[0, 1])
    perm = np.array([np.corrcoef(rx, rng.permutation(ry))[0, 1] for _ in range(PERMUTATIONS)])
    p = float((np.abs(perm) >= abs(rho)).mean())
    return rho, max(p, 1 / PERMUTATIONS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jigsaws-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)

    rows = []
    for task in TASKS:
        trials = load_task(args.jigsaws_root, task)
        for t in trials:
            rows.append({"task": task, "trial": t["trial"], **t["scores"], **trial_measures(t, trials)})
        print(f"{task}: {len(trials)} trials", flush=True)
    measures = [k for k in rows[0] if k not in ("task", "trial", *ELEMENTS)]

    results = {}
    for m in measures:
        for el in ELEMENTS:
            entry = {}
            pooled_x, pooled_y = [], []
            for task in TASKS:
                sub = [r for r in rows if r["task"] == task]
                x = np.array([r[m] for r in sub], float)
                y = np.array([r[el] for r in sub], float)
                rho, p = spearman(x, y, rng)
                entry[task] = {"rho": round(rho, 3), "p": round(p, 4), "n": int((~np.isnan(x)).sum())}
                ok = ~np.isnan(x)
                pooled_x += list(ranks(x[ok]) / ok.sum())   # within-task rank, so tasks pool fairly
                pooled_y += list(y[ok])
            rho, p = spearman(np.array(pooled_x), np.array(pooled_y), rng)
            entry["pooled"] = {"rho": round(rho, 3), "p": round(p, 4), "n": len(pooled_x)}
            results[f"{m}|{el}"] = entry

    (args.out / "trial_measures.json").write_text(json.dumps(rows, indent=1))
    (args.out / "correlations.json").write_text(json.dumps(results, indent=1))

    lines = ["# Kinematic measures vs JIGSAWS ratings", "",
             f"{len(rows)} trials. Spearman rho between each measure and each rated element, within task "
             "and pooled (measure ranked within task). Negative rho: more of the measure, lower rating. "
             f"p from {PERMUTATIONS} permutations. Ratings come from one rater, and the 8 subjects each "
             "contribute about 5 trials, so trials are not independent.", ""]
    short = {"respect_for_tissue": "tissue", "suture_needle_handling": "needle", "time_and_motion": "motion",
             "flow_of_operation": "flow", "overall_performance": "overall", "quality_of_final_product": "product"}
    lines += ["| measure | " + " | ".join(short[e] for e in ELEMENTS) + " |",
              "|---|" + "---:|" * len(ELEMENTS)]
    for m in measures:
        cells = []
        for el in ELEMENTS:
            e = results[f"{m}|{el}"]
            tasks = "/".join(f"{e[t]['rho']:+.2f}" for t in TASKS)
            cells.append(f"{e['pooled']['rho']:+.2f} ({tasks})")
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "Cells: pooled rho (Suturing / Knot Tying / Needle Passing)."]
    (args.out / "correlations.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
