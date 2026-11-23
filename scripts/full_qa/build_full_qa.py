#!/usr/bin/env python3
"""Every eligible question of the zero-shot video test, with each yes/no type balanced.

The 11-17 zero-shot video test asked 50 questions per type, or every eligible question where
fewer qualify. This builds every question that qualifies, with the 11-09 builders' own rules,
wording and ground truth and the 11-17 video windows. So the questions asked on 11-17 come out
unchanged. Nothing is sent to a model.

Balance: every yes/no type has as many "yes" as "no" correct answers.
  Endoscapes B7    every safe keyframe and as many unsafe ones. Safe means all three CVS
                   criteria achieved, a criterion counting as achieved when at least 2 of
                   its 3 raters marked it (mean >= 0.5), which is how the July runner words
                   it. The unsafe ones are the first in the 11-09 sampler's order: one frame
                   per video before any video repeats, rotating over the CVS outcomes.
  Endoscapes B2,   left out. Their correct answer is "No" for every item: B2's safety factors
  MB140 B4         (bleeding control, energy use) are not labelled, and MultiBypass140 labels
                   no near misses. Rewording them is Yulu's call.

Steps, each in its own process because the 11-09 builders' helper modules clash by name:
  b          MultiBypass140 B1 B3 B5 B6, Endoscapes B1 B3 B5 B7   (build_units_b.py rules)
  mb140-d    MultiBypass140 D1 D2 D5: every candidate, not one per surgery (build_units_d.py)
  jigsaws-d  JIGSAWS D1 D2 D5                                       (build_units_d.py)
  vts        JIGSAWS VTS: all 628 reviewed locate questions         (build_units_vts.py)
  finish     adds the 11-17 video window to every question, checks the 11-17 questions
             against their 11-17 units, writes <out>/qa/<dataset>_<type>.jsonl and
             <out>/summary.json

Run on oprime from a run folder holding the 11-09 scripts/ and Prompts_And_Pipeline/;
run_full_qa.sh runs all five steps.
"""
from __future__ import annotations

import argparse
import collections
import csv
import functools
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

DATA = Path("/mnt/sun/shared/datasets/surgical_skill")
LEFT_OUT = {("Endoscapes2023", "B2"), ("MultiBypass140", "B4")}
ALL = 10**9  # "take every eligible item" for the builders' sample sizes
SAFE_RULE = ("Yes when all three CVS criteria are achieved, a criterion counting as achieved "
             "when at least 2 of its 3 raters marked it; otherwise No")


