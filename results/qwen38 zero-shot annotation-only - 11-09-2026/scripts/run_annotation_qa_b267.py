#!/usr/bin/env python3
"""Generate thinking-enabled B2/B6/B7 QA from selected annotation records.

The selection manifest contains one record per requested QA pair. This runner
loads the named source annotation directly from CholecT50, MultiBypass140, or
Endoscapes2023, converts frame indices to timestamps, copies the canonical
question wording exactly, and requires a non-empty backend reasoning trace for
every real inference record.

B2 is deliberately annotation-first and incomplete: CholecT50 supplies an
instrument/action/target triplet and Endoscapes supplies CVS/anatomy context,
but neither labels all visible safety factors requested by the exact B2
question. B2 answers must therefore decline to proceed without direct visual
confirmation and carry ``[VISUAL EVIDENCE NEEDED]`` for VLM verification.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_annotation_qa_jigsaws import (  # noqa: E402
    MOCK_WATERMARK,
    extract_system_prompt,
    first_sentence,
    load_text_model,
    log,
    normalize_qa_objects,
    parse_json_payload,
    run_generation,
    run_generation_llama,
    run_generation_ollama,
    thinking_trace_returned,
    verify_llama_server,
    verify_ollama_model,
)
from run_annotation_qa_multibypass140 import (  # noqa: E402
    extract_iae_events,
    find_video_record,
)


VISUAL_MARKER = "[VISUAL EVIDENCE NEEDED]"
SUPPORTED_TEMPLATES = {"B2", "B6", "B7"}
ENDOSCAPES_FPS = 25


def timestamp(seconds: int) -> str:
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}:{remaining:02d}"


def load_template_questions(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    questions: dict[str, str] = {}
    for template_id in sorted(SUPPORTED_TEMPLATES):
        match = re.search(
            rf"^## Template {template_id}:[^\n]*\n\n([^\n]+)",
            text,
            flags=re.MULTILINE,
        )
        if not match:
            raise ValueError(f"Could not find canonical {template_id} question in {path}")
        questions[template_id] = match.group(1).strip()
    return questions


def load_cholec_selection(
    item: dict[str, Any], root: Path
) -> tuple[dict[str, Any], str, str]:
    label_path = root / "labels" / f"{item['video_id']}.json"
    payload = json.loads(label_path.read_text(encoding="utf-8"))
    frame = int(item["frame"])
    rows = payload["annotations"].get(str(frame))
    if not rows:
        raise ValueError(f"No CholecT50 annotation at {item['video_id']} frame {frame}")
    triplet_names = [
        payload["categories"]["triplet"][str(int(row[0]))]
        for row in rows
        if row and int(row[0]) >= 0
    ]
    if item["required_triplet"] not in triplet_names:
        raise ValueError(
            f"Required triplet {item['required_triplet']!r} is absent from {triplet_names}"
        )
    phase_ids = sorted({int(row[-1]) for row in rows if row and int(row[-1]) >= 0})
    phase_names = [
        payload["categories"]["phase"].get(str(phase_id), f"phase_{phase_id}")
        for phase_id in phase_ids
    ]
    fps = int(payload.get("fps") or 1)
    time_label = timestamp(round(frame / fps))
    source = {
        "label_file": str(label_path),
        "frame": frame,
        "fps": fps,
        "timestamp": time_label,
        "action_triplets": triplet_names,
        "phase_ids": phase_ids,
        "phase_names": phase_names,
        "annotation_scope": (
            "Instrument/action/target triplets and phase only; no tissue-plane, "
            "hemostasis, instrument-position, or energy-safety label."
        ),
    }
    lines = [
        f"  - action_triplets: {json.dumps(triplet_names)}",
        f"  - surgical_phase: {json.dumps(phase_names)}",
        "  - labeled_scope: instrument, action, target, and phase",
        (
            "  - unlabeled_B2_factors: tissue plane, hemostasis, instrument "
            "position, and energy safety"
        ),
    ]
    return source, time_label, "\n".join(lines)


def load_endoscapes_metadata(
    metadata_csv: Path, video_id: str, frame: int
) -> dict[str, Any]:
    with metadata_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["vid"] == video_id and int(row["frame"]) == frame:
                if row.get("is_ds_keyframe") != "True":
                    raise ValueError(
                        f"Endoscapes selection {video_id}_{frame} is not a CVS keyframe"
                    )
                return {
                    "C1": float(row["C1"]),
                    "C2": float(row["C2"]),
                    "C3": float(row["C3"]),
                    "annotator_1": row.get("cvs_annotator_1"),
                    "annotator_2": row.get("cvs_annotator_2"),
                    "annotator_3": row.get("cvs_annotator_3"),
                }
    raise ValueError(f"Endoscapes keyframe {video_id}_{frame} not found")


def load_endoscapes_classes(
    root: Path, split: str | None, video_id: str, frame: int
) -> list[str]:
    if not split:
        return []
    coco_path = root / split / "annotation_coco.json"
    payload = json.loads(coco_path.read_text(encoding="utf-8"))
    filename = f"{video_id}_{frame}.jpg"
    image = next(
        (candidate for candidate in payload["images"] if candidate["file_name"] == filename),
        None,
    )
    if image is None:
        raise ValueError(f"{filename} not found in {coco_path}")
    category_names = {
        int(category["id"]): category["name"] for category in payload["categories"]
    }
    return sorted(
        {
            category_names[int(annotation["category_id"])]
            for annotation in payload["annotations"]
            if annotation["image_id"] == image["id"]
        }
    )


def criterion_text(value: float) -> str:
    if value >= 0.99:
        return f"{value:.2f} (achieved unanimously)"
    if value >= 0.5:
        return f"{value:.2f} (achieved by annotator majority, not unanimously)"
    if value <= 0.01:
        return f"{value:.2f} (not achieved unanimously)"
    return f"{value:.2f} (not achieved by annotator majority)"


def load_endoscapes_selection(
    item: dict[str, Any], root: Path
) -> tuple[dict[str, Any], str, str]:
    metadata_csv = root / "all_metadata.csv"
    frame = int(item["frame"])
    values = load_endoscapes_metadata(metadata_csv, str(item["video_id"]), frame)
    classes = load_endoscapes_classes(
        root, item.get("coco_split"), str(item["video_id"]), frame
    )
    time_label = timestamp(round(frame / ENDOSCAPES_FPS))
    source = {
        "metadata_csv": str(metadata_csv),
        "frame": frame,
        "fps_used_for_timestamp_conversion": ENDOSCAPES_FPS,
        "timestamp": time_label,
        **values,
        "labeled_structure_classes": classes,
        "annotation_scope": (
            "CVS achievement and optional class presence only; class labels do "
            "not describe appearance or spatial relationships."
        ),
    }
    lines = [
        f"  - cvs_c1_structures_identified: {criterion_text(values['C1'])}",
        (
            "  - cvs_c2_hepatocystic_triangle_cleared: "
            f"{criterion_text(values['C2'])}"
        ),
        f"  - cvs_c3_gallbladder_separated: {criterion_text(values['C3'])}",
    ]
    if classes:
        lines += [
            f"  - labeled_structure_or_tool_classes_present: {json.dumps(classes)}",
            (
                "  - class_presence_limit: names only; no appearance, condition, "
                "or position may be inferred"
            ),
        ]
    if item["template_id"] == "B2":
        lines += [
            (
                "  - unlabeled_B2_factors: hemostasis, actual instrument position, "
                "and energy safety; CVS/class labels are insufficient for the full "
                "visible safety judgment"
            )
        ]
    return source, time_label, "\n".join(lines)


def load_id_names(path: Path) -> dict[int, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.reader(handle)
        next(rows)
        return {int(row[0].strip()): row[1].strip() for row in rows}


def load_multibypass_selection(
    item: dict[str, Any], root: Path
) -> tuple[dict[str, Any], str, str]:
    pickle_path, frames = find_video_record(
        root / "IAE",
        item["center"],
        item.get("split"),
        item["video_id"],
    )
    event = next(
        (
            candidate
            for candidate in extract_iae_events(frames)
            if str(candidate["event_id"]) == str(item["event_id"])
        ),
        None,
    )
    if event is None:
        raise ValueError(
            f"Event {item['event_id']} not found for {item['video_id']} in {pickle_path}"
        )
    phase_names = load_id_names(root / "tables" / "phase.csv")
    step_names = load_id_names(root / "tables" / "step.csv")
    start = timestamp(int(event["start_frame"]))
    end = timestamp(int(event["end_frame"]))
    time_label = start if start == end else f"{start}-{end}"
    source = {
        "iae_pickle_file": str(pickle_path),
        "center": item["center"],
        "split": item.get("split"),
        "event_id": event["event_id"],
        "iae_category": event["category"],
        "iae_severity": event["severity"],
        "iae_rectified": event["rectified"],
        "phase_id": event["phase_id"],
        "phase_name": phase_names.get(event["phase_id"]),
        "step_id": event["step_id"],
        "step_name": step_names.get(event["step_id"]),
        "event_second_range": f"{event['start_frame']}-{event['end_frame']}",
        "event_timestamp": time_label,
        "annotation_frequency_fps": 1,
    }
    lines = [
        f"  - iae_category: {event['category']}",
        f"  - iae_severity: {event['severity']} (dataset severity label)",
        f"  - iae_rectified: {event['rectified']}",
        (
            f"  - operative_phase: {phase_names.get(event['phase_id'])} "
            f"(phase_id {event['phase_id']})"
        ),
        (
            f"  - operative_step: {step_names.get(event['step_id'])} "
            f"(step_id {event['step_id']})"
        ),
        f"  - event_timestamp: {time_label}",
    ]
    return source, time_label, "\n".join(lines)


def load_selection(
    item: dict[str, Any], args: argparse.Namespace
) -> tuple[dict[str, Any], str, str]:
    if item["dataset"] == "CholecT50":
        return load_cholec_selection(item, Path(args.cholec_root))
    if item["dataset"] == "Endoscapes2023":
        return load_endoscapes_selection(item, Path(args.endoscapes_root))
    if item["dataset"] == "MultiBypass140":
        return load_multibypass_selection(item, Path(args.multibypass_root))
    raise ValueError(f"Unsupported dataset in manifest: {item['dataset']}")


def expected_question(
    item: dict[str, Any], time_label: str, questions: dict[str, str]
) -> str:
    question = questions[item["template_id"]].replace("[t]", time_label)
    if item["template_id"] == "B2":
        question = question.replace("[action]", item["action"])
    if "[" in question or "]" in question:
        raise ValueError(f"Unresolved placeholder in question: {question}")
    return question


def recent_openers_block(recent_openers: list[str]) -> list[str]:
    if not recent_openers:
        return []
    return [
        "",
        "Answer openings already used in this template batch; after the required "
        "B2 opening, make the following sentence genuinely different:",
        *[f"- {opener}" for opener in recent_openers[-5:]],
    ]


def build_user_prompt(
    item: dict[str, Any],
    time_label: str,
    annotation_lines: str,
    question: str,
    recent_openers: list[str],
) -> str:
    lines = [
        "Dataset-specific input (ANNOTATION-ONLY: no frames or images are attached):",
        f"- dataset_name: {item['dataset']}",
        "- procedure_or_task: Laparoscopic surgery",
        f"- video_id: {item['video_id']}",
        f"- clip_id: {item['selection_id']}",
        f"- timestamp_or_frame: {time_label}",
        f"- requested_template_ids: {item['template_id']}",
        "- number_of_questions: 1",
        "- available_annotations:",
        annotation_lines,
        "",
        "Ground the QA pair only in these annotations. No visual content was provided.",
        "Do not invent a visible event, tissue appearance, instrument location,",
        "hemostasis finding, energy state, or physical cause.",
        *recent_openers_block(recent_openers),
        "",
        "Use this exact question text:",
        question,
        "",
        (
            "Do not mention any score, subscore, rating, rubric, annotation, "
            "assessment, performance metric, severity number or level, CVS value, "
            "raw field name, metadata, record, label, or dataset in the answer. "
            "Numeric annotation values belong only in the rationale."
        ),
    ]
    if item["template_id"] == "B2":
        lines += [
            "",
            (
                "ANNOTATION-FIRST / LATER VLM VERIFICATION: the exact B2 question "
                "requires visible evidence for multiple safety factors that are not "
                "fully labeled. Begin the answer exactly: \"No—do not proceed without "
                "direct visual confirmation.\""
            ),
            (
                f"Include the exact marker {VISUAL_MARKER} exactly once at the end of "
                "the answer and exactly once at the end of the rationale, as a "
                "standalone final marker. Never place it mid-sentence. State the known "
                "maneuver or CVS readiness context directly, then identify the missing "
                "visible safety checks. The answer must not refer to a dataset, label, "
                "metadata, annotation, or procedural log. Do not turn partial context "
                "into a definitive claim that the maneuver itself is safe or unsafe."
            ),
        ]
    elif item["template_id"] == "B6":
        lines += [
            "",
            f"The answer must name the recorded event timestamp {time_label}.",
            (
                "Explain the pause from the event category, clinically translated "
                "urgency, and phase/step context. Do not use the words severity, "
                "score, rating, record, label, or annotation in the answer. Do not "
                "speculate about the physical cause or describe what the event "
                "looked like."
            ),
        ]
    elif item["template_id"] == "B7":
        lines += [
            "",
            (
                "Answer yes, no, or not yet from achievement of all three CVS "
                "criteria. Do not use unlabeled hemostasis, energy, tissue-plane, or "
                "instrument-position claims."
            ),
        ]
    lines += ["", "Return valid JSON only, following the system prompt schema."]
    return "\n".join(lines)


ANSWER_META_RE = re.compile(
    r"\b(score|scores|subscore|subscores|rating|ratings|rubric|rubrics|"
    r"annotation|annotations|assessment|assessments|performance metric|"
    r"performance metrics|severity|criterion value|criterion values|label|"
    r"labels|metadata|dataset|record|records|iae|cvs_c[123]|procedural log)\b",
    re.IGNORECASE,
)
RAW_DECIMAL_RE = re.compile(r"(?<![\d:])(?:0|1)\.\d+\b")


def validate_output(
    item: dict[str, Any],
    time_label: str,
    expected: str,
    raw_output: str,
    qa_objects: list[dict[str, str]] | None,
    require_thinking: bool,
) -> str | None:
    if qa_objects is None:
        return "schema_mismatch: expected objects with exactly question/answer/rationale"
    qa = qa_objects[0]
    if qa["question"] != expected:
        return "question_mismatch: canonical template wording must match exactly"
    if require_thinking and not thinking_trace_returned(raw_output):
        return "missing_thinking_trace: backend returned no reasoning content"
    answer = qa["answer"]
    rationale = qa["rationale"]
    if ANSWER_META_RE.search(answer):
        return f"rule11_answer_leak: {ANSWER_META_RE.search(answer).group(0)!r}"
    if RAW_DECIMAL_RE.search(answer):
        return f"rule11_numeric_value_leak: {RAW_DECIMAL_RE.search(answer).group(0)!r}"
    if item["template_id"] == "B2":
        if not answer.startswith("No—do not proceed without direct visual confirmation."):
            return "b2_safety_gate_missing: required conservative opening is absent"
        if not (
            answer.count(VISUAL_MARKER) == 1
            and rationale.count(VISUAL_MARKER) == 1
            and answer.rstrip().endswith(VISUAL_MARKER)
            and rationale.rstrip().endswith(VISUAL_MARKER)
        ):
            return (
                "b2_visual_marker_format: marker must appear exactly once at the "
                "end of both answer and rationale"
            )
    if item["template_id"] == "B6" and time_label not in answer:
        return "b6_timestamp_missing: answer must name the recorded timestamp"
    if item["template_id"] == "B7" and not re.match(
        r"^\s*(yes|no|not yet)\b", answer, flags=re.IGNORECASE
    ):
        return "b7_decision_missing: answer must begin yes, no, or not yet"
    return None


def mock_raw_output(question: str, template_id: str, time_label: str) -> str:
    if template_id == "B2":
        answer = (
            "No—do not proceed without direct visual confirmation. "
            f"{MOCK_WATERMARK}\n\n{VISUAL_MARKER}"
        )
        rationale = f"{MOCK_WATERMARK}\n\n{VISUAL_MARKER}"
    elif template_id == "B6":
        answer = f"Pause at {time_label}. {MOCK_WATERMARK}"
        rationale = MOCK_WATERMARK
    else:
        answer = f"Not yet. {MOCK_WATERMARK}"
        rationale = MOCK_WATERMARK
    return json.dumps(
        [
            {
                "question": question,
                "answer": answer,
                "rationale": rationale,
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--system-prompt", required=True)
    parser.add_argument("--template-questions", required=True)
    parser.add_argument(
        "--templates",
        default="B2,B6,B7",
        help="Comma-separated manifest template IDs to generate",
    )
    parser.add_argument(
        "--selection-ids",
        default=None,
        help="Optional comma-separated manifest selection IDs to run",
    )
    parser.add_argument(
        "--cholec-root",
        default="/mnt/sun/shared/datasets/surgical_skill/cholet50/CholecT50",
    )
    parser.add_argument(
        "--endoscapes-root",
        default="/mnt/sun/shared/datasets/surgical_skill/Endoscapes2023",
    )
    parser.add_argument(
        "--multibypass-root",
        default="/mnt/sun/shared/datasets/surgical_skill/MultiBypass140",
    )
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--backend", choices=["real", "mock"], default="real")
    parser.add_argument(
        "--inference-engine",
        choices=["transformers", "ollama", "llama"],
        default="transformers",
    )
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--ollama-model", default="qwen3.6-35b-a3b")
    parser.add_argument("--ollama-host", default="http://localhost:11434")
    parser.add_argument("--ollama-num-ctx", type=int, default=8192)
    parser.add_argument("--llama-host", default="http://localhost:8080")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-new-tokens", type=int, default=6000)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    selected_templates = {
        value.strip() for value in args.templates.split(",") if value.strip()
    }
    unknown = selected_templates - SUPPORTED_TEMPLATES
    if unknown:
        parser.error(f"Unsupported template IDs: {sorted(unknown)}")

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = [
        item for item in manifest if item["template_id"] in selected_templates
    ]
    if args.selection_ids:
        selected_ids = {
            value.strip()
            for value in args.selection_ids.split(",")
            if value.strip()
        }
        manifest = [
            item for item in manifest if item["selection_id"] in selected_ids
        ]
    if not manifest:
        parser.error("No manifest selections match --templates")
    if len({item["selection_id"] for item in manifest}) != len(manifest):
        parser.error("Manifest selection_id values must be unique")

    system_prompt_path = Path(args.system_prompt)
    system_prompt = extract_system_prompt(system_prompt_path)
    system_prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
    template_path = Path(args.template_questions)
    questions = load_template_questions(template_path)
    template_hash = hashlib.sha256(template_path.read_bytes()).hexdigest()

    tokenizer = model = None
    if args.backend == "real":
        if args.inference_engine == "llama":
            verify_llama_server(args.llama_host)
        elif args.inference_engine == "ollama":
            verify_ollama_model(args.ollama_host, args.ollama_model)
        else:
            tokenizer, model = load_text_model(args.model_id, args.device_map)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_name = "qa_records.jsonl" if args.backend == "real" else "qa_records.mock.jsonl"
    output_path = output_dir / output_name
    valid = rejected = thinking_verified = 0
    recent_openers: dict[str, list[str]] = defaultdict(list)

    with output_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            source, time_label, annotation_lines = load_selection(item, args)
            question = expected_question(item, time_label, questions)
            user_prompt = build_user_prompt(
                item,
                time_label,
                annotation_lines,
                question,
                recent_openers[item["template_id"]],
            )
            attempts = 0
            earlier_errors: list[str] = []
            qa_objects = None
            raw_output = ""
            error = None
            while True:
                attempts += 1
                if args.backend == "mock":
                    raw_output = mock_raw_output(
                        question, item["template_id"], time_label
                    )
                elif args.inference_engine == "llama":
                    raw_output = run_generation_llama(
                        args.llama_host,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.temperature,
                    )
                elif args.inference_engine == "ollama":
                    raw_output = run_generation_ollama(
                        args.ollama_host,
                        args.ollama_model,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.ollama_num_ctx,
                        args.temperature,
                    )
                else:
                    raw_output = run_generation(
                        tokenizer,
                        model,
                        system_prompt,
                        user_prompt,
                        args.max_new_tokens,
                        args.temperature,
                    )
                qa_objects = None
                error = None
                try:
                    qa_objects = normalize_qa_objects(parse_json_payload(raw_output))
                except Exception as exc:  # noqa: BLE001
                    error = repr(exc)
                if error is None:
                    error = validate_output(
                        item,
                        time_label,
                        question,
                        raw_output,
                        qa_objects,
                        require_thinking=args.backend == "real",
                    )
                retryable = (
                    error is not None
                    and attempts <= args.max_retries
                    and args.backend == "real"
                    and args.temperature > 0
                )
                if not retryable:
                    break
                earlier_errors.append(error)
                log(f"retry {attempts}/{args.max_retries} after: {error}")

            returned_thinking = (
                args.backend == "real" and thinking_trace_returned(raw_output)
            )
            if error is None:
                status = "valid" if args.backend == "real" else "valid_mock"
                valid += 1
                if returned_thinking:
                    thinking_verified += 1
                if qa_objects:
                    recent_openers[item["template_id"]].append(
                        first_sentence(qa_objects[0]["answer"])
                    )
            else:
                status = "rejected"
                rejected += 1

            record = {
                "qa_id": (
                    f"{item['dataset'].lower()}_{item['template_id'].lower()}_"
                    f"{item['selection_id']}_anno"
                ),
                "selection_id": item["selection_id"],
                "dataset": item["dataset"],
                "procedure_or_task": (
                    "Laparoscopic Roux-en-Y Gastric Bypass"
                    if item["dataset"] == "MultiBypass140"
                    else "Laparoscopic Cholecystectomy"
                ),
                "generation_mode": "annotation_only",
                "template_id": item["template_id"],
                "video_id": item["video_id"],
                "timestamp_or_frame": time_label,
                "source_annotation": source,
                "model": {
                    "backend": args.backend,
                    "real_model_inference": args.backend == "real",
                    "inference_engine": (
                        args.inference_engine if args.backend == "real" else None
                    ),
                    "model_id": (
                        (
                            "ggml-org/Qwen3.6-35B-A3B-GGUF:Q4_K_M"
                            if args.inference_engine == "llama"
                            else (
                                args.ollama_model
                                if args.inference_engine == "ollama"
                                else args.model_id
                            )
                        )
                        if args.backend == "real"
                        else None
                    ),
                    "system_prompt_path": str(system_prompt_path),
                    "system_prompt_sha256": system_prompt_hash,
                    "template_questions_path": str(template_path),
                    "template_questions_sha256": template_hash,
                    "thinking": {
                        "enabled": args.backend == "real",
                        "required": args.backend == "real",
                        "request_parameter": (
                            {"chat_template_kwargs": {"enable_thinking": True}}
                            if args.backend == "real"
                            and args.inference_engine == "llama"
                            else (
                                {"think": True}
                                if args.backend == "real"
                                and args.inference_engine == "ollama"
                                else (
                                    {"enable_thinking": True}
                                    if args.backend == "real"
                                    else None
                                )
                            )
                        ),
                        "trace_returned": returned_thinking,
                    },
                    "attempts": attempts,
                    "retried_after_errors": earlier_errors or None,
                    "prompt": user_prompt,
                    "raw_output": raw_output,
                },
                "qa": qa_objects,
                "validation_status": status,
                "validation_error": error,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            log(
                f"{record['qa_id']}: {status}"
                + (f" ({error})" if error else "")
            )

    summary = {
        "backend": args.backend,
        "templates": sorted(selected_templates),
        "selections": len(manifest),
        "valid": valid,
        "rejected": rejected,
        "thinking_required": args.backend == "real",
        "thinking_verified": thinking_verified,
        "system_prompt_path": str(system_prompt_path),
        "system_prompt_sha256": system_prompt_hash,
        "template_questions_path": str(template_path),
        "template_questions_sha256": template_hash,
        "output": str(output_path),
    }
    (output_dir / "run_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
