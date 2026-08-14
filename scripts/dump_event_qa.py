#!/usr/bin/env python3
"""Dump an event QA batch as readable text -- no comparison, just the records.

For sending to a reviewer who asked for raw text rather than screenshots. Writes
the file itself with an explicit encoding rather than relying on a shell redirect:
on Windows, Python's stdout defaults to cp1252 when redirected, which silently
produced a file that would not decode as UTF-8 the first time this was needed.

Each record shows the question, the answer, the model's own account of what it
inferred, and the measurement the question was built from -- so a reviewer can
check the answer against its evidence without opening the JSONL or the video.

    python3 scripts/dump_event_qa.py outputs/event_qa_core_8-4-2026
    python3 scripts/dump_event_qa.py outputs/event_qa_core_8-4-2026 -o samples.txt
"""
from __future__ import annotations

import argparse
import json
import textwrap
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_event_qa import WATCH, check_batch


def phrase_shares(recs: list[dict]) -> list[tuple[str, float]]:
    """Share of answers containing each tracked phrase, worst first.

    Reported so a clean batch says what its numbers actually are rather than only
    that it passed -- "no issues" is much less informative than "25%".
    """
    import re as _re
    answers = [str((r["qa"][0] if isinstance(r["qa"], list) else r["qa"]).get("answer", ""))
               for r in recs if r.get("validation_status") == "valid" and r.get("qa")]
    if not answers:
        return []
    out = [(label, sum(1 for a in answers if _re.search(pat, a, _re.I)) / len(answers))
           for label, pat in WATCH.items()]
    return sorted(out, key=lambda kv: -kv[1])

WIDTH = 84

KINDS = {
    "error": "an error a human reviewer recorded while watching the video, at that span",
    "locate": "a repeated gesture, named by no timestamp or ordinal, where the answer"
              "\n                        must say which occurrence went wrong or that "
              "none did",
    "outlier": "a gesture that took far longer than the strongest recorded attempts at it",
    "wandering": "a span where the instrument travelled much further than in those attempts",
    # Retired: no measurable relationship to the graded scores. Kept here so an
    # older batch containing them still prints a description rather than
    # "(undocumented kind)".
    "repetition": "a step returned to more often than the strongest attempts need"
                  "  [RETIRED: rho +0.04 against the trial scores]",
    "economy": "total gesture count against the task median, as a closed choice"
               "  [RETIRED: rho +0.05]",
    "clean": "a span completed faster than the strongest attempts"
             "  [RETIRED: rho +0.05]",
}
RETIRED = {"repetition", "economy", "clean"}


def wrap(label: str, text: str) -> str:
    return textwrap.fill(f"{label}{text}", WIDTH, subsequent_indent=" " * len(label))


def evidence_line(grounding: dict) -> str:
    """The measurement in one line, whichever kind of event it came from."""
    g = grounding or {}
    # Locate records first: their grounding also carries "occurrences", which the
    # retired repetition question used with a different meaning, so shape matters
    # more than key presence here.
    if "faults" in g:
        n = g.get("occurrences", 0)
        faults = g.get("faults") or []
        if not faults:
            return (f"{g.get('gesture')} occurs {n} times; none marked as an error, in "
                    f"{', '.join(g.get('source_files', []))}")
        parts = ", ".join(
            f"occurrence {f['index']} ({f['span']}): {'; '.join(f['error_types'])}"
            for f in faults)
        return (f"{g.get('gesture')} occurs {n} times. {parts}. Recorded in "
                f"{', '.join(g.get('source_files', []))}")
    if g.get("error_types"):
        # Provenance first: the error type is the fact the question rests on, and the
        # source file makes any record traceable back to the exact annotation row.
        s = " + ".join(g["error_types"])
        s += f", recorded in {', '.join(g['source_files'])}"
        s += f"; gesture {g.get('gesture')}, frames {g.get('start_frame')}-{g.get('end_frame')}"
        if g.get("duration_s") and g.get("expert_median_s"):
            s += (f"; span ran {g['duration_s']:.1f}s against a "
                  f"{g['expert_median_s']:.1f}s median")
        return s
    if "duration_s" in g and "expert_median_s" in g:
        s = f"{g['duration_s']:.1f}s against a {g['expert_median_s']:.1f}s median"
        if "ratio" in g:
            s += f" ({g['ratio']:.1f}x)"
        return s
    if "ratio" in g and "side" in g:
        return f"{g['side']} instrument travelled {g['ratio']:.1f}x further than the median"
    if "occurrences" in g:
        return (f"{g['occurrences']} occurrences against a peer median of "
                f"{g['peer_median']:.0f}")
    if "segments" in g:
        return f"{g['segments']} gesture segments against a task median of {g['task_median']:.0f}"
    return json.dumps(g, ensure_ascii=False)