def write_units(path: Path, units: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for u in units:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")
    print(f"{path}: {len(units)}", flush=True)


def read_units(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def cvs_safe(values) -> bool:
    return all(v >= 0.5 for v in values)


# --------------------------------------------------------------------------- step b
def speed_up_endoscapes_loaders(mod) -> None:
    """The July loaders re-read all_metadata.csv and annotation_coco.json on every call.
    These return the same values from one read of each file."""
    orig_classes = mod.load_endoscapes_classes

    @functools.lru_cache(maxsize=None)
    def meta_index(path: str) -> dict:
        index: dict = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                index.setdefault((row["vid"], int(row["frame"])), row)  # first match, as July
        return index

    def load_endoscapes_metadata(metadata_csv, video_id, frame):
        row = meta_index(str(metadata_csv)).get((video_id, frame))
        if row is None:
            raise ValueError(f"Endoscapes keyframe {video_id}_{frame} not found")
        if row.get("is_ds_keyframe") != "True":
            raise ValueError(f"Endoscapes selection {video_id}_{frame} is not a CVS keyframe")
        return {"C1": float(row["C1"]), "C2": float(row["C2"]), "C3": float(row["C3"]),
                "annotator_1": row.get("cvs_annotator_1"),
                "annotator_2": row.get("cvs_annotator_2"),
                "annotator_3": row.get("cvs_annotator_3")}

    @functools.lru_cache(maxsize=None)
    def coco_index(path: str):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        names = {int(c["id"]): c["name"] for c in payload["categories"]}
        images: dict = {}
        for im in payload["images"]:
            images.setdefault(im["file_name"], im)
        classes: dict = collections.defaultdict(set)
        for a in payload["annotations"]:
            classes[a["image_id"]].add(names[int(a["category_id"])])
        return images, classes

    def load_endoscapes_classes(root, split, video_id, frame):
        if not split:
            return orig_classes(root, split, video_id, frame)
        coco_path = Path(root) / split / "annotation_coco.json"
        images, classes = coco_index(str(coco_path))
        filename = f"{video_id}_{frame}.jpg"
        image = images.get(filename)
        if image is None:
            raise ValueError(f"{filename} not found in {coco_path}")
        return sorted(classes.get(image["id"], set()))

    mod.load_endoscapes_metadata = load_endoscapes_metadata
    mod.load_endoscapes_classes = load_endoscapes_classes


def step_b(args) -> None:
    sys.path.insert(0, str(args.scripts))
    import build_units_b as B  # noqa: PLC0415

    speed_up_endoscapes_loaders(B.b1345)
    speed_up_endoscapes_loaders(B.b267)
    B.TARGET = ALL  # balanced_pick then runs to the end of the pool, in the 11-09 order
    qpath = args.prompts / "template questions.md"
    runners = {name: {"mod": mod,
                      "manifest": json.loads((args.prompts / f"{name}-selected-clips.json").read_text()),
                      "questions": mod.load_template_questions(qpath)}
               for name, mod in (("b1345", B.b1345), ("b267", B.b267))}
    load_args = SimpleNamespace(multibypass_root=str(args.mb140_root),
                                endoscapes_root=str(args.endoscapes_root), cholec_root=None)
    mb_pool = B.mb_events(args.mb140_root)
    es_pool = B.endoscapes_keyframes(args.endoscapes_root)
    values = {(r["video_id"], r["frame"]): r["values"] for r in es_pool}
    print(f"MB140 events: {len(mb_pool)}; Endoscapes keyframes: {len(es_pool)}", flush=True)

    summary = {}
    for dataset, template, runner in B.TEMPLATES:
        if (dataset, template) in LEFT_OUT:
            continue
        r = runners[runner]
        manifest, info = B.build_manifest(dataset, template, runner, r["manifest"], mb_pool, es_pool)
        info.pop("shortfall_reason", None)
        info["in_pool_order"] = len(manifest)
        if template == "B7":
            def safe(it):
                return cvs_safe(values[(str(it["video_id"]), int(it["frame"]))])
            yes = [it for it in manifest if safe(it)]
            no = [it for it in manifest if not safe(it)][: len(yes)]
            keep = {id(it) for it in yes + no}
            manifest = [it for it in manifest if id(it) in keep]
            info["balance"] = {"yes": len(yes), "no": len(no), "rule": SAFE_RULE}
        units = []
        for item in manifest:
            july = bool(item.pop("_july", False))
            source, time_label, lines = r["mod"].load_selection(item, load_args)
            question = r["mod"].expected_question(item, time_label, r["questions"])
            prefix = "mb140" if dataset == "MultiBypass140" else "endoscapes"
            unit = {
                "unit_id": f"{prefix}_{template}_{item['selection_id']}",
                "dataset": dataset, "template_id": template, "question": question,
                "ground_truth": B.ground_truth(dataset, template, lines, source, runner),
                "meta": {"selection_id": item["selection_id"], "manifest_item": item,
                         "in_july_manifest": july, "video_id": item["video_id"],
                         "center": item.get("center"), "timestamp_or_span": time_label,
                         "source_annotation_detail": source},
            }
            if template == "B7":
                unit["correct_answer"] = "Yes" if cvs_safe(
                    (source["C1"], source["C2"], source["C3"])) else "No"
            units.append(unit)
        assert len({u["unit_id"] for u in units}) == len(units)
        info["selected"] = len(units)
        summary[f"{dataset}_{template}"] = info
        write_units(args.stage / f"{dataset}_{template}.jsonl", units)
    (args.stage / "summary_b.json").write_text(json.dumps(summary, indent=2) + "\n")


# --------------------------------------------------------------------------- step mb140-d
def step_mb140_d(args) -> None:
    sys.path.insert(0, str(args.scripts))
    import build_units_d as D  # noqa: PLC0415
    import run_mb140_coaching_d as M  # noqa: PLC0415

    phases, steps = M.load_names(args.mb140_root / "tables")
    videos = M.load_videos(args.mb140_root / "IAE" / "labels")
    candidates = M.build_candidates(videos, phases, steps)
    summary = {}
    for tid in ("D1", "D2", "D5"):
        units = []
        for cand in candidates[tid]:  # every candidate; 11-09 took one question per surgery
            u = {**cand, "template_id": tid}
            question, prompt_text = M.build_prompt(u, phases)
            u["question"], u["prompt_text"] = question, prompt_text
            ev = M.evidence(u)
            # as build_units_d.mb140_units writes it
            units.append({
                "unit_id": ev["qa_id"].replace("_coaching", ""),
                "dataset": "MultiBypass140", "template_id": tid, "question": question,
                "ground_truth": {
                    "setting": "in-vivo procedure",
                    "recorded_label": "recorded adverse events, each with its time, step, phase and the "
                                      "published description of its SEVERE grade"
                                      + (", and the series count given" if tid == "D5" else ""),
                    "recorded": D.strip_sections(prompt_text),
                    "not_recorded": ("the cause of any event; who or which instrument caused it; anatomy "
                                     "beyond the step name; patient outcome; the numeric grade (only its "
                                     "description is used); any advice or technique"),
                    "localization": "NOT APPLICABLE" if tid != "D2" else "applicable",
                    "source_annotation": "MultiBypass140 intraoperative adverse event labels "
                                         "(multibypass06_corrected, CAMMA 2025-09-02), phase and step labels",
                },
                "meta": {"video_id": u["video_id"], "center": u["center"], "phase": u["phase"],
                         "start_s": u["start_s"], "end_s": u["end_s"],
                         "candidates_in_pool": len(candidates[tid])},
            })
        assert len({u["unit_id"] for u in units}) == len(units), tid
        summary[f"MultiBypass140_{tid}"] = {"eligible_pool": len(candidates[tid]), "selected": len(units)}
        write_units(args.stage / f"MultiBypass140_{tid}.jsonl", units)
    (args.stage / "summary_mb140_d.json").write_text(json.dumps(summary, indent=2) + "\n")


# --------------------------------------------------------------------------- step jigsaws-d
def step_jigsaws_d(args) -> None:
    sys.path.insert(0, str(args.scripts))
    import build_units_d as D  # noqa: PLC0415

    D.N = ALL  # spread_sample then takes every eligible trial, in the 11-09 order
    ns = SimpleNamespace(jigsaws_root=args.jigsaws_root, work=args.work_1109,
                         jigsaws_system=args.prompts / "system-prompt-jigsaws-enriched-d.md")
    summary = {}
    for tid, units in D.jigsaws_units(ns).items():
        for u in units:
            for k in ("system_prompt", "user_prompt", "answer_format", "answer_key", "generation"):
                u.pop(k, None)
        summary[f"JIGSAWS_{tid}"] = {"eligible_pool": units[0]["meta"]["eligible_in_pool"] if units else 0,
                                     "selected": len(units)}
        write_units(args.stage / f"JIGSAWS_{tid}.jsonl", units)
    (args.stage / "summary_jigsaws_d.json").write_text(json.dumps(summary, indent=2) + "\n")


# --------------------------------------------------------------------------- step vts
def step_vts(args) -> None:
    tmp = args.stage / "vts_build"
    tmp.mkdir(parents=True, exist_ok=True)
    # One of the 628 has a blank question in the review file, though its batch record holds
    # the question its answer was written for. The builder stops when the two disagree, so
    # blanks are filled from the batch record (never a non-blank one).
    review_path = args.prompts / "jigsaws_vts_locate_628_for_review.json"
    records_path = args.prompts / "jigsaws_vts_locate_628_qa_records.jsonl"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    asked = {r["qa_id"]: r["question"] for r in read_units(records_path)}
    filled = []
    for it in review["items"]:
        if not it["question"].strip() and asked.get(it["id"], "").strip():
            it["question"] = asked[it["id"]]
            filled.append(it["id"])
    patched = tmp / review_path.name
    patched.write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"blank review questions filled from the batch records: {filled}", flush=True)
    # as on 11-09: not isolated, so the builder finds its own modules, with the lab's numpy
    # (the "worst delay" questions read kinematics)
    cmd = [sys.executable, str(args.scripts / "build_units_vts.py"), "--out", str(tmp),
           "-n", str(ALL), "--jigsaws-root", str(args.jigsaws_root), "--review", str(patched),
           "--qa-records", str(records_path)]
    subprocess.run(cmd, check=True, env={**os.environ, "PYTHONPATH": str(args.numpy_path)})
    units = read_units(tmp / "units" / "JIGSAWS_VTS.jsonl")
    for u in units:
        for k in ("system_prompt", "user_prompt", "answer_format", "answer_key"):
            u.pop(k, None)
    assert len({u["unit_id"] for u in units}) == len(units)
    write_units(args.stage / "JIGSAWS_VTS.jsonl", units)
    (args.stage / "summary_vts.json").write_text(
        json.dumps({"JIGSAWS_VTS": {"eligible_pool": len(units), "selected": len(units),
                                    "question_filled_from_batch_record": filled}}, indent=2) + "\n")


# --------------------------------------------------------------------------- step finish
def step_finish(args) -> None:
    sys.path.insert(0, str(args.zs_video))
    import prep_video_units as P  # noqa: PLC0415

    P.video_duration = functools.lru_cache(maxsize=None)(P.video_duration)
    args.out.mkdir(parents=True, exist_ok=True)
    index = args.out / "video_index.json"
    if not index.exists() and (args.run_1117 / "video_index.json").exists():
        shutil.copy(args.run_1117 / "video_index.json", index)
    src = P.Sources(SimpleNamespace(jigsaws_root=args.jigsaws_root, mb140_root=args.mb140_root,
                                    endoscapes_root=args.endoscapes_root, out=args.out))

    asked = {}  # the 11-17 video units: what the models were given
    for f in sorted((args.run_1117 / "video_units").glob("*.jsonl")):
        for vu in read_units(f):
            asked[vu["unit_id"]] = vu

    problems, seen, summary = [], set(), {}
    for f in sorted(args.stage.glob("*.jsonl")):
        records = []
        for u in read_units(f):
            seen.add(u["unit_id"])
            w = P.window_for(u, src, random.Random(f"{P.SEED}-{u['unit_id']}"))
            old = asked.get(u["unit_id"])
            if old is not None:
                if old["question"] != u["question"]:
                    problems.append(f"{u['unit_id']}: question differs from 11-17")
                if old["ground_truth"] != u["ground_truth"]:
                    problems.append(f"{u['unit_id']}: ground truth differs from 11-17")
                if (abs(old["window"]["start_s"] - w["start_s"]) > 1e-6
                        or abs(old["window"]["end_s"] - w["end_s"]) > 1e-6):
                    problems.append(f"{u['unit_id']}: window differs from 11-17")
            records.append(public_record(u, w, P, old is not None))
        out = args.out / "qa" / f.name
        write_units(out, records)
        answers = collections.Counter(r["correct_answer"] for r in records if "correct_answer" in r)
        summary[f.stem] = {"questions": len(records),
                           "asked_on_11_17": sum(r["in_11_17_run"] for r in records),
                           **({"correct_answers": dict(answers)} if answers else {})}
    missing = sorted(uid for uid, vu in asked.items()
                     if (vu["dataset"], vu["template_id"]) not in LEFT_OUT and uid not in seen)
    problems += [f"{uid}: asked on 11-17 but not in the full set" for uid in missing]
    for name in ("summary_b.json", "summary_mb140_d.json", "summary_jigsaws_d.json", "summary_vts.json"):
        p = args.stage / name
        if p.exists():
            for k, v in json.loads(p.read_text()).items():
                summary.setdefault(k, {})["pool"] = v
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "pool"} for k, v in summary.items()},
                     indent=1))
    if problems:
        print(f"\n{len(problems)} PROBLEMS:\n" + "\n".join(problems[:50]))
        raise SystemExit(1)
    print("\nall questions asked on 11-17 are in the full set, with the same question, "
          "ground truth and window")


