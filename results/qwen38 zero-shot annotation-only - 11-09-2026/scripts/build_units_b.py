#!/usr/bin/env python3
"""Build zero-shot evaluation units for the July "Type B" safety-QA pipelines.

Targets (50 units each, seed 20261109):
  MultiBypass140: B1, B3, B4, B5 (July run 2026-07-29-b1345-thinking) and
                  B6 (July run 2026-07-29-b267-rule11-thinking)
  Endoscapes2023: B1, B3, B5 (b1345 run) and B2, B7 (b267 run)

The prompts are built with the July runners' own functions
(extract_system_prompt, load_template_questions, load_selection,
expected_question, build_user_prompt), imported from copies of
run_annotation_qa_b1345.py / run_annotation_qa_b267.py placed next to this
script. Nothing is sent to a model.

Zero-shot removals (everything else is exactly the July first-attempt request):
  * user prompt: the "Recent answer openings in this template batch ..." /
    "Answer openings already used in this template batch ..." block, which
    showed the model the opening sentences of its own earlier accepted answers.
    It is removed by passing an empty recent_openers list to the July
    build_user_prompt (the July code emits nothing for an empty list).
  * retry loop: July re-sampled up to --max-retries 3 times after a validator
    rejection (same prompt, no feedback text). Only the first attempt is kept;
    no validator feedback ever entered the prompt.
  * system prompt: extracted with the July extract_system_prompt unchanged; it
    has no example-answer / sample-QA / "examples" section to remove (inline
    "for example" phrases inside the hard rules are part of the rules and kept).

Selection: the July manifest selections come first (same ids, same order),
then the pool is extended with the same eligibility logic the July manifests
used, spread across centers (MB140), videos, and event categories / CVS
patterns, with random.Random(f"{seed}-{dataset}-{template}").
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import pickle
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_annotation_qa_b1345 as b1345  # noqa: E402
import run_annotation_qa_b267 as b267  # noqa: E402
import run_annotation_qa_multibypass140 as mb_mod  # noqa: E402

SEED = 20261109
TARGET = 50
MB_CENTERS = {"bern": "BernBypass70", "strasbourg": "StrasBypass70"}
B2_ENDOSCAPES_ACTION = (
    "continuing dissection toward clipping or cutting the cystic duct or artery"
)
TEMPLATES = [
    ("MultiBypass140", "B1", "b1345"),
    ("MultiBypass140", "B3", "b1345"),
    ("MultiBypass140", "B4", "b1345"),
    ("MultiBypass140", "B5", "b1345"),
    ("MultiBypass140", "B6", "b267"),
    ("Endoscapes2023", "B1", "b1345"),
    ("Endoscapes2023", "B2", "b267"),
    ("Endoscapes2023", "B3", "b1345"),
    ("Endoscapes2023", "B5", "b1345"),
    ("Endoscapes2023", "B7", "b267"),
]
ZERO_SHOT_REMOVALS = [
    "user prompt: removed the 'Recent answer openings in this template batch' "
    "(b1345) / 'Answer openings already used in this template batch' (b267) "
    "block that listed the first sentences of earlier accepted answers "
    "(recent_openers passed as [])",
    "retry loop dropped: only the first attempt is used (July re-sampled the "
    "same prompt up to 3 more times after a validator rejection; no feedback "
    "text was ever added to the prompt)",
    "system prompt: no example-answer / sample-QA section present; text kept "
    "exactly as July extract_system_prompt returns it",
]


# --------------------------------------------------------------------------
# Speed: the July find_video_record re-unpickles every fold file per call.
# Memoize the pickle loads (same return values) and patch it into the runners.
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def _load_pickle(path: str) -> dict[str, Any]:
    with open(path, "rb") as handle:
        return pickle.load(handle)


@functools.lru_cache(maxsize=None)
def cached_find_video_record(iae_root, center, split, video_id):
    splits = [split] if split else ["train", "val", "test"]
    for candidate_split in splits:
        split_dir = Path(iae_root) / "labels" / center / "labels" / candidate_split
        if not split_dir.is_dir():
            continue
        for pickle_path in sorted(split_dir.glob("*_with_iae.pickle")):
            data = _load_pickle(str(pickle_path))
            if video_id in data:
                return pickle_path, data[video_id]
    raise KeyError(f"Video {video_id!r} not found under {iae_root}/labels/{center}")


b1345.find_video_record = cached_find_video_record
b267.find_video_record = cached_find_video_record


@functools.lru_cache(maxsize=None)
def _coco(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Candidate pools
# --------------------------------------------------------------------------
def mb_videos(mb_root: Path) -> dict[str, list[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for center in MB_CENTERS:
        for split in ("train", "val", "test"):
            split_dir = mb_root / "IAE" / "labels" / center / "labels" / split
            for pickle_path in sorted(split_dir.glob("*_with_iae.pickle")):
                out[center].update(_load_pickle(str(pickle_path)).keys())
    return {center: sorted(vids) for center, vids in out.items()}


def mb_events(mb_root: Path) -> list[dict[str, Any]]:
    events = []
    for center, vids in mb_videos(mb_root).items():
        for vid in vids:
            pickle_path, frames = cached_find_video_record(
                mb_root / "IAE", center, None, vid
            )
            split = pickle_path.parent.name
            vid_events = mb_mod.extract_iae_events(frames)
            for event in vid_events:
                events.append(
                    {
                        "center": center,
                        "split": split,
                        "video_id": vid,
                        "event_id": str(event["event_id"]),
                        "category": event["category"],
                        "severity": event["severity"],
                        "n_categories_in_video": len(
                            {e["category"] for e in vid_events}
                        ),
                    }
                )
    return events


def endoscapes_keyframes(root: Path) -> list[dict[str, Any]]:
    coco_split = {}
    for split in ("train", "val", "test"):
        path = root / split / "annotation_coco.json"
        if path.exists():
            for image in _coco(str(path))["images"]:
                coco_split[image["file_name"]] = split
    rows = []
    with (root / "all_metadata.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("is_ds_keyframe") != "True":
                continue
            values = (float(row["C1"]), float(row["C2"]), float(row["C3"]))
            rows.append(
                {
                    "video_id": row["vid"],
                    "frame": int(row["frame"]),
                    "values": values,
                    "coco_split": coco_split.get(f"{row['vid']}_{row['frame']}.jpg"),
                }
            )
    return rows


def cvs_status(value: float) -> str:
    if value >= 0.99:
        return "met"
    if value <= 0.01:
        return "unmet"
    return "partial"


def cvs_outcome(values) -> str:
    if min(values) >= 0.99:
        return "all_achieved"
    if max(values) <= 0.01:
        return "none_achieved"
    return "partial"


def endoscapes_eligible(template: str, row: dict[str, Any]) -> bool:
    values = row["values"]
    deficient = [v for v in values if v < 0.99]
    if template in ("B1", "B3", "B5", "B2") and not row["coco_split"]:
        return False  # b1345 requires a COCO split; July B2 items all had one
    if template in ("B1", "B5"):
        return len(deficient) >= 1  # an unmet / incomplete CVS prerequisite
    if template == "B3":
        # "differentiated CVS profile": >=2 deficient criteria, not all equal
        return len(deficient) >= 2 and len(set(round(v, 3) for v in values)) >= 2
    return True  # B2, B7: any CVS keyframe


def endoscapes_balance_key(template: str, row: dict[str, Any]) -> str:
    values = row["values"]
    if template in ("B2", "B7"):
        return cvs_outcome(values)  # spread over yes / not yet / no states
    if template == "B3":
        return "/".join(cvs_status(v) for v in values)
    lowest = min(values)  # B1/B5: which prerequisite is most deficient
    return "+".join(
        name for name, v in zip(("C1", "C2", "C3"), values) if abs(v - lowest) < 0.01
    )


# --------------------------------------------------------------------------
# Balanced sampling
# --------------------------------------------------------------------------
def balanced_pick(
    candidates: list[dict[str, Any]],
    already: list[dict[str, Any]],
    n_needed: int,
    rng: random.Random,
    group_key,  # center (MB) or None
    balance_key,  # category / CVS pattern
    used_keys: set,
    item_key,
) -> list[dict[str, Any]]:
    """Round-robin over groups; one item per video before any video repeats;
    within a group take the least-used balance category (random tie-break)."""
    pool = [c for c in candidates if item_key(c) not in used_keys]
    rng.shuffle(pool)
    used_videos = Counter(c["video_id"] for c in already)
    bal_counts = Counter(balance_key(c) for c in already)
    groups = sorted({group_key(c) for c in pool})
    group_counts = Counter(group_key(c) for c in already)
    picks: list[dict[str, Any]] = []
    while len(picks) < n_needed and pool:
        live = [g for g in groups if any(group_key(c) == g for c in pool)]
        group = min(live, key=lambda g: (group_counts[g], rng.random()))
        in_group = [c for c in pool if group_key(c) == group]
        min_video_use = min(used_videos[c["video_id"]] for c in in_group)
        in_group = [c for c in in_group if used_videos[c["video_id"]] == min_video_use]
        cats = sorted({balance_key(c) for c in in_group})
        cat = min(cats, key=lambda k: (bal_counts[k], rng.random()))
        choice = next(c for c in in_group if balance_key(c) == cat)
        picks.append(choice)
        pool.remove(choice)
        used_videos[choice["video_id"]] += 1
        bal_counts[cat] += 1
        group_counts[group] += 1
    return picks


def build_manifest(
    dataset: str,
    template: str,
    runner: str,
    july: list[dict[str, Any]],
    mb_pool: list[dict[str, Any]] | None,
    es_pool: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(f"{SEED}-{dataset}-{template}")
    if dataset == "MultiBypass140":
        july_items = [
            it
            for it in july
            if it["template_id"] == template
            and (it["dataset"] in MB_CENTERS.values() or it["dataset"] == "MultiBypass140")
        ]
    else:
        july_items = [
            it for it in july if it["template_id"] == template and it["dataset"] == dataset
        ]
    manifest = [dict(it, _july=True) for it in july_items]
    info: dict[str, Any] = {"july_selected": len(july_items)}

    if dataset == "MultiBypass140" and template == "B3":
        video_rows = {}
        for ev in mb_pool:
            if ev["n_categories_in_video"] >= 2:
                video_rows[(ev["center"], ev["video_id"])] = {
                    "center": ev["center"],
                    "video_id": ev["video_id"],
                    "split": ev["split"],
                }
        candidates = list(video_rows.values())
        info["eligible_pool"] = len(candidates)
        info["eligibility"] = "whole video with >= 2 distinct recorded IAE categories"
        used = {(it["center"], it["video_id"]) for it in july_items}
        extra = balanced_pick(
            candidates,
            [{"video_id": it["video_id"], "center": it["center"]} for it in july_items],
            TARGET - len(manifest),
            rng,
            group_key=lambda c: c["center"],
            balance_key=lambda c: "video",
            used_keys=used,
            item_key=lambda c: (c["center"], c["video_id"]),
        )
        for c in extra:
            manifest.append(
                {
                    "dataset": MB_CENTERS[c["center"]],
                    "template_id": "B3",
                    "center": c["center"],
                    "split": c["split"],
                    "video_id": c["video_id"],
                    "scope": "video",
                    "selection_basis": "zero-shot extension: video with >= 2 distinct recorded event categories",
                }
            )
    elif dataset == "MultiBypass140":
        candidates = [ev for ev in mb_pool if ev["severity"] is not None]
        info["eligible_pool"] = len(candidates)
        info["eligibility"] = "any discrete IAE event with a severity level"
        july_keys = {(it["video_id"], str(it["event_id"])) for it in july_items}
        already = []
        for it in july_items:
            match = next(
                ev for ev in mb_pool
                if ev["video_id"] == it["video_id"] and ev["event_id"] == str(it["event_id"])
            )
            already.append(match)
        extra = balanced_pick(
            candidates,
            already,
            TARGET - len(manifest),
            rng,
            group_key=lambda c: c["center"],
            balance_key=lambda c: c["category"],
            used_keys=july_keys,
            item_key=lambda c: (c["video_id"], c["event_id"]),
        )
        for c in extra:
            item = {
                "template_id": template,
                "center": c["center"],
                "split": c["split"],
                "video_id": c["video_id"],
                "event_id": c["event_id"],
                "selection_basis": (
                    f"zero-shot extension: {c['category']} event, category-balanced "
                    "across centers and videos"
                ),
            }
            # b1345 names the center sub-dataset; the b267 B6 manifest used MultiBypass140.
            item["dataset"] = "MultiBypass140" if runner == "b267" else MB_CENTERS[c["center"]]
            manifest.append(item)
    else:
        candidates = [r for r in es_pool if endoscapes_eligible(template, r)]
        info["eligible_pool"] = len(candidates)
        info["eligibility"] = {
            "B1": "CVS keyframe in a COCO split with >= 1 criterion < 0.99",
            "B5": "CVS keyframe in a COCO split with >= 1 criterion < 0.99",
            "B3": "CVS keyframe in a COCO split with >= 2 criteria < 0.99 and not all three values equal",
            "B2": "any CVS keyframe in a COCO split",
            "B7": "any CVS keyframe (no COCO split passed, as in July B7)",
        }[template]
        july_keys = {(str(it["video_id"]), int(it["frame"])) for it in july_items}
        es_index = {(r["video_id"], r["frame"]): r for r in es_pool}
        already = [es_index[k] for k in july_keys]
        extra = balanced_pick(
            candidates,
            already,
            TARGET - len(manifest),
            rng,
            group_key=lambda c: "all",
            balance_key=lambda c: endoscapes_balance_key(template, c),
            used_keys=july_keys,
            item_key=lambda c: (c["video_id"], c["frame"]),
        )
        for c in extra:
            item = {
                "dataset": "Endoscapes2023",
                "template_id": template,
                "video_id": c["video_id"],
                "frame": c["frame"],
                "selection_basis": (
                    "zero-shot extension: CVS pattern "
                    f"{endoscapes_balance_key(template, c)}, one frame per video first"
                ),
            }
            if template != "B7":
                item["coco_split"] = c["coco_split"]
            if template == "B2":
                item["action"] = B2_ENDOSCAPES_ACTION
            manifest.append(item)

    # selection ids for new items continue the July numbering per prefix
    counters: Counter = Counter()
    for it in manifest:
        if it.get("_july"):
            prefix = it["selection_id"].rsplit("_", 1)[0]
            counters[prefix] = max(counters[prefix], int(it["selection_id"].rsplit("_", 1)[1]))
    for it in manifest:
        if "selection_id" in it:
            continue
        if dataset == "Endoscapes2023":
            prefix = f"{template.lower()}_endoscapes"
        elif runner == "b267":
            prefix = f"{template.lower()}_multibypass"
        else:
            prefix = f"{template.lower()}_{it['center']}"
        counters[prefix] += 1
        it["selection_id"] = f"{prefix}_{counters[prefix]:02d}"
    info["selected"] = len(manifest)
    if len(manifest) < TARGET:
        info["shortfall_reason"] = (
            f"only {len(manifest)} items satisfy the July eligibility rule "
            f"({info['eligibility']})"
        )
    return manifest, info


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------
MB_NOT_RECORDED = [
    "physical cause or mechanism of the adverse event",
    "the specific anatomical structure injured (only phase/step names are recorded)",
    "visual appearance of the event (bleeding volume, tissue appearance, instrument position)",
    "who or what instrument caused the event, and any corrective action taken",
    "near-miss events (only actual adverse events are labeled)",
    "clinical outcome or consequence after the event",
    "skill ratings of the surgeon",
]
ES_NOT_RECORDED = [
    "any adverse event, injury, bleeding, or near-miss record",
    "hemostasis, tissue plane, instrument position, or energy-device safety",
    "appearance, condition, or spatial position of structures (anatomy/tool boxes give class presence only)",
    "what the surgeon did or should do next (no action or step labels beyond the CVS keyframe)",
    "skill ratings of the surgeon",
]


def ground_truth(dataset, template, annotation_lines, source, runner):
    recorded = [
        line.strip()[2:] if line.strip().startswith("- ") else line.strip()
        for line in annotation_lines.splitlines()
        if line.strip()
    ]
    if dataset == "MultiBypass140":
        label = (
            "recorded adverse events for the whole video (category, severity 1-5, "
            "rectified flag, phase/step, timing)"
            if template == "B3"
            else "recorded adverse event (category, severity 1-5, rectified flag, phase/step, timing)"
        )
        src = (
            f"MultiBypass140 IAE labels: {source['iae_pickle_file']} "
            "(+ tables/phase.csv, tables/step.csv)"
        )
        not_recorded = list(MB_NOT_RECORDED)
    else:
        label = "recorded CVS criteria (3-annotator mean per criterion) at this keyframe"
        if source.get("labeled_structure_classes"):
            label += ", with anatomy/tool class presence"
        src = f"Endoscapes2023 {source['metadata_csv']}"
        if source.get("labeled_structure_classes") or (
            runner == "b1345" or template == "B2"
        ):
            split = "train/val/test"
            src += f" + COCO annotation_coco.json ({split}) for class presence"
        not_recorded = list(ES_NOT_RECORDED)
        if template == "B7":
            not_recorded.append(
                "anatomy/tool class presence was not supplied for this frame (July B7 passed no COCO split)"
            )
    # localization: N/A when the question names the time/clip; B5/B6 ask "at which timestamp"
    localization = "applicable" if template in ("B5", "B6") else "NOT APPLICABLE"
    return {
        "setting": "in-vivo procedure",
        "recorded_label": label,
        "recorded": recorded,
        "not_recorded": not_recorded,
        "localization": localization,
        "source_annotation": src,
    }


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--prompts-dir", default=str(HERE.parent / "Prompts_And_Pipeline")
    )
    parser.add_argument(
        "--multibypass-root",
        default="/mnt/sun/shared/datasets/surgical_skill/MultiBypass140",
    )
    parser.add_argument(
        "--endoscapes-root",
        default="/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023",
    )
    args = parser.parse_args()

    prompts = Path(args.prompts_dir)
    system_prompt_path = prompts / "system-prompt-A-D.md"
    questions_path = prompts / "template questions.md"
    runners = {
        "b1345": {
            "mod": b1345,
            "manifest": json.loads((prompts / "b1345-selected-clips.json").read_text()),
            "questions": b1345.load_template_questions(questions_path),
        },
        "b267": {
            "mod": b267,
            "manifest": json.loads((prompts / "b267-selected-clips.json").read_text()),
            "questions": b267.load_template_questions(questions_path),
        },
    }
    # both runners import the same extract_system_prompt from run_annotation_qa_jigsaws
    system_prompt = b1345.extract_system_prompt(system_prompt_path)
    assert system_prompt == b267.extract_system_prompt(system_prompt_path)
    load_args = SimpleNamespace(
        multibypass_root=args.multibypass_root,
        endoscapes_root=args.endoscapes_root,
        cholec_root=None,
    )

    mb_pool = mb_events(Path(args.multibypass_root))
    es_pool = endoscapes_keyframes(Path(args.endoscapes_root))
    print(f"MB140 events: {len(mb_pool)}; Endoscapes keyframes: {len(es_pool)}")

    out_dir = Path(args.out)
    (out_dir / "units").mkdir(parents=True, exist_ok=True)
    summary = {}
    for dataset, template, runner in TEMPLATES:
        r = runners[runner]
        mod = r["mod"]
        manifest, info = build_manifest(
            dataset, template, runner, r["manifest"], mb_pool, es_pool
        )
        unit_path = out_dir / "units" / f"{dataset}_{template}.jsonl"
        ids = set()
        with unit_path.open("w", encoding="utf-8") as handle:
            for item in manifest:
                july = bool(item.pop("_july", False))
                source, time_label, annotation_lines = mod.load_selection(item, load_args)
                question = mod.expected_question(item, time_label, r["questions"])
                user_prompt = mod.build_user_prompt(
                    item, time_label, annotation_lines, question, []
                )
                assert "answer openings" not in user_prompt.lower()
                unit_id = f"{'mb140' if dataset == 'MultiBypass140' else 'endoscapes'}_{template}_{item['selection_id']}"
                assert unit_id not in ids
                ids.add(unit_id)
                unit = {
                    "unit_id": unit_id,
                    "dataset": dataset,
                    "template_id": template,
                    "question": question,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "answer_format": "json_qa",
                    "answer_key": "0.answer",
                    "ground_truth": ground_truth(
                        dataset, template, annotation_lines, source, runner
                    ),
                    "meta": {
                        "selection_id": item["selection_id"],
                        "manifest_item": item,
                        "in_july_manifest": july,
                        "video_id": item["video_id"],
                        "center": item.get("center"),
                        "timestamp_or_span": time_label,
                        "source_annotation_detail": source,
                        "july_runner": f"scripts/run_annotation_qa_{runner}.py",
                        "july_run_dir": (
                            "/mnt/opr/mairuili/surgcoach_runs/2026-07-29-b1345-thinking"
                            if runner == "b1345"
                            else "/mnt/opr/mairuili/surgcoach_runs/2026-07-29-b267-rule11-thinking"
                        ),
                        "answer_shape": (
                            "reply is a JSON array with one object "
                            "{question, answer, rationale}; the answer is element 0's 'answer'"
                        ),
                        "july_generation_params": {
                            "temperature": 0.7,
                            "max_new_tokens": 7000 if runner == "b1345" else 6000,
                            "thinking": "enabled (chat_template_kwargs.enable_thinking)",
                        },
                        "zero_shot_removals": ZERO_SHOT_REMOVALS,
                        "sampling": {"seed": SEED, "target": TARGET, **info},
                    },
                }
                handle.write(json.dumps(unit, ensure_ascii=False) + "\n")
        summary[f"{dataset}_{template}"] = info
        print(f"{unit_path.name}: {info['selected']} units ({info})")
    (out_dir / "units" / "build_units_b_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
