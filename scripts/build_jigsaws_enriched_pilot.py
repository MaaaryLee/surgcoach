#!/usr/bin/env python3
"""Build trial-level JIGSAWS inputs enriched with gestures and kinematics.

This is deliberately a representation pilot, not a replacement for the existing
annotation-only runner.  It keeps one record per complete trial, adds the full
ordered gesture transcription, and summarizes the synchronized patient-side
kinematics inside every gesture span.  Trial-level GRS fields remain explicitly
trial-level; they are never copied into a gesture object.

The default eight trials cover all three tasks, all three self-proclaimed skill
levels, GRS totals from 6 to 30, repeated gestures, and the two known cases where
self-proclaimed experience and rated performance diverge.

Example on Oprime:

    python3 scripts/build_jigsaws_enriched_pilot.py \
      --jigsaws-root /mnt/sun/shared/datasets/surgical_skill/JIGSAWS \
      --output-dir outputs/enriched-input-pilot
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localize_events import (  # noqa: E402
    FPS,
    GESTURES,
    SLAVE_L_GRIP,
    SLAVE_L_VEL,
    SLAVE_L_XYZ,
    SLAVE_R_GRIP,
    SLAVE_R_VEL,
    SLAVE_R_XYZ,
    _numpy,
    read_kinematics,
    read_meta,
    read_spans,
    resolve_jigsaws_root,
    resolve_task_root,
    span_metrics,
)


PILOT_TRIALS = (
    "Suturing_D004",       # self-reported expert, GRS 8
    "Suturing_I004",       # novice, GRS 23
    "Suturing_C004",       # intermediate, GRS 30
    "Knot_Tying_G004",     # novice, GRS 6
    "Knot_Tying_H004",     # novice, GRS 22
    "Needle_Passing_C003", # intermediate, GRS 7
    "Needle_Passing_E003", # expert, GRS 13
    "Needle_Passing_B004", # novice, GRS 19
)

GRS_NAMES = (
    "respect_for_tissue",
    "suture_needle_handling",
    "time_and_motion",
    "flow_of_operation",
    "overall_performance",
    "quality_of_final_product",
)

# The holistic overall-performance element is intentionally excluded from every
# model-facing representation. It is retained only in the source record for
# provenance. The remaining values are converted to complete behavioral
# sentences so the model never sees score names, score values, or generic
# category-plus-level pairs. The 1/3/5 wording follows the modified OSATS anchors
# used by JIGSAWS; 2 and 4 are deliberately narrow intermediate paraphrases.
GRS_BEHAVIORAL_PARAPHRASES = {
    "respect_for_tissue": {
        1: "The trainee frequently used unnecessary force on the tissue.",
        2: "The trainee did not interact with the tissue with reliable care.",
        3: (
            "The trainee interacted with the tissue carefully, although occasional "
            "inadvertent damage remained."
        ),
        4: "The trainee interacted with the tissue carefully and usually appropriately.",
        5: "The trainee interacted with the tissue appropriately and consistently.",
    },
    "suture_needle_handling": {
        1: "The trainee manipulated the needle and suture awkwardly and uncertainly.",
        2: "The trainee's manipulation of the needle and suture remained uncertain overall.",
        3: "The trainee manipulated the needle and suture competently overall.",
        4: "The trainee manipulated the needle and suture with good control.",
        5: "The trainee showed excellent control while manipulating the needle and suture.",
    },
    "time_and_motion": {
        1: "The trainee made unnecessary movements.",
        2: "The trainee's movements remained unnecessarily inefficient.",
        3: "The trainee moved efficiently overall, with some unnecessary movements.",
        4: "The trainee's movements were economical and generally efficient.",
        5: "The trainee moved with clear economy and maximum efficiency.",
    },
    "flow_of_operation": {
        1: "The procedure was frequently interrupted.",
        2: "The procedure progressed with limited continuity.",
        3: "The procedure showed reasonable progression with some forward planning.",
        4: "The procedure progressed well, with efficient transitions between actions.",
        5: (
            "The course of the procedure was clearly planned, with efficient "
            "transitions between actions."
        ),
    },
    "quality_of_final_product": {
        1: "The finished work was very poor.",
        2: "The finished work fell below a competent standard.",
        3: "The finished work was competent.",
        4: "The finished work was clearly above a competent standard.",
        5: "The finished work was clearly superior.",
    },
}
TIME_AND_MOTION_INDEX = GRS_NAMES.index("time_and_motion")
LOW_TIME_AND_MOTION_MAX = 2
STRONG_TIME_AND_MOTION_MIN = 4
# Knot Tying has no trial rated 4 or 5 on time and motion and Needle Passing has
# one, so a 4+ reference never forms for either task and their motion could not
# be localized at all. There the reference falls back to the trials rated 3 ("moved
# efficiently overall, with some unnecessary movements"), which is still strictly
# above any target this localizes (2 or lower). The floor used is recorded.
FALLBACK_TIME_AND_MOTION_MIN = 3
MIN_REFERENCE_TRIALS = 3
MIN_REFERENCE_SPANS = 8
MATERIAL_REFERENCE_RATIO = 1.25
MAX_LOCALIZED_FINDINGS = 3
RATIONALE_WITH_LOCALIZATION = (
    "The general comments describe the complete trial. Only the named comparisons "
    "of action duration and instrument travel apply to individual actions; they do "
    "not establish a cause or consequence."
)
RATIONALE_WITHOUT_LOCALIZATION = (
    "The comments describe the complete trial. The listed actions establish order "
    "only and do not identify any individual action as stronger or weaker."
)

SKILL_LEVELS = {
    "N": "novice (<10 hours robotic surgical experience)",
    "I": "intermediate (10-100 hours robotic surgical experience)",
    "E": "expert (>100 hours robotic surgical experience)",
}

# Instruments are named by the trainee's hand that drives them, which is not what
# the readme's "Slave left"/"Slave right" labels suggest. The paper settles it:
# Section 2.4 calls PSM1 and PSM2 "the right and left PSMs in this dataset", and
# its Table 1 puts PSM1 in columns 39-57. The data agree. Across all 545 spans of
# hand-specific gestures in the three tasks, the readme's "slave left" columns
# (39-41) move more than the "slave right" ones (58-60) in 84-100% of right-hand
# reaches (G1, G14) and right-hand tightening (G9), but in only 7-17% of left-hand
# suture pulls (G6). The master columns agree with the gesture labels: "master
# left" moves more in 83-93% of G6 spans and 0-16% of right-hand spans. So the
# columns localize_events.py calls SLAVE_L follow the surgeon's right hand, and
# an answer saying "the left instrument" for them names the wrong hand.
SIDE_COLUMNS = {
    "right_hand_instrument": (SLAVE_L_XYZ, SLAVE_L_VEL, SLAVE_L_GRIP),
    "left_hand_instrument": (SLAVE_R_XYZ, SLAVE_R_VEL, SLAVE_R_GRIP),
}


def task_from_trial(trial_id: str) -> str:
    for task in ("Suturing", "Knot_Tying", "Needle_Passing"):
        if trial_id.startswith(f"{task}_"):
            return task
    raise ValueError(f"Cannot infer JIGSAWS task from trial id: {trial_id}")


def seconds_label(frame: int) -> str:
    seconds = frame / FPS
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def rounded(value: float | int, digits: int = 5) -> float | int:
    if isinstance(value, int):
        return value
    if not math.isfinite(float(value)):
        raise ValueError(f"Non-finite kinematic value: {value}")
    return round(float(value), digits)


def gripper_cycles(values: Any) -> int:
    """Count noise-filtered open/close cycles, matching localize_events.py."""
    np = _numpy()
    delta = np.diff(values)
    moving = np.abs(delta) > 0.01
    signs = np.sign(delta[moving])
    return int((np.diff(signs) != 0).sum() // 2) if len(signs) > 1 else 0


def motion_summary(kin: Any, start: int, end: int) -> dict[str, Any]:
    """Human-readable measurements for one inclusive, one-based frame span."""
    np = _numpy()
    segment = kin[max(0, start - 1):end]
    if not len(segment):
        raise ValueError(f"Empty kinematic slice for frames {start}-{end}")

    sides: dict[str, Any] = {}
    for side, (xyz_cols, velocity_cols, gripper_col) in SIDE_COLUMNS.items():
        positions = segment[:, xyz_cols]
        velocities = segment[:, velocity_cols]
        speed = np.linalg.norm(velocities, axis=1)
        position_steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
        velocity_steps = np.linalg.norm(np.diff(velocities, axis=0), axis=1)
        sides[side] = {
            "path_length_dataset_units": rounded(position_steps.sum()),
            "mean_speed_dataset_units_per_second": rounded(speed.mean()),
            "peak_speed_dataset_units_per_second": rounded(speed.max()),
            "stationary_fraction_below_0_005": rounded((speed < 0.005).mean(), 4),
            # This is intentionally not called physical jerk: the released signal is
            # sampled velocity, and this measure is the mean frame-to-frame change.
            "mean_velocity_change_per_frame": rounded(
                velocity_steps.mean() if len(velocity_steps) else 0.0
            ),
            "gripper_open_close_cycles": gripper_cycles(segment[:, gripper_col]),
        }

    return {
        "frame_count": int(len(segment)),
        "duration_seconds": rounded(len(segment) / FPS, 3),
        **sides,
    }


def reference_cohort(
    meta: dict[str, dict[str, Any]], target_trial: str
) -> tuple[list[str], int]:
    """Other trials of the task rated efficient enough to compare against, and the floor used."""
    for floor in (STRONG_TIME_AND_MOTION_MIN, FALLBACK_TIME_AND_MOTION_MIN):
        selected = sorted(
            trial_id
            for trial_id, info in meta.items()
            if trial_id != target_trial
            and info["grs_subscores"][TIME_AND_MOTION_INDEX] >= floor
        )
        if len(selected) >= MIN_REFERENCE_TRIALS:
            return selected, floor
    return selected, FALLBACK_TIME_AND_MOTION_MIN


def collect_reference_metrics(
    task_root: Path,
    meta: dict[str, dict[str, Any]],
    target_trial: str,
) -> tuple[dict[str, dict[str, float]], list[str], int]:
    """Median same-action metrics from independently efficient movement ratings.

    Motion is used only to localize the time-and-motion dimension, so the
    reference cohort is selected by that dimension rather than by a holistic
    total.  Fewer than three independent trials is not treated as a sufficiently
    solid reference.
    """
    selected, floor = reference_cohort(meta, target_trial)
    if len(selected) < MIN_REFERENCE_TRIALS:
        return {}, selected, floor
    pooled: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for trial_id in selected:
        kin = read_kinematics(task_root, trial_id)
        if kin is None:
            continue
        for start, end, gesture_id in read_spans(task_root, trial_id):
            metrics = span_metrics(kin, start, end)
            for name, value in metrics.items():
                pooled[gesture_id][name].append(float(value))

    baselines: dict[str, dict[str, float]] = {}
    for gesture_id, metrics in pooled.items():
        span_count = len(metrics.get("duration_s", []))
        if span_count < MIN_REFERENCE_SPANS:
            continue
        baselines[gesture_id] = {
            name: statistics.median(values) for name, values in metrics.items() if values
        }
        baselines[gesture_id]["reference_span_count"] = float(span_count)
    return baselines, selected, floor


def time_evidence(
    task_root: Path,
    target_spans: list[tuple[int, int, str]],
    reference_trial_ids: list[str],
) -> dict[str, Any] | None:
    """Where a trial's time went, against the same reference group as the steps.

    Trial time, first to last annotated frame, is the one measure that tracks the
    Time and motion rating across JIGSAWS (validate_jigsaws_kinematic_measures.py:
    Spearman -0.49 in Suturing, -0.81 in Knot Tying, -0.14 and not significant in
    Needle Passing), so it is what an answer may use to say where time was lost.
    Per gesture: total seconds and number of segments here, the median of each over
    the reference trials (zero where a trial has none of that gesture), and the extra
    seconds. Durations are (end - start) / 30 from the transcription.
    """
    def per_gesture(spans: list[tuple[int, int, str]]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for start, end, gesture_id in spans:
            entry = out.setdefault(gesture_id, [0.0, 0])
            entry[0] += (end - start) / FPS
            entry[1] += 1
        return out

    def trial_seconds(spans: list[tuple[int, int, str]]) -> float:
        return (max(e for _, e, _ in spans) - min(s for s, _, _ in spans)) / FPS

    reference = []
    for trial_id in reference_trial_ids:
        spans = read_spans(task_root, trial_id)
        if spans:
            reference.append((trial_seconds(spans), per_gesture(spans)))
    if len(reference) < MIN_REFERENCE_TRIALS:
        return None
    mine = per_gesture(target_spans)
    order = {g: i for i, g in enumerate(GESTURES)}
    gestures = sorted(set(mine) | {g for _, totals in reference for g in totals}, key=lambda g: order.get(g, 99))
    by_gesture = []
    for g in gestures:
        ref_seconds = statistics.median(totals.get(g, [0.0, 0])[0] for _, totals in reference)
        ref_count = statistics.median(totals.get(g, [0.0, 0])[1] for _, totals in reference)
        seconds, count = mine.get(g, [0.0, 0])
        by_gesture.append({
            "gesture_id": g,
            "total_seconds": rounded(seconds, 3),
            "count": int(count),
            "reference_median_total_seconds": rounded(ref_seconds, 3),
            "reference_median_count": ref_count,
            "extra_seconds": rounded(seconds - ref_seconds, 3),
        })
    seconds = trial_seconds(target_spans)
    reference_median = statistics.median(s for s, _ in reference)
    return {
        "reference_trial_count": len(reference),
        "trial_seconds": rounded(seconds, 3),
        "reference_median_trial_seconds": rounded(reference_median, 3),
        "ratio": rounded(seconds / reference_median, 3),
        "by_gesture": by_gesture,
    }


def comparison_to_reference(
    raw_metrics: dict[str, float], baseline: dict[str, float] | None
) -> dict[str, Any] | None:
    if not baseline:
        return None

    comparisons: dict[str, Any] = {
        "reference_span_count": int(baseline["reference_span_count"]),
    }
    # span_metrics() names columns by the readme's slave left/right; see
    # SIDE_COLUMNS for why its "left" is the right-hand instrument.
    fields = {
        "duration_ratio": "duration_s",
        "right_hand_path_ratio": "path_left_m",
        "left_hand_path_ratio": "path_right_m",
        "right_hand_velocity_change_ratio": "jerk_left",
        "left_hand_velocity_change_ratio": "jerk_right",
    }
    for output_name, metric_name in fields.items():
        denominator = baseline.get(metric_name, 0.0)
        # Very short transcription spans are intentionally omitted by the
        # baseline metric function; they still retain their direct motion summary.
        if denominator and metric_name in raw_metrics:
            comparisons[output_name] = rounded(raw_metrics[metric_name] / denominator, 3)
    # The values behind the three ratios a rationale quotes, so it can state them
    # and verify_jigsaws_step_evidence.py can recompute each from the raw files.
    for name, metric_name in (
        ("duration_s", "duration_s"),
        ("right_hand_path", "path_left_m"),
        ("left_hand_path", "path_right_m"),
    ):
        if metric_name in raw_metrics and baseline.get(metric_name):
            comparisons[name] = rounded(raw_metrics[metric_name])
            comparisons[f"reference_median_{name}"] = rounded(baseline[metric_name])
    return comparisons if len(comparisons) > 1 else None


def qualitative_teaching_signals(record: dict[str, Any]) -> list[str]:
    """Translate selected values to behavioral prose without score names/numbers."""
    scores = record["trial_level_annotations"]["grs_subscores"]
    signals: list[str] = []
    for key, paraphrases in GRS_BEHAVIORAL_PARAPHRASES.items():
        value = int(scores[key])
        if value not in paraphrases:
            raise ValueError(f"Unexpected assessment value for {key}: {value}")
        signals.append(paraphrases[value])
    return signals


# What the trainee did in each gesture, as the past-tense clause of "the third time
# you ...". Spoken to the trainee, because the D answers are feedback to them.
# G3 drops "the tissue": Needle Passing pushes the needle through rings, and the
# label's wording would put tissue into that task.
GESTURE_CLAUSES = {
    "G1": "reached for the needle with your right hand",
    "G2": "positioned the needle",
    "G3": "pushed the needle through",
    "G4": "passed the needle from your left hand to your right",
    "G5": "moved to the center with the needle in grip",
    "G6": "pulled the suture with your left hand",
    "G7": "pulled the suture with your right hand",
    "G8": "oriented the needle",
    "G9": "used your right hand to help tighten the suture",
    "G10": "loosened more suture",
    "G11": "dropped the suture and moved to the end points",
    "G12": "reached for the needle with your left hand",
    "G13": "made the C loop around your right hand",
    "G14": "reached for the suture with your right hand",
    "G15": "pulled the suture with both hands",
}
ORDINALS = (
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
    "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth",
)
# Spoken in place of "the same-action reference", which no surgeon would say.
REFERENCE_PHRASE = "the same step in the more efficient runs of this task"


def occurrence_phrase(gesture_id: str, index: int, total: int) -> str:
    """'The third time you pushed the needle through', or 'When you ...' for a one-off."""
    clause = GESTURE_CLAUSES[gesture_id]
    if total <= 1:
        return f"When you {clause}"
    if index == total:
        return f"The last time you {clause}"
    if index <= len(ORDINALS):
        return f"The {ORDINALS[index - 1]} time you {clause}"
    return f"One of the later times you {clause}"


def size_words(ratio: float, unit: str) -> str:
    """A measured ratio in words, rounded to what a surgeon would say aloud.

    unit is "long" or "far". The exact ratio stays in the record for audit.
    """
    comparative = {"long": "longer", "far": "farther"}[unit]
    if ratio < 1.4:
        return f"somewhat {comparative} than"
    if ratio < 1.75:
        return f"about one and a half times as {unit} as"
    if ratio < 2.5:
        return f"about twice as {unit} as"
    whole = round(ratio)
    if whole <= 8:
        number = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}[whole]
        return f"about {number} times as {unit} as"
    return f"more than eight times as {unit} as"


def finding_statement(item: dict[str, Any], total: int, ref: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """One localized comparison as a spoken sentence, plus its measured values."""
    duration = float(ref.get("duration_ratio", 0.0))
    paths = {
        "right-hand": float(ref.get("right_hand_path_ratio", 0.0)),
        "left-hand": float(ref.get("left_hand_path_ratio", 0.0)),
    }
    long_enough = duration >= MATERIAL_REFERENCE_RATIO
    farther = {hand: r for hand, r in paths.items() if r >= MATERIAL_REFERENCE_RATIO}
    if not long_enough and not farther:
        return None

    opening = occurrence_phrase(item["gesture_id"], int(item["gesture_occurrence"]), total)
    # Each size phrase ends in its comparison word ("as" or "than"); it is split
    # off so it can be dropped mid-sentence and kept only before the reference.
    travel, comparator = "", ""
    if len(farther) == 2:
        right, _ = size_words(farther["right-hand"], "far").rsplit(" ", 1)
        left, comparator = size_words(farther["left-hand"], "far").rsplit(" ", 1)
        if right == left:
            travel = f"both instruments traveled {right}"
        else:
            travel = (
                f"your right-hand instrument traveled {right} and your "
                f"left-hand instrument {left}"
            )
    elif farther:
        (hand, ratio), = farther.items()
        size, comparator = size_words(ratio, "far").rsplit(" ", 1)
        travel = f"your {hand} instrument traveled {size}"

    if long_enough:
        sentence = f"{opening}, it took {size_words(duration, 'long')} {REFERENCE_PHRASE}"
        sentence += f", and {travel}." if travel else "."
    else:
        sentence = f"{opening}, {travel} {comparator} in {REFERENCE_PHRASE}."
    detail = {
        "sequence_index": int(item["sequence_index"]),
        "gesture_id": item["gesture_id"],
        "gesture_description": item["gesture_description"],
        "occurrence": int(item["gesture_occurrence"]),
        "occurrences_in_trial": total,
        "start_frame": item["start_frame"],
        "end_frame": item["end_frame"],
        "start_time": item["start_time"],
        "end_time": item["end_time"],
        "duration_ratio": round(duration, 3),
        "right_hand_path_ratio": round(paths["right-hand"], 3),
        "left_hand_path_ratio": round(paths["left-hand"], 3),
        "reference_span_count": int(ref.get("reference_span_count", 0)),
        "measured": {
            key: ref[key]
            for key in (
                "duration_s", "reference_median_duration_s",
                "right_hand_path", "reference_median_right_hand_path",
                "left_hand_path", "reference_median_left_hand_path",
            )
            if key in ref
        },
        "statement": sentence,
    }
    return sentence, detail


def integrated_motion_localization(record: dict[str, Any]) -> dict[str, Any]:
    """Localize only a low movement-economy annotation with solid references."""
    trial = record["trial_level_annotations"]
    time_score = int(trial["grs_subscores"]["time_and_motion"])
    if time_score > LOW_TIME_AND_MOTION_MAX:
        return {
            "status": "not_used_without_low_movement_economy_annotation",
            "findings": [],
            "details": [],
        }

    reference_trials_used = record["gesture_level_evidence"]["reference_trial_ids"]
    if len(reference_trials_used) < MIN_REFERENCE_TRIALS:
        return {
            "status": "unavailable_without_adequate_independent_reference_trials",
            "findings": [],
            "details": [],
        }

    gestures = record["gesture_level_evidence"]["ordered_sequence"]
    totals = Counter(item["gesture_id"] for item in gestures)
    candidates: list[tuple[float, int, str, dict[str, Any]]] = []
    for item in gestures:
        ref = item["comparison_with_strong_same_task_same_gesture_reference"]
        if not ref or int(ref.get("reference_span_count", 0)) < MIN_REFERENCE_SPANS:
            continue
        found = finding_statement(item, totals[item["gesture_id"]], ref)
        if found is None:
            continue
        sentence, detail = found
        strongest_ratio = max(
            detail["duration_ratio"], detail["right_hand_path_ratio"], detail["left_hand_path_ratio"]
        )
        candidates.append((strongest_ratio, int(item["sequence_index"]), sentence, detail))

    if not candidates:
        return {
            "status": "no_material_same_action_difference_found",
            "findings": [],
            "details": [],
        }
    strongest = sorted(candidates, key=lambda row: (-row[0], row[1]))[
        :MAX_LOCALIZED_FINDINGS
    ]
    strongest.sort(key=lambda row: row[1])
    return {
        "status": "available_and_annotation_anchored",
        "findings": [sentence for _, _, sentence, _ in strongest],
        "details": [detail for _, _, _, detail in strongest],
    }


def required_rationale(record: dict[str, Any]) -> str:
    """Return the exact public scope note; never ask the model to invent one."""
    if integrated_motion_localization(record)["findings"]:
        return RATIONALE_WITH_LOCALIZATION
    return RATIONALE_WITHOUT_LOCALIZATION


def build_trial_record(
    jigsaws_root: Path,
    trial_id: str,
) -> dict[str, Any]:
    task = task_from_trial(trial_id)
    task_root = resolve_task_root(jigsaws_root, task)
    meta_all = read_meta(task_root, task)
    if trial_id not in meta_all:
        raise ValueError(f"{trial_id} is absent from {task_root}/meta_file_{task}.txt")
    meta = meta_all[trial_id]
    spans = read_spans(task_root, trial_id)
    if not spans:
        raise ValueError(f"No gesture transcription for {trial_id}")
    kin = read_kinematics(task_root, trial_id)
    if kin is None:
        raise ValueError(f"No kinematics for {trial_id}")
    if getattr(kin, "ndim", 0) != 2 or kin.shape[1] != 76:
        raise ValueError(f"Expected {trial_id} kinematics to have 76 columns; got {kin.shape}")
    if max(end for _, end, _ in spans) > len(kin):
        raise ValueError(
            f"{trial_id} transcription ends after its kinematics: "
            f"{max(end for _, end, _ in spans)} > {len(kin)}"
        )

    baselines, reference_trial_ids, reference_floor = collect_reference_metrics(
        task_root, meta_all, target_trial=trial_id
    )
    occurrences: Counter[str] = Counter()
    gestures: list[dict[str, Any]] = []
    for ordinal, (start, end, gesture_id) in enumerate(spans, 1):
        occurrences[gesture_id] += 1
        raw_metrics = span_metrics(kin, start, end)
        gestures.append(
            {
                "sequence_index": ordinal,
                "gesture_occurrence": occurrences[gesture_id],
                "start_frame": start,
                "end_frame": end,
                "start_time": seconds_label(start),
                "end_time": seconds_label(end),
                "gesture_id": gesture_id,
                "gesture_description": GESTURES.get(gesture_id, "unknown gesture"),
                "kinematic_summary": motion_summary(kin, start, end),
                "comparison_with_strong_same_task_same_gesture_reference": (
                    comparison_to_reference(raw_metrics, baselines.get(gesture_id))
                ),
            }
        )

    grs_subscores = dict(zip(GRS_NAMES, meta["grs_subscores"]))
    start_frame = min(start for start, _, _ in spans)
    end_frame = max(end for _, end, _ in spans)
    return {
        "representation_version": "jigsaws-integrated-evidence-v5",
        "dataset": "JIGSAWS",
        "task": task.replace("_", " "),
        "trial_id": trial_id,
        "fps": FPS,
        "trial_extent": {
            "start_frame": start_frame,
            "end_frame": end_frame,
            "start_time": seconds_label(start_frame),
            "end_time": seconds_label(end_frame),
            "gesture_count": len(gestures),
        },
        "trial_level_annotations": {
            "scope": (
                "These ratings apply to the complete trial, not to an individual "
                "gesture, frame range, or minute."
            ),
            "skill_level": meta["skill_level"],
            "skill_level_description": SKILL_LEVELS.get(meta["skill_level"], "unknown"),
            "grs_total": meta["grs_total"],
            "grs_subscores": grs_subscores,
        },
        "gesture_level_evidence": {
            "scope": (
                "Gesture labels identify the annotated action and timing. Kinematic "
                "summaries describe robot motion inside that span; neither source alone "
                "establishes a visual error, failed grasp, needle drop, tissue injury, "
                "contact force, or causal explanation."
            ),
            "reference_definition": (
                "Motion comparisons use the median for the same gesture and task from "
                "independent trials rated 4 or 5 on time and motion, or 3 or above "
                "when fewer than three such trials exist. The target trial is excluded, "
                "and fewer than three reference trials or fewer than eight reference "
                "spans is treated as insufficient."
            ),
            "reference_min_time_and_motion": reference_floor,
            "reference_trial_ids": reference_trial_ids,
            "time_evidence": time_evidence(task_root, spans, reference_trial_ids),
            "ordered_sequence": gestures,
        },
        "source_files": {
            "meta": str(task_root / f"meta_file_{task}.txt"),
            "transcription": str(task_root / "transcriptions" / f"{trial_id}.txt"),
            "kinematics": str(
                task_root / "kinematics" / "AllGestures" / f"{trial_id}.txt"
            ),
        },
    }


def compact_prompt_text(record: dict[str, Any]) -> str:
    """Model-facing evidence with private values already translated and gated."""
    signals = qualitative_teaching_signals(record)
    localization = integrated_motion_localization(record)
    lines = [
        "JIGSAWS INTEGRATED EVIDENCE INPUT",
        f"Task: {record['task']}",
        f"Trial: {record['trial_id']}",
        "",
        "TRIAL-WIDE TEACHER STATEMENTS",
        "Each sentence applies only to the complete trial. It is already the "
        "full permitted interpretation of one source value: paraphrase it "
        "naturally, but do not make it more specific. A holistic summary field "
        "and self-reported experience category were excluded from consideration.",
        *[f"- {signal}" for signal in signals],
        "",
        "ORDERED ANNOTATED WORKFLOW",
        "The following list establishes action identity and order only, not action "
        "quality or success.",
    ]
    for gesture in record["gesture_level_evidence"]["ordered_sequence"]:
        lines.append(f"- {gesture['gesture_description']}")

    lines += ["", "INTEGRATED MOTION LOCALIZATION"]
    if localization["findings"]:
        lines += [
            "The trial-wide concern with economy of movement is the anchor for "
            "these same-action comparisons. These are the only permitted "
            "action-specific motion observations:",
            *[f"- {finding}" for finding in localization["findings"]],
        ]
    else:
        lines += [
            "No action-specific motion localization is supported under the required "
            "annotation and reference criteria. Keep all comments trial-wide.",
        ]

    lines += [
        "",
        "OUTPUT BOUNDARY",
        "Describe supported strengths and concerns only. Do not expose internal "
        "values or names, infer causes or clinical consequences, or give any advice, "
        "practice instruction, corrective action, or recommendation.",
        "",
        "REQUIRED RATIONALE",
        "Copy the following sentence(s) exactly into the rationale field:",
        required_rationale(record),
    ]
    return "\n".join(lines)


def validate_record(record: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    gestures = record["gesture_level_evidence"]["ordered_sequence"]
    if not gestures:
        problems.append("no gestures")
    for expected_index, gesture in enumerate(gestures, 1):
        if gesture["sequence_index"] != expected_index:
            problems.append(f"non-contiguous sequence index at {expected_index}")
        if gesture["gesture_description"] == "unknown gesture":
            problems.append(f"unknown gesture {gesture['gesture_id']}")
        if gesture["kinematic_summary"]["frame_count"] <= 0:
            problems.append(f"empty kinematics at gesture {expected_index}")
    if "complete trial" not in record["trial_level_annotations"]["scope"]:
        problems.append("missing trial-level scope warning")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jigsaws-root", default=None)
    parser.add_argument(
        "--trials",
        default=",".join(PILOT_TRIALS),
        help="Comma-separated trial ids; default is the fixed eight-trial pilot",
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    jigsaws_root = resolve_jigsaws_root(args.jigsaws_root)
    output_dir = Path(args.output_dir)
    prompt_dir = output_dir / "prompt_inputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir.mkdir(parents=True, exist_ok=True)

    trial_ids = [item.strip() for item in args.trials.split(",") if item.strip()]
    records: list[dict[str, Any]] = []
    all_problems: dict[str, list[str]] = {}
    for trial_id in trial_ids:
        print(f"building {trial_id}", flush=True)
        record = build_trial_record(jigsaws_root, trial_id)
        problems = validate_record(record)
        if problems:
            all_problems[trial_id] = problems
        records.append(record)
        (prompt_dir / f"{trial_id}.txt").write_text(
            compact_prompt_text(record) + "\n", encoding="utf-8"
        )

    jsonl_path = output_dir / "enriched_trial_inputs.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    tasks = Counter(record["task"] for record in records)
    skills = Counter(
        record["trial_level_annotations"]["skill_level"] for record in records
    )
    gesture_ids = Counter(
        gesture["gesture_id"]
        for record in records
        for gesture in record["gesture_level_evidence"]["ordered_sequence"]
    )
    prompt_sizes = {
        record["trial_id"]: len(compact_prompt_text(record))
        for record in records
    }
    summary = {
        "status": "valid" if not all_problems else "invalid",
        "records": len(records),
        "trials": trial_ids,
        "tasks": dict(tasks),
        "skill_levels": dict(skills),
        "grs_total_range": [
            min(r["trial_level_annotations"]["grs_total"] for r in records),
            max(r["trial_level_annotations"]["grs_total"] for r in records),
        ],
        "total_gesture_segments": sum(gesture_ids.values()),
        "distinct_gesture_ids": sorted(gesture_ids),
        "gesture_id_counts": dict(sorted(gesture_ids.items())),
        "prompt_characters_by_trial": prompt_sizes,
        "approximate_prompt_tokens_by_trial": {
            trial_id: math.ceil(chars / 4) for trial_id, chars in prompt_sizes.items()
        },
        "validation_problems": all_problems,
        "output": str(jsonl_path),
    }
    (output_dir / "representation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not all_problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
