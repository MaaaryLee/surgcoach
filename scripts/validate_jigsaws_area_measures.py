#!/usr/bin/env python3
"""Do any gesture or kinematic measures show what earned a tissue, flow or needle rating?

An answer may point to what a trainee did only through a measure that moves with the
rating it is used for. validate_jigsaws_kinematic_measures.py (2026-10-13) found that only
trial time does, and only for Time and motion. This tests measures built from the rubric's
own words for the other areas (Gao et al. 2014, Table 3). Each measure's area and expected
direction were fixed before any result was seen.

Flow of operation: 1 "Frequently interrupted flow to discuss the next move"; 5 "Obviously
planned course of operation with efficient transitions between moves".
  transition_pause_s    mean time both tool tips are still within 0.5 s of a gesture
                        boundary (expected negative: longer pauses, lower rating)
  interruptions_pm      stretches of 1 s or more with both tips still, per minute (negative)
  atypical_transitions  share of gesture-to-gesture transitions that the other subjects'
                        trials of the task make less than 5% of the time (negative)
Respect for tissue: 1 "Frequently used unnecessary force on tissue"; 3 "occasionally caused
inadvertent damage".
  tissue_passes         G3 Pushing needle through tissue segments (negative: extra passes)
  push_speed            mean PSM1 (right) tip speed during G3 (no expected direction)
  push_jerk             mean frame-to-frame change in PSM1 velocity during G3 (negative)
  pull_speed            mean PSM2 (left) tip speed during G6 Pulling suture with left hand;
                        in Knot Tying, both tips during G15 Pulling suture with both hands
                        (negative)
Suture/needle handling: 1 "Awkward and unsure with repeated entanglement".
  orienting_share       share of trial time in G8 Orienting needle (negative)
Time and motion (reference, from 2026-10-13):
  duration_s            trial length

Each measure is tested within each task against every rating (Spearman, 5,000
permutations). Against its own area it is also tested with the other three technique
ratings held fixed (partial Spearman), since the ratings move together and a measure of
general skill would otherwise pass for a measure of one area. Holm's correction runs over
the own-area tests. A measure holds up in a task when its own-area correlation has the
expected sign with Holm-adjusted p < 0.05 and its partial correlation keeps that sign with
p < 0.05.

Usage:
  python3 scripts/validate_jigsaws_area_measures.py --jigsaws-root <root> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_jigsaws_kinematic_measures import (  # noqa: E402
    ELEMENTS, FPS, IDLE_SPEED, PERMUTATIONS, PSM1_VEL, PSM2_VEL, TASKS, load_task, ranks, spearman,
)

TECHNIQUE = ("respect_for_tissue", "suture_needle_handling", "time_and_motion", "flow_of_operation")
# Each measure's own area and expected sign (0: no expected direction).
OWN_AREA = {
    "transition_pause_s": ("flow_of_operation", -1),
    "interruptions_pm": ("flow_of_operation", -1),
    "atypical_transitions": ("flow_of_operation", -1),
    "tissue_passes": ("respect_for_tissue", -1),
    "push_speed": ("respect_for_tissue", 0),
    "push_jerk": ("respect_for_tissue", -1),
    "pull_speed": ("respect_for_tissue", -1),
    "orienting_share": ("suture_needle_handling", -1),
    "duration_s": ("time_and_motion", -1),
}
BOUNDARY_FRAMES = FPS // 2
INTERRUPTION_FRAMES = FPS
RARE = 0.05


def subject(trial: str) -> str:
    return trial.rsplit("_", 1)[1][0]


def still_runs(still: np.ndarray, min_len: int) -> int:
    runs, length = 0, 0
    for s in np.append(still, False):
        if s:
            length += 1
        else:
            runs += length >= min_len
            length = 0
    return runs


def gesture_frames(spans: list[tuple[int, int, str]], gestures: set[str]) -> np.ndarray:
    """0-based kinematics rows inside the named gestures."""
    rows = [np.arange(s - 1, e) for s, e, g in spans if g in gestures]
    return np.concatenate(rows) if rows else np.array([], int)


def measures(t: dict, transitions: dict[str, Counter]) -> dict[str, float]:
    kin, spans, task = t["kin"], t["spans"], t["task"]
    start, end = spans[0][0], spans[-1][1]
    speed_r = np.linalg.norm(kin[:, PSM1_VEL], axis=1)
    speed_l = np.linalg.norm(kin[:, PSM2_VEL], axis=1)
    still = (speed_r < IDLE_SPEED) & (speed_l < IDLE_SPEED)
    minutes = (end - start + 1) / FPS / 60

    pauses = []
    for (_, _, _), (s, _, _) in zip(spans, spans[1:]):
        lo, hi = max(0, s - 1 - BOUNDARY_FRAMES), min(len(kin), s - 1 + BOUNDARY_FRAMES)
        pauses.append(still[lo:hi].sum() / FPS)
    pairs = [(a[2], b[2]) for a, b in zip(spans, spans[1:])]
    rare = sum(
        transitions[a][b] / max(1, sum(transitions[a].values())) < RARE for a, b in pairs
    )

    out = {
        "transition_pause_s": float(np.mean(pauses)) if pauses else float("nan"),
        "interruptions_pm": still_runs(still[start - 1:end], INTERRUPTION_FRAMES) / minutes,
        "atypical_transitions": rare / len(pairs) if pairs else float("nan"),
        "duration_s": (end - start + 1) / FPS,
    }
    g3 = gesture_frames(spans, {"G3"})
    if task in ("Suturing", "Needle_Passing"):
        out["tissue_passes"] = float(sum(g == "G3" for _, _, g in spans))
        out["push_speed"] = float(speed_r[g3].mean()) if len(g3) else float("nan")
        jerk = np.linalg.norm(np.diff(kin[g3][:, PSM1_VEL], axis=0), axis=1) if len(g3) > 1 else []
        out["push_jerk"] = float(np.mean(jerk)) if len(jerk) else float("nan")
        g6 = gesture_frames(spans, {"G6"})
        out["pull_speed"] = float(speed_l[g6].mean()) if len(g6) else float("nan")
        g8 = gesture_frames(spans, {"G8"})
        out["orienting_share"] = len(g8) / (end - start + 1)
    else:
        for name in ("tissue_passes", "push_speed", "push_jerk", "orienting_share"):
            out[name] = float("nan")
        g15 = gesture_frames(spans, {"G15"})
        out["pull_speed"] = float(((speed_r[g15] + speed_l[g15]) / 2).mean()) if len(g15) else float("nan")
    return out


def partial_spearman(x: np.ndarray, y: np.ndarray, controls: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    """Spearman correlation of x and y with the controls' ranks regressed out of both."""
    ok = ~np.isnan(x)
    rx, ry = ranks(x[ok]), ranks(y[ok])
    design = np.column_stack([np.ones(ok.sum())] + [ranks(c[ok]) for c in controls.T])
    def residual(v):
        return v - design @ np.linalg.lstsq(design, v, rcond=None)[0]
    ex, ey = residual(rx), residual(ry)
    rho = float(np.corrcoef(ex, ey)[0, 1])
    perm = np.array([np.corrcoef(rng.permutation(ex), ey)[0, 1] for _ in range(PERMUTATIONS)])
    return rho, max(float((np.abs(perm) >= abs(rho)).mean()), 1 / PERMUTATIONS)


