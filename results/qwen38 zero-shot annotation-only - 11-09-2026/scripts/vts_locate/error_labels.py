#!/usr/bin/env python3
"""Parse the UVA-DSA executional error labels and check they align with JIGSAWS.

Source: github.com/UVA-DSA/ExecProc_Error_Analysis, accompanying arXiv 2106.11962
(Hutchinson, Li, Cantrell, Schenkman, Alemzadeh). The repository states no licence,
so this reads the labels for analysis; anything published from them needs the
authors' permission and a citation, which is a question for a human, not this script.

Why these matter here: our own event detectors infer that something went wrong from
duration and distance, so a question can state the delay as fact but has to ask the
model to guess the cause. These labels record what actually happened, per gesture
instance, with frame bounds -- which is the difference between a question whose
answer can be checked and one whose answer can only sound plausible.

Format, which is not uniform across the three groups of files:

  Suturing consensus + Error_specific   ",files,label_err1_nor0", absolute paths
                                        from the annotator's machine, trial id
                                        "Suturing_B001"
  Needle Passing consensus              "name,label_err1_nor0", bare basename,
                                        trial id "Needle_Passing_B001"
  Needle Passing Error_specific         same columns, but trial id
                                        "NeedlePassing_B001" -- no underscore

That last one is a trap: parsed naively it matches no JIGSAWS trial and yields
silently empty results rather than an error, so trial ids are normalised here.

The filename carries everything else: Suturing_B001_1125_1401.avi is trial
Suturing_B001, frames 1125 to 1401 -- directly comparable to a transcription span.
The label column is binary (1 = error), so the error TYPE comes from which file a
row appears in, and types are gesture-specific: only G5 and G6 have needledrop
files, only G4 and G8 have position files.

    python3 scripts/error_labels.py --report
    python3 scripts/error_labels.py --check-alignment
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localize_events import (JIGSAWS_ROOT_CANDIDATES, read_meta, read_spans,
                             resolve_jigsaws_root, resolve_task_root)

# Where the labels live, probed in order. Two machines, two locations, and no reason
# to make either one pass a flag: the cluster copy sits beside JIGSAWS on the shared
# mount so the whole lab can use one copy, and the local copy is a clone under the
# gitignored outputs/ tree. An explicit path always wins over both.
LABEL_ROOT_CANDIDATES = (
    "/mnt/sun/shared/datasets/surgical_skill/ExecProc_Error/Error_Labels",
    "outputs/datasets/error_labels/Error_Labels",
)


def resolve_label_root(explicit: str | None = None) -> Path | None:
    """First existing candidate, or the explicit path if one was given."""
    if explicit:
        return Path(explicit)
    for cand in LABEL_ROOT_CANDIDATES:
        if Path(cand).is_dir():
            return Path(cand)
    return None

# Human names for the error types, keyed by the token in the filename. The paper
# names four; "curve" appears only as a DRAFT file and is reported separately
# rather than mixed in.
ERROR_TYPES = {
    "attempts": "Multiple Attempts",
    "needledrop": "Needle Drop",
    "position": "Needle Orientation",
    "needle_position": "Needle Orientation",
    "outofview": "Out of View",
    "curve": "Needle Curvature (draft)",
}

# Greedy on the trial id, because it contains digits of its own: the trial in
# Suturing_B001_1125_1401.avi is "Suturing_B001", so only the final two numeric
# groups are the frame bounds. A character class of letters and underscores matches
# nothing here, which is the kind of failure that returns an empty result rather
# than an error.
CLIP = re.compile(r"^(.+)_(\d+)_(\d+)\.avi$")


def normalise_trial(raw: str) -> str:
    """NeedlePassing_B001 -> Needle_Passing_B001; others pass through."""
    if raw.startswith("NeedlePassing"):
        return "Needle_Passing" + raw[len("NeedlePassing"):]
    return raw


def parse_file(path: Path) -> list[dict[str, Any]]:
    """Rows from one label CSV, whichever of the three layouts it uses."""
    name = path.stem                      # e.g. error_S_G5_needledrop, G3_OutofView
    gesture_match = re.search(r"G(\d+)", name)
    gesture = f"G{gesture_match.group(1)}" if gesture_match else None

    # Error type from the filename suffix after the gesture token. A file with no
    # suffix is the consensus "any error" label for that gesture.
    tail = name.split(gesture, 1)[1].lstrip("_").lower() if gesture else ""
    error_type = ERROR_TYPES.get(tail.replace("_draft", ""), "Any error" if not tail else tail)

    out: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # The clip path lives under "files" (Suturing) or "name" (Needle Passing).
            clip = row.get("files") or row.get("name") or ""
            m = CLIP.search(clip.replace("\\", "/").rsplit("/", 1)[-1])
            if not m:
                continue
            raw_trial, start, end = m.group(1), int(m.group(2)), int(m.group(3))
            trial = normalise_trial(raw_trial)
            label = (row.get("label_err1_nor0") or "").strip()
            if label not in {"0", "1"}:
                continue
            out.append({
                "trial_id": trial,
                "task": "Needle_Passing" if trial.startswith("Needle_Passing") else "Suturing",
                "start_frame": start,
                "end_frame": end,
                "gesture": gesture,
                "error_type": error_type,
                "is_error": label == "1",
                "source_file": path.name,
            })
    return out


def load_all(label_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(label_root.rglob("*.csv")):
        rows.extend(parse_file(path))
    return rows


def report(rows: list[dict[str, Any]]) -> None:
    print(f"{len(rows)} label rows parsed\n")
    print("by task:")
    for task in sorted({r["task"] for r in rows}):
        t = [r for r in rows if r["task"] == task]
        trials = {r["trial_id"] for r in t}
        errs = sum(1 for r in t if r["is_error"])
        print(f"  {task:16s} {len(t):5d} rows  {len(trials):3d} trials  "
              f"{errs:5d} marked error ({errs / len(t):.0%})")

    print("\nby error type (specific files only, 'Any error' is the consensus label):")
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["error_type"]].append(r)
    for etype, rs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        errs = sum(1 for r in rs if r["is_error"])
        gestures = sorted({r["gesture"] for r in rs}, key=lambda g: int(g[1:]))
        print(f"  {etype:26s} {len(rs):5d} instances  {errs:5d} errors "
              f"({errs / len(rs):3.0%})  in {', '.join(gestures)}")

    print("\nerror-bearing instances per trial, top 10:")
    per_trial = Counter(r["trial_id"] for r in rows if r["is_error"]
                        and r["error_type"] != "Any error")
    for trial, n in per_trial.most_common(10):
        print(f"  {trial:24s} {n}")
    print(f"  ({len(per_trial)} trials have at least one specific labelled error)")


def check_alignment(rows: list[dict[str, Any]], jigsaws_root: Path) -> int:
    """Do the label frame bounds correspond to real transcription spans?

    If they do not, nothing can be joined and the whole plan fails, so this runs
    before any generation code gets written.
    """
    print("do label frame bounds match transcription gesture spans?\n")
    problems = 0
    for task in ("Suturing", "Needle_Passing"):
        task_rows = [r for r in rows if r["task"] == task]
        if not task_rows:
            print(f"  {task}: no rows parsed")
            problems += 1
            continue
        try:
            root = resolve_task_root(jigsaws_root, task)
        except Exception as exc:  # noqa: BLE001
            print(f"  {task}: cannot resolve dataset root ({exc})")
            problems += 1
            continue
        meta = read_meta(root, task)
        # Read each transcription once. Without this the loop below opens the same
        # ~67 files once per label row -- 4,159 reads instead of 67. Unnoticeable on
        # a local disk with a warm page cache, minutes on a shared network mount,
        # which is exactly where this check is most useful.
        spans_cache: dict[str, list] = {}

        def spans_for(trial: str) -> list:
            if trial not in spans_cache:
                spans_cache[trial] = read_spans(root, trial) if trial in meta else []
            return spans_cache[trial]

        exact = near = missing = no_transcript = 0
        gesture_mismatch = 0
        for r in task_rows:
            spans = read_spans(root, r["trial_id"]) if r["trial_id"] in meta else None
            if not spans:
                no_transcript += 1
                continue
            hit = [(s, e, g) for s, e, g in spans
                   if s == r["start_frame"] and e == r["end_frame"]]
            if hit:
                exact += 1
                if r["gesture"] and hit[0][2] != r["gesture"]:
                    gesture_mismatch += 1
            elif any(abs(s - r["start_frame"]) <= 2 and abs(e - r["end_frame"]) <= 2
                     for s, e, _ in spans):
                near += 1
            else:
                missing += 1
        total = len(task_rows)
        print(f"  {task}")
        print(f"    exact frame match      {exact:5d} / {total}  ({exact / total:.0%})")
        print(f"    within 2 frames        {near:5d}")
        print(f"    no matching span       {missing:5d}")
        print(f"    trial not in JIGSAWS   {no_transcript:5d}")
        print(f"    gesture id disagrees   {gesture_mismatch:5d}  (of the exact matches)")
        if exact / total < 0.8:
            problems += 1
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label-root", default=None,
                    help=f"default: first of {', '.join(LABEL_ROOT_CANDIDATES)} that exists")
    ap.add_argument("--jigsaws-root", default=None,
                    help=f"default: first of {', '.join(JIGSAWS_ROOT_CANDIDATES)} "
                         f"that exists")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check-alignment", action="store_true")
    args = ap.parse_args()

    root = resolve_label_root(args.label_root)
    if root is None or not root.exists():
        raise SystemExit(
            f"no labels found. Looked in:\n"
            + "".join(f"  {c}\n" for c in LABEL_ROOT_CANDIDATES)
            + "Clone them, or pass --label-root:\n"
            f"  git clone --depth 1 "
            f"https://github.com/UVA-DSA/ExecProc_Error_Analysis.git <dir>")
    print(f"labels: {root}")
    rows = load_all(root)
    if not rows:
        raise SystemExit(f"parsed 0 rows from {root} -- the format has changed")

    if args.report or not (args.report or args.check_alignment):
        report(rows)
    if args.check_alignment:
        jigsaws = resolve_jigsaws_root(args.jigsaws_root)
        print(f"jigsaws: {jigsaws}\n")
        return check_alignment(rows, jigsaws)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
