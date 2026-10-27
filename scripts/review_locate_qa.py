#!/usr/bin/env python3
"""Audit every JIGSAWS locate answer against the annotation it was written from.

The second gate, after check_locate_qa.py. That script checks the questions --
each has exactly one answer, and the text alone does not give it away -- and the
generator checks that an answer is valid JSON. Neither reads what the answer says.
This does: a model audit of each answer against the annotation lines the answering
model was given, in the form run_jigsaws_enriched_d.py uses for Template D. The
auditor does not rewrite anything; it returns every problem as the exact words, the
rule broken and why, and nothing else.

Before any answer is trusted to it, the auditor is tested on answers with a known
verdict, built from real records: the faithful answer must pass, and copies with a
wrong time, the wrong occurrence, an added judgement or a wrong duration must each
be flagged. Run that alone with --calibrate-only.

    python3 scripts/review_locate_qa.py "results/outputs ..." --llama-host http://127.0.0.1:8080 \\
        --calibrate-only
    python3 scripts/review_locate_qa.py "results/outputs ..." --llama-host http://127.0.0.1:8080 \\
        --shard 1/8 -o review-1.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_jigsaws import run_generation_llama, verify_llama_server

JUDGE_SYSTEM = """You audit one answer to a question about where something happens in a recorded
robotic training task, against the annotation the answer was written from. You do not
rewrite it. Report every problem, and nothing else.

Rules:
1. Time span. The answer must give the time span the annotation gives, both ends,
   with the same numbers ("from 0:39.7 to 0:45.1"). Flag a missing end, a different
   number, a rounded time, or a vague time in place of the span.
