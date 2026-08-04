#!/usr/bin/env python3
"""Put the generic Type D answers next to the localized event answers, per trial.

Review feedback on the Type C/D batches was that the answers read generically --
the first sentence of D1 or D2 could apply to any video without watching it --
and that broad questions give the model too much freedom, which invites
hallucination and makes verification hard. This prints the two side by side for
the same trial so that claim can be checked rather than argued about, and prints
raw text because raw text was what was asked for.

The pairing is only possible because both batches cover the same 15 trials
(scripts/stratified_trials.txt). Point it at any two output directories.

Under each pair it reports what actually separates them:

  anchored openings   whether the first sentence names something specific -- a
                      number, a timestamp, or the action the question was about.
                      NOT whether the first sentences differ from each other:
                      measured that way the generic batch scores 20 of 20,
                      because "Rebuilding foundational instrument control must
                      come before complex sequences" is lexically unique and
                      still fits any novice in the dataset. Lexical variety was
                      never the complaint; applying without watching was.
  length              measured 70 words mean for the generic batch against 55
                      for the event one. A smaller gap than it first appeared --
                      an earlier note here claimed ~120 for generic, which no
                      batch supports. An answer too long to check against its
                      own evidence was the second complaint, so the figure
                      matters and is reported rather than asserted.
  question variety    how many distinct question wordings the batch used for
                      this trial. 7 of the 10 A-D templates ask a literally
                      identical question of every trial, differing only in the
                      timestamp, which is the mechanism behind generic answers:
                      nothing in the input distinguishes one moment from another.

    python3 scripts/compare_generic_vs_event.py \
        --generic outputs/template_d_stratified_7-29-2026 \
        --events  outputs/event_qa_27996
    python3 scripts/compare_generic_vs_event.py --trial Knot_Tying_G004 ...
"""
from __future__ import annotations

import argparse
import json
import re
import textwrap
from pathlib import Path
from typing import Any

WIDTH = 88


def load(path: Path) -> list[dict[str, Any]]:
    f = path / "qa_records.jsonl" if path.is_dir() else path
    if not f.exists():
        raise SystemExit(f"no records at {f}")
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def qa_of(rec: dict) -> tuple[str, str]:
    qa = rec.get("qa")
    if not qa:
        return "", ""
    item = qa[0] if isinstance(qa, list) else qa
    return str(item.get("question", "")), str(item.get("answer", ""))


def first_sentence(text: str) -> str:
    parts = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return parts[0] if parts else ""


# A first sentence is anchored when it names something that could only come from
# this recording: a figure, a timestamp, or the specific action under discussion.
# Generic coaching language -- "rebuild foundational control", "focus on economy
# of motion" -- names none of those and is what "could apply to any video" means.
ANCHOR = re.compile(
    r"\b\d|\d+:\d|"
    r"(?:reach\w*|orient\w*|position\w*|push\w*|pull\w*|transfer\w*|loop\w*|"
    r"tighten\w*|wrap\w*|segment\w*|stitch\w*|throw\w*|knot\w*|needle|suture)\b",
    re.IGNORECASE,
)


def is_anchored(answer: str) -> bool:
    return bool(ANCHOR.search(first_sentence(answer)))


def wrap(label: str, text: str) -> str:
    return textwrap.fill(f"{label}{text}", WIDTH,
                         subsequent_indent=" " * len(label)) if text else f"{label}(none)"


def kind_of(rec: dict) -> str:
    return rec.get("template_id") or rec.get("question_kind") or "?"


