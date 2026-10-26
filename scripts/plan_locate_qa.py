#!/usr/bin/env python3
"""Choose which JIGSAWS locate questions to ask: the ones that need the video.

VideoTreeSearch keeps a question only if a model cannot answer it from the text
alone. For a locate question the text alone is enough when the task's fixed order
gives the answer away: "the third stitch" sits in the third quarter of nearly every
Suturing recording. This builds every candidate question over the whole dataset,
guesses each answer span from the question alone (blind_ious in
check_locate_qa.py), and keeps only the questions that guess misses -- repeating
until a guess made from the kept questions themselves misses all of them.

The guess needs the other trials, so this runs over the whole dataset at once; the
generator, which runs a few trials per GPU job, reads the result with --keep.

    python3 scripts/plan_locate_qa.py -o outputs/locate_keep.json
    python3 scripts/generate_event_qa.py --vts --keep outputs/locate_keep.json --task Suturing
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_locate_qa import BLIND_IOU, blind_ious
from error_labels import load_all, resolve_label_root
from generate_event_qa import VTS_MIN_TRIAL_S, build_vts_questions
from localize_events import (build_baselines, read_meta, read_spans,
                             resolve_jigsaws_root, resolve_task_root)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default=None)
    ap.add_argument("--min-trial-s", type=float, default=VTS_MIN_TRIAL_S)
    ap.add_argument("--knot-tying-min-trial-s", type=float, default=30.0,
                    help="Knot Tying recordings have a median of 45 s, so the shared "
                         "minimum would leave few of them")
    ap.add_argument("--blind-iou", type=float, default=BLIND_IOU,
                    help="drop a question the text-only guess finds at this IoU or "
                         "better; VideoTreeSearch counts 0.3 as a correct localization")
    ap.add_argument("-o", "--output", required=True, help="the keep list, as JSON")
    args = ap.parse_args()

    labels = load_all(resolve_label_root())
    root = resolve_jigsaws_root(args.jigsaws_root)
    candidates, tally = [], Counter()
    for task in ("Suturing", "Needle_Passing", "Knot_Tying"):
        troot = resolve_task_root(root, task)
        meta = read_meta(troot, task)
        floor = args.knot_tying_min_trial_s if task == "Knot_Tying" else args.min_trial_s
        for trial in meta:
            if not read_spans(troot, trial):
                continue
            for q in build_vts_questions(task, trial, troot, labels,
                                         build_baselines(troot, task, meta, exclude=trial),
                                         min_trial_s=floor, tally=tally):
                candidates.append({"task": task, "trial_id": trial,
                                   "question_kind": q["kind"], "span": q["span"],
                                   "recorded": q["recorded"], "grounding": q["grounding"]})

    # Repeat until the guess finds nothing. One pass guesses from where answers sit
    # across every candidate; once the easy ones are gone, the rest have typical
    # positions of their own, which is what a model trained on the kept set would
    # learn. Over the 1,297 questions of 10-25 one pass kept 707, of which a guess
    # from those 707 still found 67; two more passes settle at 628.
    kept, passes = list(candidates), 0
    while True:
        scores = blind_ious(kept)
        found = [x is not None and x >= args.blind_iou for x in scores]
        if not any(found):
            break
        passes += 1
        print(f"pass {passes}: the guess finds {sum(found)} of {len(kept)}")
        kept = [c for c, f in zip(kept, found) if not f]

    keep = [{"trial_id": c["trial_id"], "kind": c["question_kind"], "span": c["span"],
             "recorded": c["recorded"],
             "blind_iou": None if score is None else round(score, 3)}
            for c, score in zip(kept, scores)]
    Path(args.output).write_text(json.dumps(keep, indent=1) + "\n", encoding="utf-8")

    before = Counter(c["question_kind"] for c in candidates)
    after = Counter(c["question_kind"] for c in kept)
    print(f"\n{'kind':20s} {'candidates':>10s} {'kept':>6s}")
    for kind, n in sorted(before.items(), key=lambda kv: -after[kv[0]]):
        print(f"{kind:20s} {n:10d} {after[kind]:6d}")
    print(f"{'all':20s} {len(candidates):10d} {len(kept):6d}   -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
