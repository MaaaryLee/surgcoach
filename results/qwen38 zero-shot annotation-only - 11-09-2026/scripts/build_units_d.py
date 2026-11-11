#!/usr/bin/env python3
"""Zero-shot units for the two Template D coaching pipelines (JIGSAWS and MultiBypass140).

Each unit is the first-attempt request the pipeline sends, built with the pipeline's own
functions, minus what is not zero-shot:
  - MultiBypass140: the system prompt's "Examples of the voice" block is removed.
  - JIGSAWS: the "OPENINGS ALREADY USED IN THIS BATCH" list (earlier outputs) is never added.
  - Both: no retry-with-feedback, no audit judge, no self-test (those live in the runners'
    main loops, which are not used).
Question gating is the pipelines' own (it defines which questions are well posed).

Run on oprime from the run dir:
  python3 scripts/build_units_d.py --out units \
     --jigsaws-root /mnt/sun/shared/datasets/surgical_skill/JIGSAWS \
     --iae-root <...>/labels --tables <...>/tables
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SEED = 20261109
N = 50


def strip_sections(text: str, names: tuple[str, ...] = ("QUESTION", "ANSWER PLAN")) -> str:
    """Drop the named header sections (header line up to the next blank line)."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() in names:
            skipping = True
            continue
        if skipping:
            if not line.strip():
                skipping = False
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def system_prompt_from(path: Path, drop_examples: bool) -> str:
    text = path.read_text(encoding="utf-8")
    block = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    prompt = block.group(1).strip() if block else text.strip()
    if drop_examples:
        new = re.sub(r"\nExamples of the voice\..*?(?=\nWhat you may not do:)", "\n", prompt, flags=re.DOTALL)
        if new == prompt:
            raise SystemExit("examples block not found in " + str(path))
        prompt = new
    return prompt


def spread_sample(items: list, key, n: int) -> list:
    """Seeded sample of n, round-robin over groups given by key, so videos/tasks spread."""
    rng = random.Random(SEED)
    groups: dict = {}
    for it in items:
        groups.setdefault(key(it), []).append(it)
    for g in groups.values():
        rng.shuffle(g)
    order = sorted(groups)
    rng.shuffle(order)
    picked = []
    while len(picked) < n and any(groups[k] for k in order):
        for k in order:
            if groups[k] and len(picked) < n:
                picked.append(groups[k].pop())
    return picked


# ---------------------------------------------------------------------------
def jigsaws_units(args) -> dict[str, list[dict]]:
    import run_jigsaws_enriched_d as J

    trials = sorted(p.stem for p in args.jigsaws_root.glob("*/transcriptions/*.txt"))
    enriched = args.work / "jigsaws_enriched_all.jsonl"
    if not enriched.exists():
        subprocess.run([sys.executable, str(HERE / "build_jigsaws_enriched_pilot.py"),
                        "--jigsaws-root", str(args.jigsaws_root), "--trials", ",".join(trials),
                        "--output-dir", str(args.work / "jigsaws_enriched")], check=True)
        (args.work / "jigsaws_enriched" / "enriched_trial_inputs.jsonl").replace(enriched)
    records = [json.loads(l) for l in enriched.read_text(encoding="utf-8").splitlines() if l.strip()]
    system = system_prompt_from(args.jigsaws_system, drop_examples=False)
    out: dict[str, list[dict]] = {}
    for tid in ("D1", "D2", "D5"):
        eligible = []
        for rec in records:
            try:
                eligible.append(J.build_unit(rec, tid))
            except ValueError:
                pass
        chosen = spread_sample(eligible, key=lambda u: u["task"], n=N)
        units = []
        for u in chosen:
            user = J.user_prompt(u, [])
            recorded = strip_sections(u["prompt_body"])
            units.append({
                "unit_id": f"jigsaws_{tid}_{u['trial_id']}",
                "dataset": "JIGSAWS", "template_id": tid, "question": u["question"],
                "system_prompt": system, "user_prompt": user,
                "answer_format": "json_answer", "answer_key": "answer",
                "generation": {"temperature": 0.7, "max_tokens": 8000, "thinking": True},
                "ground_truth": {
                    "setting": "bench-top training task",
                    "recorded_label": "recorded ratings, with the rubric's description of each described level"
                                      + (", and the run's timing against the most efficient runs" if u["timing"] else ""),
                    "recorded": recorded,
                    "not_recorded": ("any reason why anything happened; any quality at a particular moment (the "
                                     "ratings cover the whole run); errors; a description for levels 2 and 4; "
                                     "anatomy, patient, bleeding; practice advice or technique"),
                    "localization": "NOT APPLICABLE",
                    "source_annotation": "JIGSAWS meta file GRS ratings (one expert rater), Gao et al. 2014 "
                                         "Table 3 descriptions, gesture transcriptions",
                },
                "meta": {"trial_id": u["trial_id"], "task": u["task"], "span": u["span"], "scores": u["scores"],
                         "eligible_in_pool": len(eligible)},
            })
        out[tid] = units
    return out


