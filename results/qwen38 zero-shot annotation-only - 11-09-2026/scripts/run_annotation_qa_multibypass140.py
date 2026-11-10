#!/usr/bin/env python3
"""Annotation-only QA generation for MultiBypass140, Template B (adverse-event coaching).

This runner never reads video or extracts frames. It reads only the per-frame
Intraoperative Adverse Event (IAE) labels already annotated by surgeons: an
event category, a 1-5 severity level, and phase/step context. There are no
canned or label-templated answers anywhere in this script; the only
label-derived values passed to the model are the annotations themselves.

Of the six Type-B templates, only B6 (cause_of_error) is answerable from these
labels without fabricating visual detail -- B1-B5 require anatomical
structure, tissue plane, or a described visible event that MultiBypass140's
IAE labels do not contain, and B3/B4's risk taxonomies do not match this
dataset's 5 IAE categories. B1-B5 are refused with a reason, never generated.

Every output record carries provenance: backend ("real" or "mock"),
real_model_inference, model_id, the exact prompt, and the raw model output.
Mock records are additionally watermarked and written with
validation_status "valid_mock" so they can never be confused with real data.
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_jigsaws import (  # noqa: E402
    MOCK_WATERMARK,
    extract_system_prompt,
    load_text_model,
    log,
    normalize_qa_objects,
    parse_json_payload,
    run_generation,
    run_generation_ollama,
    verify_ollama_model,
)

IAE_CATEGORIES = [
    "Bleeding",
    "Mechanical injury",
    "Thermal injury",
    "Ischemic injury",
    "Insufficient closure of anastomosis",
]

SUPPORTED_TEMPLATES = {
    "B6": "recorded IAE category, severity, and phase/step context",
}
UNSUPPORTED_TEMPLATES = {
    "B1": "MultiBypass140 has no anatomical-structure or visible-evidence labels",
    "B2": "MultiBypass140 has no per-action/instrument/tissue-plane labels to judge safety-to-proceed",
    "B3": "MultiBypass140's 5 IAE categories do not match the requested risk taxonomy",
    "B4": "IAE labels record events that occurred, not near-misses, and the taxonomy also does not match",
    "B5": "requires a described visible event and at-risk anatomical structure not present in the labels",
}

B6_QUESTION = "At which timestamp should a supervisor pause the trainee for coaching, and why?"


def find_video_record(iae_root: Path, center: str, split: str | None, video_id: str) -> tuple[Path, list[dict[str, Any]]]:
    splits = [split] if split else ["train", "val", "test"]
    for candidate_split in splits:
        split_dir = iae_root / "labels" / center / "labels" / candidate_split
        if not split_dir.is_dir():
            continue
        for pickle_path in sorted(split_dir.glob("*_with_iae.pickle")):
            with pickle_path.open("rb") as handle:
                data = pickle.load(handle)
            if video_id in data:
                return pickle_path, data[video_id]
    raise KeyError(f"Video {video_id!r} not found under {iae_root}/labels/{center}/labels/{{{','.join(splits)}}}")


def active_severity(frame: dict[str, Any], category: str) -> int | None:
    for level in range(1, 6):
        if frame.get(f"{category} - {level}") == 1:
            return level
    return None


def extract_iae_events(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group frames sharing an Event_ID into discrete adverse-event spans.

    MultiBypass140's IAE labels are per-frame (1fps); Event_ID links frames
    belonging to the same real-world event so we generate one QA item per
    event, not one per second of footage.
    """
    events: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for frame in frames:
        if not frame.get("Overall"):
            continue
        event_ids = frame.get("Event_ID") or []
        if not event_ids:
            continue
        category = next((c for c in IAE_CATEGORIES if frame.get(c) == 1), None)
        if category is None:
            continue
        severity = active_severity(frame, category)
        rectified = bool(frame.get(f"{category} - rectified"))
        for event_id in event_ids:
            if event_id not in events:
                events[event_id] = {
                    "event_id": event_id,
                    "category": category,
                    "severity": severity,
                    "rectified": rectified,
                    "start_frame": frame["Original_frame_id"],
                    "end_frame": frame["Original_frame_id"],
                    "phase_id": frame.get("Phase_gt"),
                    "step_id": frame.get("Step_gt"),
                }
                order.append(event_id)
            else:
                existing = events[event_id]
                existing["end_frame"] = frame["Original_frame_id"]
                if severity is not None:
                    existing["severity"] = severity if existing["severity"] is None else max(existing["severity"], severity)
                existing["rectified"] = existing["rectified"] or rectified
    return [events[event_id] for event_id in order]


