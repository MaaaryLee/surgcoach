#!/usr/bin/env python3
"""Annotation-only QA generation for Endoscapes2023, Template B (CVS safety check).

This runner never reads video or extracts frames. It reads only the per-frame
Critical View of Safety (CVS) criterion scores already annotated by 3 expert
surgeons: C1 (structures clearly identified), C2 (hepatocystic triangle
cleared), C3 (gallbladder separated from liver), each averaged across
annotators on a 0-1 scale. There are no canned or label-templated answers
anywhere in this script; the only label-derived values passed to the model
are the annotations themselves.

Of the Type-B templates, only B7 (cvs_safety_check, a new template scoped to
what this dataset actually labels) is answerable without fabricating detail.
B2 as originally worded asks for tissue plane, hemostasis, instrument
position, and energy safety, none of which are labeled here -- reusing that
wording would mean fabricating most of the answer. B1, B3, B4, B5, and B6 are
refused for the same reason: no injury-severity scale, no injury-mechanism
labels, and no discrete adverse-event records exist in this dataset.
"""

from __future__ import annotations

import argparse
import csv
import json
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

SUPPORTED_TEMPLATES = {
    "B7": "recorded CVS criterion scores (C1, C2, C3)",
}
UNSUPPORTED_TEMPLATES = {
    "B1": "no injury-severity scale or visible-evidence labels",
    "B2": "Endoscapes2023's CVS scores do not cover this template's itemized categories (tissue plane, hemostasis, instrument position, energy safety)",
    "B3": "no injury-severity scale to rank, and the risk taxonomy does not match CVS criteria",
    "B4": "no discrete adverse-event or near-miss records are provided",
    "B5": "requires a described visible event and at-risk anatomical structure not present in the labels",
    "B6": "no discrete adverse-event record with severity is provided",
}

B7_QUESTION_TEMPLATE = "Based on the frame at [t], is it medically safe to proceed with clipping or cutting the cystic duct or artery?"


def load_cvs_keyframes(metadata_csv: Path, video_id: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    with metadata_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["vid"] != video_id:
                continue
            if row.get("is_ds_keyframe") != "True":
                continue
            frames.append(
                {
                    "frame": int(row["frame"]),
                    "C1": float(row["C1"]) if row["C1"] else None,
                    "C2": float(row["C2"]) if row["C2"] else None,
                    "C3": float(row["C3"]) if row["C3"] else None,
                }
            )
    frames.sort(key=lambda item: item["frame"])
    return frames


def cvs_criterion_text(value: float | None) -> str:
    # Averaged across exactly 3 annotators, so the only possible values are
    # 0, 1/3, 2/3, and 1 -- a 0.5 majority-vote split is unambiguous and
    # immune to float rounding (unlike a near-2/3 threshold, which 2/3 itself
    # can fail due to floating-point representation).
    if value is None:
        return "not scored"
    if value >= 0.5:
        qualifier = "unanimous" if value >= 0.99 else "majority, not unanimous"
        return f"{value:.2f} (achieved - {qualifier})"
    qualifier = "unanimous" if value <= 0.01 else "majority, not unanimous"
    return f"{value:.2f} (not achieved - {qualifier})"


def build_user_prompt(template_id: str, video_id: str, keyframe: dict[str, Any]) -> str:
    frame_label = str(keyframe["frame"])
    question = B7_QUESTION_TEMPLATE.replace("[t]", frame_label)
    lines = [
        "Dataset-specific input (ANNOTATION-ONLY: no frames or images are attached):",
        "- dataset_name: Endoscapes2023",
        "- procedure_or_task: Laparoscopic Cholecystectomy (Critical View of Safety assessment)",
        f"- video_id: {video_id}",
        f"- clip_id: {video_id}_{frame_label}",
        f"- timestamp_or_frame: frame {frame_label}",
        f"- requested_template_ids: {template_id}",
        "- number_of_questions: 1",
        "- available_annotations:",
        f"  - cvs_c1_structures_identified: {cvs_criterion_text(keyframe['C1'])}",
        f"  - cvs_c2_hepatocystic_triangle_cleared: {cvs_criterion_text(keyframe['C2'])}",
        f"  - cvs_c3_gallbladder_separated: {cvs_criterion_text(keyframe['C3'])}",
        "",
        "Ground the QA pair only in these annotations. Do not describe any visual",
        "content; no visual content was provided.",
        "",
        "Use this exact question text:",
        question,
        "",
        "Return valid JSON only, following the system prompt schema.",
    ]
    return "\n".join(lines)


def expected_question(video_id: str, keyframe: dict[str, Any]) -> str:
    return B7_QUESTION_TEMPLATE.replace("[t]", str(keyframe["frame"]))


def mock_raw_output(template_id: str, video_id: str, keyframe: dict[str, Any]) -> str:
    return json.dumps(
        [
            {
                "question": expected_question(video_id, keyframe),
                "answer": f"{MOCK_WATERMARK} placeholder answer for {template_id}",
                "rationale": f"{MOCK_WATERMARK} placeholder rationale for {template_id}",
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata-csv",
        default="/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023/all_metadata.csv",
    )
    parser.add_argument("--system-prompt", required=True, help="Path to annotation-only system-prompt-A-D.md")
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--video-id", default="1", help="Value of the 'vid' column in all_metadata.csv")
    parser.add_argument("--templates", default="B7")
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
            "These templates cannot be generated annotation-only from Endoscapes2023: "
            f"{json.dumps(reasons)}"
        )
    templates = [item for item in requested if item in SUPPORTED_TEMPLATES]

    metadata_csv = Path(args.metadata_csv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    system_prompt_path = Path(args.system_prompt)
    system_prompt = extract_system_prompt(system_prompt_path)

    keyframes = load_cvs_keyframes(metadata_csv, args.video_id)
    if not keyframes:
        raise SystemExit(f"No CVS keyframes found for video {args.video_id!r} in {metadata_csv}")
    log(f"loaded {len(keyframes)} CVS keyframe(s) for video {args.video_id} from {metadata_csv}")

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
        for keyframe in keyframes:
            for template_id in templates:
                user_prompt = build_user_prompt(template_id, args.video_id, keyframe)
                if args.backend == "mock":
                    raw_output = mock_raw_output(template_id, args.video_id, keyframe)
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

                expected = expected_question(args.video_id, keyframe)
                if error is None and qa_objects[0]["question"] != expected:
                    error = "question_mismatch: B7 question must match template wording exactly"

                if error is None:
                    status = "valid" if args.backend == "real" else "valid_mock"
                    valid += 1
                else:
                    status = "rejected"
                    rejected += 1

                frame_label = str(keyframe["frame"])
                record = {
                    "qa_id": f"endoscapes2023_{template_id.lower()}_{args.video_id}_{frame_label}_anno",
                    "dataset": "Endoscapes2023",
                    "procedure_or_task": "Laparoscopic Cholecystectomy",
                    "generation_mode": "annotation_only",
                    "template_id": template_id,
                    "template_support": SUPPORTED_TEMPLATES[template_id],
                    "video_id": args.video_id,
                    "timestamp_or_frame": frame_label,
                    "source_annotation": {
                        "metadata_csv": str(metadata_csv),
                        "C1": keyframe["C1"],
                        "C2": keyframe["C2"],
                        "C3": keyframe["C3"],
                        "frame": keyframe["frame"],
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
        "keyframes": len(keyframes),
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