def report(trial: str, generic: list[dict], events: list[dict], per_family: int) -> dict:
    print("=" * WIDTH)
    info = (generic or events)[0].get("source_annotation", {})
    print(f"{trial}   skill {info.get('skill_level')}   GRS {info.get('grs_total')}/30")
    print("=" * WIDTH)

    stats: dict[str, Any] = {}
    for name, recs in (("GENERIC (trial-level scores)", generic), ("EVENT (one localized span)", events)):
        print(f"\n--- {name}: {len(recs)} records ---")
        for rec in recs[:per_family]:
            question, answer = qa_of(rec)
            print()
            print(wrap(f"[{kind_of(rec)}] Q: ", question))
            print()
            print(wrap("          A: ", answer))
        if len(recs) > per_family:
            print(f"\n  ... {len(recs) - per_family} more not shown")

        words = [len(qa_of(r)[1].split()) for r in recs if qa_of(r)[1]]
        distinct = len({re.sub(r"[\d:.\-]+", "#", qa_of(r)[0]) for r in recs if qa_of(r)[0]})
        stats[name] = {"records": len(recs), "distinct_questions": distinct,
                       "mean_words": sum(words) // len(words) if words else 0}
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generic", default="outputs/template_d_stratified_7-29-2026")
    ap.add_argument("--events", required=True)
    ap.add_argument("--trial", default=None, help="one trial; default is every shared trial")
    ap.add_argument("--per-family", type=int, default=2,
                    help="records shown per family per trial (default 2)")
    args = ap.parse_args()

    generic = load(Path(args.generic))
    events = load(Path(args.events))
    g_trials = {r["trial_id"] for r in generic}
    e_trials = {r["trial_id"] for r in events}
    shared = sorted(g_trials & e_trials)
    if args.trial:
        if args.trial not in shared:
            raise SystemExit(f"{args.trial} is not in both batches. Shared: {', '.join(shared) or 'none'}")
        shared = [args.trial]
    if not shared:
        raise SystemExit(
            f"no trial appears in both batches -- nothing to compare.\n"
            f"  generic: {len(g_trials)} trials\n  events:  {len(e_trials)} trials\n"
            f"Both batches must cover the same trials; see scripts/stratified_trials.txt.")

    print(f"{len(shared)} shared trial(s): {', '.join(shared)}")
    # Only trials in both batches. Reporting a one-sided trial as a comparison
    # would be the same mistake as an earlier report that claimed "0 issues"
    # from a hardcoded string rather than from the records.
    only_g, only_e = sorted(g_trials - e_trials), sorted(e_trials - g_trials)
    if only_g:
        print(f"generic-only, not compared ({len(only_g)}): {', '.join(only_g)}")
    if only_e:
        print(f"event-only, not compared ({len(only_e)}): {', '.join(only_e)}")

    totals: dict[str, list[int]] = {}
    for trial in shared:
        stats = report(trial,
                       [r for r in generic if r["trial_id"] == trial],
                       [r for r in events if r["trial_id"] == trial],
                       args.per_family)
        for name, s in stats.items():
            totals.setdefault(name, []).append(s["mean_words"])

    print()
    print("=" * WIDTH)
    print("ACROSS THE SHARED TRIALS")
    print("=" * WIDTH)
    for name, recs in (("GENERIC (trial-level scores)", generic), ("EVENT (one localized span)", events)):
        recs = [r for r in recs if r["trial_id"] in shared]
        answers = [qa_of(r)[1] for r in recs if qa_of(r)[1]]
        words = [len(a.split()) for a in answers]
        # Numbers masked out, so two questions differing only in their timestamp
        # count as one wording. That masking is the whole measurement: it is what
        # showed 7 of the 10 A-D templates asking one question of every trial.
        shapes = {re.sub(r"[\d:.\-]+", "#", qa_of(r)[0]) for r in recs if qa_of(r)[0]}
        anchored = sum(1 for a in answers if is_anchored(a))
        print(f"\n{name}")
        print(f"  records                      {len(recs)}")
        print(f"  distinct question wordings   {len(shapes)} of {len(recs)}  (numbers masked)")
        print(f"  anchored first sentences     {anchored} of {len(answers)}  "
              f"(names a figure, time or the action)")
        if words:
            print(f"  answer length                mean {sum(words)//len(words)}, "
                  f"min {min(words)}, max {max(words)} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