2. What it is. The answer must name the same thing the annotation records: the same
   phase, action or error, and where the annotation says which occurrence or which
   phase it is, that one, said in the answer. Saying the action as a verb is fine
   ("the surgeon orients the needle" for "orienting the needle"). Flag a different
   action, error type, occurrence number or phase, and an answer that leaves out the
   occurrence number or the phase the annotation gives ("the second time", "during
   the third stitch"). That an action happens only once does not need repeating.
   Where the annotation says an action is done again right after another, flag an
   answer that does not say it is a return, or does not name the action that came
   right before it.
3. Durations. Where the annotation gives durations, the answer must give them, with
   the same numbers. Flag a missing or different duration. Where the annotation gives
   none, flag any duration the answer states; an answer without one is correct.
4. Nothing added. Flag any judgement of how something was done (well, poorly,
   correctly, typical, efficient, smooth, careful), any cause or reason, any
   consequence, any advice or suggestion, any technique or detail of hands or
   instruments, any anatomy, patient or bleeding, and any event, error or step the
   annotation does not mention.
5. Answers the question. The answer must say where or when the thing the question
   asks about happens. Flag an answer that locates something else, or gives no
   location.
6. Person. The answer must not address anyone as "you".
Ignore style: rewording that keeps the meaning is fine. Repeating what the annotation
says is never a problem: which occurrence it is, the action during which an error
happened, that an action happens only once, or another error recorded on the same
stretch.

Return one JSON object and nothing else:
{"problems": [{"quote": "<exact words from the answer>", "rule": <1-6>, "why": "<short>"}]}
Use {"problems": []} when there are none."""


def judge_prompt(record: dict[str, Any], answer: str) -> str:
    return "\n".join(["ANNOTATION", record["facts_shown_to_model"], "",
                      "QUESTION", record.get("question") or record["qa"][0]["question"], "",
                      "ANSWER TO AUDIT", answer])


def parse_judge(text: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    payload = json.loads(cleaned[start:end + 1])
    problems = payload.get("problems")
    if not isinstance(problems, list):
        raise ValueError("judge reply has no problems list")
    return problems


def run_judge(host: str, record: dict[str, Any], answer: str,
              max_tokens: int) -> tuple[list[dict[str, Any]], str]:
    last_error = ""
    for attempt in range(3):
        raw = run_generation_llama(host, JUDGE_SYSTEM, judge_prompt(record, answer),
                                   max_tokens, 0.0 if attempt == 0 else 0.3)
        try:
            return parse_judge(raw), raw
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
    raise ValueError(f"judge reply unreadable: {last_error}")


def calibration_cases(records: list[dict[str, Any]]) -> list[tuple[str, bool, dict, str]]:
    """Answers with a known verdict, built from the first step-by-order and the first
    worst-delay record: (name, should be flagged, record, answer)."""
    step = next(r for r in records if r["question_kind"] == "vts_step_nth")
    worst = next(r for r in records if r["question_kind"] == "vts_worst")
    redo = next(r for r in records if r["question_kind"] == "vts_redo")
    phase_step = next(r for r in records if r["question_kind"] == "vts_step_in_phase")
    a, b = step["span"].split("-")
    nth = re.search(r"\((\w+) of", step["recorded"]).group(1)
    other = "fourth" if nth != "fourth" else "second"
    action = step["recorded"].split(" (")[0]
    faithful = f"The {nth} time the surgeon is {action} runs from {a} to {b}."
    late = f"{float(b.split(':')[1]) + 2.0:04.1f}"
    wa, wb = worst["span"].split("-")
    took = re.search(r"Duration: ([\d.]+) seconds", worst["facts_shown_to_model"]).group(1)
    usual = re.search(r"take about ([\d.]+) seconds", worst["facts_shown_to_model"]).group(1)
    w_action = re.search(r"- Action: (.+)", worst["facts_shown_to_model"]).group(1)
    pa, pb = phase_step["span"].split("-")
    p_action, p_throw = re.match(r"(.+), in the (.+), \d", phase_step["recorded"]).groups()
    phase_faithful = f"The surgeon is {p_action} during the {p_throw} from {pa} to {pb}."
    ra, rb = redo["span"].split("-")
    r_action, r_after = re.search(r"Redo -- (.+?) again after (.+?)(?: \(|,)",
                                  redo["recorded"]).groups()
    redo_faithful = (f"The surgeon goes back to {r_action} right after {r_after}, from {ra} "
                     f"to {rb}.")
    w_faithful = (f"The surgeon loses the most time on {w_action} from {wa} to {wb}, taking "
                  f"{took} seconds where the strongest recorded attempts take about {usual} "
                  f"seconds.")
    return [
        ("faithful", False, step, faithful),
        ("wrong time", True, step, faithful.replace(b, f"{b.split(':')[0]}:{late}")),
        ("wrong occurrence", True, step, faithful.replace(f"The {nth} time", f"The {other} time")),
        # The first full audit passed six answers that never said which occurrence
        # they were; the rule then only named a different occurrence as a problem.
        ("occurrence left out", True, step,
         f"The surgeon is {action} from {a} to {b}."),
        ("added judgement and advice", True, step,
         faithful[:-1] + ", and the surgeon handles it carefully and efficiently; keep the "
                         "instruments close to the tissue."),
        ("faithful worst delay", False, worst, w_faithful),
        # The second full audit passed six redo answers that named the action but not
        # that it was a return, which is what makes the moment the only answer.
        ("faithful return", False, redo, redo_faithful),
        ("return left out", True, redo, f"The surgeon is {r_action} from {ra} to {rb}."),
        ("what came before left out", True, redo,
         f"The surgeon goes back to {r_action} from {ra} to {rb}."),
        # The third full audit flagged 18 answers like this one: right, but without
        # "the only time" or a sentence saying no duration was given.
        ("faithful step in phase", False, phase_step, phase_faithful),
        ("wrong duration", True, worst, w_faithful.replace(f"taking {took}",
                                                           f"taking {float(took) + 5:.1f}")),
    ]


def calibrate(host: str, records: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    cases = []
    for name, should_flag, record, answer in calibration_cases(records):
        try:
            problems, _ = run_judge(host, record, answer, max_tokens)
            correct = bool(problems) == should_flag
        except ValueError as exc:
            problems, correct = [{"why": str(exc)}], False
        cases.append({"case": name, "should_flag": should_flag, "flagged": bool(problems),
                      "correct": correct, "answer": answer, "problems": problems})
    return {"all_correct": all(c["correct"] for c in cases), "cases": cases}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch", help="a batch directory holding qa_records.jsonl")
    ap.add_argument("--llama-host", required=True)
    ap.add_argument("--max-tokens", type=int, default=12000)
    ap.add_argument("--shard", default="1/1", help="i/n: review every n-th record from i")
    ap.add_argument("--calibrate-only", action="store_true",
                    help="run the self-test on answers with a known verdict, and stop")
    ap.add_argument("-o", "--output", default=None, help="review records, as JSON lines")
    args = ap.parse_args()

    records = [json.loads(line) for path in sorted(Path(args.batch).rglob("qa_records.jsonl"))
               for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    verify_llama_server(args.llama_host)
    if args.calibrate_only:
        result = calibrate(args.llama_host, records, args.max_tokens)
        print(json.dumps(result, indent=2))
        return 0 if result["all_correct"] else 1

    i, n = (int(x) for x in args.shard.split("/"))
    mine = records[i - 1::n]
    out = Path(args.output or f"review-{i}-of-{n}.jsonl")
    done = set()
    if out.exists():
        done = {json.loads(l)["qa_id"] for l in out.read_text().splitlines() if l.strip()}
    with out.open("a", encoding="utf-8") as sink:
        for count, record in enumerate(mine, start=1):
            if record["qa_id"] in done:
                continue
            try:
                problems, raw = run_judge(args.llama_host, record,
                                          record["qa"][0]["answer"], args.max_tokens)
                status = "reviewed"
            except ValueError as exc:
                problems, raw, status = [], str(exc), "unreadable"
            sink.write(json.dumps({"qa_id": record["qa_id"], "status": status,
                                   "problems": problems, "judge_raw": raw},
                                  ensure_ascii=False) + "\n")
            sink.flush()
            print(f"[{count}/{len(mine)}] {record['qa_id']}: {status}, "
                  f"{len(problems)} problem(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