def build_user_prompt(template_id: str, video_id: str, event: dict[str, Any]) -> str:
    frame_label = f"{event['start_frame']}-{event['end_frame']}"
    severity_text = str(event["severity"]) if event["severity"] else "not specified in the annotation"
    lines = [
        "Dataset-specific input (ANNOTATION-ONLY: no frames or images are attached):",
        "- dataset_name: MultiBypass140",
        "- procedure_or_task: Laparoscopic Roux-en-Y Gastric Bypass",
        f"- video_id: {video_id}",
        f"- clip_id: {video_id}_{frame_label}",
        f"- timestamp_or_frame: frames {frame_label}",
        f"- requested_template_ids: {template_id}",
        "- number_of_questions: 1",
        "- available_annotations:",
        f"  - iae_category: {event['category']}",
        f"  - iae_severity: {severity_text} (scale 1=mild to 5=severe; range differs slightly by category)",
        f"  - iae_rectified: {event['rectified']}",
        f"  - phase_id: {event['phase_id']}",
        f"  - step_id: {event['step_id']}",
        f"  - event_frame_range: {frame_label}",
        "",
        "Ground the QA pair only in these annotations. Do not describe any visual",
        "content; no visual content was provided.",
        "",
        "Use this exact question text:",
        B6_QUESTION,
        "",
        "Return valid JSON only, following the system prompt schema.",
    ]
    return "\n".join(lines)


