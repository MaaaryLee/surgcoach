#!/usr/bin/env python3
"""Annotation-only QA generation for JIGSAWS tasks (Suturing, Knot_Tying, Needle_Passing), templates A-D.

This runner never reads video or extracts frames. Prompts are built solely from
real JIGSAWS annotations (meta_file GRS scores, skill level, gesture
transcriptions), and the model generates question/answer/rationale itself.
There are no canned or label-templated answers anywhere in this script; the
only label-derived values passed to the model are the annotations themselves.

Every output record carries provenance: backend ("real" or "mock"),
real_model_inference, model_id, the exact prompt, and the raw model output.
Mock records are additionally watermarked and written with
validation_status "valid_mock" so they can never be confused with real data.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GRS_NAMES = [
    "respect_for_tissue",
    "suture_needle_handling",
    "time_and_motion",
    "flow_of_operation",
    "overall_performance",
    "quality_of_final_product",
]

# JIGSAWS self-proclaimed experience levels (Gao et al., 2014).
SKILL_LEVELS = {
    "N": "novice, <10 hours robotic surgical practice",
    "I": "intermediate, 10-100 hours robotic surgical practice",
    "E": "expert, >100 hours robotic surgical practice",
}

# Standard JIGSAWS gesture vocabulary (Gao et al., 2014).
GESTURE_DEFINITIONS = {
    "G1": "Reaching for needle with right hand",
    "G2": "Positioning needle",
    "G3": "Pushing needle through tissue",
    "G4": "Transferring needle from left to right",
    "G5": "Moving to center with needle in grip",
    "G6": "Pulling suture with left hand",
    "G7": "Pulling suture with right hand",
    "G8": "Orienting needle",
    "G9": "Using right hand to help tighten suture",
    "G10": "Loosening more suture",
    "G11": "Dropping suture at end and moving to end points",
    "G12": "Reaching for needle with left hand",
    "G13": "Making C loop around right hand",
    "G14": "Reaching for suture with right hand",
    "G15": "Pulling suture with both hands",
}

# Templates that JIGSAWS annotations (skill level, GRS scores, gesture spans)
# can actually support without visual input. Everything else is refused with a
# reason instead of being generated from thin air.
SUPPORTED_TEMPLATES = {
    "A3": "gesture labels define the annotated action",
    "C1": "respect_for_tissue GRS subscore",
    "C2": "suture_needle_handling GRS subscore",
    "C3": "time_and_motion GRS subscore",
    "C6": "flow_of_operation GRS subscore",
    "C7": "skill_level and grs_total",
    "D1": "GRS subscores and gesture span",
    "D2": "GRS subscores rank the three feedback points",
    "D3": "GRS subscores rank the three feedback points",
    "D4": "weakest GRS subscore selects the drill target",
    "D5": "strongest GRS subscore supports reinforcement",
}
UNSUPPORTED_TEMPLATES = {
    "A1": "JIGSAWS has no anatomy labels",
    "A2": "JIGSAWS has no instrument labels",
    "A4": "JIGSAWS has no visual-field-quality labels",
    "B1": "JIGSAWS has no safety/error labels",
    "B2": "JIGSAWS has no safety/error labels",
    "B3": "JIGSAWS has no safety/error labels",
    "B4": "JIGSAWS has no near-miss labels",
    "B5": "JIGSAWS has no safety/error labels",
    "C4": "JIGSAWS has no bimanual-coordination label",
    "C5": "JIGSAWS has no targeting/accuracy label",
}

MOCK_WATERMARK = "[MOCK OUTPUT - NOT MODEL-GENERATED - SMOKE TEST ONLY]"


def log(message: str) -> None:
    print(message, flush=True)


def extract_system_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    prompt = match.group(1).strip() if match else text.strip()

    def include_file(include_match: re.Match[str]) -> str:
        include_path = (path.parent / include_match.group(1).strip()).resolve()
        return include_path.read_text(encoding="utf-8").strip()

    return re.sub(r"\{\{include:\s*([^}]+?)\s*\}\}", include_file, prompt).strip()


def load_d_templates(path: Path) -> dict[str, str]:
    """Parse canonical D1-D5 question wording from the template markdown."""
    text = path.read_text(encoding="utf-8")
    templates: dict[str, str] = {}
    for match in re.finditer(
        r"## Template (D\d)[^\n]*\n+```text\n(.*?)\n```", text, flags=re.DOTALL
    ):
        templates[match.group(1)] = match.group(2).strip()
    if not templates:
        # Fall back to fence-less section bodies.
        for match in re.finditer(
            r"## Template (D\d)[^\n]*\n+(.*?)(?=\n## |\Z)", text, flags=re.DOTALL
        ):
            templates[match.group(1)] = match.group(2).strip()
    missing = {"D1", "D2", "D3", "D4", "D5"} - set(templates)
    if missing:
        raise ValueError(f"Missing D templates in {path}: {sorted(missing)}")
    return templates


def read_meta(dataset_root: Path, trial_id: str, task: str) -> dict[str, Any]:
    meta_path = dataset_root / f"meta_file_{task}.txt"
    for line in meta_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if parts[0] != trial_id:
            continue
        scores = {name: int(value) for name, value in zip(GRS_NAMES, parts[3:9])}
        return {
            "trial_id": parts[0],
            "skill_level": parts[1],
            "grs_total": int(parts[2]),
            "grs_subscores": scores,
            "source_file": str(meta_path),
        }
    raise KeyError(f"Trial {trial_id} not found in {meta_path}")


def read_gesture_spans(dataset_root: Path, trial_id: str) -> list[dict[str, Any]]:
    path = dataset_root / "transcriptions" / f"{trial_id}.txt"
    spans: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        start, end, gesture_id = line.split()[:3]
        spans.append(
            {
                "gesture_id": gesture_id,
                "start_frame": int(start),
                "end_frame": int(end),
                "source_file": str(path),
            }
        )
    if not spans:
        raise ValueError(f"No gesture spans in {path}")
    return spans


def build_user_prompt(
    template_id: str,
    meta: dict[str, Any],
    span: dict[str, Any],
    d_templates: dict[str, str],
    task: str,
) -> str:
    frame_label = f"{span['start_frame']}-{span['end_frame']}"
    gesture_definition = GESTURE_DEFINITIONS.get(span["gesture_id"], "unknown gesture")
    lines = [
        "Dataset-specific input (ANNOTATION-ONLY: no frames or images are attached):",
        "- dataset_name: JIGSAWS",
        f"- procedure_or_task: {task.replace('_', ' ')}",
        f"- video_id: {meta['trial_id']}",
        f"- clip_id: {meta['trial_id']}_{frame_label}",
        f"- timestamp_or_frame: frames {frame_label}",
        f"- requested_template_ids: {template_id}",
        "- number_of_questions: 1",
        "- available_annotations:",
        f"  - trial_id: {meta['trial_id']}",
        f"  - skill_level: {meta['skill_level']} ({SKILL_LEVELS.get(meta['skill_level'], 'unknown')})",
        f"  - grs_total: {meta['grs_total']}",
        f"  - grs_subscores: {json.dumps(meta['grs_subscores'], sort_keys=True)}",
        f"  - gesture_id: {span['gesture_id']} ({gesture_definition})",
        f"  - gesture_frame_range: {frame_label}",
        "",
        "Ground the QA pair only in these annotations. Do not describe any visual",
        "content; no visual content was provided.",
    ]
    if template_id.startswith("D"):
        question = d_templates[template_id].replace(
            "[a certain video span]", f"frames {frame_label}"
        )
        lines += ["", "Use this exact question text:", question]
    lines += ["", "Return valid JSON only, following the system prompt schema."]
    return "\n".join(lines)


def expected_d_question(
    template_id: str, span: dict[str, Any], d_templates: dict[str, str]
) -> str | None:
    if not template_id.startswith("D"):
        return None
    frame_label = f"{span['start_frame']}-{span['end_frame']}"
    return d_templates[template_id].replace("[a certain video span]", f"frames {frame_label}")


def parse_json_payload(text: str) -> Any:
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"^.*?</think>", "", stripped, flags=re.DOTALL | re.IGNORECASE)
    stripped = re.sub(r"<\|[^>]+?\|>", "", stripped).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        stripped = fence_match.group(1).strip()
    start_candidates = [index for index in (stripped.find("["), stripped.find("{")) if index >= 0]
    if not start_candidates:
        return json.loads(stripped)
    start = min(start_candidates)
    opener = stripped[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start : index + 1])
    return json.loads(stripped[start:])


def normalize_qa_objects(payload: Any) -> list[dict[str, Any]] | None:
    items = payload if isinstance(payload, list) else [payload]
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        if set(item.keys()) != {"question", "answer", "rationale"}:
            return None
        normalized.append(item)
    return normalized or None


def load_text_model(model_id: str, device_map: str) -> tuple[Any, Any]:
    import torch
    import transformers
    from transformers import AutoProcessor

    log(f"loading processor for {model_id}")
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    kwargs: dict[str, Any] = {
        "device_map": device_map,
        "trust_remote_code": True,
        "dtype": torch.bfloat16 if torch.cuda.is_available() else "auto",
    }
    # Qwen3.6 is a multimodal architecture; try VL model classes before CausalLM
    # (same proven chain as the legacy runner). Text-only prompts work fine.
    class_names = [
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
        "AutoModelForMultimodalLM",
        "AutoModelForCausalLM",
    ]
    errors: list[str] = []
    for class_name in class_names:
        model_cls = getattr(transformers, class_name, None)
        if model_cls is None:
            continue
        try:
            log(f"loading model with {class_name}")
            model = model_cls.from_pretrained(model_id, **kwargs)
            model.eval()
            log(f"model loaded: {type(model)}")
            hf_map = getattr(model, "hf_device_map", None) or {}
            devices = sorted({str(v) for v in hf_map.values()})
            log(f"device map devices: {devices}")
            offloaded = [k for k, v in hf_map.items() if str(v) in {"cpu", "disk"}]
            if offloaded:
                raise RuntimeError(
                    f"{len(offloaded)} modules offloaded to CPU/disk (insufficient free "
                    f"GPU memory); refusing to run — first offloaded: {offloaded[:5]}"
                )
            return processor, model
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{class_name}: {exc}")
    raise RuntimeError("Could not load model with any AutoModel class:\n" + "\n".join(errors))


def run_generation(
    processor: Any,
    model: Any,
    system_prompt: str,
    user_prompt: str,
    max_new_tokens: int,
) -> str:
    import torch

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=True,
    )
    inputs = processor(text=[text], padding=True, return_tensors="pt")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    inputs = inputs.to(device)
    with torch.no_grad():
        generated_ids = model.generate(
            **inputs, max_new_tokens=max_new_tokens, do_sample=False
        )
    trimmed = generated_ids[0][inputs.input_ids.shape[1] :]
    return processor.batch_decode(
        [trimmed], skip_special_tokens=False, clean_up_tokenization_spaces=False
    )[0]


def mock_raw_output(
    template_id: str, span: dict[str, Any], d_templates: dict[str, str]
) -> str:
    frame_label = f"{span['start_frame']}-{span['end_frame']}"
    question = expected_d_question(template_id, span, d_templates) or (
        f"{MOCK_WATERMARK} placeholder question for {template_id} at frames {frame_label}"
    )
    return json.dumps(
        [
            {
                "question": question,
                "answer": f"{MOCK_WATERMARK} placeholder answer for {template_id}",
                "rationale": f"{MOCK_WATERMARK} placeholder rationale for {template_id}",
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default="Suturing", help="JIGSAWS task: Suturing, Knot_Tying, or Needle_Passing")
    parser.add_argument("--dataset-root", default=None, help="Default: /mnt/sun/shared/datasets/surgical_skill/JIGSAWS/<task>")
    parser.add_argument("--system-prompt", required=True, help="Path to annotation-only system-prompt-A-D.md")
    parser.add_argument("--d-templates", default=None, help="Path to template-d-question-templates.md (default: alongside system prompt)")
    parser.add_argument("--model-id", default="Qwen/Qwen3.6-35B-A3B")
    parser.add_argument("--trial-id", default="Suturing_B001")
    parser.add_argument("--gesture-id", default=None, help="Restrict to one gesture ID (default: all spans in the trial)")
    parser.add_argument("--max-spans", type=int, default=0, help="Cap the number of gesture spans processed (0 = all)")
    parser.add_argument("--templates", default="A3,C1,C2,C3,C6,C7,D1,D2,D3,D4,D5")
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument("--device-map", default="auto")
    parser.add_argument("--backend", choices=["real", "mock"], default="real",
                        help="mock builds watermarked placeholder records for pipeline smoke tests only")
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
            "These templates cannot be generated annotation-only from JIGSAWS: "
            f"{json.dumps(reasons)}"
        )
    templates = [item for item in requested if item in SUPPORTED_TEMPLATES]

    dataset_root = Path(
        args.dataset_root
        or f"/mnt/sun/shared/datasets/surgical_skill/JIGSAWS/{args.task}"
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    system_prompt_path = Path(args.system_prompt)
    system_prompt = extract_system_prompt(system_prompt_path)
    d_templates_path = (
        Path(args.d_templates)
        if args.d_templates
        else system_prompt_path.parent / "template-d-question-templates.md"
    )
    d_templates = load_d_templates(d_templates_path)

    meta = read_meta(dataset_root, args.trial_id, args.task)
    spans = read_gesture_spans(dataset_root, args.trial_id)
    if args.gesture_id:
        spans = [span for span in spans if span["gesture_id"] == args.gesture_id]
        if not spans:
            raise SystemExit(f"Gesture {args.gesture_id} not found for {args.trial_id}")
    if args.max_spans > 0:
        spans = spans[: args.max_spans]

    processor = model = None
    if args.backend == "real":
        processor, model = load_text_model(args.model_id, args.device_map)

    output_name = "qa_records.jsonl" if args.backend == "real" else "qa_records.mock.jsonl"
    output_path = output_dir / output_name
    valid = rejected = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for span in spans:
            for template_id in templates:
                user_prompt = build_user_prompt(template_id, meta, span, d_templates, args.task)
                if args.backend == "mock":
                    raw_output = mock_raw_output(template_id, span, d_templates)
                else:
                    raw_output = run_generation(
                        processor, model, system_prompt, user_prompt, args.max_new_tokens
                    )

                qa_objects = None
                error = None
                try:
                    qa_objects = normalize_qa_objects(parse_json_payload(raw_output))
                    if qa_objects is None:
                        error = "schema_mismatch: expected objects with exactly question/answer/rationale"
                except Exception as exc:  # noqa: BLE001
                    error = repr(exc)

                expected_question = expected_d_question(template_id, span, d_templates)
                model_question = None
                if error is None and expected_question is not None:
                    model_question = qa_objects[0]["question"]
                    first_paragraph = expected_question.split("\n\n")[0].strip()
                    if model_question.strip() in (expected_question.strip(), first_paragraph):
                        # The D question is a deterministic template constant, so
                        # canonicalize it; the model's literal echo is preserved in
                        # model.question_echo and raw_output.
                        qa_objects[0]["question"] = expected_question
                    else:
                        error = "question_mismatch: D question must match template wording"

                if error is None:
                    status = "valid" if args.backend == "real" else "valid_mock"
                    valid += 1
                else:
                    status = "rejected"
                    rejected += 1

                frame_label = f"{span['start_frame']}-{span['end_frame']}"
                record = {
                    "qa_id": f"jigsaws_{args.task.lower()}_{template_id.lower()}_{meta['trial_id']}_{span['gesture_id']}_{frame_label.replace('-', '_')}_anno",
                    "dataset": "JIGSAWS",
                    "procedure_or_task": args.task.replace("_", " "),
                    "generation_mode": "annotation_only",
                    "template_id": template_id,
                    "template_support": SUPPORTED_TEMPLATES[template_id],
                    "trial_id": meta["trial_id"],
                    "timestamp_or_frame": frame_label,
                    "source_annotation": {
                        "meta_file": meta["source_file"],
                        "transcription_file": span["source_file"],
                        "skill_level": meta["skill_level"],
                        "grs_total": meta["grs_total"],
                        "grs_subscores": meta["grs_subscores"],
                        "gesture_id": span["gesture_id"],
                        "gesture_definition": GESTURE_DEFINITIONS.get(span["gesture_id"]),
                        "gesture_frame_range": frame_label,
                    },
                    "model": {
                        "backend": args.backend,
                        "real_model_inference": args.backend == "real",
                        "model_id": args.model_id if args.backend == "real" else None,
                        "system_prompt_path": str(system_prompt_path),
                        "prompt": user_prompt,
                        "raw_output": raw_output,
                        "question_echo": model_question,
                    },
                    "qa": qa_objects,
                    "validation_status": status,
                    "validation_error": error,
                }
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                log(f"{record['qa_id']}: {status}" + (f" ({error})" if error else ""))

    summary = {
        "backend": args.backend,
        "task": args.task,
        "trial_id": args.trial_id,
        "templates": templates,
        "spans": len(spans),
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