def pick_examples(recs: list[dict], n: int) -> list[dict]:
    """N records spread across tasks, question kinds and score bands.

    Taking the first N would give several records from one trial, which reads as
    padding and hides whether the set generalises. Greedy: each pick is the record
    whose (task, kind, score band) combination is least represented so far.
    """
    usable = [r for r in recs if r.get("qa")]
    if len(usable) <= n:
        return usable

    def band(rec: dict) -> str:
        g = rec.get("source_annotation", {}).get("grs_total") or 0
        return "low" if g <= 12 else "mid" if g <= 20 else "high"

    chosen: list[dict] = []
    seen_task: Counter = Counter()
    seen_kind: Counter = Counter()
    seen_band: Counter = Counter()
    seen_trial: Counter = Counter()
    remaining = list(usable)
    while len(chosen) < n and remaining:
        # Lower score is better: prefer whatever is least covered so far. Trial is
        # weighted hardest so two records from one video are a last resort.
        remaining.sort(key=lambda r: (seen_trial[r["trial_id"]] * 3
                                      + seen_task[r["task"]]
                                      + seen_kind[r["question_kind"]]
                                      + seen_band[band(r)],
                                      r["trial_id"], r["question_kind"]))
        pick = remaining.pop(0)
        chosen.append(pick)
        seen_task[pick["task"]] += 1
        seen_kind[pick["question_kind"]] += 1
        seen_band[band(pick)] += 1
        seen_trial[pick["trial_id"]] += 1
    return sorted(chosen, key=lambda r: (r["task"], r["trial_id"], r["question_kind"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="directory holding qa_records.jsonl, or the file itself")
    ap.add_argument("-o", "--output", default=None,
                    help="default: <batch>/event_qa_samples.txt")
    ap.add_argument("--examples", type=int, default=0,
                    help="show only N records, chosen to spread across tasks, question "
                         "kinds and score bands rather than taken from the front")
    args = ap.parse_args()

    path = Path(args.batch)
    f = path / "qa_records.jsonl" if path.is_dir() else path
    if not f.exists():
        raise SystemExit(f"no records at {f}")
    recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not recs:
        raise SystemExit(f"{f} is empty")

    out = Path(args.output) if args.output else (
        (path if path.is_dir() else path.parent) / "event_qa_samples.txt")

    all_recs = recs
    if args.examples:
        recs = pick_examples(recs, args.examples)

    lines: list[str] = []
    add = lines.append

    valid = [r for r in recs if r.get("validation_status") == "valid"]
    kinds = Counter(r["question_kind"] for r in recs)
    trials = sorted({r["trial_id"] for r in recs})
    words = [len(str(r["qa"][0]["answer"]).split()) for r in valid if r.get("qa")]

    # Both kinds are built from the UVA-DSA labels, so both belong on the
    # error-grounded branch. Testing only for "error" sent a 38-record locate batch
    # down the proxy branch, where it was described as stating "one measured fact
    # about one span" -- the opposite of what a locate question does -- and asserted
    # "there are no 'good execution' records" when 25 of the 38 were exactly that.
    # A report generator that misdescribes its own input is worse than no report.
    locate_grounded = bool(kinds.get("locate"))
    error_grounded = bool(kinds.get("error")) or locate_grounded
    add("ERROR-GROUNDED QA SAMPLES" if error_grounded else "EVENT-GROUNDED QA SAMPLES")
    add("=" * WIDTH)
    add("")
    if locate_grounded:
        negatives = sum(1 for r in recs
                        if not (r.get("grounding") or {}).get("faults"))
        add("Each question names a gesture the trial performs several times and asks how")
        add("those went. It gives no timestamp, no ordinal and no count, so the model has")
        add("to find which occurrence went wrong -- the half of the task a timestamped")
        add("question removes. The model never saw the video: it is given, separately from")
        add("the question, which occurrences carry a recorded error and which do not.")
        add("")
        add("This is only well posed because the UVA-DSA labels are exhaustive rather than")
        add("a list of errors. Every gesture instance is explicitly scored, so for a given")
        add("trial and gesture we know which occurrences are clean, an answer naming the")
        add("wrong one is measurably wrong, and a gesture with no errors at all is a valid")
        add("question whose correct answer is that nothing went wrong.")
        add("")
        add(f"{negatives} of these {len(recs)} records are such negatives -- the")
        add("direct test of whether the model invents a fault where there is none,")
        add("which no fault-only batch can measure.")
        add("")
        add("Each answer carries only what the annotations state. Where a record has a")
        add("recorded fault, the likely explanation is in a separate `inferred_cause`")
        add("field, printed below under its own heading and never run together with the")
        add("answer. Nothing records whether those explanations are correct: the labels")
        add("say THAT the needle was at the wrong angle, never WHY. Keeping them in one")
        add("field left 31% of the answer text unverifiable with nothing marking which")
        add("31%. Reviewing whether the causes are clinically plausible is a separate")
        add("question from reviewing the answers, and needs someone who operates.")
        add("")
        add("Error labels are from the UVA-DSA consensus annotations for JIGSAWS")
        add("(github.com/UVA-DSA/ExecProc_Error_Analysis, arXiv 2106.11962, Hutchinson et")
        add("al.), whose frame bounds match the JIGSAWS gesture transcriptions exactly.")
        add("A gesture is skipped unless every one of its occurrences in that trial carries")
        add("a label: without that, 'did any of them go wrong' has no defensible answer.")
        add("")
        add("Out of View labels are excluded. They are 57% of all labelled errors but")
        add("record an instrument leaving the camera frame, which is a framing problem whose")
        add("only coaching is 'keep it in view'.")
    elif error_grounded:
        add("Each question names an error that a human reviewer recorded while watching the")
        add("video, at the span where it happened, and asks about that error specifically.")
        add("The model itself never saw the video: it is given the recorded error, the")
        add("gesture, the timestamps and a duration comparison, and nothing else. So the")
        add("subject of every question is a fact, not an inference, which is what allows an")
        add("answer to be checked rather than merely judged plausible.")
        add("")
        add("Error labels are from the UVA-DSA consensus annotations for JIGSAWS")
        add("(github.com/UVA-DSA/ExecProc_Error_Analysis, arXiv 2106.11962, Hutchinson et")
        add("al.). Their frame bounds match the JIGSAWS gesture transcriptions exactly --")
        add("4,159 rows, 100% exact, gesture IDs agreeing throughout -- and generation")
        add("refuses to run unless that check passes, because attaching the right error to")
        add("the wrong moment would produce a batch that looks healthy and is wrong")
        add("throughout. Each record below cites the CSV it came from.")
        add("")
        add("Out of View labels are excluded. They are 57% of all labelled errors but")
        add("record an instrument leaving the camera frame, which is a framing problem whose")
        add("only coaching is 'keep it in view'.")
    else:
        add("Each question states one measured fact about one span of the recording, taken")
        add("from the JIGSAWS gesture transcriptions and kinematics. No frames were used and")
        add("the model never saw the video, so every answer rests only on the figure in its")
        add("own question plus general surgical knowledge -- which is what makes it checkable.")
        add("Every stated duration was verified against the raw gesture transcription.")
    add("")
    if args.examples and len(all_recs) > len(recs):
        add(f"{len(recs)} examples shown, chosen to spread across tasks, question kinds and")
        add(f"score bands, out of {len(all_recs)} records over "
            f"{len({r['trial_id'] for r in all_recs})} trials.")
    else:
        add(f"{len(recs)} records over {len(trials)} trials. {len(valid)} valid.")
    if words:
        add(f"Answer length: mean {sum(words)//len(words)}, min {min(words)}, "
            f"max {max(words)} words.")
    add("")
    add("Question kinds:")
    for kind, desc in KINDS.items():
        if kinds.get(kind):
            add(f"  {kind:11s} {kinds[kind]:>2}   {desc}")
    unknown = set(kinds) - set(KINDS)
    for kind in sorted(unknown):
        add(f"  {kind:11s} {kinds[kind]:>2}   (undocumented kind)")
    add("")
    if error_grounded:
        # Measured from these records, never hardcoded. An earlier report generator in
        # this project stated "0 rejected" as a literal string and was wrong.
        batch_issues = check_batch(all_recs)
        if batch_issues:
            add("KNOWN LIMITATION, stated rather than left to be noticed. Measured across")
            add("this batch:")
            for issue in batch_issues:
                add(f"  - {issue}")
            add("Answers converging on one phrasing is the reviewer's original complaint")
            add("reappearing a sentence further in, so it is reported here rather than")
            add("left to be noticed.")
        else:
            shares = phrase_shares(all_recs)
            add("No phrase appears in more than 40% of these answers, and no word sequence")
            add("in more than half, on the automated check in scripts/check_event_qa.py.")
            if shares:
                add("The most repeated tracked phrases, for reference:")
                for label, share in shares[:3]:
                    add(f"  - {label}: {share:.0%} of answers")
            add("That was not true of earlier batches. Asking about a recorded error rather")
            add("than an inferred one took the worst case from 93% of answers to 25%,")
            add("and assigning each record its hedge and its opening rather than asking")
            add("for variety took it the rest of the way.")
    elif RETIRED & set(kinds):
        add("This batch contains retired question kinds. Their measures were correlated")
        add("against the graded scores over all 103 trials and showed no relationship, so")
        add("they are no longer generated: a question can be cleanly verifiable and still")
        add("be about the wrong quantity.")
    else:
        add("KNOWN LIMITATION, stated rather than left to be noticed. The corrections in")
        add("these answers read alike -- most name an indirect path or mid-action")
        add("hesitation and prescribe committing to a direct one. That is not a prompting")
        add("failure but a consequence of the evidence available: the annotations record")
        add("that a span took too long or travelled too far, and never why. One signal")
        add("admits one plausible explanation, so the model gives it every time. What can")
        add("be claimed for these records is narrow and true -- they localize WHERE a")
        add("trial went wrong, at Spearman -0.44 (duration) and -0.35 (distance) against")
        add("the graded scores. They do not establish why. Gesture-level error labels,")
        add("which name four distinct failure types, are what would make the cause")
        add("answerable and the corrections genuinely different from each other.")
    add("")
    if not error_grounded:
        add("")
        add("There are no 'good execution' records. A detector for well-executed spans was")
        add("built and measured at Spearman +0.05, meaning it found nothing, so it was")
        add("withdrawn rather than shipped. A trial can therefore yield no records at all:")
        add("nothing in it crosses a fault threshold. (Locate batches do have negatives --")
        add("see above -- because a recorded label can state that a span was clean, which")
        add("a motion proxy cannot.)")
    add("")

    for trial in trials:
        group = [r for r in recs if r["trial_id"] == trial]
        info = group[0].get("source_annotation", {})
        add("")
        add("=" * WIDTH)
        add(f"{trial}   skill {info.get('skill_level')}   "
            f"GRS {info.get('grs_total')}/30   {len(group)} records")
        add("=" * WIDTH)
        for rec in group:
            add("")
            add(f"[{rec['question_kind']}]  {rec.get('span', '')}")
            add("-" * WIDTH)
            if not rec.get("qa"):
                add(f"  REJECTED: {rec.get('validation_error')}")
                continue
            qa = rec["qa"][0]
            add(wrap("Q: ", str(qa.get("question", ""))))
            add("")
            add(wrap("A: ", str(qa.get("answer", ""))))
            # Printed under its own heading, and never run together with the answer.
            # The whole reason it is a separate field is that a reader cannot tell an
            # annotation-supported statement from an inference once they share a
            # paragraph -- so a report that reflows them into one would undo the
            # thing being reported.
            if str(qa.get("inferred_cause", "") or "").strip():
                add("")
                add(wrap("Inferred cause (NOT recorded anywhere -- an inference): ",
                         str(qa["inferred_cause"])))
            if qa.get("rationale"):
                add("")
                add(wrap("Rationale: ", str(qa["rationale"])))
            add("")
            add(wrap("Measured from the annotations: ", evidence_line(rec.get("grounding"))))

    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    print(f"  {len(recs)} records, {len(lines)} lines, {len(text)} chars, UTF-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
