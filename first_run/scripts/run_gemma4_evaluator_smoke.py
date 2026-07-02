#!/usr/bin/env python3
"""Smoke-test generated QA delivery into LLM and VLM evaluator prompts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


LLM_KEYS = {"label_score", "rationale_score", "evidence_score", "evaluator_notes"}
VLM_KEYS = {"anatomy_score", "timestamp_score", "action_score", "evaluator_notes"}


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def parse_json_object(text: str, expected_keys: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    stripped = text.strip()
    stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.DOTALL | re.IGNORECASE).strip()
    stripped = re.sub(r"<\|[^>]+?\|>", "", stripped).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        if start >= 0:
            depth = 0
            in_string = False
            escape = False
            end = None
            for index, char in enumerate(stripped[start:], start=start):
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if end is not None:
                stripped = stripped[start:end]
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        return None, f"json_parse_error: {exc}"
    if not isinstance(parsed, dict):
        return None, "output_not_object"
    if set(parsed) != expected_keys:
        return None, f"schema_key_mismatch: {sorted(parsed)}"
    for key in expected_keys - {"evaluator_notes"}:
        if parsed.get(key) not in (0, 1, 2):
            return None, f"score_out_of_range: {key}={parsed.get(key)!r}"
    if not isinstance(parsed.get("evaluator_notes"), str):
        return None, "evaluator_notes_not_string"
    return parsed, None


def build_ground_truth(record: dict[str, Any]) -> dict[str, Any]:
    annotation = record.get("source_annotation", {})
    return {
        "expected_question": record.get("question"),
        "expected_answer_focus": record.get("label_supported_focus"),
        "reference_feedback": record.get("label_supported_reference_feedback"),
        "expected_evidence_timestamp": record.get("timestamp_or_frame"),
        "source_annotation": annotation,
        "visual_evidence": record.get("visual_evidence", {}),
    }


def build_llm_prompt(record: dict[str, Any]) -> str:
    return f"""You are an expert AI evaluator and surgical attending.

Routing: This is Text-Based Evaluation via LLM Rubric v1.1 because the input contains GROUND_TRUTH_DATA.

Objective: Grade the AI surgical copilot's generated QA JSON against verified ground truth data.

Rubric:
- label_score (0-2): 2 exact taxonomy/focus match; 1 partially correct or adjacent; 0 incorrect.
- rationale_score (0-2): 2 clinically correct and grounded; 1 vague/incomplete; 0 flawed or hallucinated.
- evidence_score (0-2): 2 exact critical timestamp/frame range; 1 broad but related; 0 missing or incorrect.

Strict formatting: Return only one JSON object with exactly these keys:
label_score, rationale_score, evidence_score, evaluator_notes.

[DATASET_AND_TASK]:
JIGSAWS - Template D1 Suturing Coaching QA

[GROUND_TRUTH_DATA]:
{json.dumps(build_ground_truth(record), ensure_ascii=False, indent=2)}

[AI_OUTPUT_JSON]:
{json.dumps(record.get("parsed_output"), ensure_ascii=False, indent=2)}
"""


def build_vlm_prompt(record: dict[str, Any]) -> str:
    return f"""You are an expert AI evaluator and surgical attending.

Routing: This is Visual Evidence Evaluation via VLM Rubric v1.0 because the input contains VIDEO_EVIDENCE.

Objective: Grade whether the proposed QA pair matches the visual reality of the provided JIGSAWS Suturing frames.

Rubric:
- anatomy_score (0-2): 2 confirms correct suturing simulation/task and relevant visible structures; 1 ambiguous but not contradictory; 0 wrong procedure or hallucinated anatomy.
- timestamp_score (0-2): 2 QA refers to the exact provided frame range; 1 nearby or broad; 0 missing or wrong timestamp.
- action_score (0-2): 2 QA matches visible tool/tissue interaction or clearly states uncertainty; 1 plausible but ambiguous; 0 contradicts visible evidence or hallucinates actions/tools.

Strict formatting: Return only one JSON object with exactly these keys:
anatomy_score, timestamp_score, action_score, evaluator_notes.

[DATASET_AND_TASK]:
JIGSAWS - Template D1 Suturing Visual Grounding Verification

[VIDEO_EVIDENCE]:
Use the attached visual evidence frames for {record.get("timestamp_or_frame")}.
Source visual evidence manifest:
{json.dumps(record.get("visual_evidence", {}), ensure_ascii=False, indent=2)}

