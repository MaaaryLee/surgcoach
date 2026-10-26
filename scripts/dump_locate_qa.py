#!/usr/bin/env python3
"""Write the JIGSAWS locate questions in the layout of the MultiBypass140 locate file.

The MultiBypass140 set is reviewed as one text file, multibypass140_locate_full_*.txt:
a header with counts, then every record as its video, its kind, what the annotation
recorded, the question and the answer. This writes the JIGSAWS set the same way so
the two can be read side by side, plus a Markdown list holding only the questions.

The questions are rebuilt from the labels with build_vts_questions, so this works
before any answer exists. Given the output directory of a --vts run, the answers are
joined in by trial, kind and span.

    python3 scripts/dump_locate_qa.py -o outputs/jigsaws_locate.txt
    python3 scripts/dump_locate_qa.py --records outputs/locate_35890 \\
        -o outputs/locate_35890/jigsaws_locate.txt --md outputs/locate_35890/questions.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import textwrap
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from error_labels import load_all, resolve_label_root
from generate_event_qa import build_vts_questions
from localize_events import (FPS, build_baselines, read_meta, read_spans,
                             resolve_jigsaws_root, resolve_task_root)

SHOW = {"vts_phase": "phase", "vts_longest": "longest phase", "vts_step": "step",
        "vts_step_in_phase": "step in phase", "vts_step_nth": "step by order",
        "vts_event": "event", "vts_event_nth": "event by order",
        "vts_worst": "worst delay", "vts_redo": "redo"}


def wrap(prefix: str, text: str) -> str:
    return textwrap.fill(text, width=78, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jigsaws-root", default=None)
    ap.add_argument("--records", default=None,
                    help="a --vts output directory; its qa_records.jsonl answers are "
                         "joined in")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--md", default=None, help="also write the questions alone here")
    args = ap.parse_args()

    # Given a run, its records are the set: the questions that were asked and
    # answered. Rebuilding them instead gave a different set -- the run starts one
    # process per task, so the quarter balancing restarts per task, and 7 of 166
    # Needle Passing picks in job 35884 differed from the rebuilt ones.
    answers: dict[tuple[str, str, str], str] = {}
    records, lengths = [], {}
    order = ("Suturing", "Needle_Passing", "Knot_Tying")
    for path in sorted(Path(args.records).rglob("qa_records.jsonl")) if args.records else []:
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            qa = (r.get("qa") or [{}])[0]
            q = {"kind": r["question_kind"], "span": r["span"],
                 "question": r.get("question") or qa.get("question", ""),
                 "recorded": r["recorded"], "grounding": r["grounding"]}
            records.append((r["task"], r["trial_id"], q))
            lengths[r["trial_id"]] = r["grounding"]["trial_s"]
            if qa.get("answer"):
                answers[r["trial_id"], r["question_kind"], r["span"]] = qa["answer"]
    # The kind breaks ties: a phase and the step that opens it start on the same
    # frame, and without it their order followed whichever file was read first.
    records.sort(key=lambda x: (order.index(x[0]), x[1],
                                x[2]["grounding"]["clue_start_frame"], x[2]["kind"]))

    if not records:
        labels = load_all(resolve_label_root())
        root = resolve_jigsaws_root(args.jigsaws_root)
        tally: Counter = Counter()
        for task in order:
            troot = resolve_task_root(root, task)
            meta = read_meta(troot, task)
            for trial in meta:
                spans = read_spans(troot, trial)
                if not spans:
                    continue
                lengths[trial] = max(e for _, e, _ in spans) / FPS
                for q in build_vts_questions(
                        task, trial, troot, labels,
                        build_baselines(troot, task, meta, exclude=trial), tally=tally):
                    records.append((task, trial, q))

    used = sorted({t for _, t, _ in records})
    lens = [lengths[t] for t in used]
    clues = [q["grounding"]["clue_s"] for *_, q in records]
    quarters = Counter(q["grounding"]["quarter"] for *_, q in records)
    answered = sum((t, q["kind"], q["span"]) in answers for _, t, q in records)
    lines = [
        "JIGSAWS FIND-IT-IN-THE-RECORDING QA", "=" * 78, "",
        f"records    {len(records)} questions, "
        + (f"{answered} answered" if answers else "answers not yet generated"),
        f"trials     {len(used)}   tasks {dict(Counter(t for t, *_ in records))}",
        f"kinds      {dict(Counter(SHOW[q['kind']] for *_, q in records))}",
        f"recording  median {statistics.median(lens):.0f} s, range "
        f"{min(lens):.0f}-{max(lens):.0f} s",
        f"answer     a span of median {statistics.median(clues):.1f} s, range "
        f"{min(clues):.1f}-{max(clues):.1f} s",
        "quarter    where the answer falls in its recording: "
        + ", ".join(f"Q{k} {quarters[k]}" for k in range(1, 5)), "",
        wrap("note       ", "The kinds of the MultiBypass140 locate set on bench-top "
             "recordings: questions over whole recordings, each with exactly one "
             "answer. Every stretch that passes the filters is asked about once, so "
             "the quarters are not balanced. Every span is read from the raw "
             "labels."),
        "", "-" * 78, f"ALL {len(records)} RECORDS", "-" * 78, ""]
    for _, trial, q in records:
        lines += [f"  [{trial}]  {SHOW[q['kind']]}",
                  wrap("    recorded: ", q["recorded"]),
                  wrap("    Q: ", q["question"])]
        answer = answers.get((trial, q["kind"], q["span"]))
        if answer:
            lines.append(wrap("    A: ", answer))
        lines.append("")
    lines += ["-" * 78, "LIMITS", "-" * 78, "", textwrap.fill(
        "Each question has exactly one answer in its recording: one throw by its "
        "order, a gesture performed once in the trial or once in its throw or named "
        "by its occurrence, an error that is the only one of its type or named by its "
        "occurrence, a return to an action right after starting the next, the "
        "longest throw, or the single step that runs furthest over the time the "
        "strongest trials take; the "
        "two extremes only by a clear margin over the next. Questions a guess from "
        "the text alone locates at IoU 0.3 or better are left out "
        "(plan_locate_qa.py). JIGSAWS labels no phases, so a throw (a stitch, a pass "
        "through a ring, a throw of the knot) is cut from the gesture transcription, "
        "and only in trials with the number of throws the task calls for. The errors "
        "are the UVA-DSA consensus labels, which cover Suturing and Needle Passing "
        "only, and carry no grade; the worst is therefore the longest overrun, not the "
        "most serious error. Knot Tying recordings have a median of 45 s, so few of "
        "its stretches are under 20% of the video. The generating model never saw "
        "video.", width=78, initial_indent="  ", subsequent_indent="  "), ""]
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    if args.md:
        Path(args.md).write_text("".join(f"{i}. {q['question']}\n" for i, (*_, q)
                                         in enumerate(records, start=1)),
                                 encoding="utf-8")
    print(f"wrote {args.output}: {len(records)} questions, {answered} answered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