# ---------------------------------------------------------------------------
def mb140_units(args) -> dict[str, list[dict]]:
    import run_mb140_coaching_d as M

    phases, steps = M.load_names(args.tables)
    videos = M.load_videos(args.iae_root)
    candidates = M.build_candidates(videos, phases, steps)
    selection = M.select(candidates, {"D1": N, "D2": N, "D5": N})
    system = system_prompt_from(args.mb140_system, drop_examples=True)
    out: dict[str, list[dict]] = {"D1": [], "D2": [], "D5": []}
    for u in selection:
        question, prompt_text = M.build_prompt(u, phases)
        u["question"], u["prompt_text"] = question, prompt_text
        ev = M.evidence(u)
        tid = u["template_id"]
        out[tid].append({
            "unit_id": ev["qa_id"].replace("_coaching", ""),
            "dataset": "MultiBypass140", "template_id": tid, "question": question,
            "system_prompt": system, "user_prompt": prompt_text + '\n\nReturn {"answer": "..."} only.',
            "answer_format": "json_answer", "answer_key": "answer",
            "generation": {"temperature": 0.6, "max_tokens": 10000, "thinking": True},
            "ground_truth": {
                "setting": "in-vivo procedure",
                "recorded_label": "recorded adverse events, each with its time, step, phase and the published "
                                  "description of its SEVERE grade" + (", and the series count given" if tid == "D5" else ""),
                "recorded": strip_sections(prompt_text),
                "not_recorded": ("the cause of any event; who or which instrument caused it; anatomy beyond the "
                                 "step name; patient outcome; the numeric grade (only its description is used); "
                                 "any advice or technique"),
                "localization": "NOT APPLICABLE" if tid != "D2" else "applicable",
                "source_annotation": "MultiBypass140 intraoperative adverse event labels "
                                     "(multibypass06_corrected, CAMMA 2025-09-02), phase and step labels",
            },
            "meta": {"video_id": u["video_id"], "center": u["center"], "phase": u["phase"],
                     "start_s": u["start_s"], "end_s": u["end_s"],
                     "candidates_in_pool": len(candidates[tid])},
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, default=Path("work"))
    ap.add_argument("--jigsaws-root", type=Path)
    ap.add_argument("--jigsaws-system", type=Path, default=Path("Prompts_And_Pipeline/system-prompt-jigsaws-enriched-d.md"))
    ap.add_argument("--iae-root", type=Path)
    ap.add_argument("--tables", type=Path)
    ap.add_argument("--mb140-system", type=Path, default=Path("Prompts_And_Pipeline/system-prompt-mb140-coaching-d.md"))
    ap.add_argument("--only", choices=["jigsaws", "mb140"], default=None)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    jobs = []
    if args.only in (None, "jigsaws"):
        jobs.append(("JIGSAWS", jigsaws_units))
    if args.only in (None, "mb140"):
        jobs.append(("MultiBypass140", mb140_units))
    for name, fn in jobs:
        for tid, units in fn(args).items():
            path = args.out / f"{name}_{tid}.jsonl"
            path.write_text("".join(json.dumps(u, ensure_ascii=False) + "\n" for u in units), encoding="utf-8")
            print(f"{path}: {len(units)} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
