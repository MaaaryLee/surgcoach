#!/usr/bin/env python3
"""Temporally localize errors and good executions in JIGSAWS trials.

Written in response to review feedback that trial-level coaching QA is too
generic: every Template D record for a trial is built from the same six GRS
subscores, so nothing in the input distinguishes second 12 from second 90 and
nothing in the output can either. The fix is to ground questions in specific
moments instead, which needs specific moments to exist first.

This produces them from two annotation sources the pipeline had never used:

  transcriptions/  frame-accurate gesture spans (start, end, gesture id)
  kinematics/      76 variables per frame, on the same frame numbering

No video is read, and no error label is invented. JIGSAWS does not annotate
errors, so "error" here is operationalized as a measurable deviation from how
the same gesture is performed in expert trials of the same task:

  redo            the trainee returned to a gesture after one intervening
                  gesture (X -> Y -> X), i.e. had to do X again
  slow            the span took far longer than the expert median for that
                  gesture
  wandering       the instrument travelled much further than experts do to
                  accomplish the same gesture
  jerky           high mean acceleration change, i.e. unsteady motion
  idle            a large fraction of the span had the instrument nearly
                  stationary
  regrasp         many gripper open/close cycles within one gesture

and "good execution" as the same comparison in the trainee's favour. Every
event carries the numbers it was derived from, so a generated answer can be
checked against the annotation rather than taken on trust.

Expert baselines come from trials whose meta_file skill level is E. Where a task
has too few expert spans for a gesture, that gesture is skipped rather than
compared against a baseline of one.

    python3 scripts/localize_events.py --task Suturing --trial Suturing_D004
    python3 scripts/localize_events.py --all -o outputs/events.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import numpy as np

FPS = 30  # JIGSAWS capture rate, per Gao et al. 2014

# Gesture vocabulary (Gao et al. 2014). Used only to describe an event in words;
# nothing is inferred from the label beyond what the span itself says.
GESTURES = {
    "G1": "reaching for the needle with the right hand",
    "G2": "positioning the needle",
    "G3": "pushing the needle through the tissue",
    "G4": "transferring the needle from left to right",
    "G5": "moving to the centre with the needle in grip",
    "G6": "pulling suture with the left hand",
    "G7": "pulling suture with the right hand",
    "G8": "orienting the needle",
    "G9": "using the right hand to help tighten the suture",
    "G10": "loosening more suture",
    "G11": "dropping the suture and moving to the end points",
    "G12": "reaching for the needle with the left hand",
    "G13": "making a C loop around the right hand",
    "G14": "reaching for the suture with the right hand",
    "G15": "pulling the suture with both hands",
}

# Zero-based kinematics columns, from the dataset readme. Slave = patient-side
# instruments, the ones actually manipulating tissue, so those are the ones worth
# measuring; master side is the surgeon's hands on the console.
SLAVE_L_XYZ = slice(38, 41)
SLAVE_L_VEL = slice(50, 53)
SLAVE_L_GRIP = 56
SLAVE_R_XYZ = slice(57, 60)
SLAVE_R_VEL = slice(69, 72)
SLAVE_R_GRIP = 75

# Thresholds. Deliberately loose: an event is meant to be worth asking about, not
# statistically significant on its own, and a generated question states the
# measured ratio so a reader can judge it.
SLOW_RATIO = 2.0
FAST_RATIO = 0.6
PATH_RATIO = 2.0
TIGHT_PATH_RATIO = 0.7
JERK_RATIO = 2.0
IDLE_FRACTION = 0.5
IDLE_SPEED = 0.005          # metres/sample; below this the tip is ~stationary
REGRASP_MIN = 3
MIN_EXPERT_SPANS = 4        # fewer than this and the baseline is not a baseline
MIN_SPAN_FRAMES = 15        # half a second; shorter spans measure mostly noise


def timestamp(frame: int) -> str:
    seconds = frame / FPS
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def resolve_task_root(jigsaws_root: Path, task: str) -> Path:
    """Locate a task directory whatever depth it sits at."""
    hits = list(jigsaws_root.rglob(f"meta_file_{task}.txt"))
    if not hits:
        raise SystemExit(f"no meta_file_{task}.txt under {jigsaws_root}")
    return hits[0].parent


def read_meta(task_root: Path, task: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in (task_root / f"meta_file_{task}.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        out[parts[0]] = {
            "skill_level": parts[1],
            "grs_total": int(parts[2]),
            "grs_subscores": [int(x) for x in parts[3:9]],
        }
    return out


def read_spans(task_root: Path, trial: str) -> list[tuple[int, int, str]]:
    path = task_root / "transcriptions" / f"{trial}.txt"
    if not path.exists():
        return []
    spans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            spans.append((int(parts[0]), int(parts[1]), parts[2]))
    return spans


def read_kinematics(task_root: Path, trial: str) -> np.ndarray | None:
    for sub in ("AllGestures", ""):
        path = task_root / "kinematics" / sub / f"{trial}.txt"
        if path.exists():
            return np.loadtxt(path)
    return None


def span_metrics(kin: np.ndarray | None, start: int, end: int) -> dict[str, float]:
    """Motion measures for one gesture span.

    Frame numbers in the transcription are 1-based and index the kinematics rows
    directly, so the slice is [start-1:end].
    """
    if kin is None or end - start < MIN_SPAN_FRAMES:
        return {}
    seg = kin[max(0, start - 1):end]
    if len(seg) < MIN_SPAN_FRAMES:
        return {}

    metrics: dict[str, float] = {"duration_s": (end - start) / FPS}
    for label, xyz, vel, grip in (
        ("left", SLAVE_L_XYZ, SLAVE_L_VEL, SLAVE_L_GRIP),
        ("right", SLAVE_R_XYZ, SLAVE_R_VEL, SLAVE_R_GRIP),
    ):
        pos = seg[:, xyz]
        step = np.linalg.norm(np.diff(pos, axis=0), axis=1)
        metrics[f"path_{label}_m"] = float(step.sum())
        speed = np.linalg.norm(seg[:, vel], axis=1)
        metrics[f"idle_frac_{label}"] = float((speed < IDLE_SPEED).mean())
        # jerk proxy: how much the velocity vector changes frame to frame
        metrics[f"jerk_{label}"] = float(np.linalg.norm(np.diff(seg[:, vel], axis=0), axis=1).mean())
        # gripper open/close cycles: sign changes in the angle's derivative,
        # counted only when the movement is larger than sensor noise
        d = np.diff(seg[:, grip])
        moving = np.abs(d) > 0.01
        signs = np.sign(d[moving])
        metrics[f"regrasp_{label}"] = int((np.diff(signs) != 0).sum() // 2) if len(signs) > 1 else 0
    return metrics


def reference_trials(meta: dict, exclude: str | None = None) -> list[str]:
    """Trials to build the baseline from: the strongest performances, by score.

    Not skill_level == "E". The self-reported label and the rated performance
    disagree in this dataset -- Suturing_D004 is labelled expert and scores 8 of
    30, Knot_Tying_H004 is labelled novice and scores 22 -- so a baseline built
    from the label includes trials that went badly. The score is what was
    actually observed, so the baseline uses the top third by grs_total.

    The trial being analysed is always excluded, or it is partly compared against
    itself and its own deviations are damped out of the median.
    """
    ranked = sorted((t for t in meta if t != exclude),
                    key=lambda t: -meta[t]["grs_total"])
    keep = max(4, len(ranked) // 3)
    return ranked[:keep]


def build_baselines(task_root: Path, task: str, meta: dict,
                    exclude: str | None = None) -> dict[str, dict[str, float]]:
    """Median metrics per gesture across the strongest trials of this task."""
    pooled: dict[str, dict[str, list[float]]] = {}
    refs = set(reference_trials(meta, exclude))
    for trial, info in meta.items():
        if trial not in refs:
            continue
        spans = read_spans(task_root, trial)
        if not spans:
            continue
        kin = read_kinematics(task_root, trial)
        for start, end, gesture in spans:
            m = span_metrics(kin, start, end)
            if not m:
                continue
            bucket = pooled.setdefault(gesture, {})
            for k, v in m.items():
                bucket.setdefault(k, []).append(v)
    baselines = {}
    for gesture, metrics in pooled.items():
        n = len(metrics.get("duration_s", []))
        if n < MIN_EXPERT_SPANS:
            continue
        baselines[gesture] = {k: statistics.median(v) for k, v in metrics.items()}
        baselines[gesture]["n_expert_spans"] = n
    return baselines


def events_for_trial(task_root: Path, task: str, trial: str, meta: dict,
                     baselines: dict) -> list[dict[str, Any]]:
    spans = read_spans(task_root, trial)
    if not spans:
        return []
    kin = read_kinematics(task_root, trial)
    events: list[dict[str, Any]] = []

    def add(kind, polarity, start, end, gesture, detail, evidence):
        events.append({
            "kind": kind,
            "polarity": polarity,
            "start_frame": start,
            "end_frame": end,
            "span": f"{timestamp(start)}-{timestamp(end)}",
            "gesture": gesture,
            "gesture_label": GESTURES.get(gesture, gesture),
            "detail": detail,
            "evidence": evidence,
        })

    # redo: returned to a gesture after exactly one intervening gesture
    for i in range(len(spans) - 2):
        a, b, c = spans[i], spans[i + 1], spans[i + 2]
        if a[2] == c[2] and a[2] != b[2]:
            add("redo", "error", a[0], c[1], a[2],
                f"returned to {a[2]} after {b[2]}, repeating "
                f"'{GESTURES.get(a[2], a[2])}'",
                {"sequence": f"{a[2]}->{b[2]}->{c[2]}",
                 "first_span": f"{timestamp(a[0])}-{timestamp(a[1])}",
                 "repeat_span": f"{timestamp(c[0])}-{timestamp(c[1])}"})

    # per-span comparisons against the expert baseline for the same gesture
    for start, end, gesture in spans:
        base = baselines.get(gesture)
        m = span_metrics(kin, start, end)
        if not base or not m:
            continue
        n = base["n_expert_spans"]

        ratio = m["duration_s"] / base["duration_s"] if base["duration_s"] else 0
        if ratio >= SLOW_RATIO:
            add("slow", "error", start, end, gesture,
                f"took {m['duration_s']:.1f}s against an expert median of "
                f"{base['duration_s']:.1f}s ({ratio:.1f}x)",
                {"duration_s": round(m["duration_s"], 2),
                 "expert_median_s": round(base["duration_s"], 2),
                 "ratio": round(ratio, 2), "n_expert_spans": n})
        elif 0 < ratio <= FAST_RATIO:
            add("efficient", "good", start, end, gesture,
                f"completed in {m['duration_s']:.1f}s against an expert median of "
                f"{base['duration_s']:.1f}s ({ratio:.1f}x)",
                {"duration_s": round(m["duration_s"], 2),
                 "expert_median_s": round(base["duration_s"], 2),
                 "ratio": round(ratio, 2), "n_expert_spans": n})

        for side in ("left", "right"):
            pk, bk = f"path_{side}_m", f"path_{side}_m"
            if base.get(bk):
                pr = m[pk] / base[bk]
                if pr >= PATH_RATIO:
                    add("wandering", "error", start, end, gesture,
                        f"the {side} instrument travelled {m[pk]:.3f} against an expert "
                        f"median of {base[bk]:.3f} for the same gesture ({pr:.1f}x)",
                        {"path": round(m[pk], 4), "expert_median": round(base[bk], 4),
                         "ratio": round(pr, 2), "side": side, "n_expert_spans": n})
                elif pr <= TIGHT_PATH_RATIO:
                    add("economical", "good", start, end, gesture,
                        f"the {side} instrument travelled {m[pk]:.3f} against an expert "
                        f"median of {base[bk]:.3f} ({pr:.1f}x)",
                        {"path": round(m[pk], 4), "expert_median": round(base[bk], 4),
                         "ratio": round(pr, 2), "side": side, "n_expert_spans": n})

            jk = f"jerk_{side}"
            if base.get(jk):
                jr = m[jk] / base[jk]
                if jr >= JERK_RATIO:
                    add("jerky", "error", start, end, gesture,
                        f"the {side} instrument's motion changed direction and speed "
                        f"{jr:.1f}x more than experts do for this gesture",
                        {"jerk": round(m[jk], 5), "expert_median": round(base[jk], 5),
                         "ratio": round(jr, 2), "side": side, "n_expert_spans": n})

            if m[f"idle_frac_{side}"] >= IDLE_FRACTION:
                add("idle", "error", start, end, gesture,
                    f"the {side} instrument was nearly stationary for "
                    f"{m[f'idle_frac_{side}'] * 100:.0f}% of this span",
                    {"idle_fraction": round(m[f"idle_frac_{side}"], 3), "side": side})

            if m[f"regrasp_{side}"] >= REGRASP_MIN:
                add("regrasp", "error", start, end, gesture,
                    f"the {side} gripper opened and closed {m[f'regrasp_{side}']} times "
                    f"within this single gesture",
                    {"cycles": m[f"regrasp_{side}"], "side": side})

    events.sort(key=lambda e: (e["start_frame"], e["kind"]))
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default="outputs/datasets/JIGSAWS")
    ap.add_argument("--task", default=None, help="Suturing, Knot_Tying or Needle_Passing")
    ap.add_argument("--trial", default=None, help="one trial; default is every trial in the task")
    ap.add_argument("--all", action="store_true", help="all three tasks")
    ap.add_argument("-o", "--output", default=None, help="write events as JSON here")
    args = ap.parse_args()

    root = Path(args.jigsaws_root)
    tasks = ["Suturing", "Knot_Tying", "Needle_Passing"] if args.all else [args.task]
    if not tasks or tasks == [None]:
        ap.error("give --task or --all")

    everything: list[dict[str, Any]] = []
    for task in tasks:
        task_root = resolve_task_root(root, task)
        meta = read_meta(task_root, task)
        refs = reference_trials(meta)
        print(f"{task}: baseline from the top {len(refs)} trials by GRS "
              f"({meta[refs[-1]]['grs_total']}-{meta[refs[0]]['grs_total']}/30)")

        trials = [args.trial] if args.trial else [
            t for t in meta if (task_root / "transcriptions" / f"{t}.txt").exists()]
        for trial in trials:
            # rebuilt per trial so the trial under analysis is never in its own
            # baseline; cheap enough at this dataset size
            baselines = build_baselines(task_root, task, meta, exclude=trial)
            evs = events_for_trial(task_root, task, trial, meta, baselines)
            info = meta.get(trial, {})
            counts: dict[str, int] = {}
            for e in evs:
                counts[e["kind"]] = counts.get(e["kind"], 0) + 1
            print(f"  {trial:24s} skill {info.get('skill_level', '?')} "
                  f"GRS {info.get('grs_total', '?'):>2} -> {len(evs):3d} events  "
                  + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
            everything.append({
                "task": task, "trial_id": trial,
                "skill_level": info.get("skill_level"),
                "grs_total": info.get("grs_total"),
                "grs_subscores": info.get("grs_subscores"),
                "events": evs,
            })

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(everything, indent=2) + "\n", encoding="utf-8")
        total = sum(len(t["events"]) for t in everything)
        print(f"\nwrote {out}: {len(everything)} trials, {total} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