def holm(pvalues: list[float]) -> list[float]:
    order = np.argsort(pvalues)
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


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
            # Typical transitions come from the other subjects' trials, so a trial is never
            # judged against its own habits.
            transitions: dict[str, Counter] = defaultdict(Counter)
            for o in trials:
                if subject(o["trial"]) != subject(t["trial"]):
                    for a, b in zip(o["spans"], o["spans"][1:]):
                        transitions[a[2]][b[2]] += 1
            rows.append({"task": task, "trial": t["trial"], **t["scores"], **measures(t, transitions)})
        print(f"{task}: {len(trials)} trials", flush=True)

    results, own_tests = {}, []
    for name, (area, sign) in OWN_AREA.items():
        for task in TASKS:
            sub = [r for r in rows if r["task"] == task]
            x = np.array([r[name] for r in sub], float)
            # A measure that does not vary within a task has no correlation to test:
            # interruptions_pm is 0 in every Needle Passing trial.
            if len(np.unique(x[~np.isnan(x)])) < 2:
                continue
            entry = {}
            for el in ELEMENTS:
                y = np.array([r[el] for r in sub], float)
                rho, p = spearman(x, y, rng)
                entry[el] = {"rho": round(rho, 3), "p": round(p, 4)}
            others = np.array([[r[a] for a in TECHNIQUE if a != area] for r in sub], float)
            prho, pp = partial_spearman(x, np.array([r[area] for r in sub], float), others, rng)
            entry["partial"] = {"rho": round(prho, 3), "p": round(pp, 4)}
            entry["n"] = int((~np.isnan(x)).sum())
            results[f"{name}|{task}"] = entry
            own_tests.append((name, task, area, sign))
    adjusted = holm([results[f"{n}|{t}"][a]["p"] for n, t, a, _ in own_tests])
    for (name, task, area, sign), p_holm in zip(own_tests, adjusted):
        e = results[f"{name}|{task}"]
        rho, prho, pp = e[area]["rho"], e["partial"]["rho"], e["partial"]["p"]
        right_sign = (sign == 0) or (np.sign(rho) == sign)
        e["own_area"] = area
        e["holm_p"] = round(p_holm, 4)
        e["holds_up"] = bool(right_sign and p_holm < 0.05 and np.sign(prho) == np.sign(rho) and pp < 0.05)

    (args.out / "trial_measures.json").write_text(json.dumps(rows, indent=1))
    (args.out / "correlations.json").write_text(json.dumps(results, indent=1))

    short = {"respect_for_tissue": "tissue", "suture_needle_handling": "needle", "time_and_motion": "motion",
             "flow_of_operation": "flow", "overall_performance": "overall", "quality_of_final_product": "product"}
    lines = [
        "# Gesture and kinematic measures for each technique area", "",
        f"{len(rows)} trials. Spearman rho within each task, p from {PERMUTATIONS} permutations. "
        "Negative rho: more of the measure, lower rating. Ratings come from one rater, and 8 subjects "
        "each contribute about 5 trials, so trials are not independent.", "",
        "## Against its own area", "",
        "| measure | own area | task | n | rho | p | Holm p | partial rho (other areas fixed) | partial p | holds up |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, task, area, _ in own_tests:
        e = results[f"{name}|{task}"]
        lines.append(
            f"| {name} | {short[area]} | {task} | {e['n']} | {e[area]['rho']:+.2f} | {e[area]['p']:.3f} | "
            f"{e['holm_p']:.3f} | {e['partial']['rho']:+.2f} | {e['partial']['p']:.3f} | "
            f"{'yes' if e['holds_up'] else 'no'} |"
        )
    lines += ["", "## Against every rating", "",
              "| measure | task | " + " | ".join(short[e] for e in ELEMENTS) + " |",
              "|---|---|" + "---:|" * len(ELEMENTS)]
    for name, task, _, _ in own_tests:
        e = results[f"{name}|{task}"]
        lines.append(f"| {name} | {task} | " + " | ".join(
            f"{e[el]['rho']:+.2f}{'*' if e[el]['p'] < 0.05 else ''}" for el in ELEMENTS) + " |")
    lines += ["", "* p < 0.05 before correction."]
    (args.out / "correlations.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