def mock_raw_output(template_id: str, event: dict[str, Any]) -> str:
    return json.dumps(
        [
            {
                "question": B6_QUESTION,
                "answer": f"{MOCK_WATERMARK} placeholder answer for {template_id}",
                "rationale": f"{MOCK_WATERMARK} placeholder rationale for {template_id}",
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iae-root",
        default="/mnt/sun/shared/datasets/surgical_skill/MultiBypass140/IAE",
    )
    parser.add_argument("--system-prompt", required=True, help="Path to annotation-only system-prompt-A-D.md")
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--center", choices=["bern", "strasbourg"], default="bern")
    parser.add_argument("--split", choices=["train", "val", "test"], default=None, help="Default: search train/val/test")
    parser.add_argument("--video-id", default="BBP20")
    parser.add_argument("--templates", default="B6")
    parser.add_argument("--max-new-tokens", type=int, default=6000)
    parser.add_argument("--temperature", type=float, default=0.0,
                         help="0.0 (default) is greedy/deterministic. >0 enables sampling, trading "
                              "reproducibility for lexical variety across records that share the same "
                              "underlying scores -- recommended for real dataset-building runs.")
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--backend", choices=["real", "mock"], default="real",
                        help="mock builds watermarked placeholder records for pipeline smoke tests only")
    parser.add_argument("--inference-engine", choices=["transformers", "ollama"], default="transformers")
    parser.add_argument("--ollama-model", default="qwen3.6-35b-a3b")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument("--ollama-num-ctx", type=int, default=8192)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    requested = [item.strip() for item in args.templates.split(",") if item.strip()]
    unsupported = [item for item in requested if item in UNSUPPORTED_TEMPLATES]
    unknown = [item for item in requested if item not in SUPPORTED_TEMPLATES and item not in UNSUPPORTED_TEMPLATES]
    if unknown:
        parser.error(f"Unknown template IDs: {unknown}")
    if unsupported:
        reasons = {item: UNSUPPORTED_TEMPLATES[item] for item in unsupported}
        parser.error(
            "These templates cannot be generated annotation-only from MultiBypass140: "
            f"{json.dumps(reasons)}"
        )
    templates = [item for item in requested if item in SUPPORTED_TEMPLATES]

    iae_root = Path(args.iae_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    system_prompt_path = Path(args.system_prompt)
    system_prompt = extract_system_prompt(system_prompt_path)

    pickle_path, frames = find_video_record(iae_root, args.center, args.split, args.video_id)
    log(f"loaded {args.video_id} from {pickle_path} ({len(frames)} frames)")
    events = extract_iae_events(frames)
    if not events:
        raise SystemExit(f"No IAE events found for {args.video_id} in {pickle_path}")
    log(f"found {len(events)} adverse-event span(s)")

    tokenizer = model = None
    if args.backend == "real":
        if args.inference_engine == "ollama":
            verify_ollama_model(args.ollama_host, args.ollama_model)
        else:
            tokenizer, model = load_text_model(args.model_id, args.device_map)

    output_name = "qa_records.jsonl" if args.backend == "real" else "qa_records.mock.jsonl"
    output_path = output_dir / output_name
    valid = rejected = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for event in events:
            for template_id in templates:
                user_prompt = build_user_prompt(template_id, args.video_id, event)
                if args.backend == "mock":
                    raw_output = mock_raw_output(template_id, event)
                elif args.inference_engine == "ollama":
                    raw_output = run_generation_ollama(
                        args.ollama_host, args.ollama_model, system_prompt, user_prompt,
                        args.max_new_tokens, args.ollama_num_ctx, args.temperature,
                    )
                else:
                    raw_output = run_generation(
                        tokenizer, model, system_prompt, user_prompt, args.max_new_tokens, args.temperature
                    )

                qa_objects = None
                error = None
                try:
                    qa_objects = normalize_qa_objects(parse_json_payload(raw_output))
                    if qa_objects is None:
                        error = "schema_mismatch: expected objects with exactly question/answer/rationale"
                except Exception as exc:  # noqa: BLE001
                    error = repr(exc)

                if error is None and qa_objects[0]["question"] != B6_QUESTION:
                    error = "question_mismatch: B6 question must match template wording exactly"

                if error is None:
                    status = "valid" if args.backend == "real" else "valid_mock"
                    valid += 1
                else:
                    status = "rejected"
                    rejected += 1

                frame_label = f"{event['start_frame']}-{event['end_frame']}"
                record = {
                    "qa_id": f"multibypass140_{template_id.lower()}_{args.video_id}_{frame_label.replace('-', '_')}_anno",
                    "dataset": "MultiBypass140",
                    "procedure_or_task": "Laparoscopic Roux-en-Y Gastric Bypass",
                    "generation_mode": "annotation_only",
                    "template_id": template_id,
                    "template_support": SUPPORTED_TEMPLATES[template_id],
                    "video_id": args.video_id,
                    "timestamp_or_frame": frame_label,
                    "source_annotation": {
                        "iae_pickle_file": str(pickle_path),
                        "center": args.center,
                        "event_id": event["event_id"],
                        "iae_category": event["category"],
                        "iae_severity": event["severity"],
                        "iae_rectified": event["rectified"],
                        "phase_id": event["phase_id"],
                        "step_id": event["step_id"],
                        "event_frame_range": frame_label,
                    },
                    "model": {
                        "backend": args.backend,
                        "real_model_inference": args.backend == "real",
                        "inference_engine": args.inference_engine if args.backend == "real" else None,
                        "model_id": (
                            (args.ollama_model if args.inference_engine == "ollama" else args.model_id)
                            if args.backend == "real"
                            else None
                        ),
                        "system_prompt_path": str(system_prompt_path),
                        "prompt": user_prompt,
                        "raw_output": raw_output,
                    },
                    "qa": qa_objects,
                    "validation_status": status,
                    "validation_error": error,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                log(f"{record['qa_id']}: {status}" + (f" ({error})" if error else ""))

    summary = {
        "backend": args.backend,
        "video_id": args.video_id,
        "templates": templates,
        "events": len(events),
        "valid": valid,
        "rejected": rejected,
        "output": str(output_path),
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
