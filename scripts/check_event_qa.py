#!/usr/bin/env python3
"""Check event-grounded QA records.

check_template_d.py cannot be reused here. It decides whether a claim is grounded
by looking up a GRS subscore, and event records have no subscores -- they have a
question that states one measured fact about one span. That difference makes the
rule simpler and stricter:

    an answer may assert only what its own question asserted, plus general
    surgical knowledge. Everything else is unmeasured.

So "your reach took 28 seconds" is grounded when the question says so, while
"relax your wrist" is not, because nothing measured the wrist. That exact drift
appeared in the first prototype batch, which is why this exists.

Checks, in the order they tend to matter:

  unmeasured    a mechanic the question never mentions -- wrist, grip force, tip
                control, tremor, posture. The prototype's "consciously relaxing
                your wrist" is the motivating case.
  meta          a score, rating, rubric or benchmark referred to as such. The
                question gives a comparison figure; the answer should speak in
                seconds or repetitions, not in scores.
  patient       consequences that need a living patient. These are bench-top
                recordings on synthetic models.
  unanchored    an answer that never refers to the span or the action, which is
                the failure the whole event design exists to prevent.
  verbose       more than MAX_SENTENCES or MAX_WORDS. Brevity was an explicit
                review request: a long answer cannot be checked against the one
                fact it was given.
  contradicts   criticism in answer to a question reporting performance better
                than the comparison, or praise in answer to one reporting worse.

    python3 scripts/check_event_qa.py outputs/event_qa_prototype
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_SENTENCES = 4      # the prompt asks for two or three; four is the tolerance
MAX_WORDS = 90

# Mechanics nothing in an event question can measure. The kinematics do record
# tooltip position and gripper angle, but no event question reports them, so an
# answer naming them is going beyond its own evidence.
UNMEASURED = re.compile(
    r"\b(wrist\w*|elbow\w*|forearm\w*|shoulder\w*|posture|"
    r"grip (?:pressure|force|strength|tension)|"
    r"finger (?:tension|placement|position)|"
    r"tip control|tremor\w*|hand tremor|"
    r"depth perception|bimanual)\b",
    re.IGNORECASE,
)
META = re.compile(
    r"\b(scores?|scored|scoring|subscore\w*|rubrics?|ratings?|percentile\w*|"
    r"benchmark\w*|metrics?|grading|assessments?)\b",
    re.IGNORECASE,
)
PATIENT = re.compile(
    r"\b(patient\w*|bleed\w*|h(a)?emorrhag\w*|ischemi\w*|ischaemi\w*|necrosis|"
    r"perfusion|healing|wound|incision|fascia|postoperative|complication\w*|"
    r"iatrogen\w*)\b",
    re.IGNORECASE,
)
# words that mark criticism vs approval, for the contradiction check
NEGATIVE = re.compile(r"\b(delay\w*|slow\w*|excess\w*|unnecessary|inefficien\w*|"
                      r"fragment\w*|below|struggl\w*|hesitat\w*|wasted|too (?:long|many|much))\b",
                      re.IGNORECASE)
POSITIVE = re.compile(r"\b(efficient\w*|direct|strong\w*|well[- ]established|"
                      r"outperform\w*|faster|above|solid|reliable|good)\b",
                      re.IGNORECASE)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_record(rec: dict) -> list[str]:
    issues: list[str] = []
    if rec.get("validation_status") != "valid" or not rec.get("qa"):
        return [f"REJECTED: {rec.get('validation_error')}"]
    qa = rec["qa"][0] if isinstance(rec["qa"], list) else rec["qa"]
    question = str(qa.get("question", ""))
    answer = str(qa.get("answer", ""))
    if not answer.strip():
        return ["empty answer"]

    # unmeasured mechanics: flagged only when the question did not raise them
    for m in UNMEASURED.finditer(answer):
        term = m.group(0)
        if not re.search(re.escape(term), question, re.IGNORECASE):
            issues.append(f"asserts a mechanic the question never measured: {term!r}")
            break

    m = META.search(answer)
    if m:
        issues.append(f"refers to the measurement apparatus rather than the fact: {m.group(0)!r}")
    m = PATIENT.search(answer)
    if m:
        issues.append(f"real-patient consequence on a bench-top recording: {m.group(0)!r}")

    # anchoring: the answer must point at the span or the action the question named
    span = rec.get("span", "")
    span_parts = [p for p in re.split(r"[-]", span) if p]
    action_words = set(re.findall(r"\b(?:needle|suture|loop|instrument|reach\w*|orient\w*|"
                                  r"position\w*|push\w*|pull\w*|transfer\w*|segment\w*)\b",
                                  question, re.IGNORECASE))
    anchored = any(p and p in answer for p in span_parts) or any(
        re.search(re.escape(w), answer, re.IGNORECASE) for w in action_words)
    if not anchored:
        issues.append("never refers to the span or the action the question named")

    sents = sentences(answer)
    words = len(answer.split())
    if len(sents) > MAX_SENTENCES or words > MAX_WORDS:
        issues.append(f"too long: {len(sents)} sentences, {words} words "
                      f"(limit {MAX_SENTENCES}/{MAX_WORDS})")

    # contradiction: the question's own polarity vs the answer's
    kind = rec.get("question_kind")
    if kind == "clean" and NEGATIVE.search(answer) and not POSITIVE.search(answer):
        issues.append("question reports better-than-comparison but the answer only criticises")
    if kind in ("outlier", "wandering", "repetition") and POSITIVE.search(answer) \
            and not NEGATIVE.search(answer):
        issues.append("question reports worse-than-comparison but the answer only praises")
    return issues


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 1
    grand_total = grand_issues = unusable = 0
    for p in paths:
        path = Path(p)
        f = path / "qa_records.jsonl" if path.is_dir() else path
        if not f.exists():
            print(f"!! missing: {f}")
            unusable += 1
            continue
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not recs:
            print(f"\n=== {f} (0 records) ===")
            print("  !! empty -- nothing was checked")
            unusable += 1
            continue
        print(f"\n=== {f} ({len(recs)} records) ===")
        n = 0
        for r in recs:
            issues = check_record(r)
            if issues:
                n += len(issues)
                print(f"  {r.get('question_kind', '?'):11s} {r.get('trial_id', '?'):22s} -> {issues}")
        if not n:
            print("  all clean")
        grand_total += len(recs)
        grand_issues += n
    print(f"\n{'=' * 70}")
    print(f"TOTAL: {grand_total} records, {grand_issues} issues")
    if unusable:
        print(f"{unusable} input(s) missing or empty -- NOT checked")
    return 1 if grand_issues or unusable else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