[PROPOSED_QA_JSON]:
{json.dumps(record.get("parsed_output"), ensure_ascii=False, indent=2)}
"""


def visual_evidence_frame_paths(record: dict[str, Any]) -> list[str]:
    visual_evidence = record.get("visual_evidence", {})
    frame_paths = visual_evidence.get("frame_paths")
    if isinstance(frame_paths, list) and frame_paths:
        return [str(path) for path in frame_paths]
    return [str(path) for path in record.get("model", {}).get("sampled_frames", [])]


def load_model(model_id: str, device_map: str) -> tuple[Any, Any]:
    import torch
    import transformers
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    kwargs: dict[str, Any] = {"device_map": device_map, "trust_remote_code": True}
    kwargs["dtype"] = torch.bfloat16 if torch.cuda.is_available() else "auto"

    model_class_names = [
        "Gemma4ForConditionalGeneration",
        "AutoModelForImageTextToText",
        "AutoModelForMultimodalLM",
        "AutoModelForCausalLM",
    ]
    errors: list[str] = []
    for class_name in model_class_names:
        model_cls = getattr(transformers, class_name, None)
        if model_cls is None:
            continue
        try:
            model = model_cls.from_pretrained(model_id, **kwargs)
            model.eval()
            return processor, model
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{class_name}: {exc}")
    raise RuntimeError("Could not load evaluator model:\n" + "\n".join(errors))


def run_evaluator(
    processor: Any,
    model: Any,
    prompt: str,
    image_paths: list[str],
    max_new_tokens: int,
) -> str:
    import torch
    from PIL import Image

    content: list[dict[str, Any]] = []
    images = []
    for path in image_paths:
        image = Image.open(path).convert("RGB")
        images.append(image)
        content.append({"type": "image"})
    content.append({"type": "text", "text": prompt})
    messages = [
        {"role": "system", "content": "You are in evaluator JSON-only mode. Return only the required JSON object."},
        {"role": "user", "content": content},
    ]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(
        text=[text],
        images=images or None,
        padding=True,
        return_tensors="pt",
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    inputs = inputs.to(device)
    with torch.no_grad():
        generated_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
        )
    generated_ids_trimmed = [
        out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
    ]
    return processor.batch_decode(
        generated_ids_trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-jsonl", required=True)
    parser.add_argument("--llm-output-jsonl", required=True)
    parser.add_argument("--vlm-output-jsonl", required=True)
    parser.add_argument("--rejected-jsonl", required=True)
    parser.add_argument("--model-id", default="google/gemma-4-31B-it")
    parser.add_argument("--max-records", type=int, default=2, help="0 means read all generated records.")
    parser.add_argument("--min-valid", type=int, default=2)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--mock-llm-raw-output", default=None)
    parser.add_argument("--mock-vlm-raw-output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records: list[dict[str, Any]] = []
    with Path(args.generated_jsonl).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
            if args.max_records > 0 and len(records) >= args.max_records:
                break
    if len(records) < args.min_valid:
        print(f"Need at least {args.min_valid} generated records, found {len(records)}.", file=sys.stderr)
        raise SystemExit(2)

    processor = model = None
    if args.mock_llm_raw_output is None or args.mock_vlm_raw_output is None:
        processor, model = load_model(args.model_id, args.device_map)
    llm_valid = 0
    vlm_valid = 0

    for record in records:
        if args.mock_llm_raw_output is not None:
            llm_raw = args.mock_llm_raw_output
        else:
            llm_raw = run_evaluator(processor, model, build_llm_prompt(record), [], args.max_new_tokens)
        llm_parsed, llm_error = parse_json_object(llm_raw, LLM_KEYS)
        llm_record = {
            "qa_id": record.get("qa_id"),
            "stage": "llm_rubric_v1.1",
            "raw_output": llm_raw,
            "parsed_output": llm_parsed,
            "validation_error": llm_error,
        }
        if llm_error is None:
            llm_valid += 1
            append_jsonl(Path(args.llm_output_jsonl), llm_record)
        else:
            append_jsonl(Path(args.rejected_jsonl), llm_record)

        frame_paths = visual_evidence_frame_paths(record)
        missing_frames = [path for path in frame_paths if not Path(path).exists()]
        if not frame_paths or missing_frames:
            vlm_record = {
                "qa_id": record.get("qa_id"),
                "stage": "vlm_rubric_v1.0",
                "raw_output": None,
                "parsed_output": None,
                "validation_error": "missing_video_evidence_frames",
                "missing_frames": missing_frames,
            }
            append_jsonl(Path(args.rejected_jsonl), vlm_record)
            continue
        if args.mock_vlm_raw_output is not None:
            vlm_raw = args.mock_vlm_raw_output
        else:
            vlm_raw = run_evaluator(processor, model, build_vlm_prompt(record), frame_paths, args.max_new_tokens)
        vlm_parsed, vlm_error = parse_json_object(vlm_raw, VLM_KEYS)
        vlm_record = {
            "qa_id": record.get("qa_id"),
            "stage": "vlm_rubric_v1.0",
            "raw_output": vlm_raw,
            "parsed_output": vlm_parsed,
            "validation_error": vlm_error,
        }
        if vlm_error is None:
            vlm_valid += 1
            append_jsonl(Path(args.vlm_output_jsonl), vlm_record)
        else:
            append_jsonl(Path(args.rejected_jsonl), vlm_record)

    summary = {
        "generated_records": len(records),
        "llm_valid": llm_valid,
        "vlm_valid": vlm_valid,
        "min_valid": args.min_valid,
    }
    print(json.dumps(summary, indent=2), flush=True)
    if llm_valid < args.min_valid or vlm_valid < args.min_valid:
        raise SystemExit(5)


if __name__ == "__main__":
    main()
