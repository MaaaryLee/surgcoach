#!/usr/bin/env python3
"""Zero-shot units for the JIGSAWS VTS "locate" questions (template_id "VTS").

Samples 50 of the 628 reviewed locate questions (blind-filtered, audited; the
10-25/10-26 batch) and rebuilds each one's FIRST-ATTEMPT request exactly as
generate_event_qa.py --vts sends it:

    system  = extract_system_prompt(Prompts_And_Pipeline/system-prompt-jigsaws-locate.md)
              (only the ```text block is sent; the Markdown around it never was)
    user    = VTS_USER_PROMPT.format(question=..., facts=..., guidance="")

The facts block is rebuilt with the pipeline's own build_vts_questions over the raw
JIGSAWS transcriptions + UVA-DSA error labels (+ kinematics baselines, used only by
the "worst" kind), matched to the reviewed record by (trial, kind, span, recorded),
and cross-checked against facts_shown_to_model in the batch's qa_records.jsonl.
The question text is the one in the reviewed record (what was finally asked).

Zero-shot: the locate prompts contain no example answers, sample QA, prior outputs
or calibration, so nothing is cut from the text that is sent. Left out of the run
are the pipeline's non-zero-shot steps: the --max-retries resampling loop, the
regeneration of 23 answers after the audit, and the review_locate_qa.py audit pass
with its calibration self-test.

    python3 scripts/build_units_vts.py --out .
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The locate pipeline's own modules, as run on 10-25/10-26 (byte-identical to
# 2026-10-26-jigsaws-locate-review/scripts/), kept in a subfolder so they do not
# clash with other builders' versions of localize_events.py etc. in scripts/.
sys.path.insert(0, str(HERE / "vts_locate"))
from error_labels import check_alignment, load_all, resolve_label_root  # noqa: E402
from generate_event_qa import (VTS_MIN_TRIAL_S, VTS_USER_PROMPT,  # noqa: E402
                               build_vts_questions)
from localize_events import (build_baselines, read_meta, read_spans,  # noqa: E402
                             resolve_jigsaws_root, resolve_task_root)
from run_annotation_qa_jigsaws import extract_system_prompt  # noqa: E402

SEED = 20261109
TASKS = ("Suturing", "Needle_Passing", "Knot_Tying")
# plan_locate_qa.py: Knot Tying recordings are short, so its floor is 30 s.
MIN_TRIAL_S = {"Suturing": VTS_MIN_TRIAL_S, "Needle_Passing": VTS_MIN_TRIAL_S,
               "Knot_Tying": 30.0}
# review-file kind -> generator kind
KIND = {"phase": "vts_phase", "step": "vts_step", "step in phase": "vts_step_in_phase",
        "step by order": "vts_step_nth", "event": "vts_event",
        "event by order": "vts_event_nth", "worst delay": "vts_worst",
        "redo": "vts_redo", "longest phase": "vts_longest"}

RECORDED_LABEL = {
    "phase": "recorded throw of the task (phase) and its time span",
    "longest phase": "recorded longest throw, its duration against the next longest, and its time span",
    "step": "recorded action and its time span",
    "step in phase": "recorded action within its throw and its time span",
    "step by order": "recorded action, which occurrence it is, and its time span",
    "event": "recorded executional error, the action it happened during, and its time span",
    "event by order": "recorded executional error, which occurrence it is, the action it "
                      "happened during, and its time span",
    "worst delay": "recorded action, its duration against the strongest trials' time "
                   "for the same action, and its time span",
    "redo": "recorded return to an action, the action right before it, and its time span",
}

NOT_RECORDED_COMMON = [
    "how any action was performed: technique, order of moves, hand or instrument "
    "detail, quality, smoothness or skill (the gesture labels record only which "
    "action and when)",
    "why anything happened or took longer, and any consequence",
    "anatomy, tissue, bleeding or any patient (this is a synthetic bench-top model)",
    "the trial's skill level or GRS score (not given to the model)",
    "advice or what the surgeon should do differently",
]
NOT_RECORDED_NO_ERRORS = (
    "whether any error occurred here: no error labels were given for this question, "
    "so the answer must not mention needle drops, wrong angles, failed attempts or "
    "any other error")
NOT_RECORDED_ERRORS = (
    "any error type beyond the UVA-DSA labels shown (needle drop, needle orientation, "
    "multiple attempts), and what the error looked like or how it was recovered")


def task_of(trial: str) -> str:
    return next(t for t in TASKS if trial.startswith(t))


def sample(items: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Round-robin over (kind, task) cells, one per cell per round, preferring
    trials not yet used; the last partial round picks cells at random."""
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for it in items:
        cells[(it["kind"], it["task"])].append(it)
    for key in sorted(cells):
        rng.shuffle(cells[key])
    picked, used_trials = [], Counter()
    while len(picked) < n:
        live = [k for k in sorted(cells) if cells[k]]
        if not live:
            break
        if len(picked) + len(live) > n:
            live = rng.sample(live, n - len(picked))
        for k in live:
            pool = cells[k]
            best = min(range(len(pool)), key=lambda i: (used_trials[pool[i]["trial"]], i))
            it = pool.pop(best)
            used_trials[it["trial"]] += 1
            picked.append(it)
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    run_dir = HERE.parent
    ap.add_argument("--out", required=True, help="writes <out>/units/JIGSAWS_VTS.jsonl")
    ap.add_argument("--review", default=str(
        run_dir / "Prompts_And_Pipeline" / "jigsaws_vts_locate_628_for_review.json"))
    ap.add_argument("--qa-records", default=str(
        run_dir / "Prompts_And_Pipeline" / "jigsaws_vts_locate_628_qa_records.jsonl"),
        help="the batch's qa_records.jsonl, to cross-check the rebuilt facts")
    ap.add_argument("--system-prompt", default=str(
        run_dir / "Prompts_And_Pipeline" / "system-prompt-jigsaws-locate.md"))
    ap.add_argument("--jigsaws-root", default="/mnt/sun/shared/datasets/surgical_skill/JIGSAWS")
    ap.add_argument("-n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    items = review["items"]
    records = {}
    if Path(args.qa_records).exists():
        for line in Path(args.qa_records).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                records[r["qa_id"]] = r
    print(f"review items: {len(items)}; qa_records: {len(records)}")

    rng = random.Random(args.seed)
    picked = sample(items, args.n, rng)

    system_prompt = extract_system_prompt(Path(args.system_prompt))
    labels = load_all(resolve_label_root())
    root = resolve_jigsaws_root(args.jigsaws_root)
    if check_alignment(labels, root) != 0:
        raise SystemExit("error labels do not align with the transcriptions")

    # Rebuild every question of each task in the generator's own order. Only the
    # facts are taken from the rebuild: the wording of a question is picked from a
    # bank by a counter that depends on which trials one job ran (the batch was
    # split across jobs and 23 answers regenerated), so the question asked is
    # taken from the reviewed record, and checked to be one of the bank's wordings
    # for the same facts.
    built: dict[tuple, dict] = {}
    for task in sorted({task_of(it["trial"]) for it in picked}, key=TASKS.index):
        troot = resolve_task_root(root, task)
        meta = read_meta(troot, task)
        tally: Counter = Counter()
        for trial in meta:
            if not (troot / "transcriptions" / f"{trial}.txt").exists():
                continue
            if not read_spans(troot, trial):
                continue
            for q in build_vts_questions(task, trial, troot, labels,
                                         build_baselines(troot, task, meta, exclude=trial),
                                         min_trial_s=MIN_TRIAL_S[task], tally=tally):
                built[(trial, q["kind"], q["span"], q["recorded"])] = q

    out_dir = Path(args.out) / "units"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "JIGSAWS_VTS.jsonl"
    reworded = facts_mismatch = 0
    units = []
    for it in picked:
        kind_g = KIND[it["kind"]]
        span = f"{it['answer_span']['start']}-{it['answer_span']['end']}"
        key = (it["trial"], kind_g, span, it["labels_record"])
        q = built.get(key)
        if q is None:
            raise SystemExit(f"rebuild found no question for {key}")
        rec = records.get(it["id"])
        if rec is not None and rec["facts_shown_to_model"] != q["facts"]:
            facts_mismatch += 1
            print(f"FACTS DIFFER {it['id']}")
        question = it["question"]
        if rec is not None and rec["question"] != question:
            raise SystemExit(f"review and qa_records disagree on the question of {it['id']}")
        if question != q["question"]:
            reworded += 1
        user_prompt = VTS_USER_PROMPT.format(question=question, facts=q["facts"],
                                             guidance=q["guidance"])
        has_errors = kind_g in ("vts_event", "vts_event_nth")
        g = q["grounding"]
        sources = [f"JIGSAWS {it['task']} gesture transcription "
                   f"(transcriptions/{it['trial']}.txt)"]
        if has_errors:
            sources.append("UVA-DSA ExecProc_Error_Analysis error labels: "
                           + ", ".join(g.get("source_files", [])))
        if kind_g == "vts_worst":
            sources.append("JIGSAWS kinematics: step durations against the strongest "
                           "other trials' median for the same gesture")
        info = read_meta(resolve_task_root(root, it["task"]), it["task"]).get(it["trial"], {})
        units.append({
            "unit_id": f"jigsaws_VTS_{it['trial']}_{kind_g}_{span.replace(':', '')}",
            "dataset": "JIGSAWS",
            "template_id": "VTS",
            "question": question,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            # The prompt asks for a one-element ARRAY of {question, answer, rationale};
            # the pipeline unwraps parsed[0] and also accepts a bare object.
            "answer_format": "json_qa",
            "answer_key": "answer",
            "ground_truth": {
                "setting": "bench-top training task",
                "recorded_label": RECORDED_LABEL[it["kind"]],
                "recorded": [l[2:] if l.startswith("- ") else l
                             for l in q["facts"].splitlines()],
                "not_recorded": NOT_RECORDED_COMMON
                + [NOT_RECORDED_ERRORS if has_errors else NOT_RECORDED_NO_ERRORS],
                "localization": "applicable",
                "source_annotation": "; ".join(sources),
            },
            "meta": {
                "task": it["task"], "trial_id": it["trial"],
                "question_kind": it["kind"], "generator_kind": kind_g,
                "answer_span": it["answer_span"],
                "recording_length_s": it["recording_length_s"],
                "labels_record": it["labels_record"],
                "grounding": g,
                "skill_level": info.get("skill_level"), "grs_total": info.get("grs_total"),
                "source_review_id": it["id"],
                "source_batch": "results/outputs JIGSAWS VTS locate 628 QA blind-filtered "
                                "- 10-25-2026 (review file of 10-26)",
                "question_wording_differs_from_rebuild": question != q["question"],
                "pipeline_generation": {"backend": "llama.cpp /v1/chat/completions",
                                        "temperature": 0.6, "max_tokens": 10000,
                                        "enable_thinking": True},
                "sampling": f"seed {args.seed}; round-robin over (kind, task) cells of "
                            f"the 628, preferring unused trials; {len(picked)} of "
                            f"{len(items)}",
            },
        })
    with out_path.open("w", encoding="utf-8") as fh:
        for u in units:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")

    print(f"\nwrote {out_path}: {len(units)} units, {len({u['meta']['trial_id'] for u in units})} trials")
    print(f"facts differing from qa_records: {facts_mismatch}; "
          f"questions whose bank wording differs from a fresh rebuild "
          f"(the reviewed wording is used): {reworded}")
    print("by kind:", dict(Counter(u["meta"]["question_kind"] for u in units)))
    print("by task:", dict(Counter(u["meta"]["task"] for u in units)))
    print("by kind x task:", dict(Counter((u["meta"]["question_kind"], u["meta"]["task"])
                                          for u in units)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
