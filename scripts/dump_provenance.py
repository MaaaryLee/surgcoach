#!/usr/bin/env python3
"""Write the annotation trail behind every record in a batch, as readable text.

For a reviewer asking "what is this actually based on". Each record rests on three
frame-aligned sources, and this shows all of them side by side:

  meta_file          the trial's skill level and GRS scores
  transcriptions     every occurrence of the gesture, so an ordinal reference or a
                     "which of the four" question can be checked by eye
  UVA-DSA labels     one row per gesture instance per error type, each explicitly
                     scored ERROR or not -- including the rows we deliberately do
                     not show the model

That last part is the point of printing the rows rather than summarising them. Out
of View is excluded by design (57% of all labels, a framing problem rather than a
technique error), so the label set is richer than what reaches the model, and a
reviewer should be able to see the difference rather than take it on trust.

    python3 scripts/dump_provenance.py outputs/event_qa_ordinal_8-7-2026
    python3 scripts/dump_provenance.py outputs/event_qa_28605 -o provenance.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from error_labels import load_all, resolve_label_root
from localize_events import (GESTURES, read_meta, read_spans, resolve_jigsaws_root,
                             resolve_task_root, timestamp)

WIDTH = 86
SUBSCORES = ("respect for tissue", "suture/needle handling", "time and motion",
             "flow of operation", "overall performance", "quality of final product")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="directory holding qa_records.jsonl, or the file")
    ap.add_argument("-o", "--output", default=None,
                    help="default: <batch>/provenance.txt")
    ap.add_argument("--jigsaws-root", default=None)
    ap.add_argument("--label-root", default=None)
    args = ap.parse_args()

    path = Path(args.batch)
    f = path / "qa_records.jsonl" if path.is_dir() else path
    if not f.exists():
        raise SystemExit(f"no records at {f}")
    recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not recs:
        raise SystemExit(f"{f} is empty")
    out = Path(args.output) if args.output else (
        (path if path.is_dir() else path.parent) / "provenance.txt")

    jigsaws = resolve_jigsaws_root(args.jigsaws_root)
    label_root = resolve_label_root(args.label_root)
    labels = load_all(label_root) if label_root else []

    lines: list[str] = []
    add = lines.append
    add("ANNOTATION TRAIL")
    add("=" * WIDTH)
    add("")
    add(f"{len(recs)} records from {f}")
    add(f"JIGSAWS: {jigsaws}")
    add(f"Error labels: {label_root or '(none found)'}")
    add("")
    add("Every question below is built from three frame-aligned sources: the trial's")
    add("GRS scores, the gesture transcription, and the UVA-DSA error labels. The label")
    add("rows are printed in full, including rows scored 'not an error' and rows for")
    add("error types we deliberately exclude, so what the model was shown can be")
    add("compared against everything that was available.")
    add("")

    meta_cache: dict[str, dict] = {}
    spans_cache: dict[tuple[str, str], list] = {}
    for rec in recs:
        task, trial = rec.get("task"), rec.get("trial_id")
        g = rec.get("grounding") or {}
        gesture = g.get("gesture")
        try:
            root = resolve_task_root(jigsaws, task)
            if task not in meta_cache:
                meta_cache[task] = read_meta(root, task)
            meta = meta_cache[task]
            if (task, trial) not in spans_cache:
                spans_cache[(task, trial)] = read_spans(root, trial)
            spans = spans_cache[(task, trial)]
        except Exception as exc:  # noqa: BLE001
            add(f"!! {trial}: cannot read dataset ({exc})")
            continue

        info = meta.get(trial, {})
        add("=" * WIDTH)
        add(f"{trial}   task {task}   skill {info.get('skill_level')}   "
            f"GRS {info.get('grs_total')}/30")
        subs = info.get("grs_subscores") or []
        if len(subs) == len(SUBSCORES):
            add("   " + "  ".join(f"{n} {v}" for n, v in zip(SUBSCORES, subs)))
        add("")
        qa = (rec.get("qa") or [{}])[0]
        add("QUESTION")
        add(f"   {qa.get('question','')}")
        add("")

        if gesture:
            occ = sorted((s, e) for s, e, gg in spans if gg == gesture)
            add(f"TRANSCRIPTION -- every occurrence of {gesture} "
                f"({GESTURES.get(gesture, gesture)}):")
            faults = {f["start_frame"] for f in g.get("faults", [])}
            target = g.get("start_frame")
            for i, (s, e) in enumerate(occ, 1):
                mark = ""
                if s == target or s in faults:
                    mark = "   <-- the moment in question"
                add(f"   {i}. frames {s:5d}-{e:5d}   {timestamp(s)}-{timestamp(e)}{mark}")
            add("")

            add("ERROR LABEL ROWS (every file that scored these spans):")
            wanted = {(s, e) for s, e in occ}
            rows = [l for l in labels if l["trial_id"] == trial
                    and (l["start_frame"], l["end_frame"]) in wanted]
            if not rows:
                add("   (none -- this task or gesture is not covered by the label set)")
            for l in sorted(rows, key=lambda l: (l["start_frame"], l["source_file"])):
                idx = [i for i, (s, _) in enumerate(occ, 1) if s == l["start_frame"]]
                add(f"   occ {idx[0] if idx else '?'}  {l['source_file']:30s} "
                    f"{l['error_type']:24s} "
                    f"{'ERROR' if l['is_error'] else 'not an error'}")
            add("")

        add("WHAT THE MODEL WAS SHOWN (the question withholds this):")
        for line in str(rec.get("facts_shown_to_model", "")).splitlines():
            add(f"   {line}")
        if rec.get("answer_guidance"):
            add(f"   (guidance) {rec['answer_guidance']}")
        add("")
        add("ANSWER")
        for line in str(qa.get("answer", "")).splitlines():
            add(f"   {line}")
        add("")

    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {len(recs)} records, {len(lines)} lines, {len(text)} chars, UTF-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