def public_record(u: dict, w: dict, P, asked: bool) -> dict:
    meta = u.get("meta", {})
    if u["dataset"] == "JIGSAWS":
        vid = meta["trial_id"]
    elif u["dataset"] == "Endoscapes2023":
        vid = str(meta["video_id"])
    else:
        vid = meta["video_id"]
    video = {"id": vid, "start_s": round(w["start_s"], 1), "end_s": round(w["end_s"], 1),
             "window": f"{P.clock(w['start_s'], w['hours'])}-{P.clock(w['end_s'], w['hours'])}",
             "rule": w["rule"]}
    if "keyframe" in w:
        video["keyframe"] = w["keyframe"]
    gt = dict(u["ground_truth"])
    gt["source_annotation"] = gt["source_annotation"].replace(f"{DATA}/", "")
    rec = {"unit_id": u["unit_id"], "dataset": u["dataset"], "template_id": u["template_id"],
           "question": u["question"], "video": video}
    if "correct_answer" in u:
        rec["correct_answer"] = u["correct_answer"]
    rec["ground_truth"] = gt
    rec["in_11_17_run"] = asked
    return rec


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["b", "mb140-d", "jigsaws-d", "vts", "finish"])
    ap.add_argument("--scripts", type=Path, default=Path("scripts"), help="the 11-09 builders")
    ap.add_argument("--prompts", type=Path, default=Path("Prompts_And_Pipeline"))
    ap.add_argument("--zs-video", type=Path, default=Path("scripts/zs_video"))
    ap.add_argument("--stage", type=Path, default=Path("stage"))
    ap.add_argument("--out", type=Path, default=Path("full_qa"))
    ap.add_argument("--work-1109", type=Path,
                    default=Path("/mnt/opr/mairuili/surgcoach_runs/2026-11-09-qwen38-zero-shot/work"))
    ap.add_argument("--run-1117", type=Path,
                    default=Path("/mnt/opr/mairuili/surgcoach_runs/2026-11-17-zs-video"))
    ap.add_argument("--jigsaws-root", type=Path, default=DATA / "JIGSAWS")
    ap.add_argument("--mb140-root", type=Path, default=DATA / "MultiBypass140")
    ap.add_argument("--endoscapes-root", type=Path, default=DATA / "Endoscapes2023")
    ap.add_argument("--numpy-path", type=Path, default=DATA / ".python/qwen36/site-packages")
    args = ap.parse_args()
    args.stage.mkdir(parents=True, exist_ok=True)
    {"b": step_b, "mb140-d": step_mb140_d, "jigsaws-d": step_jigsaws_d, "vts": step_vts,
     "finish": step_finish}[args.step](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
