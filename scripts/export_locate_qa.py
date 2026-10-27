#!/usr/bin/env python3
"""Write a locate batch, with its audit, as one JSON file for people to check.

qa_records.jsonl is the source of truth but is built for the pipeline: one line per
record, with prompts, raw model output and provenance. A reviewer needs the question,
the answer, where in which video, what the labels record there, and anything the
audit (review_locate_qa.py) flagged -- plus a place to say whether it is right. Each
item carries an empty `review` field for that.

    python3 scripts/export_locate_qa.py "results/outputs ..." --audit outputs/review \\
        -o "results/outputs .../jigsaws_vts_locate_for_review.json"
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

SHOW = {"vts_phase": "phase", "vts_longest": "longest phase", "vts_step": "step",
        "vts_step_in_phase": "step in phase", "vts_step_nth": "step by order",
        "vts_event": "event", "vts_event_nth": "event by order",
        "vts_worst": "worst delay", "vts_redo": "redo"}
KINDS = {
    "phase": "One throw by its number (a stitch, a pass through a ring, a throw of the "
             "knot), cut from the gesture labels at each needle transfer.",
    "step": "A gesture the surgeon performs once in the whole recording.",
    "step in phase": "A gesture performed once within one throw.",
    "step by order": "A gesture named by its occurrence (\"the second time...\").",
    "event": "An executional error that is the only one of its type in the recording "
             "(UVA-DSA labels).",
    "event by order": "An executional error named by its occurrence (\"the second needle "
                      "orientation error\").",
    "worst delay": "The step furthest over the time the strongest trials take for the same "
                   "action, by a clear margin over the next; stands in for the "
                   "MultiBypass140 'worst bleed'.",
    "redo": "The surgeon goes back to an action right after starting the next one.",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="a batch directory holding qa_records.jsonl")
    ap.add_argument("--audit", default=None,
                    help="a directory of review_locate_qa.py output (review-*.jsonl)")
    ap.add_argument("--calibration", default=None,
                    help="the auditor's self-test result, as JSON, to record with the audit")
    ap.add_argument("--check", action="append", default=[],
                    help="a line for the file's 'checks' list, e.g. a check_locate_qa.py "
                         "result; repeat for several")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    records = [json.loads(line) for path in sorted(Path(args.batch).rglob("qa_records.jsonl"))
               for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    audit = {}
    for path in sorted(Path(args.audit).glob("review-*.jsonl")) if args.audit else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                a = json.loads(line)
                audit[a["qa_id"]] = a

    items = []
    for r in records:
        g, qa = r["grounding"], r["qa"][0]
        start, end = r["span"].split("-")
        a = audit.get(r["qa_id"])
        items.append({
            "id": r["qa_id"],
            "task": r["task"],
            "trial": r["trial_id"],
            "videos": [f"{r['trial_id']}_capture1.avi", f"{r['trial_id']}_capture2.avi"],
            "kind": SHOW[r["question_kind"]],
            "question": r.get("question") or qa["question"],
            "answer": qa["answer"],
            "answer_span": {"start": start, "end": end,
                            "start_s": round(g["clue_start_frame"] / 30, 2),
                            "end_s": round(g["clue_end_frame"] / 30, 2),
                            "start_frame": g["clue_start_frame"],
                            "end_frame": g["clue_end_frame"]},
            "recording_length_s": g["trial_s"],
            "labels_record": r["recorded"],
            "audit": None if a is None else {"status": a["status"],
                                             "problems": a["problems"]},
            "review": {"ok": None, "comment": ""},
        })

    flagged = [i for i in items if i["audit"] and i["audit"]["problems"]]
    calibration = (json.loads(Path(args.calibration).read_text(encoding="utf-8"))
                   if args.calibration else None)
    doc = {
        "title": "JIGSAWS find-it-in-the-recording (VideoTreeSearch-style) QA, for review",
        "prepared": date.today().isoformat(),
        "source": str(Path(args.batch)),
        "how_to_review": (
            "Open the trial's video at answer_span and check three things: the answer's "
            "span is where the question's thing happens; it is the only place in the "
            "recording that answers the question; and the answer says nothing the labels "
            "do not record. Set review.ok to true or false and add a comment where "
            "needed."),
        "about": {
            "format": "One question about a whole recording, answered by one stretch of "
                      "it, as in VideoTreeSearch (arXiv 2607.16189) and the MultiBypass140 "
                      "locate set. Answers give the time span and what the labels record "
                      "there; no feedback.",
            "filters": "Clue at least 3 s and at most 20% of the recording; exactly one "
                       "answer per recording, verified from the raw labels; any question "
                       "a guess from its text alone places at IoU >= 0.3 dropped, "
                       "repeated until none is found.",
            "times": "minutes:seconds from the start of the video, at 30 fps; frames "
                     "match the JIGSAWS transcriptions.",
            "kinds": KINDS,
        },
        "checks": args.check,
        "counts": {
            "questions": len(items),
            "trials": len({i["trial"] for i in items}),
            "by_task": dict(Counter(i["task"] for i in items)),
            "by_kind": dict(Counter(i["kind"] for i in items)),
            "audit_flagged": len(flagged),
        },
        "audit": {
            "how": "A second pass of the same model reads each answer against the labels "
                   "it was written from and lists every problem, by rule: 1 time span, "
                   "2 what it is, 3 durations, 4 nothing added, 5 answers the question, "
                   "6 no 'you'.",
            "self_test": calibration,
        },
        "items": items,
    }
    Path(args.output).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(f"wrote {args.output}: {len(items)} items, {len(flagged)} flagged by the audit, "
          f"{sum(i['audit'] is None for i in items)} not audited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
